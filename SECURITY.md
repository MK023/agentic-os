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

The hub runs five services on Railway today, and six once Loki lands — the Phase 1.5
log store is written and gated but its Railway service and R2 bucket do not exist yet, so
nothing below describes it as running. **None of them has a public platform domain.** The
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
`terminal_type`, `session.id`, plus scrape metadata — no credential, no session
content, no email. What 30 days of it *is*, though, is a behavioural profile at
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
| WAF custom rule on `otel.` — since 2026-08-20 it passes **two** paths, `POST /v1/metrics` and `POST /v1/logs` | Cloudflare dashboard | `smoke.yml` — three assertions requiring an exact `403` (no header, `GET /`, `POST /v1/traces`) | every scheduled run |
| `bearertokenauth` on the ingest | in git (`docker/otel-collector-config.yaml`) | `smoke.yml` — three assertions requiring `401`: an empty and a wrong bearer on `/v1/metrics`, a wrong bearer on `/v1/logs` | every scheduled run |
| Access policy on `grafana.` (single email) | Cloudflare dashboard | `sorveglianza.yml` — runs `scripts/verify-hub.sh` daily; a `200` without credentials is treated as the failure it is | every scheduled run |
| Access + Service Auth on `status.` | Cloudflare dashboard | same job, same script: it calls `/status` exactly as the site's Worker does, with the Access service token *and* the bearer | every scheduled run |
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
