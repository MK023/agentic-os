# Deploying the hub on Railway

Production runs on Railway (Hobby plan). `docker/docker-compose.yml` stays as the
local environment — same images, same configuration files, so what runs on a laptop
and what runs in production do not drift.

## Why Railway

Decided 2026-07-29, after a VPS variant had already been written and validated. The
hub is six small services for one user: a VPS costs about the same and adds a server
to patch, harden and back up — maintenance that has nothing to do with the goal.
Railway gives private networking by default and no machine at all. The
infrastructure-practice argument belongs to a different project; this one is meant to
be *used*, not demonstrated. Reasoning in full: `../docs/DECISIONS.md`.

## Topology

Six services in one project — `loki` since 2026-08-21. **None of them takes a public
Railway domain** — the
only ingress is the tunnel, so the platform's own hostnames stay unused and there is
no second door.

| Service | Reached at | Public? |
|---|---|---|
| `otel-collector` | `otel-collector.railway.internal:8889` (metrics), `:4318` (OTLP over HTTP), `:4317` (OTLP over gRPC, bound on all interfaces and authenticated the same way) | via tunnel only |
| `prometheus` | `prometheus.railway.internal:9090` | never: it could authenticate (`--web.config.file`) and is not configured to, so no route is the control |
| `grafana` | `grafana.railway.internal:3000` | via tunnel + Cloudflare Access |
| `status-api` | `status-api.railway.internal:8000` | via tunnel + Access service token |
| `loki` | `loki.railway.internal:3100` | never: no Tunnel route, and `auth_enabled: false` is about multi-tenancy, not access |
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
| `prometheus` | *(empty)* | `/railway/prometheus/railway.json` | **5 GB** at `/prometheus` |
| `grafana` | *(empty)* | `/railway/grafana/railway.json` | none |
| `loki` | *(empty)* | `/railway/loki/railway.json` | **5 GB** at `/loki` |
| `cloudflared` | *(empty)* | `/railway/cloudflared/railway.json` | none |
| `status-api` | `services/public-status-api` | `/railway/status-api/railway.json` | none |

Root Directory is also the build context. Five of the six leave it empty on purpose:
their Dockerfiles copy configuration out of `docker/`, so they need the whole repo.
The status API is the exception — its Dockerfile uses paths relative to its own
directory.

Volume size depends on the plan: **Free and Trial cap volumes at 0.5 GB**, Hobby at
5 GB, and resizing is a paid-plan operation. **Do not "start small and raise it
later"** — that advice used to be here and it is the recipe for the incident this
project has now had twice. `--storage.tsdb.retention.size` is the only back-pressure
Prometheus has from the disk, and a cap **above** the volume can never fire: the disk
fills, compaction fails once a minute, and ingestion keeps working out of the head in
RAM, so nothing looks wrong from outside while persistence is already dead. The cap
is `3GB` (3 GiB) and therefore requires a volume of at least 5 GB. If the volume ever
changes, the cap in `railway/prometheus/Dockerfile` and `docker/docker-compose.yml`
changes in the same commit — the CI gate compares those two files and the Grafana
panel to each other, but nothing can compare them to a volume size that lives only in
the Railway UI.

### Restart policy

`ON_FAILURE` on five services, `ALWAYS` on `loki`. The general rule and its exception
both come from the same reasoning, so read them together.

`ON_FAILURE` is the default here because these are daemons: a clean exit is not a normal
event, and the honest response to one is to notice it rather than restart forever and
never find out. (This section also used to say the Trial plan does not offer `ALWAYS`.
The project moved to Hobby on 2026-08-19, so that constraint is gone.)

