# Agentic OS — Phase 1: Claude Code Observability Hub

Personal platform to observe (and later orchestrate) AI-assisted work across
projects. Phase 1 is an OTel Collector + Prometheus + Grafana + a small status API,
running on **Railway** behind a Cloudflare Tunnel, so Claude Code usage is visible
live in a private dashboard and as a sanitized public widget on marcobellingeri.dev.

A VPS variant was written and validated first; it was dropped on 2026-07-29, because
for four small services used by one person a PaaS costs the same and removes the
machine to maintain. Moving cost five files, which is the part of the story worth
remembering — see `docs/DECISIONS.md`.

Design and reasoning: `docs/superpowers/specs/2026-07-28-agentic-os-phase1-design.md` ·
Closed decisions: `docs/DECISIONS.md` · Deployment: `railway/README.md` ·
What is left: `docs/BLOCKERS.md`

## Layout

| Path | What |
|---|---|
| `railway/` | Production: one Dockerfile + one config per service, and the deployment guide |
| `docker/` | The local environment, same images and same config files (see `docs/LOCAL_DRY_RUN.md`) |
| `services/public-status-api/` | FastAPI service exposing three whitelisted aggregate numbers |
| `scripts/` | `verify-hub.sh`, the post-deploy smoke test |
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
rollback is a redeploy of the previous image. Level 4 (canary, progressive
delivery) would be theatre here — there is no traffic to measure.

The **security baseline is not graduated by level** and is applied in full,
copied from marcobellingeri.dev's live CI: actions pinned to SHAs, minimal
per-job `permissions:`, gitleaks at zero tolerance, zizmor (workflow SAST),
`dependency-review-action`, Checkov for IaC, SonarCloud as quality gate.

Deliberately **not** adopted: SBOM + signed attestation, human approval gates,
canary. Three Python dependencies and no distributed artifact — that would be
Level 4 cargo cult.

Gate policy: `compose`, `images`, `image-users`, `status-api-tests`, `checkov`,
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

1. **Shape**: static analysis as the ground floor for infra (`docker compose
   config`, an image build of every service, Checkov); a small unit-heavy pyramid for
   the two application components (status API here, widget in the site repo).
   The complexity is inside the functions, not in the composition.
2. **Coverage on new code**: 100% on the status API (currently met: 9 tests,
   100% on `main.py` and `sentry.py`). No repo-wide threshold — most of this
   repo is Dockerfiles and YAML, where line coverage means nothing.
3. **Blocking mutation score (nightly)**: `require_valid_token` in the status
   API — the only authorization comparison in the project.
   `.github/workflows/mutation.yml` runs mutmut nightly and fails on any
   surviving mutant *in that function*; survivors elsewhere (the envelope payload
   in `sentry.py`) are reported, not blocking. The first run found four survivors
   here even at 100% coverage — all four were the 401's `detail` string, which no
   test asserted. Now it does.
4. **Security taxonomy**: OWASP API Security Top 10 for the status API; MITRE
   ATT&CK for the infra surface (Tunnel, Access, the Railway project). Not MITRE ATLAS and not
   OWASP LLM Top 10 — Phase 1 observes a model's usage, it never calls one.
   Those apply from Phase 4 (session RAG).
5. **Flaky policy**: every *test* is deterministic (pytest with mocks) plus one bash
   smoke test. The first non-deterministic failure this pipeline ever had was not a
   test but a gate — the `images` job timing out against Docker Hub's token endpoint
   on 2026-07-29. It retries three times now; if it recurs, the next step is
   mirroring the base images to GHCR rather than depending on an anonymous pull.
   A gate that fails at random stops being a signal, so it gets fixed or removed —
   never re-run until green.

In this project, production monitoring is literally both the deliverable and the
top rung of the pyramid: Grafana/Prometheus is what the repo builds *and* how the
running system is observed.

## Repository hygiene

Same baseline as `marcobellingeri.dev`, because it is cheap and does not scale with
project size: MIT `LICENSE`, `SECURITY.md` (private disclosure, and what the system
deliberately does not expose), `CONTRIBUTING.md` (the loop, and the house rule about
three layers of verification), a `pre-commit` hook that runs gitleaks before a push
rather than after, and `.github/dependabot.yml`.

Dependabot covers three ecosystems here, and the Docker one earns its place: every
base image is pinned to a version rather than `:latest`, which is a supply-chain
decision, but a pin ages silently. These entries are what make it noisy instead.

```bash
git config core.hooksPath .githooks   # once per clone
```

## Status

**Live since 2026-07-29.** The five services run on Railway, the Tunnel serves the
three hostnames, and `marcobellingeri.dev/api/agentic-status` answers with real
numbers (verified the same day: sessions, tokens and cost from actual usage).
`scripts/verify-hub.sh` is the post-deploy check; `railway/README.md` records how
the deploy went, including the three platform behaviours the first deploy settled.

What remains open — one volume check worth doing a day after go-live, and the
things that are still a judgement call — is in `docs/BLOCKERS.md`.
