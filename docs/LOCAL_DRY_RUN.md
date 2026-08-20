# Running the whole stack locally

Everything except `cloudflared` runs on a laptop with a Docker daemon. This is how
the metric names, the ingest auth and the label set were established as facts rather
than as readings of the vendor docs — repeat it whenever a producer, an image or a
Claude Code version changes.

## 1. Fake secrets, real stack

```bash
cd docker
cat > .env <<EOF
CLOUDFLARE_TUNNEL_TOKEN=unused-locally
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 8)
STATUS_API_TOKEN=$(openssl rand -hex 16)
OTLP_INGEST_TOKEN=$(openssl rand -hex 16)
SENTRY_DSN=
EOF
```

`.env` is gitignored. In production the compose network is the only route between
services, so nothing publishes ports; locally you need them. Keep that override out
of the repo:

```bash
cat > /tmp/ports.yml <<'EOF'
services:
  otel-collector: { ports: ["4318:4318", "8889:8889"] }
  prometheus:     { ports: ["9090:9090"] }
  grafana:        { ports: ["3000:3000"] }
  status-api:     { ports: ["8000:8000"] }
EOF

docker compose -f docker-compose.yml -f /tmp/ports.yml up -d --build \
  otel-collector prometheus grafana status-api
```

`cloudflared` is deliberately not started: without a real tunnel token it would only
restart-loop.

The compose file gives each container a network alias matching its Railway internal
DNS name, so `prometheus.yml` and Grafana's datasource — which name
`otel-collector.railway.internal` and `prometheus.railway.internal` — are correct
here too. That is why there is one copy of each config rather than a local one and a
production one drifting apart.

## 2. Feed it the real client

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=otlp \
       OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
       OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
       OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $OTLP_INGEST_TOKEN" \
       OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative \
       OTEL_METRIC_EXPORT_INTERVAL=5000
claude -p "reply only: ok" --model claude-haiku-4-5-20251001
```

A short export interval matters: the default batches on the order of minutes, and
you will conclude the pipeline is broken while it is merely waiting.

## 3. The four checks worth running

```bash
# Ingest auth: expect 401, 401, 200
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:4318/v1/metrics -H 'Content-Type: application/json' -d '{}'
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:4318/v1/metrics -H 'Content-Type: application/json' -H 'Authorization: Bearer wrong' -d '{}'
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:4318/v1/metrics -H 'Content-Type: application/json' -H "Authorization: Bearer $OTLP_INGEST_TOKEN" -d '{}'

# Everything below reads the exporter's LIVE /metrics, where a series lives for five
# minutes after its last export (`metric_expiration`, and see §4). So run these right
# after feeding the client — and run this guard first, because on an empty /metrics
# every grep that follows "passes" by matching nothing. A privacy check that reports
# "no identifying label found" when there was nothing to look at is the same lie as an
# alert rule that cannot fire.
curl -s localhost:8889/metrics | grep -c '^claude_code' # must be > 0, else re-export

# Metric names as Prometheus will actually see them
curl -s localhost:8889/metrics | grep -v '^#' | grep -o '^claude_code[a-z_]*' | sort -u

# Labels — read them, don't assume them. Nothing identifying may appear here.
curl -s localhost:8889/metrics | grep claude_code | grep -o '[a-z_]*=' | sort -u

# The allow-list holds against attributes nobody emits yet: send one and check it
# is gone. This is the check that a delete-list would fail.
curl -s -X POST localhost:4318/v1/metrics -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $OTLP_INGEST_TOKEN" -d '{"resourceMetrics":[{"resource":{"attributes":[]},
  "scopeMetrics":[{"metrics":[{"name":"claude_code.session.count","sum":{"aggregationTemporality":2,
  "isMonotonic":true,"dataPoints":[{"asDouble":1,"timeUnixNano":"1","attributes":[
  {"key":"user.name","value":{"stringValue":"whoever"}}]}]}}]}]}]}'
curl -s localhost:8889/metrics | grep -c user_name    # expect 0
curl -s localhost:8889/metrics | grep -c session_id   # expect > 0 — proves the 0 above
                                                      # means "dropped", not "empty"