`loki` is the exception, and copying the general rule onto it would undo a fix. Loki
serves `GET /ingester/shutdown` on its private port with no authentication, and it exits
with code **0**, which `ON_FAILURE` reads as a deliberate stop. Measured: one
unauthenticated GET stopped the log store and nothing brought it back. `ALWAYS` turns a
permanent stop into a restart. It does not close the route, which cannot be closed, and
`docs/DECISIONS.md` has what that trade costs. A step in `images.yml` fails if this value
changes.

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
| `cloudflared` | `TUNNEL_TOKEN`, `PORT=2000` (same reason as `status-api`: it has a `healthcheckPath` on `/ready`) |
| `otel-collector` | `OTLP_INGEST_TOKEN` |
| `prometheus` | `RAILWAY_RUN_UID=0` (see "volume ownership" below — not a secret, and not optional) |
| `grafana` | `GF_SECURITY_ADMIN_PASSWORD`, `SLACK_WEBHOOK_URL` (set 2026-08-21; without it the entrypoint substitutes a placeholder and alerts evaluate but never send). `GF_AUTH_ANONYMOUS_ENABLED=false` was listed here and has been removed: it repeated the vendor default and read as hardening without changing anything. The settings that do change something live in `docker/grafana/grafana.ini` |
| `status-api` | `PROMETHEUS_URL=http://prometheus.railway.internal:9090`, `STATUS_API_TOKEN`, `SENTRY_DSN` (optional), `PORT=8000` (Railway probes the port this names; declaring `healthcheckPath` without it fails every deploy) |
| `loki` | the four `LOKI_R2_*`, plus `RAILWAY_RUN_UID=0` (same reason as `prometheus`). `LOKI_R2_ENDPOINT` goes in **without** a scheme — `-verify-config` accepts the `https://` form and Loki dies at startup on it |

Generate the two tokens once, with `openssl rand -hex 32`. `OTLP_INGEST_TOKEN` also
goes into Claude Code's `OTEL_EXPORTER_OTLP_HEADERS` (see
`../docs/CLAUDE_CODE_TELEMETRY.md`); `STATUS_API_TOKEN` also goes into the site's
Worker secrets.

## Tunnel ingress

Same three hostnames as `../docs/CLOUDFLARE_TUNNEL_SETUP.md`, pointed at the internal
DNS names instead of Docker service names:

- `grafana.marcobellingeri.dev` → `http://grafana.railway.internal:3000`
- `status.marcobellingeri.dev` → `http://status-api.railway.internal:8000`
- `otel.marcobellingeri.dev` → `http://otel-collector.railway.internal:4318`

## Three things to check on the first deploy

These are the places where a platform behaves differently from a laptop, and none of
them can be verified without an account. Check them deliberately rather than
discovering them from an empty dashboard.

1. ~~**Prometheus and its volume's ownership.**~~ **Answered on 2026-07-29 by the
   first deploy, and the answer was no.** With the volume attached, Prometheus
   exited immediately:

   ```
   Error opening query log file file=/prometheus/queries.active
   err="open /prometheus/queries.active: permission denied"
   ```

   The image runs as `nobody` (65534) and Railway presents the volume owned by root.
   Railway documents exactly one supported answer: *"Docker images that run as a
   non-root UID by default will have permissions issues when performing operations
   within an attached volume. If you are affected by this, you can set
   `RAILWAY_RUN_UID=0` environment variable in your service."*

   So `prometheus` carries `RAILWAY_RUN_UID=0` and runs as root — and since
   2026-08-21 `loki` does too, for the same reason: its image runs as `10001` and its
   volume on `/loki` is presented owned by root. **This paragraph said "and only that
   one" until then**, which is the sentence a second volume was always going to
   falsify. The cost is stated plainly: these are the two services in this project
   without the non-root property. The `cap_drop` clause used to read as though this
   service were the exception; it is not. **No Railway service has `cap_drop` or
   `no-new-privileges`** — the platform exposes no equivalent, so those two lines in
   `docker/docker-compose.yml` are local fidelity, not a production control, for all
   six services. What limits the exposure here is that Prometheus has no public
   domain and is reachable only from inside the project's private network.

   The variable, not `USER 0` in the Dockerfile, on purpose: the weakening then
   applies only where it is needed. Locally, Docker gives a named volume the image
   user's ownership, so `docs/LOCAL_DRY_RUN.md` keeps running Prometheus unprivileged.
