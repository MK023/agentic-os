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

**`metric_expiration: 5m` — the default, and it was `25h` until 2026-08-14.** The
default drops a counter from `/metrics` five minutes after its last update, so under an
*instant* query "cost today" read zero for most of a day in which someone stopped
working for lunch. 25h fixed that, and it was the right fix for exactly as long as the
queries were instant.

It became the bug the moment they stopped being — see "the three public numbers" below.
A series the Collector keeps re-exporting keeps landing in the TSDB, so a 25h window on
top of 25h of expiration counts each session for ~50h. Expiration decides when a session
stops counting; the query decides how far back we look. They must not both be the window.

**Prometheus retention is capped by size as well as time, because time alone cannot
protect a disk.** `--storage.tsdb.retention.time=30d` was the only limit until
2026-08-13, when the 500 MB volume filled and compaction began failing once a minute:
`compact head: persist head block: populate block: write chunks: preallocate: no space
left on device`. With only a time limit there is no back-pressure from the disk —
Prometheus grows to fill whatever it is given, long before the days run out.

**The failure was invisible from outside, which is the part worth remembering.**
Ingestion kept working because the head block lives in memory, so the public widget
went on serving correct, moving numbers while persistence was already broken. Nothing
was down; something was doomed. It surfaced only by reading the service's own logs —
and a volume check on 2026-07-30 had already looked at this and passed it, because it
measured *growth* (50 MB) and never divided it into *capacity*. Accumulating was the
signal we wanted, not the question that mattered.

Now `retention.time=7d` **and** `retention.size=300MB`, whichever binds first. The
arithmetic, so the next person can redo it rather than trust it: measured growth is
~50 MB/day, so seven days is ~350 MB, which does not fit a 500 MB volume — compaction
writes the new block *before* deleting the old ones, and the WAL shares the volume but
is **not** counted inside `retention.size`. 300 MB leaves 40% headroom for both, and
at the observed rate the size cap is what actually binds, around day six. So the
honest claim is "about a week", not "seven days". Neither number derives itself: if
the volume is ever resized, both have to be revisited by hand.

Reducing the window from 30 days to about 7 is a cost decision, not a technical one —
the project runs on free credits, and a bigger volume is recurring spend for history
nobody has yet needed.

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

Summing each series' own total is right instead: each carries its own session's total.
Until 2026-08-14 that was a plain `sum()` and the window came from the Collector's
`metric_expiration: 25h`, which dropped a series a day after its last update.

**Corrected 2026-08-14 — the plain sum undercounted after every Collector restart.** An
instant query can only see what the exporter is exposing *right now*, and a restart
starts that exporter empty: every session that had already ended was simply gone.
Reproduced against Prometheus 3.13.2, the pinned version, with the real data shape — 3
sessions / 6600 tokens read back as **1 / 3300** after a restart, while
`sum(max_over_time(...[25h]))` held 3 / 6600. On a counter that only ever grows the max
over the window *is* the final value, so that query reads the same as a plain sum for a
live series and keeps reading it for a dead one.

The queries and `metric_expiration` are **one change, never two**. `max_over_time` alone
overcounts, measured the same day: with expiration at 25h the Collector keeps
re-exporting a session the query can already see in the TSDB, and the effective window
becomes ~50h. With expiration back at the 5m default the behaviour is correct — two live
sessions read 2, one that just ended reads 2, once it falls out of the window it reads 1.

So "today" still means "the last 25 hours of activity", which is what the numbers are
honestly able to say. It just no longer means "unless the Collector restarted".

**What it costs, written down because it is a real trade and not a free win.** The 25h
expiration made the Collector a second copy of the day: a wiped or recreated
`prometheus-data` volume healed itself on the next scrape. It does not any more —
Prometheus' disk is now the only copy, and per the entry above that disk is the part of
this system with an actual failure on its record. If the volume is lost, the three
numbers read ~0 for the rest of the day, `/status` answers 200, and that is
indistinguishable from a morning when nobody worked. The trade is worth making because
the other side of it is a silent double count, but it moves the single point of failure
rather than removing it.

**Both halves ship through three separate Railway services** — the Collector config, the
status API and the Grafana dashboard live in three images with three `watchPatterns`.
The platform cannot land them atomically, and the dangerous order is real: 5m expiration
in production under the old instant `sum()` *is* the undercount bug, live and green. So
after a merge that touches this pair, check the running `commitHash` of all three
services, not just that the deploys are green.

They stay indicative either way — that is the tool's stated scope: *"If you need 100%
accuracy, such as for per-request billing, Prometheus is not a good choice"*.

**The Collector's own telemetry is scraped, because a flat series says nothing.** A
cumulative counter that stops growing looks identical whether the client stopped
exporting or nobody was working: the total panels look back 25h and go on showing the
same number in both cases, and the live `rate()` panel goes quiet in both cases too, so
every panel flatlines correctly. That ambiguity was
misdiagnosed three times between 29 and 30 July — once as a wedged exporter, once as
a client needing a restart, both wrong — before anyone noticed the signal simply
cannot answer the question.

