# CLAUDE.md — agentic-os

> Project memory, loaded every turn. Short and dense. General rules (PRs, baseline
> security, the two MUST models) live in the global CLAUDE.md — here only what is
> specific to this repo. A section that grows becomes a file with a path pointer.

## What this is

Personal observability hub for AI-assisted work. Phase 1: OTel Collector →
Prometheus → Grafana plus a tiny FastAPI status API on **Railway**
behind a Cloudflare Tunnel, with a public widget living in the
`marcobellingeri.dev` repo. **Phase 1.5 adds a sixth service, `loki`** — a private
log store whose chunks and index live on **Cloudflare R2**, so a full Railway volume
cannot take them with it; the local disk holds only the active index and the
`tsdb_shipper` cache. **Live since 2026-08-21**: six in `docker/` and `railway/` is now
also six on the platform. What that deploy proved, rather than assumed: Loki wrote
`loki_cluster_seed.json` to R2 on startup, so the credentials, the schemeless endpoint
and `use_thanos_objstore: true` all work in production — and the 5 GB volume mounted
without the `permission denied` that `RAILWAY_RUN_UID=0` exists to prevent. What it did
**not** prove is the log path end to end: nothing reaches Loki until a client exports
with `OTEL_LOGS_EXPORTER=otlp`, and until then an empty log store is the correct state,
not a fault.
The priority here is **the public/private boundary**:
three aggregate numbers are public, everything else stays inside the project.

## Layout

- `railway/` — production: one Dockerfile and one `railway.json` per service, plus
  `railway/README.md`, which is the deployment guide. **Start here** for infra.
- `docker/` — the local environment and the single source of every configuration
  file the Railway images copy in. `docker/.env` is local fake secrets, never in git.
- `services/public-status-api/` — the only application code (and the only place
  coverage/mutation thresholds apply).
