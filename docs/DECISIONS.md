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

**`healthcheckPath` needs a `PORT` service variable, and the order is not optional.**
Railway sends the probe to the port named by `PORT`, not the port the process is
listening on — *"Not listening on the `PORT` variable or omitting it when using target
ports can result in your health check returning a `service unavailable` error"*.
Declared without it on 2026-08-13, `/healthz` failed every status-api deploy while the
previous container kept serving, and the four following commits were correctly
`SKIPPED` for watch paths, so production ran pre-#90 code with every gate green and no
signal at all. `PORT` was set on 2026-08-20 (`8000` on status-api, `2000` on
cloudflared) and the paths declared **after**: `/healthz` and `/ready`. Those two
numbers live on Railway, outside git, and must match each Dockerfile's `CMD` — nothing
in this repository can gate that. The default timeout, 300s, is left alone; cloudflared
opens its edge connection well inside it.

**A healthcheck here gates the deploy and then stops.** *"Railway does not monitor the
healthcheck endpoint after the deployment has gone live."* So `SUCCESS` on those two
services now means "it served a 200 once", not "it is serving" — a real improvement
over "the container started", and still not monitoring. The scrape of cloudflared's
metrics port and `smoke.yml` are what watch a running system; this is a gate on the
promotion, and reading it as more than that is how a green dashboard hides a dead
service.

**The other four services deliberately have no `healthcheckPath`, decided
2026-08-20.** Grafana, Prometheus and the Collector could all answer one — Grafana has
`/api/health`, Prometheus has `/-/healthy` and `/-/ready`, and the Collector has the
`health_check` extension — and since 2026-08-21 `loki` joins them, with `/ready` it does
not use. None of them gets one, and the reason is not inertia:

- **Railway wants a literal 200 with no credentials.** Grafana's and Prometheus'
  health routes would satisfy that by opening an *unauthenticated* route, and Grafana
  is the one service here with a public hostname (behind Access). The Collector's only
  route is the ingest, which authenticates and answers `401`, so giving it a
  healthcheck means the `health_check` extension **and a new listening port** — a new
  surface on the one service that accepts writes from the internet.
- **A Prometheus `up` scrape already watches all three, and watches more.** Railway
  stops probing once a deploy is promoted; the scrape runs every 15 seconds for as
  long as the service lives. A healthcheck would tell us less, later, in exchange for
  surface.
- **The gain is confined to the promotion.** On these three, `SUCCESS` would move from
  "the container started" to "it served a 200 once" — which is worth having on
  status-api, the one thing the public numbers pass through, and worth much less on
  three services that a dashboard panel is already watching live.

So this is closed, not pending. **If it is ever reopened, `PORT` goes first**: the
2026-08-13 failure cost six days of production running old code with every gate green,
and the order is written above precisely so it is not rediscovered.

**status-api's `watchPatterns` did not include its own `railway.json`**, alone among
the five services — it listed `services/public-status-api/**` and nothing else, while
every other service lists `railway/<service>/**`. A commit touching only its Railway
config would therefore be `SKIPPED` and the config change would never apply: the
2026-08-13 failure mode again, one directory over. Fixed on 2026-08-20 by adding
`railway/status-api/**`.

**No Kubernetes, no K3s, no Docker Swarm.** Five small services, one user. Docker's
own documentation: *"If you're not planning on deploying with Swarm, use Docker
Compose instead"* — everything Swarm adds (multi-host networking, cross-node service
discovery, cluster reconciliation) exists for more than one node. An orchestrator
here would also mean a machine to run it on, which is the thing we just removed.

**Railway Hobby, from 2026-08-19, because the free plan had no room left to give.**
Prometheus had been failing compaction once a minute since 2026-08-13 — `no space
left on device` — and the fix could not be a smaller retention: the volume was
already at **0.5 GB, which is exactly the free/trial maximum**, and Railway's plans page reserves
self-serve volume resizing for *"Pro users and above"*. Measured, not read: the
resize from 500 MB to 5 GB was performed on Hobby on 2026-08-19 and worked. Where the
doc and the platform disagree, this line records what the platform did — but do not
plan on it holding. There was no configuration change available, at
any size, that would have helped. Hobby is $5/month including $5 of usage credits;
measured consumption across the five services is ~0.96 GB RAM and ~0.015 vCPU, which
at $10/GB and $20/vCPU is **~$10/month**. Pro ($20) buys nothing this project needs: its
volume ceiling is far higher, but the volume in use is 0.24 GB. (Railway's own pages
disagree on that ceiling — 50 GB on the volumes reference, 1 TB on the plans page —
so no figure is quoted here; it changes nothing about the choice.)

The upgrade bought two ceilings, not one. The volume went 500 MB → 5 GB (and was
wiped by hand, so the history before that date is gone — today's public numbers
dropped from 5/33.8M/$35.87 to 3/9.8M/$8.68 in the same hour, and nothing in CI
noticed, because the smoke probe checks `null` and `stale`, never magnitude). The
per-service memory limit went **1 GB → 8 GB**, which matters more than it looks:
while compaction fails the head block is never flushed, so RAM grows monotonically —
at 329 MB against a 1 GB limit, the disk fault had an OOM behind it.

**The plan change is also the trigger this repo already wrote down for Loki.** The
deferral said it holds *"if the plan changes in October"*. It changed in August. The
condition fired early; the decision is due, and the date in that paragraph is now
wrong rather than merely pending.

**Retention is 30d / 3GB, and both numbers are hand-derived from the volume size.**
At the measured ~50 MB/day, 30 days is ~1.5 GB, so on a 5 GB volume **time** binds
first and size is the safety net — the exact inverse of the 7d/300MB pair it
replaces, which was tuned to 500 MB and would have truncated history to 6% of a disk
already paid for. The margin stays, but one half of the reason it used to give was
backwards and is corrected here: compaction does write the new block *before*
deleting the old ones and `retention_size` is enforced after the fact rather than
reserved — but the WAL is **not** outside the accounting. Prometheus: *"Only the
persistent blocks are deleted to honor this retention although WAL and m-mapped
chunks are counted in the total size."* They are excluded from deletion, not from
the count. (The margin is also ~36%, not 40%: `3GB` is powers-of-2, so 3.22 GB
decimal against a 5 GB volume.) The original 7-day window was a cost
decision, not a technical one, and the cost changed: storage bills on **used**, not
provisioned, at $0.15/GB/month, so ~1.5 GB is ~$0.23/month.

**Those two numbers live in two files, so now a CI step compares them.** Production
reads them from `railway/prometheus/Dockerfile`, local from
`docker/docker-compose.yml`, and the compose file claimed in a comment that they were
*"the same two limits as production"*. That was true when written and depended on
nothing to stay true — the same shape as the stale pricing test that survived two
days in #75. A comment is not a gate.

**Per-service resource caps: the first version of this paragraph said they do not
exist, and that was wrong.** It was written on 2026-08-19 from two absences — the CLI
exposes no CPU/memory flag, and the scaling page says only that a service scales *"up
to the specified vCPU and Memory limits of your plan"*. Railway's published JSON
schema carries `deploy.limitOverride.containers.{cpu, memoryBytes, diskBytes}`, along
with `healthcheckPath`, `healthcheckTimeout` and `numReplicas`. Of those, only
`limitOverride` and `healthcheckTimeout` are still undeclared here: `numReplicas` was
already set on status-api when this paragraph was written — the sentence was wrong the
day it was typed — and `healthcheckPath` landed on 2026-08-20. **A CLI that does not surface a field is not a platform that
lacks it** — precisely the mistake this file already records about
`RAILWAY_GIT_COMMIT_SHA`, where three tools read configuration and the conclusion was
drawn about the runtime.

