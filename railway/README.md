# Deploying the hub on Railway

Production runs on Railway (Hobby plan). `docker/docker-compose.yml` stays as the
local environment — same images, same configuration files, so what runs on a laptop
and what runs in production do not drift.

## Why Railway

Decided 2026-07-29, after a VPS variant had already been written and validated. The
hub is five small services for one user: a VPS costs about the same and adds a server
to patch, harden and back up — maintenance that has nothing to do with the goal.
Railway gives private networking by default and no machine at all. The
infrastructure-practice argument belongs to a different project; this one is meant to
be *used*, not demonstrated. Reasoning in full: `../docs/DECISIONS.md`.

## Topology

Five services in one project. **None of them takes a public Railway domain** — the
only ingress is the tunnel, so the platform's own hostnames stay unused and there is
no second door.

| Service | Reached at | Public? |
|---|---|---|
| `otel-collector` | `otel-collector.railway.internal:8889` (metrics), `:4318` (OTLP) | via tunnel only |
| `prometheus` | `prometheus.railway.internal:9090` | never — no auth of its own |
| `grafana` | `grafana.railway.internal:3000` | via tunnel + Cloudflare Access |
| `status-api` | `status-api.railway.internal:8000` | via tunnel + Access service token |
| `cloudflared` | — | outbound only |

Private DNS is `SERVICE_NAME.railway.internal`. Environments created after October
2025 resolve it on both IPv4 and IPv6, so nothing has to listen on `::` — legacy
environments are IPv6-only, which would break a service bound to `0.0.0.0`.

## Per-service settings

Config-as-code overrides the dashboard, but two things are **not** in the config file
and must be set in the UI: the **Root Directory**, the **config file path**, and
volumes. The config path does not follow the root directory — it is absolute from the
repository root.

| Service | Root Directory | Config file path | Volume |
|---|---|---|---|
| `otel-collector` | *(empty — repo root)* | `/railway/otel-collector/railway.json` | none |
| `prometheus` | *(empty)* | `/railway/prometheus/railway.json` | 1 GB at `/prometheus` |
| `grafana` | *(empty)* | `/railway/grafana/railway.json` | none |
| `cloudflared` | *(empty)* | `/railway/cloudflared/railway.json` | none |
| `status-api` | `services/public-status-api` | `/railway/status-api/railway.json` | none |

Root Directory is also the build context. Four of the five leave it empty on purpose:
their Dockerfiles copy configuration out of `docker/`, so they need the whole repo.
The status API is the exception — its Dockerfile uses paths relative to its own
directory.

Volume size depends on the plan: **Trial caps volumes at 0.5 GB**, Hobby at 5 GB.
Either is generous for a hub storing a handful of series at a 15s scrape — start at
the trial cap and raise it later if a Phase 2 producer arrives.

### Starting on the Trial plan

Per service the Trial gives 1 GB RAM, 2 vCPU and 4 GB image size — comfortably above
what any of these five need (the largest sits around 300 MB). Two limits will be felt
before the resources are:

- **$5 of one-time credit.** At this footprint the project runs somewhere around
  $10/month, so the trial is roughly a fortnight of real use, not a free tier.
- **Images are kept 24 hours.** Rolling back to a build older than that is not
  possible; redeploying from the commit is.

Neither blocks a first deploy. They decide when the Hobby plan stops being optional.

## Variables

| Service | Variables |
|---|---|
| `cloudflared` | `TUNNEL_TOKEN` |
| `otel-collector` | `OTLP_INGEST_TOKEN` |
| `grafana` | `GF_SECURITY_ADMIN_PASSWORD`, `GF_AUTH_ANONYMOUS_ENABLED=false` |
| `status-api` | `PROMETHEUS_URL=http://prometheus.railway.internal:9090`, `STATUS_API_TOKEN`, `SENTRY_DSN` (optional) |

Generate the two tokens once, with `openssl rand -hex 32`. `OTLP_INGEST_TOKEN` also
goes into Claude Code's `OTEL_EXPORTER_OTLP_HEADERS` (see
`../docs/CLAUDE_CODE_TELEMETRY.md`); `STATUS_API_TOKEN` also goes into the site's
Worker secrets.

## Tunnel ingress

Same three hostnames as `../docs/CLOUDFLARE_TUNNEL_SETUP.md`, pointed at the internal
DNS names instead of Docker service names:

- `grafana.yourdomain.com` → `http://grafana.railway.internal:3000`
- `status.yourdomain.com` → `http://status-api.railway.internal:8000`
- `otel.yourdomain.com` → `http://otel-collector.railway.internal:4318`

## Three things to check on the first deploy

These are the places where a platform behaves differently from a laptop, and none of
them can be verified without an account. Check them deliberately rather than
discovering them from an empty dashboard.

1. **Prometheus and its volume's ownership.** The image runs as `nobody` (65534). If
   Railway presents the volume owned by root, Prometheus exits with a permission
   error on `/prometheus` at startup. The fallback is a `USER 0` line in
   `railway/prometheus/Dockerfile` — a real loss (it is the only service that would
   then run as root), so try it as written first and read the logs.
2. **Whether the built image's `CMD` survives.** Every Dockerfile here states its
   command explicitly instead of relying on Railway's `startCommand`, precisely so
   this is one thing rather than two. If a service starts with the wrong arguments,
   check for a `startCommand` left over in the dashboard — config-as-code does not
   clear dashboard values it does not mention.
3. **Railway ignores the Dockerfile `HEALTHCHECK`.** It has its own
   `healthcheckPath`, which is deliberately unset here: the status API's only
   endpoint requires a token and would answer 401 to an unauthenticated probe. The
   consequence is that a deploy counts as successful when the container starts, not
   when it serves. `scripts/verify-hub.sh` is what actually confirms the hub works,
   and it stays a manual step after deploying.

## What the move actually cost

Five files. Everything else survived unchanged: the Collector configuration and its
label allow-list, the dashboards, the status API and its tests, the CI gates, the
site widget, the Cloudflare Tunnel design and every security decision behind it.
Measuring that *before* choosing is what made the decision cheap instead of
agonising — and it is the reason this repo now has no second deployment path to keep
in sync.
