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

**The three public numbers are indicative, not accounting.** They are `increase()`
over 24h, and Prometheus extrapolates to the window edges: measured, real growth of
23787 tokens read as 27956 with sparse samples. This is not a workaround, it is the
tool's stated scope — *"If you need 100% accuracy, such as for per-request billing,
Prometheus is not a good choice"*.

**Delta temporality: evaluated, refused, revisit at beta.** It would let the
Collector sum sessions and remove the `session_id` label entirely — the better
architecture on paper, and the OTel data model points at it for short-lived
processes. `deltatocumulative` is alpha, accumulates in memory (every redeploy resets
the counters), and drops streams after 5 minutes idle by default. Not a good trade
for a verified setup at this size.

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

**Langfuse no** — Phase 1 makes no model call of its own; there is nothing to trace.
A standing decision for Phase 4 (session RAG), not a gap today.

**LangChain no**, in any phase. The portfolio already has a lighter proven pattern
(direct embedding + pgvector + a direct Anthropic call), and fewer dependencies is
the whole posture.

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
