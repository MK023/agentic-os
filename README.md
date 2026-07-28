# Agentic OS — Phase 1: Claude Code Observability Hub

Personal platform to observe (and later orchestrate) AI-assisted work across
projects. Phase 1 is a single Hostinger VPS, Terraform-provisioned, running an
OTel Collector + Prometheus + Grafana behind a Cloudflare Tunnel, so Claude Code
usage is visible live in a private dashboard and as a sanitized public widget on
marcobellingeri.dev.

Design: `docs/superpowers/specs/2026-07-28-agentic-os-phase1-design.md` ·
Plan: `docs/superpowers/plans/2026-07-28-phase1-observability-hub.md` ·
Checklist: `TASKS.md`

## Layout

| Path | What |
|---|---|
| `terraform/hostinger-vps/` | VPS provisioning (`hostinger/hostinger` provider) |
| `docker/` | Compose stack: cloudflared, OTel Collector, Prometheus, Grafana, status API |
| `services/public-status-api/` | FastAPI service exposing three whitelisted aggregate numbers |
| `scripts/` | `bootstrap.sh` (runs on the VPS at first boot), `verify-hub.sh` (post-deploy smoke test) |
| `docs/` | Cloudflare Tunnel setup, Claude Code telemetry configuration |

## Security boundary

The public surface is exactly three numbers — sessions, tokens, cost — served by
`services/public-status-api` behind a Cloudflare Access service token. No session
content, no free-form PromQL, no path from the public widget to anything else.
Prometheus never gets a Tunnel hostname of its own; the OTLP ingest endpoint is
authenticated inside the Collector (`bearertokenauth`), because a public Tunnel
hostname is not an access control (CVE-2026-28798 pattern). Claude Code's
`user.email`/`session.id` resource attributes are deliberately not converted into
Prometheus labels.

## Pipeline level: 1 — and why

Reference model: `~/GitHub/Atlas/concepts/pipeline-cicd.md`. Declared **Level 1**
(`PR → Lint → Test → Build → Dependency audit`): one developer, personal project,
rollback is `terraform destroy` + redeploy. Level 4 (canary, progressive
delivery) would be theatre here — there is no traffic to measure.

The **security baseline is not graduated by level** and is applied in full,
copied from marcobellingeri.dev's live CI: actions pinned to SHAs, minimal
per-job `permissions:`, gitleaks at zero tolerance, zizmor (workflow SAST),
`dependency-review-action`, Checkov for IaC, SonarCloud as quality gate.

Deliberately **not** adopted: SBOM + signed attestation, human approval gates,
canary. Three Python dependencies and no distributed artifact — that would be
Level 4 cargo cult.

Gate policy: `terraform`, `compose`, `status-api-tests`, `checkov`,
`workflow-lint`, `gitleaks`, `dependency-audit` and `sonar` all block the merge.
Checkov blocks on HIGH and above and soft-fails LOW/MEDIUM; `dependency-audit`
(pip-audit) blocks on any advisory against the three pinned dependencies. Nothing
runs with `continue-on-error`.

Two gates depend on account state rather than on code:

- **`dependency-audit` uses pip-audit, not `dependency-review-action`**, because
  this repository is private and that action requires GitHub Code Security /
  Advanced Security there (confirmed live on PR #1: *"Dependency review is not
  supported on this repository"*). The `dependency-review` job is still in the
  workflow and turns itself on if the repo is ever made public — its diff-scoped
  view is better at catching what a single PR introduces.
- **`sonar` stays red until a SonarCloud project exists and `SONAR_TOKEN` is set**
  as a repository secret. SonarCloud's free tier does cover private projects up to
  50k LoC, so this is a setup step, not a paywall. The job is deliberately *not*
  made to skip when the secret is missing: a quality gate that disappears when its
  credential does is the `continue-on-error` antipattern with extra steps.

## Test contract

Reference model: `~/GitHub/Atlas/concepts/testing-pyramid.md`.

1. **Shape**: static analysis as the ground floor for infra (`terraform
   validate`, `docker compose config`, Checkov); a small unit-heavy pyramid for
   the two application components (status API here, widget in the site repo).
   The complexity is inside the functions, not in the composition.
2. **Coverage on new code**: 100% on the status API (currently met: 8 tests,
   100% on `main.py` and `sentry.py`). No repo-wide threshold — most of this
   repo is Terraform/YAML, where line coverage means nothing.
3. **Blocking mutation score (nightly)**: `require_valid_token` in the status
   API — the only authorization comparison in the project.
   `.github/workflows/mutation.yml` runs mutmut nightly and fails on any
   surviving mutant *in that function*; survivors elsewhere (the envelope payload
   in `sentry.py`) are reported, not blocking. The first run found four survivors
   here even at 100% coverage — all four were the 401's `detail` string, which no
   test asserted. Now it does.
4. **Security taxonomy**: OWASP API Security Top 10 for the status API; MITRE
   ATT&CK for the infra surface (Tunnel/Access/VPS). Not MITRE ATLAS and not
   OWASP LLM Top 10 — Phase 1 observes a model's usage, it never calls one.
   Those apply from Phase 4 (session RAG).
5. **Flaky policy**: none today, every test is deterministic (pytest/vitest with
   mocks) plus one bash smoke test. `FLAKY.md` gets created when the first
   non-deterministic test does.

In this project, production monitoring is literally both the deliverable and the
top rung of the pyramid: Grafana/Prometheus is what the repo builds *and* how the
running system is observed.

## Status

Code and docs are written and locally verified. Not yet run against real
infrastructure: `terraform apply`, the Cloudflare Tunnel creation, and filling
`docker/.env` on the VPS are manual steps, deliberately outside CI. The Hostinger
account does not exist yet, so `data_center_id`/`template_id` in
`terraform.tfvars` are still placeholders.
