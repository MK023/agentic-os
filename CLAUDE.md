# CLAUDE.md — agentic-os

> Project memory, loaded every turn. Short and dense. General rules (PRs, baseline
> security, the two MUST models) live in the global CLAUDE.md — here only what is
> specific to this repo. A section that grows becomes a file with a path pointer.

## What this is

Personal observability hub for AI-assisted work. Phase 1: one Hostinger VPS
(Terraform) running OTel Collector → Prometheus → Grafana + a tiny FastAPI status
API, all behind a Cloudflare Tunnel, with a public widget living in the
`marcobellingeri.dev` repo. The priority here is **the public/private boundary**:
three aggregate numbers are public, everything else never leaves the VPS.

## Layout

- `terraform/hostinger-vps/` — VPS provisioning. **Start here** for infra.
- `docker/` — the whole runtime: collector config, Prometheus, Grafana provisioning,
  compose file. `docker/.env` is real secrets on the VPS, never in git.
- `services/public-status-api/` — the only application code (and the only place
  coverage/mutation thresholds apply).
- `scripts/` — `bootstrap.sh` (runs on the VPS at first boot), `verify-hub.sh`
  (post-deploy smoke test).

## Commands

```bash
terraform -chdir=terraform/hostinger-vps fmt -check && terraform -chdir=terraform/hostinger-vps validate
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
- **Autonomy stops before real infrastructure**: `terraform apply`, `cloudflared
  tunnel create`, Cloudflare Access policies and filling `docker/.env` on the VPS are
  Marco's, always. Writing and validating the code for them is not.
- Vendor behaviour is read from the vendor's docs before it is written down here.
  Twelve bugs in the original plan survived an execute-everything pass and died to a
  read-the-docs pass; that is the house lesson.

## Conventions

- Metric names live in three places — the Collector's `translation_strategy`,
  `docker/grafana/dashboards/claude-code.json`, and `QUERIES` in `main.py`. Change one,
  change all three.
- Every image and action is pinned to a version or SHA. Never `:latest`.
- Comments say *why*, and name the failure they prevent.

## Security (non-negotiable)

- Public surface is exactly three numbers. No free-form PromQL, no session content.
- OTLP ingest authenticates **inside the Collector** (`bearertokenauth`): a public
  Tunnel hostname is not an access control (CVE-2026-28798 pattern).
- Prometheus never gets a Tunnel hostname — it has no auth of its own.
- Secrets live in `docker/.env` on the VPS and in GitHub/Worker secrets. Never in git.

## What NOT to do (closed decisions — don't reopen without new data)

- **No Kubernetes/K3s**, no self-hosted Langfuse, no LangChain. One VPS, one user.
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
- No app-level rate limiter in the status API: Cloudflare does it at the edge.

## References (read on demand)

`README.md` (pipeline level, test contract, gate policy) · `docs/BLOCKERS.md` (what is
blocked on which account, with evidence) · `docs/CLOUDFLARE_TUNNEL_SETUP.md` ·
`docs/CLAUDE_CODE_TELEMETRY.md` · `docs/superpowers/specs/` and `plans/` (why, and the
plan as executed) · `TASKS.md` (checklist + the bug table).
