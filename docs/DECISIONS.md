# Decisions, and what measuring changed about them

The short version of everything that would otherwise be rediscovered. Each entry is a
decision that is **closed** — reopen one only with new evidence, not with a new
opinion. Design and reasoning at length: `superpowers/specs/`.

## Platform

**Railway, five services, no VPS.** A Hostinger VPS variant was written and validated
first (Terraform module, post-install bootstrap, the lot). Dropped on 2026-07-29: for
five small services used by one person, a PaaS costs about the same and removes a
machine to patch, harden and back up. The move cost **five files** — everything else
in the repo was already platform-neutral, and knowing that beforehand is what made
the decision cheap rather than agonising. The VPS path is gone from the repo on
purpose: two deployment paths mean one of them is always subtly wrong.

**No Kubernetes, no K3s, no Docker Swarm.** Five small services, one user. Docker's
own documentation: *"If you're not planning on deploying with Swarm, use Docker
Compose instead"* — everything Swarm adds (multi-host networking, cross-node service
discovery, cluster reconciliation) exists for more than one node. An orchestrator
here would also mean a machine to run it on, which is the thing we just removed.

**One copy of every configuration file**, in `docker/`. The Railway images copy them
at build time; the local compose file mounts them *and* gives each container a
network alias equal to its Railway internal DNS name, so hostnames inside those files
are correct in both environments. Never fork a config "just for local".

## Security and privacy

**The OTLP ingest authenticates inside the Collector** (`bearertokenauth`), not at the
edge. A public hostname is not an access control: that is the CVE-2026-28798 pattern
(ZimaOS, an unauthenticated internal endpoint behind a tunnel). Verified live: 401
with no token, 401 with a wrong one, 200 with the right one.

**Metric labels are an allow-list**, not a delete-list. Claude Code sends identity as
*data point* attributes, so `resource_to_telemetry_conversion: false` does **not**
keep it out — measured against v2.1.220, `user_email` arrived carrying a real
address, with `user_id`, `user_account_id`, `user_account_uuid` and `organization_id`
beside it. A delete-list holds only until the next release adds a sixth attribute;
the allow-list drops what it has not heard of. Verified by sending attributes no
version emits (`user.name`, `user.phone`, `workspace.path`) — all dropped.

**`session.id` stays a label**, and deleting it is a mistake that looks like a fix.
The counters are cumulative per process, so without it two concurrent sessions write
the same series and the last export wins — measured: two parallel sessions produced
one series reading `1` instead of `2`. It is a random per-run UUID, it never leaves
the hub, and the public endpoint returns only sums.

**Prometheus never gets a hostname of its own**, on any ingress. Its HTTP API has no
authentication; the only safe exposure is the project's private network.

**No service takes a public Railway domain.** The Cloudflare Tunnel is the only way
in, so the platform's own hostnames stay unused rather than sitting unprotected
beside the front door.

**Every image runs as a non-root user, with one documented exception.** Three of the
four base images already did, invisibly to a scanner reading a `FROM`; `cloudflared`
did not, and now does — a connector making only outbound connections needs no
privilege.

The exception is **Prometheus on Railway**, which carries `RAILWAY_RUN_UID=0`. Its
attached volume is presented owned by root, so as `nobody` it cannot even create
`/prometheus/queries.active` and exits at startup — measured, not anticipated.
Railway documents running as root as the supported answer. The exposure is bounded
by the fact that Prometheus has no public domain and no authentication surface of
its own; the weakening is applied as a Railway variable rather than a `USER 0` line
so that the local run keeps the stronger posture. If this ever needs revisiting, the
alternative is an entrypoint that chowns the mount and drops privileges — more moving
parts inside the one service that has none today.

A CI gate keeps that exception single: `scripts/check-image-users.sh` fails the build
if any Dockerfile here lacks a `USER` or declares root. A written exception stays an
exception; a copied one becomes the norm, and the copying is what the gate stops.

## Metrics semantics

**Metric names are pinned, not inherited.** `translation_strategy:
UnderscoreEscapingWithoutSuffixes`, because the default appends type *and unit*
suffixes — `claude_code.token.usage` with unit `tokens` would become
`claude_code_token_usage_tokens_total`, a name that depends on someone else's unit
metadata. Measured against the real client, Prometheus sees
`claude_code_session_count`, `claude_code_token_usage`, `claude_code_cost_usage` and
`claude_code_active_time_total`.

**`metric_expiration: 25h`.** The 5-minute default drops a counter from `/metrics`
five minutes after its last update, so "cost today" would read zero for most of a day
in which someone stopped working for lunch.

**The three public numbers are plain sums, and they are indicative, not
accounting.** The shape of the data decides the query: Claude Code emits **one series
per session** (`session_id` is a label), each a cumulative counter for that process
alone — born, growing while the session runs, flat forever after.

