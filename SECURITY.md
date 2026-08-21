# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| main    | Yes       |

## Reporting a vulnerability

If you find a security vulnerability, **do not open a public issue**.

Send a private report to: **mkdevpy@proton.me**

Include:

- A description of the vulnerability
- Steps to reproduce it
- The potential impact

I will reply within **72 hours** and work with you to fix it before any public
disclosure.

## What this project exposes, and what it deliberately does not

The hub runs six services on Railway. The sixth is the Phase 1.5 log store, **live
since 2026-08-21**: this paragraph said "five today, six once Loki lands" until then.
Loki takes no Tunnel hostname and no public Railway domain, so nothing about the public
surface changed when it landed — what changed is that a component described here as
absent is now running, which is the half that goes stale silently. **None of them has a public platform domain.** The
only ingress is a Cloudflare Tunnel with three hostnames:

- `grafana.`: Cloudflare Access with a single-email policy.
- `status.`: Cloudflare Access with Service Auth, plus the application's own
  bearer token. Two independent layers — **and the Worker on marcobellingeri.dev
  holds both credentials**, which is the part that matters: on the path that is
  actually reachable from the internet, every request arrives pre-authenticated.
  The two layers stop a direct caller at `status.` and stop nothing on the public
  route. Stating the layers without stating who holds the keys is how a trust
  boundary gets described as stronger than it is.
- `otel.`: no Access application, because Claude Code cannot send Access headers.
  Authentication is handled by the `bearertokenauth` extension **inside the OTel
  Collector**. Since 2026-08-20 a WAF custom rule also blocks everything except a `POST`
  to **two** paths — `/v1/metrics`, and `/v1/logs` since the Phase 1.5 logs pipeline —
  each **carrying an `Authorization` header**, before it reaches the tunnel; the header's
  presence only, never its value. Measured on both paths: a wrong or empty bearer still
  reaches the Collector and still gets `401`; no header at all gets `403` from the edge.
  `/v1/traces` is still `403`. It narrows what can be *attempted*; it authenticates
  nothing, and it is not what keeps the ingest closed. **A second open path is a second
  thing to assert**, so `smoke.yml` also sends a wrong bearer at `/v1/logs` and requires
  the Collector's `401`: a route opened and not asserted is a route opened and forgotten.
  Propagation is not instant — the new path answered `403` for a couple of minutes after
  the rule was changed, which is a rule arriving, not a rule broken.

A public hostname is not an access control. This follows the CVE-2026-28798
pattern and is the reason this project's design starts from authentication at
the ingestion layer.

Prometheus never gets a hostname of its own — **and the reason written here until
2026-08-19 was wrong**. It said Prometheus "has no authentication"; the vendor's own
security page says the opposite: *"Prometheus, and most exporters, support TLS.
Including authentication of clients via TLS client certificates."* Native TLS and
basic auth exist, via `--web.config.file`. They are simply **not configured here**,
because the decision does not need them: the same page states these endpoints
*"should not be exposed to publicly accessible networks like the internet"*, and no
route means no exposure to authenticate against. Declaring a vendor limitation that
does not exist is the same failure as declaring a control that does not exist — it
just points the other way.

Loki never gets a hostname either, **for the same reason and not for a different
one**: it has no route, so there is nothing to authenticate against. Two things it is
*not*: it is not that Loki cannot authenticate, and it is not `auth_enabled: false` in
`docker/loki.yaml`, which governs **multi-tenancy** — the `X-Scope-OrgID` header — and
says nothing about access. A key that reads like "authentication off" is exactly what a
later reader turns into a security claim, in whichever direction suits the sentence. The
only thing that reaches Loki is the Collector, on Railway's private network, and Grafana
querying it from behind Access.

### What Loki actually serves on `:3100` — measured 2026-08-21, not read

`loki` was the one service never audited: the other five were swept in the week of
2026-08-20 (#127 Prometheus CORS, #128 pprof on cloudflared, #129 six hardenings on
status-api), and Loki was born after. This is that sweep, run **against the live
container** on the shipped config, because that is the method that found those defects.

"No route means nothing to authenticate against" stays true. What follows is what
anything *already inside* the private network can do without a credential — which is the
question a perimeter never answers.