What is *actually* known, and the distinction matters because overcorrecting would
repeat the error in the other direction: the field is **in the schema**, it is **not
in the config-as-code documentation** (checked), and **nobody has measured whether
the Hobby plan enforces it**. Two things were added to that on 2026-08-20, both read
from Railway's public GraphQL collection, which needs no token:
`serviceInstanceLimitOverride` and `serviceInstanceLimits` are **queries**, so the
experiment below can be *verified* rather than merely declared; and `status-api`
currently has **no override set** — its deploy config carries only
`numReplicas: 1`. So the experiment is still unrun, and now it has a way to check
its own result. So the honest statement is that the workspace usage
limit is the only backstop anyone has *verified*, not the only one that exists.
Declaring `limitOverride` on `status-api` — the one service reachable from the
internet — is a cheap experiment, and it must be measured before this paragraph is
rewritten again.

**Measured 2026-08-20, and the measurement moved the question rather than answering
it.** Railway's own metrics for `status-api` over 24 hours:

| | average | peak |
|---|---|---|
| CPU | 0.0017 vCPU | **0.0094 vCPU** |
| Memory | 0.065 GB | **0.087 GB** |

And Railway bills *"per minute for what the service actually consumes"*, not for
provisioned capacity — their own right-sizing guide, which also names the feature in
the dashboard: **service settings → Deploy → Replica Limits**. Put those two facts
together and **a per-service cap cannot lower this bill**. It is a runaway guard — it
binds only if real consumption climbs, which is what a bug or a flood would do — and
it is not a cost lever, because a cap above actual usage changes a
consumption-metered bill by exactly nothing. That reframes the open item: the useful
question was never *"does Hobby enforce `limitOverride`"* but *"would a cap change
anything here"*, and the answer to the second is no while the service sits four
orders of magnitude under the plan ceiling.

**Two things stay genuinely unknown, and neither is worth a guess.** First, the plan
limits Railway reports for this workspace carry `minReplicaCpuVCPU: 4` and
`minReplicaMemoryGB: 4` beside a per-replica maximum of 8/8 — but the *measured*
`CPU_LIMIT` for `status-api` ranged from **2 to 8 vCPU** over the same 24 hours, and
`MEMORY_LIMIT_GB` from **1 to 8**. A limit observed at 2 is below a reported minimum
of 4, so those fields do not mean "the floor you are allowed to set", or the metric
does not mean what its name suggests. **Unresolved, and named as unresolved rather
than resolved in whichever direction is convenient.** Second, nothing here has set an
override, so enforcement is still untested — and setting one now would test a control
that has already been shown unable to bind. The experiment got cheaper to *skip* than
to run, which is a legitimate way for an open item to close.

**The status-api → Prometheus hop is plain HTTP**, and this file had an entry for
every other transport decision except that one. It crosses only Railway's private
network, carries no credential (the queries are constant strings, Prometheus wants no
token), and both ends are ours. Correct for a single-tenant project; recorded because
an absent entry reads as an oversight rather than as a decision.

**Base images are pinned by digest as well as tag, since 2026-08-19.** The argument
was already written down in this file for the gitleaks binary — *"alone, a version
pin is a label, and a GitHub release asset can be replaced under the same label"* —
and had never been carried across to the five base images, where the same property
holds: a tag is a mutable pointer, and an upstream namespace compromise re-pushes
`grafana/grafana:13.1.4` with different bytes while the repository diff stays empty.
Dependabot maintains digest-pinned tags natively, so the cost is one line per file.

**And it is the only provenance control that means anything here — the absence of an
SBOM and of build attestation stays deliberate, for a reason the docs never stated.**
This repository never *publishes* an artefact: Railway builds the images from the
Dockerfiles on its own infrastructure and runs them, and the CI-built `agentic-*:ci`
images die with the runner. There is no published digest for anyone — the maintainer
included — to verify a signature or an attestation against, so signing would produce
a signature no verifier ever checks. That is the theatre the README refuses. Pinning
the inputs by digest is the same control applied where it can actually be checked.

**Both lockfiles are compiled on the interpreter production runs, and a gate watches
the header.** `pip-compile` resolves environment markers against the running Python,
so `requirements.txt`'s header saying 3.12 while the Dockerfile, the six workflow
pins and `sonar.python.version` all said 3.14 meant the lock described a resolution
nothing executes. Regenerated on 3.14 — no version changed, which is the honest
outcome: the risk was structural, not yet realised. `scripts/check-python-versions.sh`
now watches the two lockfile headers as the seventh and eighth places, because the
version being written in many places is precisely why it drifts.

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

**Prometheus never gets a hostname of its own**, on any ingress. Not because it cannot
authenticate: it supports TLS and basic auth through `--web.config.file`, which is on the
vendor's own security page. It is simply not configured to here, and no route means
nothing to authenticate. The reason written in this file until 2026-08-21 was the false
one, "its HTTP API has no authentication", already corrected once in `SECURITY.md` and
left standing here.

**No service takes a public Railway domain.** The Cloudflare Tunnel is the only way
in, so the platform's own hostnames stay unused rather than sitting unprotected
beside the front door.

**Every image runs as a non-root user, with one documented exception.** All four base
images already did, invisibly to a scanner reading a `FROM` — including `cloudflared`,
which this file and its Dockerfile both claimed ran as root until 2026-08-20.
Measured with `docker image inspect` on the pinned digest and on the previous
`2026.7.3`: both declare `USER 65532:65532`. The `USER` line in each Dockerfile is
therefore not a fix but a **declaration** — it pins the identity here so a base-image
bump cannot change it silently, and it is what the gate below can actually read.

The exception is **Prometheus on Railway**, which carries `RAILWAY_RUN_UID=0`. Its
attached volume is presented owned by root, so as `nobody` it cannot even create
`/prometheus/queries.active` and exits at startup — measured, not anticipated.
Railway documents running as root as the supported answer. The exposure is bounded
by the fact that Prometheus has no public domain and no configured authentication of
its own; the weakening is applied as a Railway variable rather than a `USER 0` line
so that the local run keeps the stronger posture. If this ever needs revisiting, the
alternative is an entrypoint that chowns the mount and drops privileges — more moving
parts inside the one service that has none today.

A CI gate keeps that exception single: `scripts/check-image-users.sh` fails the build
if any Dockerfile here lacks a `USER` or declares root. A written exception stays an
exception; a copied one becomes the norm, and the copying is what the gate stops.

**cloudflared's metrics port serves `net/http/pprof`, and that is a declared cost,
not an absence.** `--metrics 0.0.0.0:2000` is the only listener the connector has, and
its mux does more than `/metrics` and `/ready`: it mounts the whole
`http.DefaultServeMux` under `/debug/`, where `net/http/pprof` registered itself.
Measured on the binary extracted from the pinned digest — `docker cp`, then
disassembling `metrics.newMetricsHandler` and reading the route strings out of
`.rodata`, then confirmed against the vendor's source. The routes are `/metrics`,
`/ready`, `/healthcheck`, `/quicktunnel`, `/config`,
`/diag/{configuration,tunnel,system}` and `/debug/*`. Exactly one is blocked:
`/debug/pprof/cmdline` answers `403 forbidden`, and the vendor's own comment says why
— *"prevent leaking secret command-line arguments (e.g. tunnel tokens) that are
exposed via os.Args"*.