`otelcol_receiver_accepted_metric_points` can: it counts payloads *arriving*,
independent of their content. Above zero somebody is talking, at zero nobody is.
Enabled with `service.telemetry.metrics.readers` and scraped as a second job on port
**8888**, the same container as the pipeline output on 8889 — two ports, two
different questions. Bound to `0.0.0.0` because Prometheus is another container;
still no Tunnel hostname, so it stays on the private network like everything else.

**Scraping it was only half the fix: the dashboard had no panel that could go
red.** All six panels were built on Claude Code metrics plus the Collector's
payload counters, so a dead service produced a flat line, and a flat line is the
same picture as a quiet afternoon. On 2026-08-13 that cost hours: Prometheus's
volume filled, compaction failed once a minute, and the three public numbers kept
moving — correctly — because the head is in RAM. Nothing on screen changed.

`up` is the metric that fixes the shape of the problem. Prometheus synthesises it
for every configured target, so a dead target reads `0` rather than vanishing;
`rate()` over a vanished series has nothing to evaluate and stays silent forever.
Alongside it, `prometheus_tsdb_compactions_failed_total` (a counter, so
`increase(...[1h])`) and `prometheus_tsdb_storage_blocks_bytes` (a gauge, with
thresholds anchored to `--storage.tsdb.retention.size=300MB`) make the 13/08
failure visible in the one place someone actually looks. Both names verified
against the v3.13.2 source rather than recalled.

The panels sit at the **top** of the dashboard, which is why every pre-existing
panel moved down four grid rows. Health below the fold is health nobody reads,
and being unread for hours is the exact failure being fixed.

One thing this cannot cover: if Prometheus itself dies, every panel here reads
"nessun dato", because nothing can measure itself while dead. That case belongs
to the Sentry watchdog and to `smoke.yml`, which observe the hub from outside it.

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

**The cost in Claude Code's own status bar cannot be collected, and would not be
wanted anyway.** The obvious source for a per-session cost looks like the number
already on screen. It is not reachable: `ccstatusline` computes nothing, it prints
`cost.total_cost_usd` out of the JSON Claude Code writes to the statusline command's
**stdin** — a field that appears in no metric and, checked on 2026-07-30, in no
transcript either. Even reachable it would be the client's own estimate, the one
measured at $0.90 against $2.53 of tokens. Per-session cost is therefore the same
`tokens × list price` arithmetic as everything else, kept `by (session_id)` instead of
summed.

**Per-session cost joins its terms with `or`, never `+`.** `+` matches label sets, so
adding one sum per model drops every session that did not use *all* of them — the
normal case — and the panel comes back empty rather than wrong-looking, which is the
worse failure of the two. `or` unions the series and a single `sum by (session_id)`
adds them, so a missing model costs nothing instead of erasing the session. Both
behaviours are pinned in `scripts/cost-per-session.promql-test.yml`: the `or` form
prices two single-model sessions at $10 and $1, the `+` form returns zero rows.
`promtool test rules` runs it without a server or a Docker daemon.

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

**And the rule had already been broken, by `gitleaks`, for exactly that reason.**
The job carried `if: pull_request.user.login != 'dependabot[bot]'`, so the ten
Dependabot PRs merged on 2026-08-13 entered `main` with no secret scan at all and
a green required check — not passed, never run. The justification was real
(gitleaks-action needs a `GITHUB_TOKEN` that never reaches those PRs, and would
have sat red blocking every bump), which is what makes it worth writing down: a
legitimate reason produced precisely the antipattern named one paragraph above.

The fix was to stop needing the credential. The pinned CLI binary wants no token,
so the exception lost its reason and went away along with `pull-requests: read`.
**One path, not two** — keeping the action for humans and the CLI for Dependabot
would have left standing the branch nobody looks at, which is the branch where
things rot. Pinned by version *and* by SHA-256: alone, a version pin is a label,
and a GitHub release asset can be replaced under the same label. Read from the
vendor's docs rather than recalled: `detect` has been deprecated since v8.19, the
command is `gitleaks git`.

**Twelve blocking gates in five workflow files, split by function** (2026-08-13),
after `validate.yml` reached thirteen jobs doing unrelated jobs. The load-bearing
constraint is that **the job names are the contract**: the ruleset requires those
twelve strings as status-check contexts, so a rename during the split would have
detached a check from the gate it was meant to enforce. The blocks were therefore
moved verbatim, comments included, and the twelve job bodies verified identical
before and after. No `paths` filters, deliberately — a required check that never
starts leaves a PR pending forever rather than passing, so it would need a "skip"
job reporting success, which is machinery bought with a few minutes of free
runner time. One `concurrency` group per file, because the shared group would
have let a push cancel the other four workflows, and a cancelled required check
is neither red nor green.