The sweep missed one direction, and it is the one a route table cannot show: **what the
service sends out on its own**. Loki's default `analytics.reporting_enabled` is `true`,
so every few hours it posted version, host, memory, ingestion and query counters and a
cluster id to `stats.grafana.org`. No log content, and still an undeclared third party
receiving an activity profile from the service whose written posture is "no route, no
egress beyond R2". It is off since 2026-08-22, the same setting `grafana.ini` turns off
for Grafana, and both states were read back from the shipped binary rather than assumed.
One consequence is worth knowing before somebody reads the bucket: `loki_cluster_seed.json`
is written by that module, not by ingestion, so it stops appearing. The R2 witness is now
the first object under `index/` or `chunks/`.

| Route | Measured | What it does | Closable here |
|---|---|---|---|
| `GET /ingester/shutdown` | `204`, then **exit code 0** | Stops the log store. See below — this one changed a deploy setting | **no** |
| `POST /loki/api/v1/delete` | `403` | Closed on 2026-08-21 by `deletion_mode: disabled` in `docker/loki.yaml`. This row read "`204` on this branch" for a day after the merge. `scripts/prova-ritenzione-loki.sh` proves both halves: the route refuses **and** retention still runs, because turning the delete API off is the kind of change that can quietly stop the compactor with it | no |
| `POST /log_level?log_level=debug` | `200`, level changes | Floods the 5 GB volume and the R2 bill; or `error` to go quiet | **no** |
| `GET /flush` | `204` | Forces a flush: many small chunks, R2 writes are billed per request | **no** |
| `GET /config` | `200` | Prints `endpoint`, `bucket_name` and `access_key_id` **in cleartext**; `secret_access_key` is masked | **not tried** — see below; a candidate exists (`native_aws_auth_enabled`) and declaring it closed or impossible without running it would be the same mistake this section exists to avoid |
| `GET /debug/pprof/heap` | `200` | Heap dump — log records in flight | **no**, see below |
| `POST /ingester/prepare_shutdown` | `204`, state `set` | Arms the shutdown path; `DELETE` disarms it | **no** |
| `POST /loki/api/v1/push` | `204` | **Bypasses both allow-lists** — see below | bounded, not closed |

**The shutdown route was worse than this file used to imply, and the difference is a
measurement.** Loki exits with code **0** (`docker wait`, on a real container).
Railway's own docs say `On Failure` restarts "only if it stops due to an error (e.g.
crashes, exits with a non-zero code)" — so under the previous
`restartPolicyType: ON_FAILURE`, one unauthenticated GET stopped the log store **and
nothing brought it back**: this service has no `healthcheckPath` (a closed decision), and
at the time nothing watched `up{job="loki"}` either. Since 2026-08-21 `railway/loki/railway.json`
declares `ALWAYS`, which turns a permanent stop into a restart. It is not a fix for the
route — the route cannot be turned off — it is the difference between an outage that
ends by itself and one that waits for somebody to look.

**What `ALWAYS` costs, stated because half a trade-off is not a trade-off.** It does not
shrink the attack surface: the route stays open and unauthenticated, so one GET becomes
a restart and a *loop* of GETs becomes repeated restarts. Every start does I/O against
R2 — Loki reads the delete-request store during `init compactor`, measured — and the
plan is billed on usage while the row below still reads "R2 spend: **nobody**". The
second cost is the failure *mode*: `ALWAYS` converts a stop into **flapping**, and
flapping on a service with no `healthcheckPath` is exactly as quiet as the stop was.
That half closed later the same day, in the PR that added alerting: the `loki-giu` rule
in `docker/grafana/provisioning/alerting/regole.yaml` watches `up{job="loki"}` with
`for: 5m`, long enough that a redeploy of a service with a volume does not ring.
`scripts/prova-allarmi.sh` makes that exact rule fire by removing Loki from the network
and requires the notification to arrive.

**One thing is deliberately not answered here, because reading the docs did not settle
it.** The vendor's restart-policy page says the default `On Failure` comes "with a
maximum of 10 restarts" and that paid plans "can set any restart policy with any number
of restarts"; it does not say what bound `ALWAYS` carries when
`restartPolicyMaxRetries` is left unset, and the schema makes that key optional. So
either the cap does not apply — and the paragraph above is the whole story — or it does,
and the permanent stop returns at the eleventh request. Unmeasured, and written down as
unmeasured rather than assumed either way.