`increase(...[24h])` was the original choice and it was **wrong**, structurally.
On `claude_code_session_count`, incremented exactly once at session start, the series
appears at 1 and never moves: growth inside the window is zero, always. Measured in
production on 2026-07-29 with two real sessions — and invisible before that, because
the local test that "verified" it re-sent the same `session_id` with a higher value,
manufacturing exactly the growth that never happens in reality. A synthetic payload
can confirm a query and still be lying about the shape of the data.

Summing the current value of every live series is right instead: each carries its own
session's total, and the Collector's `metric_expiration: 25h` supplies the window by
dropping series a day after their last update. So "today" means "the last ~25 hours of
activity", which is what the numbers are honestly able to say.

They stay indicative either way — that is the tool's stated scope: *"If you need 100%
accuracy, such as for per-request billing, Prometheus is not a good choice"*.

**The Collector's own telemetry is scraped, because a flat series says nothing.** A
cumulative counter that stops growing looks identical whether the client stopped
exporting or nobody was working: the Collector keeps exposing the last value for 25h
(`metric_expiration`) and Prometheus keeps scraping unchanged samples, so `rate()` is
zero in both cases and every panel flatlines correctly. That ambiguity was
misdiagnosed three times between 29 and 30 July — once as a wedged exporter, once as
a client needing a restart, both wrong — before anyone noticed the signal simply
cannot answer the question.

`otelcol_receiver_accepted_metric_points` can: it counts payloads *arriving*,
independent of their content. Above zero somebody is talking, at zero nobody is.
Enabled with `service.telemetry.metrics.readers` and scraped as a second job on port
**8888**, the same container as the pipeline output on 8889 — two ports, two
different questions. Bound to `0.0.0.0` because Prometheus is another container;
still no Tunnel hostname, so it stays on the private network like everything else.

Two traps worth naming. `service::telemetry::metrics::address` has been **ignored**
since v0.123.0: writing it is not a syntax error, so nothing complains and the port
simply never opens — a silent failure that only reading the vendor docs prevents. And
`docker compose config` never validated this file at all: compose knows its own
schema, not the Collector's, and the config is merely a file mounted into a
container. CI now runs `validate` against the built image, so the exact file that
ships is checked by the exact binary that will run it.

**The public cost is computed from tokens, not read from `claude_code.cost.usage`.**
That metric is Claude Code's own estimate, and on 2026-07-30 it read **$0.90** for a
session whose measured tokens price out at **$2.53** at list — 326,484 cacheCreation,
515,573 cacheRead, 22,822 input, 4,582 output on `claude-opus-5`. The token counters
sitting next to it are measured and correct, so the cost is now `tokens × list price`,
grouped `by (model, type)` because the rate depends on both.

The trade is explicit: a hand-maintained copy of somebody else's price list
(`PRICES_USD_PER_MTOK` in `main.py`, plus the flat-rate version in the Grafana panel)
against a number nobody can reproduce. The published figure is now arithmetic anyone
reading the repo can redo, which is the only kind of number worth putting on a public
page. A price change is a code change, and the comment says so.

A model or token type missing from the table is priced at the **dearest known rate**
and reported once to Sentry. Pricing a gap at zero was the tempting default and is the
dangerous one: it reads as a cheap day rather than as missing knowledge, and a number
that is quietly too low is one nobody investigates. Overstating is visible; understating
is not.

**How this was found is the part worth keeping.** Six hypotheses died first — a wedged
exporter, a client needing a restart, an export interval too long, a stale dashboard,
a label allow-list collapsing series — each guessed at a mechanism instead of narrowing
where the discrepancy lived. The one that worked was arithmetic: sum the tokens by type,
multiply by the published rates, compare. The earlier claim that the metric was off by
58× was itself wrong, and for a dull reason — it compared a 5-session token total against
a 1-session cost. **Establish what the reference number measures before comparing anything
to it.**

**Delta temporality: evaluated, refused, revisit at beta.** It would let the
Collector sum sessions and remove the `session_id` label entirely — the better
architecture on paper, and the OTel data model points at it for short-lived
processes. `deltatocumulative` is alpha, accumulates in memory (every redeploy resets
the counters), and drops streams after 5 minutes idle by default. Not a good trade
for a verified setup at this size.

## CI

