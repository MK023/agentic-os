# Running the whole stack locally, before any VPS exists

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

# Metric names as Prometheus will actually see them
curl -s localhost:8889/metrics | grep -v '^#' | grep -o '^claude_code[a-z_]*' | sort -u

# Labels — read them, don't assume them. Nothing identifying may appear here.
curl -s localhost:8889/metrics | grep claude_code | grep -o '[a-z_]*=' | sort -u

# End to end: expect 401, 401, then the three fields
curl -s -H "Authorization: Bearer $STATUS_API_TOKEN" localhost:8000/status
```

Grafana is on `localhost:3000` (`admin` + the password from `.env`); the dashboard
and the datasource arrive by provisioning, nothing to import.

## 4. Two behaviours that look like bugs and are not

- **The three numbers read `0` right after startup.** They are `increase()` over 24h,
  which measures growth *inside* the window; a series that has just appeared has not
  grown yet. Run a second session and they move.
- **`increase()` over-reports on sparse data.** Prometheus extrapolates to the window
  edges: measured here, real growth of 23787 tokens was reported as 27956 (+17%) with
  only two samples in the window. With continuous 15s scrapes the error is small, but
  these numbers are indicative, not accounting.

## 5. Leave the laptop as you found it

```bash
docker compose down -v && rm docker/.env
```