**The native push API is not governed by the two allow-lists.** Measured: a push to
`/loki/api/v1/push` carrying `etichetta_arbitraria` and `user_email` produced an index
label *and* queryable structured metadata with both values intact. `otlp_config` in
`docker/loki.yaml` governs the **OTLP** endpoint; the native endpoint has no allow-list
at all. The privacy guarantee therefore reads: *the Collector cannot leak identity into
Loki*, not *nothing can*. What bounds the damage is `max_global_streams_per_user: 200`
and the fact that reaching this endpoint already means being inside the private network.

**What `/config` leaks is not mainly the key id.** `secret_access_key` comes back
masked (`********`) — a vendor default, not a control here, and nothing re-reads it. The
key id alone is not a credential: on its own it opens nothing, so calling
least-privilege its "mitigation" describes the wrong thing. What the same block does
expose in cleartext is `endpoint` and `bucket_name`, and in production the endpoint is
`<ACCOUNT_ID>.r2.cloudflarestorage.com` — **the Cloudflare account id and the bucket
name**, which are worth more to somebody mapping this setup than the key id is. The R2
token being least-privilege (`403` on any other bucket and on the account, verified once
on 2026-08-21) bounds what a leaked *secret* could do; it bounds nothing about log
destruction, because that goes through Loki, which holds the token.

**pprof stays open, deliberately, and the alternative was measured rather than
assumed.** The flag's own help text says `-server.register-instrumentation` registers
"the intrumentation handlers (/metrics etc)" — "etc" is not an answer, so it was run:
with `=false`, `/metrics` **and** `/debug/pprof/*` both return `404`, while `/ready`
still serves. Closing the heap dump therefore deletes Loki's only witness, since this
service has no healthcheck and the Prometheus scrape is what watches it. A declared
absence beats trading the monitor for the hardening.

The public surface of the whole system is three aggregate numbers (sessions,
tokens, cost). No session content, no free-form PromQL, and no path from the
public widget to anything else.

**Aggregated over metrics *and* over time.** The three numbers alone were only half
the statement: sampled without limit they are also a presence feed — the second at
which a session starts, how intense it is, which model is running (the cost/token
ratio moves with the model mix). Since 2026-08-19 `/status` serves from a 60-second
cache, so the origin computes one pass per minute whatever the incoming rate, and
the resolution of that side channel is capped at the same minute. The caller is throttled too, and this
paragraph said otherwise until 2026-08-20: the site's Worker has applied a per-IP rate
limiting binding to `/api/agentic-status` since 2026-08-16 — 60 requests / 60s, with
`Retry-After: 60` on the `429`. The two do different jobs: the cache bounds what
unlimited sampling *buys*, the limiter bounds how much sampling one address gets. And
the limiter is a ceiling rather than a guillotine — measured against production on
2026-08-20, 75 requests in ~11s drew no `429` at all while 200 in ~26s drew 13, the
first at request 167, because the binding counts per datacentre and is eventually
consistent by design.

**What the Prometheus volume holds, stated plainly.** After the Collector's
allow-list the labels are `model`, `type`, `query_source`, `start_type`,
`terminal_type`, `session.id` — no credential, no session content, no email;
counted against the live TSDB on 2026-08-20, zero strings shaped like an address.

