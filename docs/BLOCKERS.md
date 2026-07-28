# Open blockers and what the documentation says about them

Written 2026-07-28, after executing all eight Phase 1 blocks. Every item below was
checked against vendor documentation (and, where possible, against the real tool)
rather than assumed. Each says whether it can be closed **now, with no credentials
at all**, or whether it waits on an account, a token, or live infrastructure.

Marco has no Hostinger account, no API tokens and no keys at the time of writing.

---

## Closed since the first run — no credentials needed

### The Collector config is now verified against the real Collector

`docker compose config` validates the compose file, not the Collector's own
configuration, and there is no Docker daemon on the planning machine. That left the
most important file in the stack unproven. Closed by downloading the real
`otelcol-contrib` 0.157.0 binary and running its `validate` subcommand — no daemon,
no container, no credentials:

```
otelcol-contrib validate --config=file:docker/otel-collector-config.yaml   # exit 0
```

The same command against the configuration **as the plan originally wrote it**
returns:

```
Error: failed to build pipelines: failed to create "prometheus" exporter for
data type "logs": telemetry type is not supported
```

So the `logs`-pipeline bug was real and would have stopped the Collector from
starting, and the fix is verified rather than reasoned.

### Both dependency gates now run

`actions/dependency-review-action` cannot run on a private repository: GitHub's docs
say it is available *"for all public repositories, as well as private repositories
that have GitHub Code Security or GitHub Advanced Security enabled"*, and PR #1
confirmed it live (`Dependency review is not supported on this repository`).

Two answers, in order. First, **pip-audit** was added as the dependency audit the
declared Level 1 requires: no licence, no secret, and it checks the whole pinned set
rather than only a PR's diff. Then Marco made **this repository public** (2026-07-28),
which unblocks `dependency-review` — so both now run, and they are kept both because
they see different things: the whole set versus what a single PR introduces. If the
repo ever goes private again, pip-audit keeps the gate alive on its own.

Going public was not enough by itself: the action then failed with *"Please ensure
that Dependency graph is enabled"*. There is no REST field for the dependency graph,
but GitHub's docs say `PUT /repos/{owner}/{repo}/vulnerability-alerts` *"enables
dependency alerts and the dependency graph"* — that call fixed it, and
`dependency-review` is green as of the same day. Secret scanning and push protection
were switched on at the same time (both free on a public repo, and the same baseline
`marcobellingeri.dev` already runs).

---

## Still open — waits on an account or a credential

### 1. Hostinger data centre and template IDs (Task 1, Step 6)

The plan says to read them from the provider's data sources. Those data sources call
the Hostinger API, which is authenticated — verified directly:

```
GET https://developers.hostinger.com/api/vps/v1/data-centers  ->  401 {"message":"Unauthenticated."}
```

There is no public listing to substitute, and **guessing the numbers is not an
option**: they are opaque IDs, and a wrong one either fails the apply or silently
provisions in the wrong region (a data-residency decision, not a cosmetic one).
`terraform.tfvars.example` keeps them as `0 # REPLACE`.

**Needs:** a Hostinger account + `HOSTINGER_API_TOKEN`. Everything else in the
Terraform module is written and passes `fmt -check` and `validate` today.

### 2. `terraform apply`, the Tunnel, and the Access applications

Deliberately outside CI in the original plan and still correct that way. The tunnel
and the two Access applications are dashboard work against the Cloudflare account
Marco already has; the sequence is written out in `docs/CLOUDFLARE_TUNNEL_SETUP.md`
and was corrected to the remotely-managed model (the planned `cloudflared tunnel
create` produces a *locally*-managed tunnel, which never prints the `TUNNEL_TOKEN`
the compose file expects).

**Needs:** the VPS to exist first, then a human at the dashboard.

### 3. The site's Worker secrets

`wrangler secret put` for `AGENTIC_OS_ACCESS_CLIENT_ID`,
`AGENTIC_OS_ACCESS_CLIENT_SECRET` and `AGENTIC_OS_STATUS_TOKEN`, plus
`AGENTIC_OS_STATUS_URL` as a plain var. All three values only exist after item 2.
Until then the endpoint answers with three `null` fields by design and the widget
shows dashes — the degraded path is tested, so this is not a broken state.

### 4. SonarCloud

The `sonar` job is red and will stay red until a SonarCloud project exists and
`SONAR_TOKEN` is set as a repository secret. SonarCloud's free tier does cover
private projects up to 50k LoC, so this is setup work, not a paywall.

The job is deliberately **not** made to skip when the secret is absent. A quality
gate that disappears together with its credential is `continue-on-error` with extra
steps, and the whole point of declaring a gate policy is that a gate either blocks
or is removed on purpose.

### 5. Docker, the parts a binary cannot stand in for

Still unproven without a daemon: that the four pinned images pull, that the status
API image builds, and that the `HEALTHCHECK` behaves as intended at runtime. The
tags were confirmed to exist on their registries, the Dockerfile avoids `curl`
(absent from `python:3.12-slim`) in favour of stdlib `urllib`, and the compose file
resolves — but none of that is the same as running it.

**Needs:** either a Docker daemon on some machine, or the VPS itself.

### 6. Claude Code's metric names, against the installed version

The names come from Claude Code's official telemetry documentation, and the
Collector pins the translation strategy so the Prometheus side is deterministic.
What cannot be checked without the hub running is whether the *installed* Claude
Code version still emits those instrument names — they are beta and versioned. The
first real session against the hub is the check; if a panel is empty, that is the
reason, and the three places to update are listed in
`docs/CLAUDE_CODE_TELEMETRY.md`.

---

## Decisions that are Marco's, not the implementation's

1. ~~**Private repo, or public?**~~ **Decided 2026-07-28: public.** Audited first
   (no secrets, no PII, no private-knowledge-base content); `dependency-review` runs
   again as a result. Two things do become visible and can still be scrubbed if
   unwanted: the `~/GitHub/Atlas/...` reference paths in `README.md`/`TASKS.md`
   (filenames of a private knowledge base, no content), and the project names in the
   spec's Phase 2 sketch, including `monferrinoAI`.
2. **Whether the `sonar` gate stays** as a declared blocking gate (then it needs the
   project + token) or is dropped from the contract. Half-measures are what the test
   contract exists to prevent.
3. **When to open the Hostinger account**, which is the only thing gating real
   infrastructure. Everything else is already written and verified.
4. **Where the widget goes on the site**, and whether it ships before the hub exists
   (it degrades to dashes, so it can).
5. ~~**Whether the site PR merges now.**~~ **Decided 2026-07-28: merged** (PR #156,
   squash, all 12 gates green). `/api/agentic-status` is live and inert — three
   `null` fields until the hub and its secrets exist.
6. **Whether the hub stays past the first month** — the original one-month framing
   with a clean `terraform destroy` is still the plan of record.