So Cloudflare shut the argv door and left the heap door open. `/debug/pprof/heap` is
unauthenticated on the project's private network: anything that gets execution in one
of the other four containers can reach `cloudflared.railway.internal:2000` with no
credentials and pull the memory of the process holding the tunnel token — which here
arrives through `TUNNEL_TOKEN`, i.e. through the door that stayed open. What that one
token is worth is written further down: **one token yields three of the other five.**

It stays as it is, and the reasons are the two that would change the answer if either
stopped being true. **There is no flag that turns `/debug/` off** — the only
parameters in the area are `--metrics`, `--metrics-update-freq` and
`--management-diagnostics`, none of which touches the mux (read on the vendor's
run-parameters page and extracted from the binary's own flag table). And **there is
only one listener**: dropping it loses the Prometheus scrape *and*
`healthcheckPath: /ready` together, putting the system's only ingress back to saying
nothing about itself. The containment is that this port has no Tunnel hostname and is
not published — reaching it means already being inside the private network. If
cloudflared ever ships a way to serve metrics without `/debug/`, this reopens.

## Metrics semantics

**Metric names are pinned, not inherited.** `translation_strategy:
UnderscoreEscapingWithoutSuffixes`, because the default appends type *and unit*
suffixes — `claude_code.token.usage` with unit `tokens` would become
`claude_code_token_usage_tokens_total`, a name that depends on someone else's unit
metadata. Measured against the real client, the Collector receives
`claude_code_session_count`, `claude_code_token_usage`, `claude_code_cost_usage` and
`claude_code_active_time_total`. **Prometheus sees the first three only**: since
2026-08-19 `filter/metric-allowlist` drops every name that is not one of those three, so
`active_time` and `lines_of_code` never reach `/metrics`. This paragraph listed four
names as if all four arrived, which sent anyone looking for `active_time` in the TSDB to
the wrong file. `scripts/prova-contratto-metriche.sh` pushes an invented name and
requires it to disappear.

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

> **Superseded on 2026-08-19** — the volume is 5 GB and the pair is now 30d / 3GB;
> see "Retention is 30d / 3GB" under Platform. The arithmetic below is kept because
> it is the reasoning that produced the 40% margin, but every number in it, and the
> free-credits premise it closes on, describes the 500 MB volume that is gone. The
> WAL clause is also wrong — see the correction above.

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

**Since 2026-08-20 it is at least no longer silent.** The sentence above was accurate
and it described a gap nobody watched: `_check_persistence` reads
`prometheus_tsdb_compactions_failed_total`, so a TSDB that is *empty but perfectly
healthy* returns the series, reports zero failures, and says nothing. A lost volume was
green everywhere — the exact shape of failure this repository exists to avoid.
`_check_zero_volume` now runs on the same pass, at no extra query: sixty consecutive
passes of three zeros — roughly an hour of continuous polling — raise a Sentry event.
Sixty, not three, because the three numbers look back 25h and hours of genuine zeros sit
between two sessions; an alert that fires every quiet morning is an alert someone
silences. **The event declares what it cannot tell apart**: "nobody worked" and "the
volume is gone" produce the identical signal from these three numbers, so it asks the
reader to look at the volume rather than announcing a fault.

**Measured 2026-08-31: sixty passes was not enough, and the reason is structural.** The
production alarm fired eight times overnight, hourly, all harmless — the last real
activity had been ~33h earlier. Sixty passes were chosen so that "a quiet morning" would
not trigger, but passes are bought by traffic and the public widget polls every 20s
against a 60s cache, so sixty passes is sixty *minutes* whenever the site has visitors.
The three queries look back **25h**, so **every gap in work longer than 25h reaches zero
legitimately** — a weekend guarantees it. No value of N fixes that: N counts passes, and
the thing that overflows is the 25h window.

**So the second source went in, and it is not `up`.** This paragraph used to say the
ambiguity needed `up` on the Collector; that was wrong and is worth saying why, because
`up` looks like the obvious witness. `up` resumes being written a scrape after a volume
is wiped, so its presence separates nothing: it reads healthy on a lost volume and on a
quiet weekend alike. What separates them is whether *the Claude Code series itself* has
history behind the window —
`count(max_over_time(claude_code_session_count{job="otel-collector"}[7d]))`, asked only
on the pass that is about to alert, once per sixty zero passes. Series present ⇒ the
volume is full and the absence is the operator's ⇒ silence. Empty vector ⇒ alert.

**Seven days, not thirty.** Thirty would match `--storage.tsdb.retention.time`, but
`retention.size` drops old blocks when the disk fills, *before* the days expire — that is
the 2026-08-13 failure in this same file. A witness resting on the oldest part of the
history loses its memory exactly in the weeks when the volume is under pressure, which is
when it is needed. Seven days sits well clear of that edge, and an absence longer than a
week deserves an event anyway. The witness window is deliberately **different** from the
25h of `QUERIES`: align the two and the witness answers identically to the three numbers
and stops witnessing, with nothing turning red.

**What is still ambiguous, declared rather than papered over.** An empty 7d window has
three causes and the event names all three: a lost volume, a fresh deploy with no history
yet, or an absence longer than seven days. Only the first is a fault. Narrowing further
would need the *age of the oldest sample in the TSDB* (a wipe resets it; an absence does
not), which is a second increment and is not built. And when the witness query itself
fails, the event says so and stays as ambiguous as it was before the probe existed —
never silent: a probe that bought silence would make the watchdog mute precisely while
Prometheus is half broken, which is adding a quiet failure while curing a loud one.

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
thresholds anchored to `--storage.tsdb.retention.size`, 80% yellow and 95% red)
make the 13/08 failure visible in the one place someone actually looks. Both names
verified against the v3.13.2 source rather than recalled.

**Anchored, and since 2026-08-19 anchored by a gate rather than by intention.** The
cap moved to `3GB` and those thresholds stayed at 240/285 MiB in the same commit —
which would have painted the panel permanently red from about day six on a perfectly
healthy hub, and a panel that is always red is a panel nobody reads. The cap lives in
three places (the production Dockerfile, the compose file, this panel), so the CI step
that compares the first two now also recomputes the panel's thresholds from the cap
and checks the title carries the same number. Note what the gauge does *not* say: it
counts blocks only, while `retention.size` also counts the WAL and m-mapped chunks,
so the panel reads lower than the number the cap is enforced against.

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

