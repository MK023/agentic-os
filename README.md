# Agentic OS — Phase 1: Claude Code Observability Hub

Personal platform to observe (and later orchestrate) AI-assisted work across
projects. Phase 1 is an OTel Collector + Prometheus + Grafana + a small status API,
running on **Railway** behind a Cloudflare Tunnel, so Claude Code usage is visible
live in a private dashboard and as a sanitized public widget on marcobellingeri.dev.

The deployment target changed on 2026-07-29, after the VPS variant was written and
validated: for four small services used by one person, a PaaS costs the same and
removes the machine to maintain. `terraform/hostinger-vps/` is kept as a working,
documented alternative — the move cost five files, which is the useful part of the
story.

Design: `docs/superpowers/specs/2026-07-28-agentic-os-phase1-design.md` ·
Plan: `docs/superpowers/plans/2026-07-28-phase1-observability-hub.md` ·
Checklist: `TASKS.md`

## Layout

| Path | What |
|---|---|
| `railway/` | Production: one Dockerfile + one config per service, and the deployment guide |
| `docker/` | The local environment, same images and same config files (see `docs/LOCAL_DRY_RUN.md`) |
| `terraform/hostinger-vps/` | The VPS alternative, written and validated, not the deployment target |
| `services/public-status-api/` | FastAPI service exposing three whitelisted aggregate numbers |
| `scripts/` | `bootstrap.sh` (runs on the VPS at first boot), `verify-hub.sh` (post-deploy smoke test) |
| `docs/` | Cloudflare Tunnel setup, Claude Code telemetry configuration |

## Security boundary

The public surface is exactly three numbers — sessions, tokens, cost — served by
`services/public-status-api` behind a Cloudflare Access service token. They are
**indicative, not accounting**: Prometheus's own documentation says it outright — *"If
you need 100% accuracy, such as for per-request billing, Prometheus is not a good
choice"* — and measurement agrees, since `increase()` extrapolates to the window
edges (real growth of 23787 tokens read as 27956 with sparse samples). The widget
says what the day looked like, not what the invoice will say. No session
content, no free-form PromQL, no path from the public widget to anything else.
Prometheus never gets a Tunnel hostname of its own; the OTLP ingest endpoint is
authenticated inside the Collector (`bearertokenauth`), because a public Tunnel
hostname is not an access control (CVE-2026-28798 pattern).

Metric labels are an **allow-list** enforced in the Collector: `model`, `type`,
`query_source`, `start_type`, `terminal_type`, `session_id` survive, everything else
is dropped before storage. That is not paranoia — Claude Code sends identity
(`user.email` with a real address, `user.id`, `user.account_id`, `user.account_uuid`,
`organization.id`) as *data point* attributes, so `resource_to_telemetry_conversion:
false` does not stop them. Measured against the real client, not assumed. An
allow-list also holds when a future release adds an attribute nobody has seen yet.

## Pipeline level: 1 — and why

Reference model: my own CI/CD maturity model (four levels, gate policy, the
pipeline itself as attack surface), kept in private engineering notes. Declared **Level 1**
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

**Two dependency gates, on purpose**: `dependency-audit` (pip-audit) checks the
whole pinned set on every run; `dependency-review` blocks on HIGH CVEs introduced
by a single PR's diff. They answer different questions, and only the first works on
a private repository — `dependency-review` needs GitHub Code Security there
(confirmed live on PR #1, while this repo was still private: *"Dependency review is
not supported on this repository"*). This repository is public, so both run.

**New code definition: 30 days** (SonarQube Cloud, inherited from the instance
default and left there deliberately). This project has no release versions to make
"previous version" meaningful — it is trunk-based, one developer, continuous
delivery, which is exactly the case the vendor's guidance points at "number of
days" for. The coverage threshold in the test contract below is a *new code*
threshold, so this setting is what defines its scope.

The `sonar` job runs on pushes to `main`, not only on pull requests: without a main
branch analysis there is no baseline and no quality-gate history, and "new code"
has nothing to be new against.

One gate depends on account state rather than on code:

- **`sonar` stays red until a SonarCloud project exists and `SONAR_TOKEN` is set**
  as a repository secret. SonarCloud's free tier does cover private projects up to
  50k LoC, so this is a setup step, not a paywall. The job is deliberately *not*
  made to skip when the secret is missing: a quality gate that disappears when its
  credential does is the `continue-on-error` antipattern with extra steps.

## Test contract

Reference model: my own testing model (shape by architecture, clean-as-you-code
coverage, mutation testing, security taxonomy by project type), kept in private
engineering notes.

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

All eight blocks are written, executed and verified locally; PR #1 carries them.
The site half already shipped: `marcobellingeri.dev/api/agentic-status` is live and
answers with three `null` fields, which is the designed degraded state until the hub
exists.

Not yet run against real infrastructure: `terraform apply`, the Cloudflare Tunnel
creation, and filling `docker/.env` on the VPS — all manual steps, deliberately
outside CI. The Hostinger account does not exist yet, so `data_center_id` and
`template_id` in `terraform.tfvars` are still placeholders, and they cannot be
looked up without it (the provider's data sources are authenticated; the API answers
401). Full list of what is blocked on what: `docs/BLOCKERS.md`.