**`smoke.yml`'s schedule is a request, not a cadence.** It asks for `*/10`; across
its first twelve real runs the interval measured between 38 and 93 minutes, median
~55, because GitHub throttles frequent schedules on public repositories. This is
recorded because the first version of the workflow's own comment claimed it would
have caught the two short outages of 2026-08-13, and that claim is false: it
catches outages that *last*, not two-minute restarts. A prober outside GitHub
Actions is what would close the gap.

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

**Criterion applied 2026-08-14, after sixteen days of real use: still no, and the
deferral now has a trigger instead of a date.** The deferral was never "revisit in
mid-August" — it was "a week of use will say which questions the metrics leave
unanswered". Sixteen days later, the questions that actually went unanswered were about
**the hub's own five containers**, not about Claude Code: a Prometheus that had stopped
compacting (found by reading the service's logs by hand), a Collector that would not
start, a deploy that did not serve. What answered them each time was a **targeted metric
plus a Sentry event** — the compaction watchdog, the Collector's self-telemetry, the
health panels — which cost a query and a line, not a service. Loki as specced ingests
Claude Code's *log events*; in sixteen days those were never the missing answer.

One thing changed today that weighs directly on this: `metric_expiration` is short now,
so **Prometheus' disk is the only copy of the day**. That volume has already filled once,
at 500 MB, with metrics alone. A log store with the filesystem backend would add storage
pressure to the one component here with a disk failure on its record — which is the same
reason the evaluation above says the storage choice (R2) is *part of the design*.

**Reopen on a trigger, not on a date** — a date-based deferral is the kind that expires
and gets renewed out of inertia. Two triggers, either one is enough:

1. **A question that comes up twice and no metric or Sentry event can answer** —
   typically "which tool call failed" or "which permission decision was taken". Second
   occurrence, not the first: once is curiosity, twice is a gap.
2. **A paid Railway plan.** Marco's point, and it is a real one: the free-credit budget
   and the deploy queue are what make a sixth service expensive here, and both are
   properties of the plan rather than of Loki. On a paid plan the arithmetic changes, and
   Loki's data is genuinely rich — richer than what the three metrics can express.

The second trigger comes with a caveat that has to be written down now, while it is
cheap: **"better data on the public site" collides with a closed invariant.** The public
surface is exactly three aggregate numbers, and no session content — see the security
section of `CLAUDE.md` and `SECURITY.md`. Log-derived data on the site is therefore not a
free upgrade: it is a design question of the form *"which aggregate derived from logs can
be public without becoming content?"*. That question is answerable — counts and rates are
aggregates the same way sessions and tokens are — but it needs its own pass, and the
answer is not "publish what Loki has". Whatever it turns out to be, it goes through the
same allow-list discipline as the labels: default deny, added deliberately.

## Observability of this project itself

**Sentry yes, from the start** — zero-dependency envelope client in the status API,
same fail-open contract as the one already running on marcobellingeri.dev: no DSN is
a no-op, a failed delivery never changes the response.

**The Sentry release is the deploy's commit SHA, and it has worked since the day it
shipped — the measurement that said otherwise was reading the wrong place.** Sentry
takes any string and explicitly suggests a commit SHA, so `RAILWAY_GIT_COMMIT_SHA` is
what makes the release a fact about what is running rather than a claim someone
remembered to update. On 2026-07-30 this was written down as impossible: `railway
variables`, `railway run -- env` and the dashboard all listed the same seven
variables, none git-related. **All three read the variables configured *on the
service*, from outside; none of them is the environment of the deployed container.**
Railway's docs say the opposite of the conclusion — the variable is "injected
automatically on every deployment" — and production had been contradicting it in
Sentry for hours before the paragraph was written:

| Event | Release tag | What was deployed then |
| --- | --- | --- |
| 2026-07-29 13:37Z, sent from the laptop | *(none)* | not on Railway at all |
| 2026-07-30 01:52Z | `67b3b02` | PR #30 |
| 2026-07-30 02:24Z (×3) | `ce3f520` | PR #32 |

Two different SHAs, each the commit actually running at that moment, and the only
event with no release is the one that never touched the platform. Nothing in the code
changed to fix this — `sentry.py` always read the right variable; the documentation
was wrong, and this repo's own `v1.0.0` was held back on the strength of it. A
hand-bumped version stays rejected: a release updated by hand is eventually wrong.

The lesson outlives the bug. The old paragraph closed by noting that tests setting
the variable with `monkeypatch` prove the code works when the variable exists and
never that it exists — the same shape-not-reality gap as the `increase()` query. That
was correct, and it applied to the paragraph itself: a CLI that reads configuration
is no more the runtime than a fixture is. **The only measurement that settled it was
the artefact production emitted on its own.** When the question is "what does the
container actually have", ask something running inside it.

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