**And it is still broken, by `sonar`, and this repository asserted otherwise until
2026-08-19.** The job carries `if: … head.repo.full_name == github.repository &&
user.login != 'dependabot[bot]'`, so on fork and Dependabot PRs the required check
reports *skipped* — which, as the paragraph below already measured, reads as green.
The local reason is sound: a token-less Sonar scan cannot pass, and unlike gitleaks
there is no token-free mode to fall back to, so the alternative is a permanently red
check blocking every merge. What was wrong was the *global* claim, repeated in
`README.md`, that this cannot happen. Found by an audit, not by a failure — which is
the only reason it is written here rather than in an incident. The residual loss is
bounded and worth stating: on those PRs `tests` still enforces `--cov-fail-under=100`
and lint, bandit, checkov and gitleaks all still run; what is missing is Sonar's own
rule set. **Nine Dependabot PRs were merged on 2026-08-19 reading `sonar skipping`
as normal.** It is normal. The documentation said it was impossible. The count was
first written here as twelve, from memory, in the paragraph whose whole subject is a
claim made without measuring — `git log` says nine (#76–#78, #80–#85).

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

**The blocking Trivy scan was answered by deleting pip, not by tuning the gate.**
Dependabot's base-image bump (#79, `python:3.14.0-slim` → `3.14.7-slim`) stayed red on
two HIGHs, `msgpack` and `pkg_resources`/setuptools, that neither of the gate's two
assumed levers can reach: they are not in `requirements.in`, so recompiling the
lockfile does not move them, and they are not Debian packages, so a newer base tag does
not either. Both live inside `pip/_vendor/`, and pip is not used at runtime — the image
runs uvicorn and installs nothing. `pip uninstall -y pip` in the install layer measured
0 HIGH on the blocking library scan and took the informative OS scan from 25 HIGH
(3.14.0, 13/08) to 9 (3.14.7, 20/08). An ignore file would have produced the same green
with the code still in the image.

**Uninstalling pip is not the same as removing it**, and the difference was found by
looking at the image rather than at the gate. `ensurepip/_bundled/` keeps a second copy
of pip as a wheel; Trivy does not read inside an archive, so the scan was already green
while the vulnerable code was still shipped, one `python -m ensurepip` away from being
installed again. The `rm -rf` of `ensurepip` is therefore part of the fix, not tidiness
— and its path is asked of the interpreter, because hard-coding `python3.14` would make
the removal disappear silently at the next minor bump with nothing turning red.

**`smoke.yml`'s failure message is chosen by the code that came back, not by the code it
expected.** The helper asserting the ingest takes a list of acceptable codes, and until
2026-08-20 it picked its explanation from that list: an expected `403` printed "the edge
stopped blocking", an expected `401` printed "anyone can write to the TSDB". Opening
`/v1/logs` is what made that wrong. With two paths allowed through the WAF and two
different components answering, `401` and `403` are both healthy codes that differ only
in *who* replied — so a reverted WAF rule would have printed "anyone can write to the
TSDB" in front of a route that was still closed, which is the worst kind of false alarm:
the one that sends somebody looking in the wrong place at 3am. The message now branches
on the **received** code. A swap between `401` and `403` is always "the reachable surface
moved"; only a `2xx` means neither control answered. Routing on the *path* had been wrong
earlier the same day for the same reason — the explanation belongs to the outcome, not to
the request.
**`--web.cors.origin` passa da `.*` a `^$`, ed è l'unica famiglia di `--web.*` che il
gate lascia entrare.** The default is not a vendor oversight: Prometheus expects to sit
behind a proxy that decides origins for it. Nothing sits in front of this one. Measured
2026-08-20 against the shipped image: `Origin: https://evil.example` comes back as
`Access-Control-Allow-Origin: https://evil.example`, so any page open in a browser that
can reach the port reads the whole TSDB in JavaScript — thirty days of a behavioural
profile, which `SECURITY.md` names as the sensitive content of that volume. On Railway's
private network the radius is small; locally, where the real data lives, the port is on
the host.

`^$` matches no origin: server-to-server queries keep working (Grafana and the status
API send no `Origin` — verified, `200` either way), a third-party browser stops. The
three write surfaces were re-verified the same day and were already off: `admin-api`,
`lifecycle`, `remote-write-receiver` all `false`.

The gate `prometheus non accende endpoint che non sa autenticare` rejected this flag,
which is exactly what it is for. Rather than weakening it, it now carries a short
allow-list of flags that **narrow** surface instead of adding it, and every future entry
has to answer the same question here. A gate that blocks the fix for an open default is
a gate that will be bypassed during an incident.

**Dependabot does not get `multi-ecosystem-groups`, decided 2026-08-22.** The problem is
real: a docker bump arrives as two pull requests, one for `docker/docker-compose.yml` and
one for `railway/<svc>/Dockerfile`, and since the divergence gate landed each half is red
on its own. `multi-ecosystem-groups` is the one feature that would merge them, the
SchemaStore schema accepts it, and GitHub validates `dependabot.yml` on every pull
request, so a *syntactically* wrong config would be caught.

That is the half that can be verified, and the other half is the one that matters. The
vendor's page documents `patterns` as required per ecosystem and says nothing about how
the key interacts with `ignore` (this repo pins `grafana/grafana` away from 13.2.0 in two
places) or with `directory`. A config that is valid and subtly wrong does not fail: it
opens fewer pull requests, or none, and the way anybody finds out is an image quietly
going months stale. There is no way to force a Dependabot run and watch, so the change
cannot be verified in the direction where it can hurt.

So the two-PR shape stays, and the procedure is written where it is needed rather than
remembered: `.github/dependabot.yml` says to take one of the two, bring the other half
into the same branch by hand, and close the other. Done four times on 2026-08-22 (#153,
#154), it costs about two minutes. Reopen this if the vendor documents the interaction,
or if a way to trigger a run on demand appears.

## Logs

**No logs pipeline today** — true until 2026-08-20, and kept because the reason
outlived the state. The Prometheus exporter supports the metrics signal only, so a `logs`
pipeline pointing at it fails Collector startup, which is why `OTEL_LOGS_EXPORTER` was
deliberately left unset rather than exporting into nothing. Phase 1.5 added the pipeline
(see the end of this section): it exports to Loki and never to `prometheus`, and a gate
now enforces that half, because until then it was a sentence in `CLAUDE.md` about a
pipeline that did not exist.

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

Marco's expected horizon for the second trigger, stated 2026-08-14: **winter, after
September.** That is recorded as an *expectation*, deliberately not as a third trigger —
the moment a date becomes the criterion again, this entry has turned back into the
deferral it replaced. Practically: if winter arrives and the plan has not changed,
nothing is due and nothing needs re-deciding; if the plan changes in October, the trigger
has fired and the date is irrelevant.

**Update 2026-08-16: the second trigger fires on 2026-09-01.** The hub stays and the
Railway plan changes on that date, so Loki is due for its design pass rather than
deferred. Worth being precise about why this is not the date-based deferral sneaking back
in: the criterion never became a date, the *condition* acquired one. If the plan change
slips, Loki slips with it and nobody has to re-decide anything. Winter, the expectation
recorded two days earlier, turned out to be four months early — which is the ordinary
fate of expectations and the reason they are not criteria.

**And the number that made "the hub stays" easy, now that there is a bill to read:
€2.05 over 12 days**, about €0.17/day, **~€5/month** for five services. This project had
estimated $10-13/month, written before anyone had measured anything; the real figure is
roughly half. Same shape as the cost of a session and the Prometheus volume: the estimate
survived until someone divided one number by another.

The second trigger comes with a caveat that has to be written down now, while it is
cheap: **"better data on the public site" collides with a closed invariant.** The public
surface is exactly three aggregate numbers, and no session content — see the security
section of `CLAUDE.md` and `SECURITY.md`. Log-derived data on the site is therefore not a
free upgrade: it is a design question of the form *"which aggregate derived from logs can
be public without becoming content?"*. That question is answerable — counts and rates are
aggregates the same way sessions and tokens are — but it needs its own pass, and the
answer is not "publish what Loki has". Whatever it turns out to be, it goes through the
same allow-list discipline as the labels: default deny, added deliberately.

**Loki's gRPC listener cannot be closed on 3.7.6, and it was tried twice.** The `:9095`
listener carries Pusher, Ingester and Querier with no credential, so the private network
has a second path beside `:3100`. Binding it to loopback makes every query fail, because
the query-frontend advertises its container address to the scheduler; forcing the
frontend onto `lo` stops the service from starting at all, because dskit filters loopback
out of that lookup. Both measured 2026-08-22, both worse than the gap. `SECURITY.md` has
the error strings and the row in the route table. What bounds it is what bounds the
native HTTP push: the ingestion ceilings and the fact that being on the private network
is the precondition.

**Phase 1.5 was implemented on 2026-08-20 and went live on 2026-08-21.** This heading
said "on a branch, not in production" for a day after the six services were running.
The shape is the one this section specced: Claude Code → the existing OTLP ingest → a
new and *separate* `logs` pipeline in the Collector → Loki 3.7.6 single binary → chunks
and index on Cloudflare R2, with Grafana querying it over the private network. The hub
goes from five services to six. The storage choice the 2026-07-29 evaluation called
"part of the design, not a detail" is settled the way it was specced, and for the reason
it was specced: R2 holds the chunks, so a full Railway volume cannot take them with it,
and the local disk keeps only the active index and the `tsdb_shipper` cache. The
filesystem backend was disqualified because it puts storage pressure on the one component
here with a disk failure on its record; R2 is what removes that, not a preference.

**Loki gets no Tunnel hostname, and the reason is that it has no route** — not that it
cannot authenticate. Same correction already made above for Prometheus, written down here
before somebody re-derives the false version: `auth_enabled: false` in `docker/loki.yaml`
is about **multi-tenancy** (the `X-Scope-OrgID` header), not about access. A config key
whose name reads like "authentication off" is exactly the kind of thing a later reader
turns into a security claim in either direction.

**What measuring overturned, and it was the assumption the plan was built on.** The
evaluation above says "the same identity attributes ride on log records", and the plan
inherited the metrics-shaped worry with it: keep identity out of the *resource*. Measured
on 2026-08-20 against client 2.1.235 with `OTEL_LOGS_EXPORTER=otlp`, the resource carries
`host.arch`, `os.type`, `os.version`, `service.name`, `service.version` — and **no
identity at all**. Identity rides on the **log records**, on every single event:
`organization.id`, `user.email`, `user.id`, `user.account_id`, `user.account_uuid`. The
vendor's documentation marks them *always included*, and no environment variable turns
them off — so the Collector's allow-list is **the only control standing on identity, not
a second line of defence**. Three more things the same run settled:

- **`prompt` and `response` do arrive `<REDACTED>` by default**, which is what the
  2026-07-29 evaluation leaned on when it called these events "metadata, not content" —
  and a default is not a control. `OTEL_LOG_USER_PROMPTS`,
  `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_DETAILS` and `OTEL_LOG_RAW_API_BODIES`
  each switch one field back on, one field each.
- **The tool events exist only if the session actually used a tool.** `tool_result` and
  `tool_decision` never show up otherwise — so an allow-list written from a tool-less
  session would have silently dropped precisely what Phase 1.5 exists to answer.
- **22 keys are kept, out of the 64 measured.**

**Two allow-lists, and the third statement nobody had planned.** Barrier one is
`transform/log-allowlist` in the Collector: three statements — `resource`, `scope`,
`log` — all `keep_keys`. Barrier two is `otlp_config` in `docker/loki.yaml`:
`ignore_defaults: true` plus a `drop` catch-all at the **bottom** of all three sections.
The plan had only two statements, `resource` and `log`. With those, the **scope**
attributes crossed the Collector untouched: removing Loki's allow-list alone left
`scope.secret` queryable while identity and record content stayed out. Two barriers that
declared themselves independent were not independent on that third set — the same shape
of error the spec had already corrected once (PR #119), found this time by *running* the
thing rather than by reading it, because no gate on the config's shape could see it.
`keep_keys(scope.attributes, [])` closed it, and each barrier now holds on its own,
verified by breaking one at a time.

**`use_thanos_objstore: true` is load-bearing and reads like decoration.** The `latest`
documentation declares it `true` by default; the 3.7.6 version reference says
`default = false`. Without the line Loki would fall back to the legacy clients and ignore
the entire `object_store` block — correct credentials, wrong storage, no error. This is
the docs-before-code rule paying out on a single word: "latest" is not the version that
runs.

**The order of Loki's allow-list fails in two different ways, and only one of them is
loud.** Catch-all at the top of `resource_attributes`: the stream ends up with no label
and Loki answers `400`. Catch-all at the top of `log_attributes`: the push returns `204`,
`-verify-config` says `config is valid`, and every piece of useful structured metadata
disappears in silence — the answer to "which tool call failed", gone, with everything
green. Two checks see the second case, and they see it differently: the allow-list gate
in `.github/workflows/images.yml` requires the catch-all last in all three sections —
it reads the shape — and `scripts/prova-privacy-log.sh` fails assertion (c), because the
useful structured metadata is gone from a real query. That pair is deliberate. An
allow-list broken in the "drop everything" direction passes (a) and (b) and reads as a
success, which is exactly why (c) exists and why a shape gate alone is not enough.

**There is no `loki -verify-config` gate in CI, and that is a supersession rather than
an omission.** The spec listed one. What shipped instead is `scripts/prova-privacy-log.sh`,
which *starts* Loki against a real object store and queries it back — strictly stronger,
because `-verify-config` was measured accepting an endpoint written `https://` that kills
Loki at startup, and accepting `delete_request_store: bananas`. A config checker that
validates key names and not values cannot be the last word on a config whose worst
failure is a value. Written down because a missing gate and a superseded one read the
same in a workflow file, and only one of them is a hole.

**Opening `/v1/logs` at the edge changes the reachable surface, so it is asserted where
the rest of the ingest is.** The WAF custom rule on `otel.` used to pass only
`POST /v1/metrics` carrying an `Authorization` header; version 3 of the rule, applied
2026-08-20 through the Rulesets API, passes `POST /v1/logs` on the same condition —
presence of the header, never its value. Measured against production straight after: no
header gets `403` from the edge on **both** paths, a wrong bearer gets `401` from the
Collector on **both**, and `/v1/traces` still gets `403`. Propagation is not instant —
immediately after the PATCH `/v1/logs` was still answering `403`, and settled to `401`
within a couple of minutes. Worth writing down, because a probe's first red run after a
rule change is otherwise read as a broken rule. `smoke.yml` asserts the new route with a
*wrong* bearer, the only shape the edge lets through.

**The cost of the sixth service is not written here, because it has not been measured.**
The €2.05 over 12 days above is five services on the plan that was running then; a Loki
figure produced today would be an estimate, and this section has already watched one
estimate ($10-13/month) get halved by the first real bill. It gets written after a week
of real running — which cannot start until the R2 bucket, its S3 credentials and the
Railway service exist, and those are Marco's.

**`loki` is the one service on `ALWAYS` instead of `ON_FAILURE`, and it is not a
preference.** Decided 2026-08-21, out of the audit of its HTTP surface (`SECURITY.md`
has the full table). Three facts that live in three different files and had never been
read together:

1. `loki` has no `healthcheckPath` — a closed decision, its witness is the Prometheus
   scrape.
2. It serves `GET /ingester/shutdown` on the private network with no credential, and
   that route stops the process.
3. **It exits with code `0`** doing so. Measured with `docker wait` on a real
   container, because the alternative was to keep assuming.

Railway's docs say `On Failure` restarts a service "only if it stops due to an error
(e.g. crashes, exits with a non-zero code)". Put together: one unauthenticated GET
stopped the log store and **nothing** brought it back — no restart, no healthcheck, no
alert rule on `up{job="loki"}`. `ALWAYS` restarts on any stop, which does not close the
route (the route is not closable) but turns a permanent silent outage into a blip.

Two things were checked before writing the value rather than after: `ALWAYS` is in the
**published** schema (`railway.schema.json`, `enum: [ON_FAILURE, ALWAYS, NEVER]`) — a
CLI menu is not the platform — and the vendor's plan limits put `ALWAYS` behind a paid
plan, which this project is on.

**Two limits of that gate, written here because a gate whose reach is overestimated is
worse than none.** It reads the repository, not the platform: the value that actually
runs lives in the Railway workspace, nothing re-reads it, and no row was added to the
"controls that live outside git" table in `SECURITY.md` for it — that is a declared gap,
not an oversight. And the vendor's own config-as-code page carries a deprecation banner:
existing files keep working for legacy services **until 2026-12-01**. After that date the
gate can stay green while the file stops being applied and the policy falls back to the
dashboard default, which is `ON_FAILURE` — the exact value it exists to prevent.

The reason cannot live next to the value, because `railway.json` is JSON and takes no
comments — so a future "let's align every service to ON_FAILURE" would read as tidying.
That is why there is a gate: `images.yml` fails if the policy moves off `ALWAYS`, and
also if `loki` ever gains a `healthcheckPath`, because that is the premise the decision
rests on. Verified red on both breakages and green on the real file, by running the
script extracted from the workflow.

**Alerting lives in Grafana, not in Alertmanager, and until 2026-08-21 it did not
live anywhere.** There was no `rule_files` in Prometheus and no Alertmanager: every
series this project collects was recorded and never evaluated, which makes it a
recording rather than a witness. The only way to notice a fault was for somebody to go
and read the logs — which worked on the night of 2026-08-20 because two of us were
awake, and does not work three weeks later.

Grafana rather than Alertmanager, for two measured reasons and not out of laziness:
Alertmanager would be a **seventh service** on a usage-billed plan, and **a Prometheus
rule cannot query Loki** — half of what is worth watching here is made of logs. Grafana
provisions rules, contact points and notification policies from YAML files, the same
mechanism already used for datasources and dashboards, so there is still exactly one
copy of every configuration and it is in `docker/`.

**Every metric in those rules was read off the running binary**, not recalled. That is
how `loki_ingester_wal_disk_usage_percent` turned up — the vendor documents it as
*"Current disk usage percentage (0.0 to 1.0) for the WAL directory"*, and the WAL
directory sits under `path_prefix: /loki`, i.e. on the 5 GB volume. The plan had this
signal written down as unavailable. A rule pointed at a metric that does not exist is
not noisy, it is **mute**, and mute is indistinguishable from "all is well".

**One rule was deliberately not written the way the plan asked.** The plan wanted "the
Collector stopped receiving" — a flat `otelcol_receiver_accepted_*`. On this project
that series is flat every night, because the producer is a person who sleeps: it would
be an alarm that rings when nothing is happening, and an alarm like that gets silenced
within a week. "Flat" stays the right question for *diagnosis* and is already a panel.
The rule watches `otelcol_receiver_refused_*` instead, which is always a fault: the data
arrived and the Collector did not take it.

**Four things in that first draft were wrong, and all four were found by running it
rather than reading it** — worth recording because three of them are invisible to any
review of the YAML. (1) With `SLACK_WEBHOOK_URL` unset, Grafana **did not start at
all**: an empty `url` makes it treat the receiver as the Slack chat API, validation
fails, and the provisioning module brings the server down with it — the opposite of the
degradation this file claimed. (2) `sum(A) + sum(B)` returns an **empty** vector when
either family is absent, and `otelcol_receiver_refused_log_records` does not exist until
the first log is exported: with `noDataState: OK` that rule was **mute**, which looks
exactly like "all is well". (3) `slack.default.title` / `slack.default.text` are
*Alertmanager* template names; inside Grafana an unknown name renders the empty string
in silence, so every notification arrived with an empty body — none of the descriptions
written into the rules reached anybody. (4) The threshold on Prometheus' TSDB was
calibrated to fire **in the normal regime**: `retention.size` is a ceiling the system is
designed to hit, so the blocks settle just under it and stay permanently above 80% with
nothing wrong; measured growth of ~88 MB/day put the first false alarm around day 26.
The rule now counts `prometheus_tsdb_size_retentions_total`, which increments exactly
when the size cap starts biting and never before.

`scripts/prova-allarmi.sh` runs the shipped Grafana and Prometheus images, makes
`up{job="loki"} == 0` true by leaving Loki off the network, and requires four things a
YAML review cannot establish: the rules are all loaded (Grafana logs a provisioning
error and carries on, so a bad rule simply does not exist), every rule names a
datasource that exists, the right rule fires while another stays inactive, and **the
notification is delivered**. That last one replaced an assertion that could never pass:
Grafana returns a contact point's URL as `[REDACTED]`, because it is a credential, so
comparing the string was impossible — the proof points `$SLACK_WEBHOOK_URL` at a local
receiver and waits for the POST instead. Measured: fires after 18s, notification lands
39s later, and the receiver sees the rule's name.
## Observability of this project itself

**The alerting chain is closed end to end, confirmed 2026-08-22.** Five rules in
`docker/grafana/provisioning/alerting/regole.yaml`, a Slack contact point and a
notification policy, all provisioned from files. `scripts/prova-allarmi.sh` makes
`up{job="loki"} == 0` fire by removing Loki from the network and requires the
notification to be **delivered**, with a second rule staying `inactive` as the negative
control. What that proof cannot reach is Slack itself: it delivers to a local receiver,
so the last inch was confirmed by the operator reading Slack. Written down because the
division of labour is the point — the repository proves everything up to the send, and a
person proves the one hop that lives on somebody else's server.

Two things had to be fixed before that sentence was true. With `SLACK_WEBHOOK_URL`
unset, an empty `url` makes Grafana fall back to the Slack chat API, the contact point
fails validation, and the provisioning module takes the **whole server** down: adding
alerting would have taken Grafana off the air. And without `[server] root_url`, every
link inside the notification pointed at `http://localhost:3000` — the proof could not
see it, because a local receiver does not open links.

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

**An alert that fires once per process is an alert that lies.** The TSDB watchdog
added in #52 did exactly what it was designed to do on 2026-08-13 and then went quiet
for six days while the fault it watches ran continuously. The dedup was a
module-level `set`: one notification per process lifetime. On Sentry the issue read
*"last seen 5 days ago"* — indistinguishable from a fault that had been **fixed**,
and it was read that way, out loud, before the logs contradicted it. The alarm
re-armed on 2026-08-19 only because an unrelated dependency bump redeployed the
service and emptied the set.

It is now a timestamp per key with a one-hour expiry, so a condition that persists
keeps saying so. One hour also happens to be the smallest unit that makes a Sentry
rule on **frequency** (rather than on issue creation) have anything to count —
without repetition, no such rule can fire, which is the other half of why nothing
called. Neither half works alone.

The watchdog also stopped running on every request. It shares the read path of the
one public endpoint, and at the time that endpoint was unthrottled: before 2026-08-19
the cost of that was CPU on free credits, after it is money. Four Prometheus queries
per public request instead of three is 33% of the load bought by an unauthenticated
caller — and removing it is a 25% cut, not the closing of the vector: the other three
queries still run once per request, which is what the Worker's limiter is for. It now
runs at most once per minute — still far denser than a condition measured in days.

**Denial-of-wallet: measured, small, and capped anyway.** Moving to usage-based
billing turned "someone floods the public endpoint" from an availability question
into a financial one, so it was worth arithmetic rather than posture. At the
repository's own rates ($20/vCPU-month, $10/GB-month) a sustained flood from one
laptop costs on the order of **$0.12–0.35/hour** — a few dollars a day, not
hundreds. Two things bound it that are not the plan ceiling: `uvicorn` runs with no
`--workers`, so the status API is one process and one GIL and can never reach the
8 vCPU the plan allows; and the per-request client churn degrades into connection
errors long before it reaches interesting money.

What the rate hid was more interesting than the rate. **The error path cost 10–100×
the success path**: every 502 opened a fresh TLS connection to sentry.io with no
limiter, while every other capture in the service had one. With Prometheus down and
the widget polling every 20s that is ~4,300 events/day with no attacker at all —
the free quota gone in a day, exactly when it is needed. Fixed by routing the 502
capture through the same throttle, keyed on the exception type so a new fault still
speaks immediately.

**The Worker's limiter exists, and this file kept calling it future work.** It
shipped on 2026-08-16 in `marcobellingeri.dev` PR #216: a per-IP rate limiting
binding on `/api/agentic-status`, 60 requests / 60s, answering `429` with
`Retry-After: 60`, `Cache-Control: no-store` and `{"error":"rate"}`. It sits on the
HTTP boundary rather than inside the handler, deliberately — the Cron probe calls the
same handler as a function, with no IP, and inside the handler it would land in the
`sconosciuto` bucket with every other IP-less caller: a flood would bounce the probe
with a `429`, the probe would read a response that is not the three numbers, and it
would report a dead hub to Sentry that is perfectly healthy. A false alarm
manufactured by our own defence.

**Measured against production on 2026-08-20, because the shape matters more than the
number.** 75 requests in ~11 seconds: **zero** `429`. 200 requests in ~26 seconds:
**13** `429`, the first at request 167. The binding counts per datacentre and is
eventually consistent by design, so it is a ceiling against a sustained flood and not
a guillotine at the 61st request. Anyone measuring it with a short burst will conclude
it is broken; that is why the burst is written down next to the result. The
`/api/contact` and `/api/ask` limiters are the same mechanism at 5 and 10 per minute.

**`/status` caches for 60 seconds, and that one change closes more than the flood.**
The origin now runs three Prometheus queries per minute whatever the incoming rate;
the sampling resolution of the presence side-channel drops to a minute; the
per-request connection churn stops mattering; and the Worker's rate limiter becomes
a second layer instead of the only one — which matters because that Worker lives in
another repository. Only successes are cached, and past the window a broken upstream
is a 502, never a stale number wearing a fresh face: a degradation that hides itself
would turn the smoke probe green against a dead hub, and that lesson is already paid
for. The watchdog lost its own interval in the same change — two clocks for one
cadence drift apart, and these two did: the cache timestamp is taken before the
queries and the probe's after, so the probe would have run once every *two* windows.

**The usage limit is set: $15 soft, $30 hard, on the workspace, since 2026-08-20.**
The paragraph this replaces said the recommended values were $15/$30 and that the
backstop had to be treated as **unverified** until a configured value and a date were
written here. They now are. The values are deliberately far from the ~$10/month
measured spend, because the hard limit takes every workload offline and a limit set
near normal spend is a self-inflicted outage.

One honest limit remains, and it is not cosmetic: **this is confirmed by the operator,
not measured from here.** Railway's MCP surface exposes projects, services, variables,
deployments, metrics and feature flags — nothing about billing or usage limits — so no
tool in this repository can read the configured value back, and no gate can notice if
it is removed. That is the same class as `PORT`: a control that lives outside git,
whose disappearance is silent.

**Checked against the platform rather than against one tool's menu, 2026-08-20.**
Railway publishes its GraphQL collection at a URL that needs no token, and reading it
splits that paragraph in two. The *limit* is confirmed unreadable: the collection
carries `usageLimitSet` and `usageLimitRemove` as mutations and no query that reads a
workspace limit back — the only limit-bearing read, `agentUsage`, is about agent spend.
But *spend* is a different matter: `usage`, `estimatedUsage` and `projectServiceUsage`
are all queries taking a `workspaceId`. So wherever this project has written that the
balance cannot be watched from here, the accurate sentence is that **it is not watched,
because no Railway token exists in CI** — which is Marco's decision, the same one the
three `verify-hub.sh` secrets are waiting on. Arguing from a tool's absence to a
platform's absence is exactly the mistake this file already records twice, on
`RAILWAY_GIT_COMMIT_SHA` and on `limitOverride`. The full table of controls that live
outside git, and who re-verifies each, is in `SECURITY.md`.

> **Amended on 2026-08-20**: the rule passes **two** paths since the Phase 1.5 log
> ingest, `POST /v1/logs` beside `POST /v1/metrics`, on the same condition. The table
> below carries both. The reasoning is unchanged, and so is the boundary: the rule reads
> whether an `Authorization` header is present, never its value.

**A WAF custom rule fronts the OTLP ingest, and it is not the authentication.**
Deployed 2026-08-20 on `otel.marcobellingeri.dev`: everything except `POST
/v1/metrics` is blocked at the edge. Access control stays where it was —
`bearertokenauth` inside the Collector — because a public hostname is not an access
control and a rule that can be edited in a dashboard is not a credential check. What
the rule buys is that scanners, wrong methods and non-existent paths stop before they
cross the tunnel, which since 2026-08-19 is measured in money rather than CPU.

**The rule also requires the `Authorization` header to be present**, added later the
same day. That could not be done first: `smoke.yml` proved the ingest authenticates by
sending a request *without* the header and requiring `401`, and blocking that at the
edge would have turned the proof into a `403` that stays green even if the Collector's
`auth:` block were deleted. So the order was: teach the smoke test to prove the
Collector with a **wrong** token (credentials present, still refused — the only shape
that survives both rule versions), then extend the rule, then tighten the no-header
check to `403` exactly, where it became the assertion that the rule exists.

Measured against production after each of the two deploys:

| request | before the header condition | after | who answered |
|---|---|---|---|
| `POST /v1/metrics`, wrong bearer | `401` | `401` | the Collector |
| `POST /v1/metrics`, empty bearer | `401` | `401` | the Collector |
| `POST /v1/metrics`, header uppercased | — | `401` | the Collector |
| `POST /v1/metrics`, **no** `Authorization` | `401` | **`403`** | the edge |
| `POST /v1/logs`, **no** `Authorization` | `401` | **`403`** | the edge |
| `POST /v1/logs`, wrong bearer | `401` | `401` | the Collector |
| `GET /`, `GET /v1/metrics` | `403` | `403` | the edge |
| `POST /v1/traces`, `POST /v1/logs` | `403` | `403` | the edge |
| `POST /v1/metrics/` (trailing slash) | `403` | `403` | the edge |

Real ingest kept landing across both changes (86.6M → 90.5M → 92.3M tokens counted).

**Header names are matched case-insensitively** — `AUTHORIZATION` passes. The vendor's
field reference does not say so for `http.request.headers.names`, and it was written
here only after measuring it, not because the lowercase literal in the expression
looked like it must work.

The rule never matches the header's **value**: a WAF rule is readable from the
dashboard and the API, so a token in there is a secret outside the secret store. The
trailing-slash case is blocked on purpose and is safe today because the OTLP HTTP
exporter sends the path exactly; if that ever changes, the symptom is a silent stop in
ingest.

Free plan, so five custom rules total and no `Log` action — verified in the vendor's
availability table, not assumed. This uses one.

**Fixing the repeat in the application was half the job: the Sentry rule notified
once too.** #86 turned the watchdog's dedup from once-per-process into an hourly
throttle, so a condition that lasts keeps saying so. It did not help. The delivering
rule fired on `2026-08-13T07:42` — the issue's *first* event — and never again through
three more occurrences and six days, because both its triggers were priority
**transitions**: once an issue is high priority it stays high, so nothing changes and
nothing fires. Two identical rules were enabled on the project; the older one had never
fired at all.

Corrected 2026-08-20: the duplicate deleted, an `Every event` trigger added beside the
two transitions, and the action throttle moved from *every trigger* to **60 minutes**,
which is the same window the application throttles on — two clocks for one cadence is
how they drift. Verified by reading the rule back: one rule, `frequencyMinutes: 60`,
three conditions. Like the WAF rule and the spending limit, this lives outside git and
no gate here can notice it changing.

**A series that is collected and never evaluated is a record, not a witness.**
`up{job="cloudflared"}` reads as monitoring and is not: this project has no
`rule_files`, no Alertmanager, and no panel on `cloudflared_tunnel_ha_connections` or
`cloudflared_tunnel_request_errors` — the two names `prometheus.yml` used to call "the
ones that count". The only consumer of any of it is the dashboard's `Target su/giù`
panel (`expr: up`), which lives in Grafana, i.e. **behind the very connector whose
death it would report**. The scrape is worth keeping — after an outage it is the only
thing that says when the tunnel went and how long it was gone — but the thing that
*notices* is outside: `smoke.yml` every ten minutes from the public internet, and
`sorveglianza.yml` once a day. Written down 2026-08-20 because the comment in
`prometheus.yml` promised more than the repository contains, which is the same defect
as an asserted control that does not exist, only quieter.

**Langfuse no** — Phase 1 makes no model call of its own; there is nothing to trace.
A standing decision for Phase 4 (session RAG), not a gap today.

**LangChain no**, in any phase. The portfolio already has a lighter proven pattern
(direct embedding + pgvector + a direct Anthropic call), and fewer dependencies is
the whole posture.

**`TUNNEL_TOKEN` is not one row among six, and the table read as though it were.**
Whoever holds it can start their own connector registered against the same tunnel,
and Cloudflare distributes requests across registered connectors — so a share of live
traffic arrives at someone else *after* the edge has already satisfied Access. That
traffic carries, in plaintext at that point, the Worker's `CF-Access-Client-Secret`,
the status API's bearer, and `OTLP_INGEST_TOKEN` on the `otel.` path. **One token
yields three of the other five.** It is also the cheapest rotation in the table —
one variable, one service, no client anywhere to update — which is the exact inverse
of `OTLP_INGEST_TOKEN`, whose three-place rotation the table does warn about.

## Where each secret lives, and what rotating one costs

Doppler is the source of truth. Railway service variables are the runtime copy. The
laptop keeps exactly one, because a shell reads it on every terminal and a network
call there fails offline.

| Secret | Doppler | Railway | Laptop | Cloudflare Worker | GitHub |
|---|---|---|---|---|---|
| `OTLP_INGEST_TOKEN` | yes | `otel-collector` | Keychain (`agentic-os-otlp-ingest`) | — | — |
| `STATUS_API_TOKEN` | yes | `status-api` | — | as `AGENTIC_OS_STATUS_TOKEN` | yes (`sorveglianza.yml`) |
| `TUNNEL_TOKEN` | yes | `cloudflared` | — | — | — |
| `GF_SECURITY_ADMIN_PASSWORD` | yes | `grafana` | — | — | — |
| `SENTRY_DSN` | yes | `status-api` | — | — | — |
| Access service token (ID + secret) | yes | **never** | for `verify-hub.sh` | `AGENTIC_OS_ACCESS_CLIENT_*` | yes (`CF_ACCESS_CLIENT_*`) |
| the four `LOKI_R2_*` (bucket, endpoint, key id, secret) | yes | `loki` | — | — | — |
| `SLACK_WEBHOOK_URL` | yes | `grafana` | — | — | yes (`notifica.yml`, since 2026-08-23) |

The **GitHub column did not exist until 2026-08-23**, and it was wrong by omission for
three days before that: `STATUS_API_TOKEN` and the Access pair had been repo secrets
since 2026-08-20 for `sorveglianza.yml`. A table titled "where each secret lives" that
is missing a home is the same defect as a control asserted and absent — it just fails in
the direction of *under*-counting the rotation. Three further secrets exist only in
GitHub and never touch a service (`RAILWAY_TOKEN`, `SENTRY_AUTH_TOKEN`, `SONAR_TOKEN`);
whether they are also mastered in Doppler is not recorded here, and this table will not
guess.

The Access credentials never touch Railway on purpose: it is Cloudflare that verifies
them, at the edge. The hub does not even see them as something to check.

**Rotation is where this bites.** `OTLP_INGEST_TOKEN` lives in three places — Doppler,
the Collector, and the shell. Change two of the three and telemetry stops **in
silence**: Claude Code keeps exporting with the old value and the Collector answers
401, which nothing surfaces. Same shape for `STATUS_API_TOKEN`, which must match
between the Railway service and the Worker secret **whose name is different**
(`AGENTIC_OS_STATUS_TOKEN`). And since 2026-08-23 `SLACK_WEBHOOK_URL` has the same
shape: rotating the Slack incoming webhook now breaks *two* consumers, Grafana's contact
point and `notifica.yml`. Change one and the other keeps posting to a dead URL — the
alerting half fails loudly (Grafana surfaces the delivery error), the CI half fails as a
red job that, by construction, has nobody left to announce it.

A Doppler → Railway sync was evaluated on 2026-07-29 and not adopted: the services hold
different secrets, so respecting least privilege would mean one Doppler config per
service to manage a handful of values that change approximately never. The count in this
paragraph has already moved once. It said "five services, four configs, five values"
until 2026-08-21, and by then Phase 1.5 had added four `LOKI_R2_*` and the alerting had
added `SLACK_WEBHOOK_URL`: eleven values across six services. That is the trigger this
decision named for itself, so it is worth reopening the next time something is added
rather than counting again.

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