2. **Whether the built image's `CMD` survives.** Every Dockerfile here states its
   command explicitly instead of relying on Railway's `startCommand`, precisely so
   this is one thing rather than two. If a service starts with the wrong arguments,
   check for a `startCommand` left over in the dashboard — config-as-code does not
   clear dashboard values it does not mention.
3. **Railway ignores the Dockerfile `HEALTHCHECK`.** It has its own
   `healthcheckPath`, declared since 2026-08-20 on the two services that can serve
   one: `/healthz` on status-api and `/ready` on cloudflared. Both need a `PORT`
   service variable — Railway probes the port that variable names, not the port the
   process listens on, and declaring the path without it fails every deploy while the
   old container keeps serving. `PORT=8000` and `PORT=2000` respectively; those two
   numbers live on Railway, outside git, and must match the `CMD` in each Dockerfile.
   Set `PORT` **first**, then the path — never the other way round.
   The remaining three services declare no path: grafana and prometheus would need an
   unauthenticated 200, and the Collector's only route is the authenticated ingest.
   For them a deploy still counts as successful when the container starts, not when it
   serves. And Railway "does not monitor the healthcheck endpoint after the deployment
   has gone live" — it is a deploy gate, never monitoring. `scripts/verify-hub.sh` is
   what actually confirms the hub works. Since 2026-08-20 `sorveglianza.yml` also runs it
   every day at 05:23 UTC, so "manual" now means "run it yourself after a deploy instead
   of waiting for the cron", not "nothing runs it".

## Symptom to cause

Everything here was met at least once on 2026-07-29. Each row is a symptom that does
**not** name its own cause, which is why the table exists.

| What you see | What it means |
|---|---|
| Build log says `railpack` / *"could not determine how to build the app"* | The **config file path** is not set on the service. It is absolute from the repo root and does not follow Root Directory |
| Build fails on `COPY ... not found` | Root Directory is set on a service whose Dockerfile copies from `docker/` — it needs the whole repo as context |
| Dashboard still shows Railpack after the fix | Expected. Config-as-code overrides at deploy time without changing the panel — the build log is the truth |
| Prometheus restarts, `permission denied` on `/prometheus` | The volume is root-owned. That service needs `RAILWAY_RUN_UID=0` |
| Prometheus logs `fs_type=OVERLAYFS_SUPER_MAGIC` | It is writing to ephemeral storage: the volume is not mounted on the running deployment. `EXT4` is what you want |
| Everything green, dashboards empty | Query `up` in Grafana Explore. `1` means Prometheus scrapes fine and nothing has ever arrived — look upstream, at the producer |
| `claude_code_*` series missing while `up = 1` | No metric ever reached the Collector. Check the client's headers (see `docs/CLAUDE_CODE_TELEMETRY.md`) |
| Widget returns `null` fields | The Worker could not reach the hub — or the site deploy that introduced `AGENTIC_OS_STATUS_URL` has not finished yet |
| Widget returns `0` fields | It **did** reach the hub. Zero is data, `null` is failure. The `cache-control` header tells them apart: `max-age=30` on success, `no-store` on the degraded path |
| Status API answers 403 | Cloudflare Access — the service token or the policy |
| Status API answers 401 | The app's own bearer token: `AGENTIC_OS_STATUS_TOKEN` on the Worker does not match `STATUS_API_TOKEN` on Railway |
| Status API answers 502 | It is alive and Prometheus is not answering. Sentry has the exception |
| `cloudflared` restarts, *"Provided Tunnel token is not valid"* | `--token` or whitespace ended up inside `TUNNEL_TOKEN` |
| The numbers are **flat** and you cannot tell whether that is a fault | Read the *Payload in arrivo al Collector* panel: above zero the data is arriving and you simply were not working; at zero no client is exporting. The other four panels cannot separate those two cases — a cumulative counter that stops growing looks the same either way |

## What the move actually cost

Five files. Everything else survived unchanged: the Collector configuration and its
label allow-list, the dashboards, the status API and its tests, the CI gates, the
site widget, the Cloudflare Tunnel design and every security decision behind it.
Measuring that *before* choosing is what made the decision cheap instead of
agonising — and it is the reason this repo now has no second deployment path to keep
in sync.
