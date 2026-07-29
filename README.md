# Agentic OS — Claude Code Observability Hub

Personal platform to observe (and later orchestrate) AI-assisted work.
**Phase 1, live since 2026-07-29**: every Claude Code session streams metrics into a
private hub — OTel Collector → Prometheus → Grafana plus a small FastAPI status
API — running as five services on **Railway** behind a **Cloudflare Tunnel**, with a
sanitized public widget on [marcobellingeri.dev](https://marcobellingeri.dev).

[![validate](https://github.com/MK023/agentic-os/actions/workflows/validate.yml/badge.svg)](https://github.com/MK023/agentic-os/actions/workflows/validate.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=MK023_agentic-os&metric=alert_status)](https://sonarcloud.io/summary/overall?id=MK023_agentic-os)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=MK023_agentic-os&metric=coverage)](https://sonarcloud.io/component_measures?id=MK023_agentic-os&metric=coverage)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)

The interesting part is the **public/private boundary, measured rather than
assumed**. Claude Code sends identity — a real email address among it — as metric
attributes; the Collector's label **allow-list** is what keeps it out of storage,
verified against the real client, including attributes no version emits yet. The
public surface of the whole system is exactly three aggregate numbers.

## Architecture

```
 Claude Code ──OTLP + bearer token──▶ otel.marcobellingeri.dev
                                            │   Cloudflare Tunnel (only ingress:
                                            │   no service has a Railway domain)
                                            ▼
                                      otel-collector    auth INSIDE the Collector,
                                            │           label allow-list before storage
                                            ▼
                                       prometheus       no public hostname, ever
                                            │           (its API has no auth of its own)
                          ┌─────────────────┴──────────────┐
                          ▼                                ▼
                       grafana                         status-api
             grafana.marcobellingeri.dev      status.marcobellingeri.dev
             (Access: single-email policy)    (Access service token + own bearer)
                                                           │
                                                           ▼
                                      marcobellingeri.dev/api/agentic-status
                                      (Worker widget: sessions, tokens, cost)
```

The three numbers are **indicative, not accounting** — Prometheus's own docs rule it
out for billing-grade accuracy, and that is the tool's stated scope, not a defect.

## Layout

| Path | What |
|---|---|
| `railway/` | Production: one Dockerfile + one config-as-code file per service, and the deployment guide (`railway/README.md`). **Start here** for infra. |
| `docker/` | The local environment **and the single copy of every configuration file** — Railway images copy them at build time, compose mounts them. |
| `services/public-status-api/` | FastAPI service exposing the three whitelisted numbers. The only application code. |
| `scripts/` | `verify-hub.sh`, the post-deploy smoke test (Railway ignores the Dockerfile `HEALTHCHECK`). |
| `docs/` | Decisions, deployment, telemetry setup, local dry run — index at the bottom. |

## Running it

The whole stack (minus the tunnel connector) runs on a laptop with the same images
and the same config files as production — `docs/LOCAL_DRY_RUN.md` is the recipe,
including how to feed it the real Claude Code client and check the privacy boundary.

The gates, runnable locally:

```bash
docker compose -f docker/docker-compose.yml config --quiet   # needs the 5 env vars set to anything
cd services/public-status-api && pytest test_main.py -q --cov=. --cov-report=term
pip-audit -r services/public-status-api/requirements.txt
zizmor --min-severity=high .github/workflows/
checkov --config-file .checkov.yml -d .
bash scripts/check-image-users.sh
```

## Security

Measured, decided and written down in [SECURITY.md](SECURITY.md) and
[docs/DECISIONS.md](docs/DECISIONS.md); the short version:

- **OTLP ingest authenticates inside the Collector** (`bearertokenauth`) — a public
  Tunnel hostname is not an access control (the CVE-2026-28798 pattern).
- **Metric labels are an allow-list**, so an attribute nobody has seen yet is a
  missing label, not a leak. `session_id` stays: without it concurrent sessions
  collapse into one series (measured, not theorised).
- **Prometheus never gets a hostname**; Grafana and the status API sit behind
  Cloudflare Access (email policy / service token + own bearer — two layers).
- **Every image runs non-root and pinned to a version**, with one documented
  platform exception enforced single by a CI guard (`scripts/check-image-users.sh`).
- **Secrets**: Doppler is the source of truth, Railway variables the runtime copy,
  `docker/.env` fake and gitignored. Never in git; gitleaks over full history plus a
  pre-commit hook.

## Pipeline level: 1 — and why

Declared **Level 1** (`PR → lint → test → build → dependency audit`) against my own
CI/CD maturity model: one developer, personal project, rollback is a redeploy.
Canary or signed attestation for three Python dependencies would be cargo cult.

The **security baseline is not graduated by level**: actions pinned to SHAs, minimal
per-job `permissions:`, gitleaks at zero tolerance, zizmor on the workflows
themselves, Checkov for IaC, two dependency gates (whole pinned set + per-PR diff),
SonarCloud as quality gate. Every gate **blocks** — nothing runs with
`continue-on-error`, and a gate whose credential is missing stays red rather than
skipping. The reasoning behind each CI decision is in `docs/DECISIONS.md`.

## Test contract

1. **Shape**: static analysis as the ground floor for infra (compose config, an
   image build of every service, Checkov); a small unit-heavy pyramid for the two
   application components (status API here, widget in the site repo).
2. **Coverage on new code**: 100% on the status API (met: 9 tests, 100% on
   `main.py` and `sentry.py`). No repo-wide threshold — line coverage on
   Dockerfiles and YAML means nothing.
3. **Blocking mutation score (nightly)**: any non-killed mutant in
   `require_valid_token` — the only authorization comparison in the project —
   fails `.github/workflows/mutation.yml`. Survivors elsewhere are non-blocking.
4. **Security taxonomy**: OWASP API Top 10 for the status API, MITRE ATT&CK for
   the infra surface. Not OWASP LLM / ATLAS — Phase 1 observes a model's usage,
   it never calls one; those arrive with Phase 4.
5. **Flaky policy**: tests are deterministic (pytest + mocks); a gate that fails
   at random gets fixed or removed, never re-run until green.

Production monitoring is literally both the deliverable and the top rung of the
pyramid: Grafana/Prometheus is what the repo builds *and* how it is observed.

## Status

**Live.** The five services run, the Tunnel serves its three hostnames, and the
public endpoint answers with real numbers. `scripts/verify-hub.sh` is the
post-deploy check; what is still open (one volume check, the judgement calls) is in
`docs/BLOCKERS.md`.

## Docs

| Doc | What it answers |
|---|---|
| [docs/DECISIONS.md](docs/DECISIONS.md) | Every closed decision, and what measuring changed about it. **Read before reopening anything.** |
| [railway/README.md](railway/README.md) | How production is deployed, per-service settings, symptom-to-cause table. |
| [docs/LOCAL_DRY_RUN.md](docs/LOCAL_DRY_RUN.md) | The whole stack on a laptop, fed by the real client. |
| [docs/CLAUDE_CODE_TELEMETRY.md](docs/CLAUDE_CODE_TELEMETRY.md) | Pointing Claude Code at the hub, and what actually reaches Prometheus. |
| [docs/CLOUDFLARE_TUNNEL_SETUP.md](docs/CLOUDFLARE_TUNNEL_SETUP.md) | The one-time tunnel + Access setup. |
| [docs/BLOCKERS.md](docs/BLOCKERS.md) | What is not done yet. |
| [SECURITY.md](SECURITY.md) | What is exposed, what deliberately is not, private disclosure. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The loop, the gates, the three layers of verification. |
| [docs/superpowers/specs/](docs/superpowers/specs/) | The Phase 1 design at length. |
