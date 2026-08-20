# Agentic OS: Claude Code Observability Hub

Personal platform to observe (and later orchestrate) AI-assisted work.

**Phase 1, live since 2026-07-29**: every Claude Code session streams metrics into a
private hub: OTel Collector → Prometheus → Grafana plus a small FastAPI status
API, running as five services on **Railway** behind a **Cloudflare Tunnel**, with a
sanitized public widget on [marcobellingeri.dev](https://marcobellingeri.dev).

[![tests](https://github.com/MK023/agentic-os/actions/workflows/tests.yml/badge.svg)](https://github.com/MK023/agentic-os/actions/workflows/tests.yml)
[![lint](https://github.com/MK023/agentic-os/actions/workflows/lint.yml/badge.svg)](https://github.com/MK023/agentic-os/actions/workflows/lint.yml)
[![sicurezza](https://github.com/MK023/agentic-os/actions/workflows/security.yml/badge.svg)](https://github.com/MK023/agentic-os/actions/workflows/security.yml)
[![immagini](https://github.com/MK023/agentic-os/actions/workflows/images.yml/badge.svg)](https://github.com/MK023/agentic-os/actions/workflows/images.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=MK023_agentic-os&metric=alert_status)](https://sonarcloud.io/summary/overall?id=MK023_agentic-os)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=MK023_agentic-os&metric=coverage)](https://sonarcloud.io/component_measures?id=MK023_agentic-os&metric=coverage)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)

The interesting part is the **public/private boundary, measured rather than
assumed**. Claude Code sends identity, including a real email address, as metric
attributes. The Collector's label **allow-list** is what keeps it out of storage,
verified against the real client, including attributes no version emits yet.

The public surface of the whole system is exactly three aggregate numbers.

## Architecture

```
 Claude Code: OTLP + bearer token──▶ otel.marcobellingeri.dev
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


The three numbers are **indicative, not accounting**. Prometheus's own docs rule
it out for billing-grade accuracy; that is the tool's stated scope, not a defect.

## Layout

| Path | What |
|---|---|
| `railway/` | Production: one Dockerfile + one config-as-code file per service, and the deployment guide (`railway/README.md`). **Start here** for infra. |
| `docker/` | The local environment **and the single copy of every configuration file**. Railway images copy them at build time; Compose mounts them. |
| `services/public-status-api/` | FastAPI service exposing the three whitelisted numbers. The only application code. |
| `scripts/` | `verify-hub.sh`, the post-deploy smoke test. Railway ignores the Dockerfile `HEALTHCHECK`; its own `healthcheckPath` gates the deploy on two services and never runs after. |
| `docs/` | Decisions, deployment, telemetry setup, local dry run. Index at the bottom. |

## Running it

The whole stack (minus the tunnel connector) runs on a laptop with the same images
and the same config files as production.
`docs/LOCAL_DRY_RUN.md` is the recipe, including how to feed it the real Claude
Code client and check the privacy boundary.

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
[docs/DECISIONS.md](docs/DECISIONS.md).

The short version:

- **OTLP ingest authenticates inside the Collector** (`bearertokenauth`): a public
  Tunnel hostname is not an access control. This follows the CVE-2026-28798
  pattern.
- **Metric labels are an allow-list**, so an attribute nobody has seen yet becomes
  a missing label, not a leak. `session_id` stays because without it concurrent
  sessions collapse into one series. This is measured, not theorised.
- **Prometheus never gets a hostname**. Grafana and the status API sit behind
  Cloudflare Access (email policy / service token + own bearer, two layers).
- **Every image runs non-root and pinned to a version**, with one documented
  platform exception. Two different guards, because the sentence used to credit one
  guard with both jobs: `scripts/check-image-users.sh` reads `USER` and nothing else,
  Checkov's `CKV_DOCKER_7` covers pinning in the Dockerfiles, and a dedicated step in
  `images.yml` covers `docker/docker-compose.yml` — which until 2026-08-19 was
  scanned by nothing at all, so a `:latest` there would have passed every gate.
- **Secrets**: Doppler is the source of truth, Railway variables are the runtime
  copy, `docker/.env` is fake and gitignored. Never in git; gitleaks runs over
  full history in CI, plus an **opt-in** pre-commit hook — `.githooks/pre-commit`
  does nothing until you run `git config core.hooksPath .githooks`, and without
  gitleaks installed locally it falls back to a short regex that a base64 blob walks
  straight past. The CI half is the one that always runs.

## Pipeline level: 1 (and why)

Declared **Level 1** (`PR → lint → test → build → dependency audit`) against my
own CI/CD maturity model: one developer, personal project, rollback is a redeploy.

Canary or signed attestation for three Python dependencies would be cargo cult.

The **security baseline is not graduated by level**: actions pinned to SHAs,
minimal per-job `permissions:`, gitleaks at zero tolerance, zizmor on the
workflows themselves, Checkov for IaC, two dependency gates (whole pinned set +
per-PR diff), SonarCloud as quality gate.

**CodeQL runs too, and it is not in this repository.** It is enabled as GitHub's
*default setup* (`python` and `actions`, weekly plus per-PR), the same way the
site repo has it — which means there is no workflow file to read, no action SHA
to pin, and nothing to maintain. It is written here because a gate that exists
only as a repository setting is invisible to anyone reading the code, and an
invisible gate is one nobody thinks to check. It complements ruff and bandit
rather than repeating them: those are linters with security rules, matching
patterns in a file; CodeQL follows a value from source to sink across files. It
reports rather than blocks — it is not one of the twelve required contexts.

One thing that is **not** enabled, having been tried: secret scanning's
*non-provider patterns*. The API accepts the change with a `200` and leaves the
value at `disabled` on both public repos, because the feature needs GitHub Secret
Protection. gitleaks is the compensating control and covers generic secrets over
full history on every PR. Worth recording that the API silently no-ops rather
than refusing — a `200` that changes nothing is the same shape as the failures
this project keeps removing.

Every gate **blocks**. Nothing runs with `continue-on-error`, and a gate whose
credential is missing stays red instead of skipping — **with one measured
exception**, found by an audit on 2026-08-19 rather than by a failure: `sonar` skips
on pull requests that cannot receive secrets (forks, and Dependabot). A token-less
Sonar scan cannot pass, and a permanently red required check would block every merge,
so the skip is deliberate; but a skipped required check reads as green, which this
repository has already measured once. What is lost on those PRs is Sonar's own rule
set, not coverage — `tests` enforces `--cov-fail-under=100` unconditionally, and
lint, bandit, checkov and gitleaks all still run.

One check runs *after* the merge rather than before it: `smoke.yml` hits the
public endpoint on a schedule and fails on three things — a status that is not
`200`, any of the three numbers coming back `null`, or the response declaring
itself `stale`. The last two are the ones that matter, because a green HTTP
status proves nothing here: the site Worker fails open by design, answering `200`
with nulls when it cannot reach the hub, and since it also serves a cached
last-known-good it can answer `200` with real-but-old numbers. Both look healthy
from outside and are not. It blocks nothing and merges nothing; it turns a silent
outage into a red run and an email. Railway ignores the Dockerfile `HEALTHCHECK`, and
its own `healthcheckPath` — set since 2026-08-20 on status-api and cloudflared — is
queried only until the deploy goes live, never after. So nothing on the platform side
watches a running service: a `SUCCESS` deploy now means "it served a 200 once".

**Its cadence is asked for, not guaranteed, and the gap is large.** The cron says
`*/10`; measured across the first twelve real runs, the interval ran between 38
and 93 minutes, median ~55 — GitHub throttles frequent schedules on public
repositories. So this probe reliably catches an outage that *lasts*: a deploy
that does not serve, a full volume, a Tunnel down. It does **not** reliably catch
a two-minute restart, and any claim that it would have seen the short 2026-08-13
windows is wrong.

**That gap is closed, and not from this repository.** On 2026-08-14 the site repo
shipped a Cloudflare Cron Trigger at `*/2` whose `scheduled` handler runs the same
probe a visitor's request would (`marcobellingeri.dev` PR #203). It adds a trigger
rather than logic: the Worker already reported to Sentry when the hub did not
answer, but that code only ran if somebody visited, and at 4am nobody does. So this
repository now has two probes with different jobs — `smoke.yml`, throttled and good
for outages that last, and an edge probe every two minutes that sees the short ones
and measures exactly what a visitor gets.

The fuller check, `scripts/verify-hub.sh`, additionally covers Grafana behind
Access and the status API's own bearer, and stays manual until its credentials
exist as GitHub secrets.

The reasoning behind each CI decision is in `docs/DECISIONS.md`.

## Test contract

1. **Shape**: static analysis as the ground floor for infra (compose config, an
   image build of every service, Checkov); a small unit-heavy pyramid for the two
   application components (status API here, widget in the site repo).

2. **Coverage on new code**: 100% on the status API, **enforced** by
   `--cov-fail-under=100` in CI (met: 25 tests, 100% on `main.py` and
   `sentry.py`). No repo-wide threshold — line coverage on Dockerfiles and YAML
   means nothing. Until 2026-08-13 this number was measured and *not* enforced:
   a contract written in a README that no gate checks is a wish, and it is the
   same defect as a gate nobody wrote a policy for, seen from the other side.

3. **Blocking mutation score (nightly)**: any non-killed mutant in
   `require_valid_token`, the only authorization comparison in the project,
   fails `.github/workflows/mutation.yml`. Survivors elsewhere are non-blocking.

4. **Security taxonomy**: OWASP API Top 10 for the status API, MITRE ATT&CK for
   the infra surface. Not OWASP LLM / ATLAS: Phase 1 observes a model's usage; it never calls one.
   Those arrive with Phase 4.

5. **Flaky policy**: tests are deterministic (pytest + mocks). A gate that fails
   at random gets fixed or removed, never re-run until green. There is no
   `FLAKY.md` because nothing is in quarantine; if something ever is, that file is
   where it goes, with a test id, an owner and a ticket.

One rule cuts across all five: **a change whose halves live in different services
needs a check that sees both.** The window of the three public numbers is set by
`max_over_time(...[25h])` in `main.py` and the dashboard, and by the Collector's
short `metric_expiration` — raise one without the other and the numbers silently
double-count or lose a day. No test suite spans those files, so the `compose` job
holds the pair.

Production monitoring is both the deliverable and the top rung of the pyramid:
Grafana/Prometheus is what the repo builds and how it is observed.

## Status

**Live.**

Tagged **v1.0.0** on 2026-08-13. The five services run, the Tunnel serves its
three hostnames, and the public endpoint answers with real numbers. `smoke.yml`
watches it from outside on a schedule; `scripts/verify-hub.sh` is the fuller
post-deploy check and stays manual.

What is still open is tracked in `docs/BLOCKERS.md`, and it is short: no
engineering task blocks anything, only judgement calls and one upstream CVE
nobody downstream can fix.

## Docs

| Doc | What it answers |
|---|---|
| [docs/DECISIONS.md](docs/DECISIONS.md) | Every closed decision and what measuring changed about it. **Read before reopening anything.** |
| [railway/README.md](railway/README.md) | How production is deployed, per-service settings, symptom-to-cause table. |
| [docs/LOCAL_DRY_RUN.md](docs/LOCAL_DRY_RUN.md) | The whole stack on a laptop, fed by the real client. |
| [docs/CLAUDE_CODE_TELEMETRY.md](docs/CLAUDE_CODE_TELEMETRY.md) | Pointing Claude Code at the hub and what actually reaches Prometheus. |
| [docs/CLOUDFLARE_TUNNEL_SETUP.md](docs/CLOUDFLARE_TUNNEL_SETUP.md) | The one-time tunnel and Access setup. |
| [docs/BLOCKERS.md](docs/BLOCKERS.md) | What is not done yet. |
| [SECURITY.md](SECURITY.md) | What is exposed, what deliberately is not, and private disclosure. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The loop, the gates, and the three layers of verification. |
| [docs/superpowers/specs/](docs/superpowers/specs/) | The Phase 1 design at length. |