- `scripts/` — `verify-hub.sh`, the fuller post-deploy check (Grafana behind
  Access + the status API's bearer). Manual: its credentials are not GitHub
  secrets. `.github/workflows/smoke.yml` is the automatic half — public endpoint
  only, no secrets, and it fails on `null` *and* on `stale`, never on the HTTP
  status alone. Railway ignores the Dockerfile `HEALTHCHECK`; it uses its own
  `healthcheckPath`, declared since 2026-08-20 on status-api (`/healthz`) and
  cloudflared (`/ready`) and **requiring a `PORT` service variable to work at all**.
  On the other four — `loki` included since Phase 1.5, same reason written in
  `railway/loki/Dockerfile` — `SUCCESS` still only means "the container started", **and that is
  a closed decision, not a gap**: their health routes would be unauthenticated, and a
  Prometheus `up` scrape watches every one of them every 15s for as long as they live,
  which is more than a healthcheck does — Railway stops probing once the deploy is live.
  **That sentence was false until 2026-08-20** and had been for months: `docker/prometheus.yml`
  scraped the Collector, cloudflared and itself, never Grafana, and the Loki job did not
  exist while this file already claimed it did. Six jobs now — the Collector has two
  (`:8889` exported, `:8888` internal), and `status-api` is the one service deliberately
  not scraped, because it is the one with both a `healthcheckPath` and an outside
  witness in `smoke.yml`. An asserted control that is absent is worse than a declared
  gap: the reader stops looking for it.

## Commands

```bash
docker build -f railway/prometheus/Dockerfile -t p .   # same for the other four
docker compose -f docker/docker-compose.yml config --quiet   # the 9 env vars only silence warnings; it exits 0 without them
bash scripts/prova-privacy-log.sh                              # gate: runs the log privacy proof, ~90s, Docker only
cd services/public-status-api && pytest test_main.py -q --cov=. --cov-report=term
pip-audit -r services/public-status-api/requirements.txt       # gate: any advisory fails
zizmor --min-severity=high .github/workflows/                  # gate: blocks on HIGH
checkov --config-file .checkov.yml -d .
cd services/public-status-api && mutmut run && mutmut results  # 0 survivors in require_valid_token
```

Dependencies are hash-locked: edit `requirements.in`, then
`pip-compile --generate-hashes --output-file requirements.txt requirements.in`.
Never hand-edit `requirements.txt`.

To validate the Collector config without a Docker daemon, download the real
`otelcol-contrib` binary and run `otelcol-contrib validate --config=file:...` —
`docker compose config` does **not** check it.

## How we work here

- Branch + PR, never straight to `main`. All gates green before "done".
- **Autonomy stops before real infrastructure**: creating Railway services, setting
  their variables, `cloudflared tunnel create` and the Cloudflare Access policies are
  Marco's, always. Writing and validating the code for them is not.
- Vendor behaviour is read from the vendor's docs before it is written down here.
  Twelve bugs in the original plan survived an execute-everything pass and died to a
  read-the-docs pass; that is the house lesson.
- **MUST: this project runs on Railway's free credits, so deploys queue and take
  time.** Never merge a run of PRs back to back and call it done. A push that arrives
  while a build is still in flight **cancels** that build, and the replacement deploy
  is `SKIPPED` if its own commit misses that service's `watchPatterns` — so the change
  lands on `main` and never reaches production, green everywhere, silently. That
  happened on 2026-08-13: #39 bumped Prometheus, #40 landed 72 seconds later, and
  production stayed on the old image. **After merging more than one PR, check the
  running commit of each affected service** (`list-deployments` → the newest `SUCCESS`
  and its `commitHash`), not just that the deploys are green. `SKIPPED` and `REMOVED`
  are the two statuses that mean "did not ship".

## Conventions

- Metric names live in three places — the Collector's `translation_strategy`,
  `docker/grafana/dashboards/claude-code.json`, and `QUERIES` in `main.py`. Change one,
  change all three.
- Every image and action is pinned to a version or SHA. Never `:latest`.
- Comments say *why*, and name the failure they prevent.

One rule holds both environments together: **there is exactly one copy of every
configuration file**, in `docker/`. The Railway images copy them at build time, the
compose file mounts them, and compose gives each container a network alias equal to
its Railway internal DNS name (`<service>.railway.internal`) so hostnames inside
those files are correct in both places. Never fork a config to "fix it for local".

## Security (non-negotiable)

- Public surface is exactly three numbers. No free-form PromQL, no session content.
- OTLP ingest authenticates **inside the Collector** (`bearertokenauth`): a public
  Tunnel hostname is not an access control (CVE-2026-28798 pattern).
- Prometheus never gets a Tunnel hostname. **Not** because it cannot authenticate —
  it supports TLS and basic auth via `--web.config.file`, verified against the vendor's
  security page — but because it is not configured to, and no route means nothing to
  authenticate. Do not restate the old, false reason.
- Secrets are Railway service variables in production, `docker/.env` locally (fake
  values, gitignored), and GitHub/Worker secrets elsewhere. Never in git.

## What NOT to do (closed decisions — don't reopen without new data)

- **No Kubernetes/K3s, no Docker Swarm**, no self-hosted Langfuse, no LangChain.
  Six small services for one user: an orchestrator buys nothing here, and Docker's
  own docs say to use Compose if you are not deploying to Swarm.
- **No going back to a VPS.** Decided 2026-07-29 and settled: the point of this
  platform is that there is no machine to maintain.
- **Never put the `prometheus` exporter in a `logs` pipeline** — metrics-only, the
  Collector refuses to start. **Since Phase 1.5 a gate watches this line**, and it is
  worth knowing which of the two it is: until then it was a sentence in a file no gate
  reads, and there was not even a `logs` pipeline to get wrong. Now
  `.github/workflows/images.yml` reads the pipeline and fails on that exporter, naming
  the consequence — the Collector does not start, so the *metrics* stop with it and the
  three public numbers freeze. A rule that is watched and a rule that is merely written
  look identical on the page and behave nothing alike.
- **Never turn the label allow-list into a delete-list**, and never assume
  `resource_to_telemetry_conversion: false` does that job: Claude Code sends identity
  as *data point* attributes, so a real email address reaches Prometheus without the
  processor (measured, not theorised). A delete-list fails open on every attribute a
  future release invents; the allow-list drops it by default. Adding a producer means
  adding its labels deliberately.
- **Never delete `session.id`** along with the identity attributes: the counters are
  cumulative per process, so without it concurrent sessions collapse into one series
  and the last export wins — two sessions read as one.
- **Never reduce the log path to one barrier, and never move the catch-all.** Two
  allow-lists on purpose: `transform/log-allowlist` in the Collector — **three**
  statements, `resource`, `scope` *and* `log`, all `keep_keys` — and `otlp_config` in
  `docker/loki.yaml` with `ignore_defaults: true` plus a `drop` catch-all **last** in all
  three sections. The `scope` statement is the one that looks droppable and is not:
  without it the scope attributes crossed the Collector untouched and only Loki's list
  stopped them, so the two "independent" barriers were not independent — measured
  2026-08-20 by running `scripts/prova-privacy-log.sh`, invisible to any gate that reads
  the config's shape. Identity rides on the **log records**, not on the resource, and the
  vendor sends it on every event with no environment variable to turn it off, so the
  allow-list is the only control on it and not a backup for one. Redaction of `prompt`
  and `response` is a *default*, and four `OTEL_LOG_*` variables switch it back off. The
  order is not cosmetic either: a catch-all at the top of `resource_attributes` makes
  Loki answer `400`, loudly; at the top of `log_attributes` the push returns `204`,
  `-verify-config` calls the config valid, and every useful piece of structured metadata
  disappears in silence. Two checks see that second case, and both run in CI: the
  allow-list gate reads the shape, `scripts/prova-privacy-log.sh` measures it.
- **Never guess Claude Code metric names.** The exporter's suffix behaviour is pinned
  precisely so they stop depending on unit metadata.
- **Never let a gate skip because its credential is missing** (the `sonar` job stays
  red instead) — that is `continue-on-error` with extra steps.
- No app-level rate limiter in the status API — that part stands. **What this line used
  to claim, "Cloudflare does it at the edge", was false**: measured 2026-08-16, 60
  requests in ~20s returned zero `429`, and the zone has no rate limiting rule at all.
  **And the correction outlived the gap**: the limiter shipped in the site's Worker on
  2026-08-16 (`marcobellingeri.dev` PR #216) — a per-IP rate limiting *binding*, 60
  requests / 60s, `Retry-After: 60` — and this file went on declaring the endpoint
  unthrottled for four days. A declared absence that has been filled is the same defect
  as an asserted control that does not exist; it just wastes worry instead of trust.
  Measured against production 2026-08-20: 75 requests in ~11s produced **zero** `429`,
  200 in ~26s produced **13**, first at request 167. The binding is per-datacentre and
  eventually consistent, so it is **a ceiling against a sustained flood, not a
  guillotine at the 61st request** — and knowing that before reading a graph is the
  difference between "it works" and "it is broken".
  **Since 2026-08-19 that gap costs money, not just CPU**: the project is on Railway's
  Hobby plan, billed on usage. **Railway's schema DOES expose a per-service cap**
  (`deploy.limitOverride`), contrary to what this line said on 2026-08-19 — it is
  undocumented in config-as-code and undeclared here, and since 2026-08-20 it is also
  **known not to be a cost lever**: `status-api` peaks at 0.0094 vCPU and 87 MB, and
  Railway bills consumption rather than provisioned capacity, so a cap above real usage
  changes the bill by nothing. It is a runaway guard. Details in `docs/DECISIONS.md`.
  **Since 2026-08-20 there are two further backstops, and neither is a rate limiter**:
  the workspace usage limit ($15 soft / $30 hard, set by the operator — no tool here can
  read it back) and a WAF custom rule on `otel.` that blocks everything except
  `POST /v1/metrics` carrying an `Authorization` header (presence only, never the
  value). The rule is defence in depth, never the auth — that stays `bearertokenauth`
  inside the Collector, and `smoke.yml` proves it with a *wrong* token, the only shape
  the edge still lets through. An asserted control that does not exist is worse than a
  declared absence — **and an absence still declared after it was filled is the same
  mistake wearing the opposite face**, which is what this bullet did for four days.

## References (read on demand)

`docs/DECISIONS.md` (every closed decision and what measuring changed about it — read
this before reopening anything) · `railway/README.md` (how production is deployed) ·
`README.md` (pipeline level, test contract, gate policy) · `docs/BLOCKERS.md` (what is
left) · `docs/CLOUDFLARE_TUNNEL_SETUP.md` · `docs/CLAUDE_CODE_TELEMETRY.md` ·
`docs/LOCAL_DRY_RUN.md` (how to verify behaviour instead of assuming it) ·
`docs/superpowers/specs/` (the design, realigned to Railway) · `SECURITY.md`
(what is exposed and what is not) · `CONTRIBUTING.md` (the loop and the gates).