**Three more labels ride along, and "plus scrape metadata" was too generous a word
for them.** `otel_scope_name`, `otel_scope_version` and `exported_job` are not added
by the scrape: they come from the *payload*. The first two are OTLP **scope** fields
— the same channel found uncovered on the log side and closed there — and
`exported_job` derives from the resource's `service.name`, which the client controls
through `OTEL_SERVICE_NAME`. None of them passes through `keep_keys`, so the
guarantee written next to that processor — *"an attribute a future release invents is
a missing label rather than a leak"* — **did not hold on these three, and it was
worse than the sentence above admitted**: measured 2026-08-21, a scope *attribute*
crossed the Collector and reached `:8889` as `otel_scope_<key>`, verbatim, through no
allow-list at all. **Closed the same day**, with the vendor's own switch and not a
third list: `without_scope_info: true` on the `prometheus` exporter drops scope name,
version, schema URL and attributes as labels, and a `resource` statement in
`transform/label-allowlist` pins `service.name` to `claude-code`, so `exported_job` is
no longer a value the client picks. `scripts/prova-contratto-metriche.sh` sends
identity on all three levels — resource, scope, data point — plus a client-chosen
`service.name`, and fails if any of it is exposed; it was run red against the previous
config before it was run green against this one. The resource half was measured too,
and did **not** leak even before: with the conversion off this exporter emitted no
`target_info`, which is why the `keep_keys` on the resource is there for the value of
`job`, not for a key that was escaping. What 30 days of it *is*, though, is a behavioural profile at
15-second resolution: when work starts, how long it lasts, how intense it is, which
models. The public endpoint's version of that side channel is bounded to one minute
by the cache; the volume is the same channel at full resolution and full history.
Acceptable for a personal project, and worth knowing it is personal data rather than
anonymous counters.

## Controls that live outside git, and who re-verifies them

Several controls this file and `docs/DECISIONS.md` assert are **not in this
repository**: they live in the Cloudflare dashboard, the Railway workspace and the
Sentry project. They exist — each was measured on the date below — but a control
nobody re-reads is one whose *disappearance* is silent, and the document would go on
asserting it. That is the same failure this project has already corrected twice, just
pointing the other way.

The rule this table exists to enforce: **every line that asserts a control names who
re-verifies it, or declares itself unverified with the date of the last measurement.**

| Control | Where it lives | Who re-verifies it | Last measured |
|---|---|---|---|
| WAF custom rule on `otel.`, which since 2026-08-20 passes **two** paths, `POST /v1/metrics` and `POST /v1/logs`. Closing `/v1/logs` again is manual, and only one direction is silent: `smoke.yml` requires an exact `401` there, so closing it at the edge turns the probe **red** and names the moved surface. What nothing here notices is the opposite mistake, leaving the path open after Loki is gone (`docs/BLOCKERS.md` §4) | Cloudflare dashboard | `smoke.yml`, four assertions requiring an exact `403` (no header on `/v1/metrics` and on `/v1/logs`, `GET /`, `POST /v1/traces`) | every scheduled run |
| `bearertokenauth` on the ingest | in git (`docker/otel-collector-config.yaml`) | `smoke.yml` — three assertions requiring `401`: an empty and a wrong bearer on `/v1/metrics`, a wrong bearer on `/v1/logs` | every scheduled run |
| Access policy on `grafana.` (single email) | Cloudflare dashboard | `sorveglianza.yml` — runs `scripts/verify-hub.sh` daily; a `200` without credentials is treated as the failure it is | every scheduled run |
| Access + Service Auth on `status.` | Cloudflare dashboard | same job, same script: it calls `/status` exactly as the site's Worker does, with the Access service token *and* the bearer | every scheduled run |
| R2 spend, from log ingestion | **nowhere still** — no ceiling on the bucket. **Two halves of this row were stale until 2026-08-21**: the bucket exists and has been serving since 2026-08-21, and the stream cap says `200`, not the `10` written here (`10` was live for a few hours on 2026-08-20 and was a denial of service with our own name on it) | **nobody.** The Railway workspace limit below does not bound a Cloudflare bill. What exists is upstream: `docker/loki.yaml` declares `ingestion_rate_mb: 1` / `ingestion_burst_size_mb: 2` / `max_global_streams_per_user: 200` instead of the vendor defaults (4 MB/s ≈ 345 GB a day toward R2), and the Collector's queue is capped. Those bound what can be *sent*, not what is *billed*. What is **not** shared between the two ingest paths is the allow-list: `otlp_config` governs the OTLP endpoint, and `POST /loki/api/v1/push` has none (measured 2026-08-21) | ceilings re-read from the shipped config 2026-08-21; the bill itself, never |
| Workspace usage limit ($15 soft / $30 hard) | Railway workspace | **still nobody for the *limit*** — see below. Since 2026-08-20 `sorveglianza.yml` watches the *consumption* instead, against ceilings in `docs/sorveglianza-baseline.json` | 2026-08-20, set and confirmed by the operator |
| Sentry alert rule (`Every event`, 60m throttle, three conditions) | Sentry project | `sorveglianza.yml` — daily, and it fails naming the six-day outage if `every_event` goes missing | every scheduled run |
| `PORT` on `status-api` and `cloudflared` | Railway service variables | **nobody**, but its absence fails every deploy loudly rather than silently | 2026-08-20, both services list `PORT` |