**Two dependency gates, on purpose.** `dependency-audit` (pip-audit) checks the whole
pinned set on every run; `dependency-review` blocks on HIGH CVEs introduced by a
single PR's diff. They answer different questions — and only the first works on a
private repository: `dependency-review` needs GitHub Code Security there (confirmed
live on PR #1, while this repo was still private: *"Dependency review is not
supported on this repository"*). This repository is public, so both run.

**Sonar's "new code" definition is 30 days**, inherited from the instance default and
left there deliberately: trunk-based, one developer, continuous delivery, no release
versions to make "previous version" meaningful — exactly the case the vendor's
guidance points at "number of days" for. The status API's 100%-on-new-code coverage
threshold is scoped by this setting.

**The `sonar` job runs on pushes to `main`, not only on PRs.** Without a main-branch
analysis there is no baseline and no quality-gate history, and "new code" has nothing
to be new against — measured: the project had `measures: []` until the first main
analysis.

**A gate whose credential is missing stays red instead of skipping** (`sonar` before
`SONAR_TOKEN` existed): a quality gate that disappears when its credential does is
the `continue-on-error` antipattern with extra steps.

## Logs

**No logs pipeline today.** The Prometheus exporter supports the metrics signal only,
so a `logs` pipeline pointing at it fails Collector startup — `OTEL_LOGS_EXPORTER` is
deliberately left unset rather than exporting into nothing.

**Grafana Loki: evaluated 2026-07-29, deferred to a Phase 1.5 with its own spec.**
The objection that mattered turned out to be unfounded — Claude Code redacts prompts,
responses, tool details and raw API bodies **by default**, so its log events are
metadata (which tool, which error, which refusal, which permission decision), not
content. Worth having. Deferred because it is a design pass, not a bolt-on: the same
identity attributes ride on log records, Loki maps resource attributes to index
labels by default and warns about cardinality, and its filesystem backend is
explicitly unreplicated with disk-full left to the operator — meaning the storage
choice (R2 is S3-compatible and already in the portfolio) is part of the design, not
a detail. And Phase 1 has not been used yet: a week of real use will say which
questions the metrics leave unanswered.

## Observability of this project itself

**Sentry yes, from the start** — zero-dependency envelope client in the status API,
same fail-open contract as the one already running on marcobellingeri.dev: no DSN is
a no-op, a failed delivery never changes the response.

**The Sentry release is the deploy's commit SHA, not a version we bump.** Sentry
takes any string and explicitly suggests a commit SHA, so both were available.
`RAILWAY_GIT_COMMIT_SHA` is injected by the platform on every deployment, which makes
the release a fact about what is running rather than a claim someone remembered to
update — and a release updated by hand is a release that is eventually wrong. The cost
of the trade is real and accepted: Sentry releases no longer line up with GitHub tags,
so "which release is this" is answered by the SHA, not by `v1.0.0`. When the variable
is absent — locally, and under `docker compose` — the field is **omitted rather than
sent empty**, so local errors do not accumulate under a version that does not exist.

**Langfuse no** — Phase 1 makes no model call of its own; there is nothing to trace.
A standing decision for Phase 4 (session RAG), not a gap today.

**LangChain no**, in any phase. The portfolio already has a lighter proven pattern
(direct embedding + pgvector + a direct Anthropic call), and fewer dependencies is
the whole posture.

## Where each secret lives, and what rotating one costs

Doppler is the source of truth. Railway service variables are the runtime copy. The
laptop keeps exactly one, because a shell reads it on every terminal and a network
call there fails offline.

| Secret | Doppler | Railway | Laptop | Cloudflare Worker |
|---|---|---|---|---|
| `OTLP_INGEST_TOKEN` | yes | `otel-collector` | Keychain (`agentic-os-otlp-ingest`) | — |
| `STATUS_API_TOKEN` | yes | `status-api` | — | as `AGENTIC_OS_STATUS_TOKEN` |
| `TUNNEL_TOKEN` | yes | `cloudflared` | — | — |
| `GF_SECURITY_ADMIN_PASSWORD` | yes | `grafana` | — | — |
| `SENTRY_DSN` | yes | `status-api` | — | — |
| Access service token (ID + secret) | yes | **never** | for `verify-hub.sh` | `AGENTIC_OS_ACCESS_CLIENT_*` |

The Access credentials never touch Railway on purpose: it is Cloudflare that verifies
them, at the edge. The hub does not even see them as something to check.

**Rotation is where this bites.** `OTLP_INGEST_TOKEN` lives in three places — Doppler,
the Collector, and the shell. Change two of the three and telemetry stops **in
silence**: Claude Code keeps exporting with the old value and the Collector answers
401, which nothing surfaces. Same shape for `STATUS_API_TOKEN`, which must match
between the Railway service and the Worker secret **whose name is different**
(`AGENTIC_OS_STATUS_TOKEN`).

A Doppler → Railway sync was evaluated on 2026-07-29 and not adopted: the five
services hold different secrets, so respecting least privilege would mean four
separate Doppler configs to manage five values that change approximately never.
Revisit if the secrets multiply, if rotation becomes routine, or if a second
environment appears.

## How things get verified here

Three layers, each of which found what the previous one could not:

1. **Re-reading** the plan found 5 problems.
2. **Running** the tools found 12 more — an exporter that does not support a signal,
   an environment variable that does not exist, a `file()` that does not expand `~`.
3. **Measuring the real behaviour** found what no documentation states: a real email
   address arriving as a Prometheus label, and the obvious fix for it silently losing
   data.

`docs/LOCAL_DRY_RUN.md` is layer 3 made repeatable. Use it whenever a producer, an
image or a Claude Code version changes.