# End to end: expect 401, 401, then the three fields
curl -s -H "Authorization: Bearer $STATUS_API_TOKEN" localhost:8000/status
```

Grafana is on `localhost:3000` (`admin` + the password from `.env`); the dashboard
and the datasource arrive by provisioning, nothing to import.

## 4. Two behaviours that look like bugs and are not

- **The three numbers read `0` right after startup**, until a session has actually
  reported. The window is the PromQL range — `max_over_time(...[25h])` — not the
  Collector's `metric_expiration`, which is back at its 5m default. It was the other
  way round until 2026-08-14, and that is worth one check of its own:

  ```bash
  # From docker/, where §1 left you. Send three sessions — three DIFFERENT session.id
  # values, see the next bullet — then read, restart only the Collector, read again.
  curl -s -H "Authorization: Bearer $STATUS_API_TOKEN" localhost:8000/status
  docker compose -f docker-compose.yml restart otel-collector
  sleep 20   # one scrape, so Prometheus notices the exporter came back empty
  curl -s -H "Authorization: Bearer $STATUS_API_TOKEN" localhost:8000/status
  ```

  **The two readings must match.** Measured on 2026-08-14 with three sessions of 2200
  tokens: `3 / 6600 / $0.03` both times, while `sum(claude_code_session_count)` — the
  query this replaced — came back **empty** from the same Prometheus. An earlier run,
  with one session still live, read 3 / 6600 before and 1 / 3300 after.

  If the readings diverge here, the pair of settings has drifted apart: check that
  `metric_expiration` is still short *and* that the queries still carry their range.
  Raising expiration back to 25h under a 25h window does not undercount — it counts
  each session for roughly twice as long.
- **A synthetic payload can lie about the shape of the data.** Re-sending the same
  `session.id` with a higher value manufactures growth that real sessions never
  produce — each real session is its own series, born and then flat. That is how an
  `increase()`-based query passed a local test and returned zero in production. When
  testing a query, send *two different* session ids rather than one twice.
- **`increase()` over-reports on sparse data.** Prometheus extrapolates to the window
  edges: measured here, real growth of 23787 tokens was reported as 27956 (+17%) with
  only two samples in the window. With continuous 15s scrapes the error is small, but
  these numbers are indicative, not accounting — and that is not a workaround, it is
  what the tool is for. Prometheus's own overview: *"If you need 100% accuracy, such
  as for per-request billing, Prometheus is not a good choice, as the collected data
  will likely not be detailed and complete enough."*

## 5. Write down that you ran it

The label half of this dry run is the only check that exists against a *new* Claude
Code version, and until 2026-08-20 it ran when somebody remembered. Now
`.github/workflows/telemetry-baseline.yml` asks the npm registry once a week what
version the client is on and goes red when its **minor** moves past what is recorded
in `docs/telemetry-baseline.json`.

So when you finish a run, update that file — the version you ran against and the
date:

```bash
python3 - <<'EOF'
import datetime, json, pathlib, re, subprocess
# `claude --version` puo' stampare righe di nvm prima della sua: si cerca la
# riga giusta, non la prima. Se il formato cambia questo esplode, ed e' quello
# che deve fare — meglio di scrivere "Running" dentro il verbale.
uscita = subprocess.check_output(["claude", "--version"], text=True)
versione = re.search(r"\b(\d+\.\d+\.\d+)\b[^\n]*\(Claude Code\)", uscita).group(1)

p = pathlib.Path("docs/telemetry-baseline.json")
v = json.loads(p.read_text())
v["versione"] = versione
v["dry_run"] = datetime.date.today().isoformat()
p.write_text(json.dumps(v, indent=2, ensure_ascii=False) + "\n")
print(v["versione"], v["dry_run"])
EOF
```

**Update it after the run, never to turn the job green.** The file is the record of a
measurement; editing it without measuring turns a reminder into a lie, and a job that
went green for the wrong reason is worse than one that was never written.

## 6. Leave the laptop as you found it

```bash
docker compose down -v && rm docker/.env
```