**Four of these rows changed on 2026-08-20, and the change is a workflow rather than a
sentence.** `sorveglianza.yml` runs `verify-hub.sh` on a schedule, so the two Cloudflare
Access policies stopped being "verified whenever somebody remembers". The Sentry rule and the Railway
consumption followed once their tokens existed — and writing them *after* the tokens,
rather than before, is what caught two things a guess would have shipped:

- **Sentry's `/projects/{org}/{project}/rules/` is a lossy legacy projection.** Sentry
  has migrated to detectors + workflows, and the same rule appears under **three
  different ids** across three views. On `/rules/` it shows **two** conditions instead
  of three — `every_event` does not survive the conversion. A probe written there would
  have gone red on a healthy alarm. The authoritative read is
  `/organizations/{org}/workflows/`.
- **An organization auth token cannot do this at all.** Its only scope is `org:ci`
  (source maps, releases, code mappings) and every read returns `403` — measured. What
  works is an **Internal Integration** with `Alerts: Read` and `Project: Read`, whose
  token belongs to the organisation rather than to a person.

The Railway probe deliberately **does not compute money**: the `MetricMeasurement` enum
carries no monetary measure (introspected) and `estimatedUsage`'s units are
undocumented — dividing `MEMORY_USAGE_GB` by the hours in a month yields ~$169 against a
real bill of ~€5. Pricing it would mean guessing the unit *and* copying the rate table
into a fourth place. What it catches is a **runaway**, which is the risk the unreadable
spend limit would only stop afterwards.

**What Railway's API does and does not give you, measured rather than assumed.**
The claim in `docs/DECISIONS.md` — that nothing here can read the usage limit back —
was written from the MCP surface, which exposes projects, services, variables,
deployments, metrics and feature flags and no billing at all. Railway also publishes
its GraphQL collection at a URL that needs no token, and reading it on 2026-08-20
sharpens the claim in both directions:

- **The limit itself: still no read.** The collection carries `usageLimitSet` and
  `usageLimitRemove` as *mutations* and no query that reads a workspace limit back.
  The only limit-bearing read is `agentUsage`, whose `softLimitCents`/`hardLimitCents`
  are about agent spend, not this. So the row above stays "nobody", and it is now
  measured rather than inferred from one tool's menu.
- **Spend itself: readable.** `usage`, `estimatedUsage` and `projectServiceUsage` are
  all queries taking a `workspaceId`. So "no gate here can watch the balance" is
  **wrong as an absolute**: what is missing is a Railway token in CI, which is the same
  decision as the three secrets `verify-hub.sh` waits on — Marco's, not the CI's.
- **Per-service caps: readable too.** `serviceInstanceLimitOverride` and
  `serviceInstanceLimits` are queries, so the open experiment in `DECISIONS.md` about
  whether Hobby enforces `deploy.limitOverride` can be *verified* through the API and
  not only declared from the schema. Measured 2026-08-20: `status-api` has no override
  set, only `numReplicas: 1`.

None of this is a token being added anywhere. It is the difference between "the
platform does not allow it" and "we have not set it up", and this repository has
already paid once for writing the first when it meant the second.

## Data handling

Metric labels are an **allow-list** enforced in the Collector.

Claude Code sends identity attributes including `user.email` with a real address,
`user.id`, `user.account_id`, `user.account_uuid`, and `organization.id` as
*data point* attributes. `resource_to_telemetry_conversion: false` does not keep
them out. This was measured against the real client, not assumed.

Everything not on the allow-list is dropped before storage, including attributes
that no version emits yet.

The reasoning behind every security decision, including what measuring changed
about them, is documented in [docs/DECISIONS.md](docs/DECISIONS.md).
