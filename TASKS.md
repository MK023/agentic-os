# Agentic OS — Phase 1 execution checklist

Consolidated hand-off file: everything to build, in order, by logical block.
Full code/exact commands for every step live in
`docs/superpowers/plans/2026-07-28-phase1-observability-hub.md` (line numbers
given per block below) — this file is the checklist + the "why" + the gotchas,
so whoever executes doesn't need to hold the whole plan in their head at once.
Rationale for every decision (why Docker Compose not K3s, why this security
fix, why Sentry now and Langfuse later) lives in
`docs/superpowers/specs/2026-07-28-agentic-os-phase1-design.md`.

**Do not start before reading "Block 0" below — it gates everything else.**

---

## Block 0 — Prerequisites (gates every other block)

Status as of 2026-07-28: **Cloudflare account exists already.** **Hostinger
account does not exist yet.** Marco's target start date is **after
2026-08-10**. Nothing below should run for real before then unless Marco says
otherwise on the executing machine.

Before Block 1 can fully close (not before it can *start* — see plan.md
"Before you start", lines 23-51):

- [ ] Hostinger account created + API token (`HOSTINGER_API_TOKEN`) — **not open yet**
- [ ] Cloudflare account with a zone + `CLOUDFLARE_API_TOKEN` (Tunnel edit) — **already have this**
- [ ] SSH keypair to attach to the VPS
- [ ] `gh` / git access to push this repo and to `MK023/marcobellingeri.dev` (Block 7 touches that repo)

Blocks 1-4, 6, 8 are fully writable/testable **without** the Hostinger account
— only one step in Block 1 and the real `terraform apply`/`cloudflared tunnel
create` need it. Don't let a missing Hostinger account block writing code.

---

## Block 1 — Infrastructure: Terraform VPS provisioning

**Plan reference:** Task 1, plan.md lines 55-176.
**Files:** `terraform/hostinger-vps/{versions,variables,main,outputs}.tf`, `terraform.tfvars.example`.
**Depends on:** nothing to *write*. Needs Hostinger account to *look up* real IDs (see below) and to `apply`.

Checklist:
- [ ] Write `versions.tf` — provider block, `HOSTINGER_API_TOKEN` from env, never hardcoded (plan.md L79-92)
- [ ] Write `variables.tf` — `vps_plan`, `data_center_id`, `template_id`, `ssh_public_key_path` (plan.md L94-112)
- [ ] Write `main.tf` — `hostinger_vps_ssh_key`, `hostinger_vps_post_install_script`, `hostinger_vps` resources (plan.md L114-138)
- [ ] Write `outputs.tf` — `vps_ip` output (plan.md L140-142)
- [ ] Write `terraform.tfvars.example` with placeholder IDs marked REPLACE (plan.md L144-149)
- [ ] **Gate**: `terraform init` + `terraform console`, look up real `data_center_id` (pick EU) and `template_id` ("Ubuntu 24.04 with Docker") from the provider's data sources — **do not guess these numbers** (plan.md L151-162)
- [ ] `terraform fmt -check && terraform validate` — must pass before commit (plan.md L164-167)
- [ ] Commit

**Reference doc:** `~/GitHub/Atlas/entities/tools/hostinger.md` (Terraform provider schema, verified against the real README, not memory) and `~/GitHub/Atlas/entities/tools/terraform.md`.

---

## Block 2 — Observability stack: Docker Compose

**Plan reference:** Task 2, plan.md lines 178-428.
**Files:** `docker/docker-compose.yml`, `docker/otel-collector-config.yaml`, `docker/prometheus.yml`, `docker/grafana/provisioning/**`.
**Depends on:** nothing external — fully writable and `docker compose config`-checkable with dummy env values.

