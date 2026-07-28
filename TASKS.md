# Agentic OS — Phase 1 execution checklist

Consolidated hand-off file: everything to build, in order, by logical block.
Full code/exact commands for every step live in
`docs/superpowers/plans/2026-07-28-phase1-observability-hub.md` (referenced
below by **Task/Step name, not line number** — a prior version of this file
cited line numbers, and an independent audit on 2026-07-28 found 31 of 47 of
them had drifted after later edits to the plan. Line numbers in a document
that keeps changing are not a stable pointer; Task/Step headings are. Search
for the quoted step title in the plan file instead of counting lines) — this
file is the checklist + the "why" + the gotchas, so whoever executes doesn't
need to hold the whole plan in their head at once. Rationale for every
decision (why Docker Compose not K3s, why this security fix, why Sentry now
and Langfuse later) lives in
`docs/superpowers/specs/2026-07-28-agentic-os-phase1-design.md`.

**Do not start before reading "Block 0" below — it gates everything else.**

**Verification status (2026-07-28):** every code block referenced below was
extracted from the plan and actually executed in a scratch environment, not
just read — real `terraform validate` against the live `hostinger/hostinger`
provider, real `pytest`/`vitest` runs, real `docker compose config`, real
image-tag existence checks. Bugs that verification found are already fixed in
the plan (see each block's "Fixed 2026-07-28" note where relevant) — this
checklist reflects the corrected version, not the original draft.

---

## Block 0 — Prerequisites (gates every other block)

Status as of 2026-07-28: **Cloudflare account exists already.** **Hostinger
account does not exist yet.** Marco's target start date is **after
2026-08-10**. Nothing below should run for real before then unless Marco says
otherwise on the executing machine.

Before Block 1 can fully close (not before it can *start* — see plan.md,
section "Before you start"):

- [ ] Hostinger account created + API token (`HOSTINGER_API_TOKEN`) — **not open yet**
- [ ] Cloudflare account with a zone + `CLOUDFLARE_API_TOKEN` (Tunnel edit) — **already have this**
- [ ] SSH keypair to attach to the VPS
- [ ] `gh` / git access to push this repo and to `MK023/marcobellingeri.dev` (Block 7 touches that repo)

Blocks 1-4, 6, 8 are fully writable/testable **without** the Hostinger account
— only one step in Block 1 and the real `terraform apply`/`cloudflared tunnel
create` need it. Don't let a missing Hostinger account block writing code.

---

## Block 1 — Infrastructure: Terraform VPS provisioning

**Plan reference:** "Task 1: Terraform module — provision the VPS".
**Files:** `terraform/hostinger-vps/{versions,variables,main,outputs}.tf`, `terraform.tfvars.example`.
**Depends on:** nothing to *write*. Needs Hostinger account to *look up* real IDs (see below) and to `apply`.

Checklist:
- [ ] Write `versions.tf` (Task 1, Step 1) — provider block, `HOSTINGER_API_TOKEN` from env, never hardcoded
- [ ] Write `variables.tf` (Task 1, Step 2) — `vps_plan`, `data_center_id`, `template_id`, `ssh_public_key_path`
- [ ] Write `main.tf` (Task 1, Step 3) — `hostinger_vps_ssh_key`, `hostinger_vps_post_install_script`, `hostinger_vps` resources
- [ ] Write `outputs.tf` (Task 1, Step 4) — `vps_ip` output
- [ ] Write `terraform.tfvars.example` (Task 1, Step 5) with placeholder IDs marked REPLACE
- [ ] **Gate**: `terraform init` + `terraform console`, look up real `data_center_id` (pick EU) and `template_id` ("Ubuntu 24.04 with Docker") from the provider's data sources — **do not guess these numbers** (Task 1, Step 6)
- [ ] `terraform fmt -check && terraform validate` — must pass before commit (Task 1, Step 7)
- [ ] Commit (Task 1, Step 8)

**Fixed 2026-07-28** (found by actually running this against the real
provider, not by reading): `main.tf`'s `=` alignment failed `terraform fmt
-check`; the hostname `"agentic-os-hub"` failed `terraform validate` with
`invalid value for hostname (must be a valid FQDN)` — the provider requires a
dotted value even though it never needs to resolve on public DNS. Both are
fixed in the plan now (`"agentic-os-hub.local"`, `terraform fmt`-clean
alignment) — `terraform init`/`fmt -check`/`validate` all pass clean as
written today.

**Reference doc:** `~/GitHub/Atlas/entities/tools/hostinger.md` (Terraform provider schema, verified against the real README, not memory) and `~/GitHub/Atlas/entities/tools/terraform.md`.

---

## Block 2 — Observability stack: Docker Compose

**Plan reference:** "Task 2: Docker Compose stack — OTel Collector, Prometheus, Grafana".
**Files:** `docker/docker-compose.yml`, `docker/otel-collector-config.yaml`, `docker/prometheus.yml`, `docker/grafana/provisioning/**`.
**Depends on:** nothing external — fully writable and `docker compose config`-checkable with dummy env values. Verified 2026-07-28: real YAML/JSON parse clean, `docker compose config --quiet` exits 0, all 4 pinned image tags confirmed to exist on their registries via `docker manifest inspect`.

Checklist:
- [ ] Write `otel-collector-config.yaml` (Task 2, Step 1) — OTLP receiver **with `bearertokenauth` extension**. This is the fix for the CVE-2026-28798-class gap found in the security sweep: an OTLP ingest endpoint behind a public Cloudflare Tunnel hostname must not be left unauthenticated.
- [ ] Write `prometheus.yml` (Task 2, Step 2) — scrape config, 15s interval
- [ ] Write Grafana provisioning: datasource (Step 3), dashboard provider (Step 4), dashboard JSON (Step 5) — **verify the `claude_code_*` metric names against your installed Claude Code version before relying on this dashboard** (they're versioned and can change)
- [ ] Write `docker-compose.yml` (Task 2, Step 6) — 4 services (`cloudflared`, `otel-collector`, `prometheus`, `grafana`) + `status-api` (Block 3). **Every image pinned to a version, never `:latest`** — check each image's current stable tag before running (the versions in the plan were confirmed to exist on 2026-07-28, but this file may run months later). Every service gets `security_opt: [no-new-privileges:true]` + `cap_drop: [ALL]`.
- [ ] **Gate**: `docker compose config --quiet` with dummy env values — must exit 0 (Task 2, Step 7)
- [ ] Commit (Task 2, Step 8)

**Security decisions baked in here, don't relax them:**
- Prometheus **never** gets a Cloudflare Tunnel hostname of its own (no auth of its own) — internal to the Docker network only.
- `OTLP_INGEST_TOKEN` env var is what the `bearertokenauth` extension checks — generate with `openssl rand -hex 32`, goes in `docker/.env` (Block 4) and Claude Code's local config (Block 6).

**Reference docs:** `~/GitHub/Atlas/entities/tools/opentelemetry.md` (Collector config, `bearertokenauth`), `~/GitHub/Atlas/entities/tools/grafana.md`, `~/GitHub/Atlas/entities/tools/prometheus.md`, `~/GitHub/Atlas/entities/tools/docker.md` (image pinning + hardening baseline).

---

## Block 3 — Application: public-safe status API

**Plan reference:** "Task 3: Public-safe status API".
**Files:** `services/public-status-api/{main.py,sentry.py,conftest.py,requirements.txt,Dockerfile,test_main.py}`.
**Depends on:** nothing external. TDD — write the tests first, they use mocked Prometheus/Sentry responses.
**Verified 2026-07-28**: all 5 tests actually run and pass (Python 3.10 locally; Docker image targets 3.12, no version-specific syntax used).

Checklist:
- [ ] Write `test_main.py` — **5 tests**, not 4 (Task 3, Step 1): whitelisted fields, missing token 401, wrong token 401, upstream HTTP failure → 502 + Sentry capture, **malformed-JSON upstream response → 502 + Sentry capture** (this 5th test was added 2026-07-28 after actually running the suite exposed a real gap — see below)
- [ ] Write `conftest.py` (Task 3, Step 2) — sets `PROMETHEUS_URL`/`STATUS_API_TOKEN` env vars before `main.py` is imported by the test module
- [ ] Run tests, confirm they fail (`ModuleNotFoundError`) (Task 3, Step 3)
- [ ] Write `requirements.txt` (Task 3, Step 4) (fastapi, uvicorn, httpx — 3 deps, deliberately minimal)
- [ ] Write `sentry.py` (Task 3, Step 5) — zero-dependency envelope client, ported from `marcobellingeri.dev/engine/lib/sentry.mjs`, same fail-open contract
- [ ] Write `main.py` (Task 3, Step 6) — **four things fixed/added on 2026-07-28, read the plan's note in full before writing this file:**
  1. **Bug fix, confirmed by running it**: the exception handler now catches `KeyError, TypeError, ValueError` in addition to `httpx.HTTPError` — a malformed-but-200 upstream response used to raise an uncaught `KeyError` that became an unhandled 500 with no Sentry capture, breaking this endpoint's "every upstream failure is a controlled 502" promise.
  2. The three Prometheus queries run **concurrently** (`asyncio.gather`), not one at a time in a loop — they're independent, sequential awaiting only triples worst-case latency for no benefit. Explicit `httpx.Timeout(10.0)`.
  3. `secrets.compare_digest` for the token check, not `!=` (timing side-channel fix).
  4. Auth moved into a proper **FastAPI dependency** (`Annotated[None, Depends(require_valid_token)]`) instead of an inline check in the route body — matches the Annotated-DI pattern already used in JobSearch/TorinoParking (`~/GitHub/Atlas/entities/tools/fastapi.md`).
  No app-level rate limiter (deliberately — Cloudflare handles that at the edge, see the `ponytail:` comment in the plan).
- [ ] Run tests, confirm **5 passed** (Task 3, Step 7)
- [ ] Write `Dockerfile` (Task 3, Step 8) — includes a `HEALTHCHECK` (shell form, so it can read the runtime `STATUS_API_TOKEN` env var). **Not verified locally on purpose** (would need `docker run` against a real daemon, and this machine is meant to stay light): confirm `curl` exists in `python:3.12-slim` before relying on it — `docker run --rm python:3.12-slim sh -c "which curl"` on the executing machine, add the `apt-get install curl` layer only if missing.
- [ ] Commit (Task 3, Step 9)

**Reference docs:** `~/GitHub/Atlas/entities/tools/fastapi.md`, `~/GitHub/Atlas/entities/tools/sentry.md` (Python port pattern).

---

## Block 4 — Secrets wiring and bootstrap

**Plan reference:** "Task 4: Bootstrap script and secret wiring".
**Files:** `scripts/bootstrap.sh`, `docker/.env.example`, `.gitignore`.
**Depends on:** Blocks 1-2 existing (bootstrap.sh assumes the repo layout they create).

Checklist:
- [ ] Write `scripts/bootstrap.sh` (Task 4, Step 1) — clones this repo onto the VPS, refuses to start the stack without a real `docker/.env`
- [ ] Write `docker/.env.example` (Task 4, Step 2) listing every required var, no real values — now 5 vars: `CLOUDFLARE_TUNNEL_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, `STATUS_API_TOKEN`, `OTLP_INGEST_TOKEN`, `SENTRY_DSN`
- [ ] Add `docker/.env` and `terraform/hostinger-vps/terraform.tfvars` to `.gitignore` (Task 4, Step 3) — **real secrets never committed**
- [ ] Commit (Task 4, Step 4)

---

## Block 5 — Cloudflare Tunnel setup (manual, on the executing machine)

**Plan reference:** "Task 5: Cloudflare Tunnel — ingress and Access policies" (note: corrected 2026-07-28 to reflect the `bearertokenauth` decision — read the current version, not an older copy).
**Files:** `docs/CLOUDFLARE_TUNNEL_SETUP.md` (doc only — the actual tunnel/DNS/Access setup is manual CLI/dashboard work, not something this repo's CI drives).
**Depends on:** the real Cloudflare account (already have it) — this block's *doc* can be written now, but *running* the sequence in it needs Blocks 1-2 deployed first (needs the VPS + compose stack up to point the tunnel at).

Checklist:
- [ ] Write `docs/CLOUDFLARE_TUNNEL_SETUP.md` (Task 5, Step 1)
- [ ] Commit the doc now; **execute** the manual sequence inside it only once Blocks 1-4 are deployed for real (Task 5, Step 2)
- [ ] Three hostnames: Grafana (Access, your email only), status API (Access, Service Auth token — feeds Block 7), OTel ingest (**no Access app** — Claude Code can't do the service-token header — auth is the `bearertokenauth` token from Block 2 instead, not "left open because the hostname is hard to guess")

---

## Block 6 — Local Claude Code telemetry (docs)

**Plan reference:** "Task 6: Local Claude Code telemetry configuration (docs)".
**Files:** `docs/CLAUDE_CODE_TELEMETRY.md`.
**Depends on:** Block 5 giving you the real Tunnel hostname + `OTLP_INGEST_TOKEN` value to fill in.

Checklist:
- [ ] Write `docs/CLAUDE_CODE_TELEMETRY.md` (Task 6, Step 1) — includes `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>`, the header the Collector's `bearertokenauth` extension checks
- [ ] Commit (Task 6, Step 2)

---

## Block 7 — Public widget (separate repo: `MK023/marcobellingeri.dev`)

**Plan reference:** "Task 7: marcobellingeri.dev — public widget (separate repo)".
**Repo:** switch to `~/GitHub/marcobellingeri.dev` for this block — not this repo.
**Files:** `src/pages/api/agentic-status.ts`, `src/components/AgenticOsWidget.astro`, `test/agentic-status.test.mjs`. (An earlier draft of the plan also listed `src/lib/agenticOsStatus.ts` with no step that ever created it — removed 2026-07-28, don't recreate it; the route's ~15 lines of logic don't earn a separate lib file.)
**Depends on:** Block 5's status API Cloudflare Access Service Token (Client ID/Secret) to actually work end-to-end; the code/tests can be written and run against mocks before that exists.
**Verified 2026-07-28**: both tests actually run and pass in an isolated vitest environment.

Checklist:
- [ ] Write the failing test (Task 7, Step 1) — mocked fetch, **two cases**: success, and degraded-fallback (hub unreachable → null fields, not an error)
- [ ] Confirm it fails (`Cannot find module`) (Task 7, Step 2)
- [ ] Write `src/pages/api/agentic-status.ts` (Task 7, Step 3) — fails open to `null` fields if the hub is unreachable, never a 500 to the visitor
- [ ] Confirm tests pass — **2 passed** (Task 7, Step 4)
- [ ] `wrangler secret put` for the two Access credentials; `AGENTIC_OS_STATUS_URL` as a plain (non-secret) var (Task 7, Step 5)
- [ ] Write `src/components/AgenticOsWidget.astro` (Task 7, Step 6) — styling follows that repo's existing `Newsstand.astro` conventions, not respecified here
- [ ] Commit (Task 7, Step 7) — in the site repo, its own CI applies (already has dependency-review-action etc. per Atlas)

---

## Block 8 — CI/CD, security gates, smoke test (this repo)

**Plan reference:** "Task 8: Smoke test and CI gate (agentic-os repo)" — the largest single block, baseline copied from `marcobellingeri.dev`'s live CI and scaled to the declared Level 1 (see spec §6.1).
**Files:** `scripts/verify-hub.sh`, `.github/workflows/validate.yml`, `sonar-project.properties`, `.gitleaks.toml`, `.checkov.yml`.
**Depends on:** Blocks 1-3 existing (the CI jobs validate/test them).
**Verified 2026-07-28**: `verify-hub.sh` actually run (produces exactly the documented two-`FAIL:`-lines/exit-1 output against nothing listening), `validate.yml` YAML parses clean with all 7 jobs present, `sonar-project.properties` and `.gitleaks.toml` syntax confirmed.

Checklist:
- [ ] Write `scripts/verify-hub.sh` (Task 8, Step 1) — post-deploy smoke test, checks Grafana health + status API
- [ ] Run it once against nothing running, confirm the expected-fail path (Task 8, Step 2)
- [ ] Write `.github/workflows/validate.yml` (Task 8, Step 3) — 7 jobs:
  - [ ] `terraform`, `compose`, `status-api-tests` jobs
  - [ ] `workflow-lint` (zizmor, blocks on HIGH)
  - [ ] `dependency-review` (blocks on HIGH, runs on Dependabot PRs too — no secrets used)
  - [ ] `gitleaks` (full history, zero tolerance, excluded only for Dependabot PRs per the documented reason)
  - [ ] `sonar` (excluded for Dependabot PRs — `SONAR_TOKEN` isn't passed to them, not a weakened gate)
  - [ ] **`actions/checkout`, `dependency-review-action`, `gitleaks-action`, `sonarqube-scan-action` are pinned to SHAs already verified live in `marcobellingeri.dev`** — reuse them, don't retype
  - [ ] **`actions/setup-python` and `hashicorp/setup-terraform` have no verified pin in this portfolio yet — look up the current release SHA before running this workflow for real, do not invent one**
- [ ] Write `sonar-project.properties` (Task 8, Step 4) — scope is `services/public-status-api` only; Terraform is Checkov's job, not Sonar's
- [ ] Write `.gitleaks.toml` (Task 8, Step 5) — start from defaults, no exceptions yet
- [ ] Write `.checkov.yml` (Task 8, Step 6) — **not a straight `cp` from langfuse-devops-lab** (fixed 2026-07-28: that file's entire `skip-check` list is Kubernetes/Helm/Supabase-specific and none of it applies here — write the trimmed version in the plan directly, `framework: [terraform, dockerfile, github_actions]`, empty `skip-check`)
- [ ] Commit (Task 8, Step 7)

**Reference doc:** `~/GitHub/Atlas/concepts/pipeline-cicd.md` ("Secondo esempio: Agentic OS" section — the declared-Level-1 reasoning) and `~/GitHub/Atlas/concepts/testing-pyramid.md` (the 5-line test contract, in spec §6.2).

---

## After all 8 blocks: what's still manual

- `terraform apply` (create the real VPS) — not part of any block above, deliberate. Run it yourself when ready.
- The Block 5 Cloudflare Tunnel CLI sequence — manual, against the real account.
- `cloudflared tunnel create` and the DNS routes — manual.
- Filling in real secrets in `docker/.env` on the VPS itself (never in git).
- Confirming `curl` in the status API's base image (Block 3) — needs a real Docker daemon, deliberately not run on the planning machine.

## What's explicitly out of scope for Phase 1 (don't add it here)

No Kubernetes/K3s. No self-hosted Langfuse (the app stays on Langfuse Cloud).
No wiring to `llm-council` (its session-linkage gap was already fixed
elsewhere on 2026-07-26). No Phase 2/3/4 work (agent hookups, Personal Portal,
session RAG) — those get their own spec → plan cycle when Marco is ready to
build them. See spec §7 for what they'll look like when that happens
(including a forward-looking note in spec §7 on LLM-specific architectural
patterns — output-untrusted, structured Pydantic responses, model-appropriate
timeouts, tool permissions in code not prompt — for whenever Phase 4 needs
them; none of it applies to Phase 1, which makes no LLM calls at all).
