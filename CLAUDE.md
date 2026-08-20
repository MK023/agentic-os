# CLAUDE.md — agentic-os

> Project memory, loaded every turn. Short and dense. General rules (PRs, baseline
> security, the two MUST models) live in the global CLAUDE.md — here only what is
> specific to this repo. A section that grows becomes a file with a path pointer.

## What this is

Personal observability hub for AI-assisted work. Phase 1: OTel Collector →
Prometheus → Grafana plus a tiny FastAPI status API, five services on **Railway**
behind a Cloudflare Tunnel, with a public widget living in the
`marcobellingeri.dev` repo. The priority here is **the public/private boundary**:
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
  On the other three, `SUCCESS` still only means "the container started" — and even
  where the path exists Railway stops probing once the deploy is live.

## Commands

```bash
docker build -f railway/prometheus/Dockerfile -t p .   # same for the other three
docker compose -f docker/docker-compose.yml config --quiet     # needs the 5 env vars set to anything
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
  Five small services for one user: an orchestrator buys nothing here, and Docker's
  own docs say to use Compose if you are not deploying to Swarm.
- **No going back to a VPS.** Decided 2026-07-29 and settled: the point of this
  platform is that there is no machine to maintain.
- **Never put the `prometheus` exporter in a `logs` pipeline** — metrics-only, the
  Collector refuses to start.
- **Never turn the label allow-list into a delete-list**, and never assume
  `resource_to_telemetry_conversion: false` does that job: Claude Code sends identity
  as *data point* attributes, so a real email address reaches Prometheus without the
  processor (measured, not theorised). A delete-list fails open on every attribute a
  future release invents; the allow-list drops it by default. Adding a producer means
  adding its labels deliberately.
- **Never delete `session.id`** along with the identity attributes: the counters are
  cumulative per process, so without it concurrent sessions collapse into one series
  and the last export wins — two sessions read as one.
- **Never guess Claude Code metric names.** The exporter's suffix behaviour is pinned
  precisely so they stop depending on unit metadata.
- **Never let a gate skip because its credential is missing** (the `sonar` job stays
  red instead) — that is `continue-on-error` with extra steps.
- No app-level rate limiter in the status API — that part stands. **What this line used
  to claim, "Cloudflare does it at the edge", was false**: measured 2026-08-16, 60
  requests in ~20s returned zero `429`, and the zone has no rate limiting rule at all.
  Nothing was throttling the public endpoint. Being added in the site's Worker, where the
  route is served; until it ships the honest statement is that the endpoint is unthrottled.
  **Since 2026-08-19 that gap costs money, not just CPU**: the project is on Railway's
  Hobby plan, billed on usage. **Railway's schema DOES expose a per-service cap**
  (`deploy.limitOverride`), contrary to what this line said on 2026-08-19 — it is
  undocumented in config-as-code, unmeasured on Hobby, and undeclared here.
  **Since 2026-08-20 there are two backstops, and neither is a rate limiter**: the
  workspace usage limit ($15 soft / $30 hard, set by the operator — no tool here can
  read it back) and a WAF custom rule on `otel.` that blocks everything except
  `POST /v1/metrics` carrying an `Authorization` header (presence only, never the
  value). The rule is defence in depth, never the auth — that stays `bearertokenauth`
  inside the Collector, and `smoke.yml` proves it with a *wrong* token, the only shape
  the edge still lets through. The Worker's rate limiter is still missing.
  An asserted control that does not exist is worse than a declared absence.

## References (read on demand)

`docs/DECISIONS.md` (every closed decision and what measuring changed about it — read
this before reopening anything) · `railway/README.md` (how production is deployed) ·
`README.md` (pipeline level, test contract, gate policy) · `docs/BLOCKERS.md` (what is
left) · `docs/CLOUDFLARE_TUNNEL_SETUP.md` · `docs/CLAUDE_CODE_TELEMETRY.md` ·
`docs/LOCAL_DRY_RUN.md` (how to verify behaviour instead of assuming it) ·
`docs/superpowers/specs/` (the design, realigned to Railway) · `SECURITY.md`
(what is exposed and what is not) · `CONTRIBUTING.md` (the loop and the gates).