Checklist:
- [ ] Write `otel-collector-config.yaml` — OTLP receiver **with `bearertokenauth` extension** (plan.md L188-224). This is the fix for the CVE-2026-28798-class gap found in the security sweep: an OTLP ingest endpoint behind a public Cloudflare Tunnel hostname must not be left unauthenticated.
- [ ] Write `prometheus.yml` — scrape config, 15s interval (plan.md L226-235)
- [ ] Write Grafana provisioning: datasource (L237-247), dashboard provider (L249-260), dashboard JSON (L262-322) — **verify the `claude_code_*` metric names against your installed Claude Code version before relying on this dashboard** (they're versioned and can change)
- [ ] Write `docker-compose.yml` — 4 services (`cloudflared`, `otel-collector`, `prometheus`, `grafana`) + `status-api` (Block 3). **Every image pinned to a version, never `:latest`** — check each image's current stable tag before running, the versions in the plan were current at spec time (plan.md L330-407). Every service gets `security_opt: [no-new-privileges:true]` + `cap_drop: [ALL]`.
- [ ] **Gate**: `docker compose config --quiet` with dummy env values — must exit 0
- [ ] Commit

**Security decisions baked in here, don't relax them:**
- Prometheus **never** gets a Cloudflare Tunnel hostname of its own (no auth of its own) — internal to the Docker network only.
- `OTLP_INGEST_TOKEN` env var is what the `bearertokenauth` extension checks — generate with `openssl rand -hex 32`, goes in `docker/.env` (Block 4) and Claude Code's local config (Block 6).

**Reference docs:** `~/GitHub/Atlas/entities/tools/opentelemetry.md` (Collector config, `bearertokenauth`), `~/GitHub/Atlas/entities/tools/grafana.md`, `~/GitHub/Atlas/entities/tools/prometheus.md`, `~/GitHub/Atlas/entities/tools/docker.md` (image pinning + hardening baseline).

---

## Block 3 — Application: public-safe status API

**Plan reference:** Task 3, plan.md lines 430-677.
**Files:** `services/public-status-api/{main.py,sentry.py,conftest.py,requirements.txt,Dockerfile,test_main.py}`.
**Depends on:** nothing external. TDD — write the tests first, they use mocked Prometheus/Sentry responses.

Checklist:
- [ ] Write `test_main.py` (4 tests: whitelisted fields, missing token 401, wrong token 401, upstream failure → 502 + Sentry capture) — plan.md L443-486
- [ ] Write `conftest.py` — sets `PROMETHEUS_URL`/`STATUS_API_TOKEN` env vars before `main.py` is imported by the test module (plan.md L500-508)
- [ ] Run tests, confirm they fail (`ModuleNotFoundError`) — plan.md L510-513
- [ ] Write `requirements.txt` (fastapi, uvicorn, httpx — 3 deps, deliberately minimal) — plan.md L515-521
- [ ] Write `sentry.py` — zero-dependency envelope client, ported from `marcobellingeri.dev/engine/lib/sentry.mjs`, same fail-open contract (plan.md L523-573)
- [ ] Write `main.py` — **`secrets.compare_digest` for the token check, not `!=`** (timing side-channel fix), no app-level rate limiter (deliberately — Cloudflare handles that at the edge, see the `ponytail:` comment in the plan) (plan.md L575-625)
- [ ] Run tests, confirm 4 passed (plan.md L627-630)
- [ ] Write `Dockerfile` (plan.md L632-641)
- [ ] Commit

**Reference docs:** `~/GitHub/Atlas/entities/tools/fastapi.md`, `~/GitHub/Atlas/entities/tools/sentry.md` (Python port pattern).

---

## Block 4 — Secrets wiring and bootstrap

**Plan reference:** Task 4, plan.md lines 679-737.
**Files:** `scripts/bootstrap.sh`, `docker/.env.example`, `.gitignore`.
**Depends on:** Blocks 1-2 existing (bootstrap.sh assumes the repo layout they create).

Checklist:
- [ ] Write `scripts/bootstrap.sh` — clones this repo onto the VPS, refuses to start the stack without a real `docker/.env` (plan.md L689-709)
- [ ] Write `docker/.env.example` listing every required var, no real values (plan.md L711-717)
- [ ] Add `docker/.env` and `terraform/hostinger-vps/terraform.tfvars` to `.gitignore` — **real secrets never committed** (plan.md L719-724)
- [ ] Commit

---

## Block 5 — Cloudflare Tunnel setup (manual, on the executing machine)

**Plan reference:** Task 5, plan.md lines 739-799 (note: corrected 2026-07-28 to reflect the `bearertokenauth` decision — read the current version, not an older copy).
**Files:** `docs/CLOUDFLARE_TUNNEL_SETUP.md` (doc only — the actual tunnel/DNS/Access setup is manual CLI/dashboard work, not something this repo's CI drives).
**Depends on:** the real Cloudflare account (already have it) — this block's *doc* can be written now, but *running* the sequence in it needs Blocks 1-2 deployed first (needs the VPS + compose stack up to point the tunnel at).

Checklist:
- [ ] Write `docs/CLOUDFLARE_TUNNEL_SETUP.md` (plan.md L750-793)
- [ ] Commit the doc now; **execute** the manual sequence inside it only once Blocks 1-4 are deployed for real
- [ ] Three hostnames: Grafana (Access, your email only), status API (Access, Service Auth token — feeds Block 7), OTel ingest (no Access app — Claude Code can't do the service-token header — auth is the `bearertokenauth` token from Block 2 instead)

---

## Block 6 — Local Claude Code telemetry (docs)

**Plan reference:** Task 6, plan.md lines 806-846.
**Files:** `docs/CLAUDE_CODE_TELEMETRY.md`.
**Depends on:** Block 5 giving you the real Tunnel hostname + `OTLP_INGEST_TOKEN` value to fill in.

Checklist:
- [ ] Write `docs/CLAUDE_CODE_TELEMETRY.md` — includes `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>`, the header the Collector's `bearertokenauth` extension checks (plan.md L809-841)
- [ ] Commit

---

## Block 7 — Public widget (separate repo: `MK023/marcobellingeri.dev`)

**Plan reference:** Task 7, plan.md lines 848-1000.
**Repo:** switch to `~/GitHub/marcobellingeri.dev` for this block — not this repo.
**Files:** `src/pages/api/agentic-status.ts`, `src/components/AgenticOsWidget.astro`, `test/agentic-status.test.mjs`.
**Depends on:** Block 5's status API Cloudflare Access Service Token (Client ID/Secret) to actually work end-to-end; the code/tests can be written and run against mocks before that exists.

Checklist:
- [ ] Write the failing test (mocked fetch, success + degraded-fallback cases) — plan.md L858-891
- [ ] Confirm it fails (`Cannot find module`) — plan.md L893-895
- [ ] Write `src/pages/api/agentic-status.ts` — fails open to `null` fields if the hub is unreachable, never a 500 to the visitor (plan.md L897-928)
- [ ] Confirm tests pass — plan.md L930-932
- [ ] `wrangler secret put` for the two Access credentials; `AGENTIC_OS_STATUS_URL` as a plain (non-secret) var — plan.md L934-941
- [ ] Write `src/components/AgenticOsWidget.astro` — styling follows that repo's existing `Newsstand.astro` conventions, not respecified here (plan.md L943-953)
- [ ] Commit (in the site repo, its own CI applies — already has dependency-review-action etc. per Atlas)

---

## Block 8 — CI/CD, security gates, smoke test (this repo)

**Plan reference:** Task 8, plan.md lines 1003-1240 — the largest single block, baseline copied from `marcobellingeri.dev`'s live CI and scaled to the declared Level 1 (see spec §6.1).
**Files:** `scripts/verify-hub.sh`, `.github/workflows/validate.yml`, `sonar-project.properties`, `.gitleaks.toml`, `.checkov.yml` (copied from `langfuse-devops-lab`).
**Depends on:** Blocks 1-3 existing (the CI jobs validate/test them).

Checklist:
- [ ] Write `scripts/verify-hub.sh` — post-deploy smoke test, checks Grafana health + status API (plan.md L1009-1036)
- [ ] Run it once against nothing running, confirm the expected-fail path (plan.md L1038-1042)
- [ ] Write `.github/workflows/validate.yml`:
  - [ ] `terraform`, `compose`, `status-api-tests` jobs
  - [ ] `workflow-lint` (zizmor, blocks on HIGH)
  - [ ] `dependency-review` (blocks on HIGH, runs on Dependabot PRs too — no secrets used)
  - [ ] `gitleaks` (full history, zero tolerance, excluded only for Dependabot PRs per the documented reason)
  - [ ] `sonar` (excluded for Dependabot PRs — `SONAR_TOKEN` isn't passed to them, not a weakened gate)
  - [ ] **`actions/checkout`, `dependency-review-action`, `gitleaks-action`, `sonarqube-scan-action` are pinned to SHAs already verified live in `marcobellingeri.dev`** — reuse them, don't retype
  - [ ] **`actions/setup-python` and `hashicorp/setup-terraform` have no verified pin in this portfolio yet — look up the current release SHA before running this workflow for real, do not invent one** (plan.md L1044-1179)
- [ ] Write `sonar-project.properties` — scope is `services/public-status-api` only; Terraform is Checkov's job, not Sonar's (plan.md L1181-1197)
- [ ] Write `.gitleaks.toml` — start from defaults, no exceptions yet (plan.md L1199-1210)
- [ ] Copy `.checkov.yml` from `langfuse-devops-lab`, strip check IDs for resources this repo doesn't have (plan.md L1212-1218)
- [ ] Commit

**Reference doc:** `~/GitHub/Atlas/concepts/pipeline-cicd.md` ("Secondo esempio: Agentic OS" section — the declared-Level-1 reasoning) and `~/GitHub/Atlas/concepts/testing-pyramid.md` (the 5-line test contract, in spec §6.2).

---

## After all 8 blocks: what's still manual

- `terraform apply` (create the real VPS) — not part of any block above, deliberate. Run it yourself when ready.
- The Block 5 Cloudflare Tunnel CLI sequence — manual, against the real account.
- `cloudflared tunnel create` and the DNS routes — manual.
- Filling in real secrets in `docker/.env` on the VPS itself (never in git).

## What's explicitly out of scope for Phase 1 (don't add it here)

No Kubernetes/K3s. No self-hosted Langfuse (the app stays on Langfuse Cloud).
No wiring to `llm-council` (its session-linkage gap was already fixed
elsewhere on 2026-07-26). No Phase 2/3/4 work (agent hookups, Personal Portal,
session RAG) — those get their own spec → plan cycle when Marco is ready to
build them. See spec §7 for what they'll look like when that happens.
