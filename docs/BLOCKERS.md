# What is not done yet

Current as of 2026-08-20. The last tag is **v1.1.0** (2026-08-16) and `main` is
ahead of it: the six services run — `loki` since 2026-08-21 — the Tunnel serves its
three hostnames, the
public endpoint answers with real numbers, and `smoke.yml` watches it from
outside — since 2026-08-20 on the ingest's own authentication and on the WAF rule
in front of it, not only on the public endpoint. Everything settled lives in
`DECISIONS.md`; everything verified lives in the commit that verified it. This
file is only what is still open.

**No engineering task blocks anything.** What follows is two things to watch, one
thing nobody downstream can fix, and a handful of judgement calls — plus, since
2026-08-20, three pieces of infrastructure that only Marco can create before Phase 1.5
runs at all.

## 1. Claude Code's metric names, against a future version

The names are measured, not assumed — but this telemetry is beta and versioned. If a
panel goes empty after a Claude Code upgrade, that is the first thing to check, and
`LOCAL_DRY_RUN.md` is how to check it in five minutes. The same dry run is still the
only check the label allow-list gets against a *new* client version — **no CI gate
sees what a future release starts sending**, and since 2026-08-20 one gate
(`telemetry-baseline.yml`) does see *that* a new minor was released, which is a
reminder rather than a measurement. The difference matters: it can tell you to look,
it cannot look for you.

**Re-run 2026-08-16 against v2.1.227**, seven patch releases after the v2.1.220 the
names and labels were first measured on. Nothing moved: same four metric names, the
label set is still exactly the allow-list (`model`, `type`, `query_source`,
`start_type`, `session_id`, plus the `otel_scope_*` the exporter adds), and zero hits
for `user_email` / `user_id` / `user_account*` / `organization_id`. Two headless
sessions produced **two distinct `session_id` values**, which is the half that matters
and the half production cannot self-check: a renamed `session.id` would collapse
concurrent sessions into one series and the numbers would simply read *lower*, with no
error anywhere.

The other half checks itself for free, and is worth knowing so this dry run is not run
out of habit: the three public numbers are non-zero, which is only possible if
`claude_code_session_count` and `claude_code_token_usage` still exist under those exact
names. The dry run is for the label set, not the metric names.

## 2. Grafana's fixable CVEs, which are not fixable here

`grafana/grafana` carries HIGH vulnerabilities that *do* have fixes — in the
upstream Go modules it vendors (`tempo`, Go stdlib, `x/net`, `x/text`), not in any
Grafana release that can be installed. The `images` job prints them without
failing, deliberately: a gate that cannot go green becomes a `continue-on-error`
within a month.

Exposure is low — Grafana has no platform domain and sits behind Cloudflare
Access with a single-email policy. **The action is to watch, not to work** — and as
of 2026-08-20 the watch has a result that inverts its own instruction. This
paragraph used to say "when Dependabot proposes a newer `grafana/grafana`, check
whether the count drops". It was measured, and the count *rises*: see "13.2.0 is a
regression" below. The standing action is now to **stay on 13.1.x and reject 13.2.0**,
which `.github/dependabot.yml` does by name.

**Triaged by the criteria the model actually asks for — exploitability, not
existence.** A count is not a risk assessment. First done 2026-08-14 on `13.1.3`'s
set; **re-run 2026-08-20 on `13.1.4`'s**, which is what production carries, because
the set had moved and the old numbers described an image nobody was running:

- **CISA KEV: none of the thirteen.** Catalogue version `2026.08.19`, 1671 entries,
  115 of them from 2026 — so a current catalogue that does contain this year, which is
  what makes "none" mean something rather than "the feed is stale". Nothing here is
  being exploited in the wild.
- **EPSS: the worst is 0.0066** — CVE-2026-39821 (`x/net/idna` via `net/http`), a
  0.66% chance of exploitation in 30 days, **48th percentile**. Every other one is
  lower, down to 0.0016 for the `tempo` information-disclosure. Both numbers moved
  *down* from the 2026-08-14 triage, whose worst was 0.0078: this is the ordinary
  background of open-source CVEs, not a signal.
- **Reachability: the two `tempo` CVEs have no configured code path here.**
  Re-checked 2026-08-20: the only provisioned datasource is still Prometheus
  (`docker/grafana/provisioning/datasources/prometheus.yml`), there is no Tempo
  datasource, and the only match for "tempo" under `docker/grafana/` is the Italian
  word inside a dashboard comment. No S3 at all. They ship inside the binary as
  vendored modules and nothing calls them.
- **`13.1.3` was the newest release** (checked against the upstream release list on
  2026-08-14; it shipped 2026-08-07). So "wait for Dependabot" was, at the time,
  waiting for something that did not exist yet — the absence of a bump was not
  neglect. **That stopped being true on 2026-08-18, and the newer release turned out
  to be worse rather than late** — see "13.2.0 is a regression" below.

So the watch is not just cheap, it is **correct by the same criteria that would
have made it blocking**. What would change the posture is a **KEV entry**, not a
higher count — and the action then would not be "upgrade", since there would still
be no release to upgrade to. It would be to narrow or switch off the exposure:
Grafana is a convenience here, and the three public numbers do not pass through
it. That is worth knowing *before* the day it matters, because a decision that has
already been made is much cheaper at 3am.

**What changed since, and why the triage above no longer describes the image.**
Dependabot bumped `grafana/grafana` to **13.1.4** on 2026-08-19 (PR #80), pinned by
digest in `docker/docker-compose.yml` and `railway/grafana/Dockerfile`. The `images`
job on `7d92dcd` (2026-08-20) reports **13 distinct HIGH CVEs** across the image's
three scanned Go binaries — 2 in `github.com/grafana/tempo`, 11 against `stdlib
v1.26.3` and 9 against `stdlib v1.26.4`, the last two overlapping. An independent
local scan of `grafana/grafana:13.1.4` on the same day, same tool and flags, returns
the same 13, which is the check that the CI count is the image's and not the build's.
**`CVE-2026-33814`, the CVE the 14/08 triage named as its worst by EPSS, is not among
them** — though 13.2.0 puts it back.

So the count moved and so did the set: the KEV and EPSS lines above were measured
against `13.1.3` and have **not** been re-measured. They are kept because the
*method* is the point — exploitability, not existence — but their numbers are not a
current statement about production. The two `tempo` findings are the one part that
carries over unchanged, and for the same reason: still no Tempo datasource, still no
S3, still nothing that calls them.

**`13.2.0` is a regression, and this is the part worth keeping.** Upstream published
`v13.2.0` and `v13.1.4` on the same day, 2026-08-18. Scanned both on 2026-08-20 with
the same tool and flags the CI uses — trivy 0.70.0, `--severity HIGH,CRITICAL
--ignore-unfixed`:

| | distinct HIGH/CRITICAL with a fix |
|---|---|
| `13.1.4` (what runs) | **13** |
| `13.2.0` (the newer minor) | **28** |

**It resolves zero of the thirteen and adds fifteen.** One of its binaries vendors
`stdlib v1.25.7` — *older* than the 1.26.3/1.26.4 in 13.1.4 — alongside an older
`x/net` (v0.51.0), `x/text`, `otel` and `grpc`, and it brings back **CVE-2026-33814**,
the very CVE the 2026-08-14 triage had named as its worst.

So "wait for Dependabot" would have made this worse, and it would have arrived
**green**: the `images` job prints third-party image CVEs without failing, on purpose,
so a bump from 13 to 28 changes nothing anybody sees. `.github/dependabot.yml` now
ignores `grafana/grafana` **13.2.0 by name**, in both places the image is pinned. Only
that version, not the 13.2.x line: a 13.2.1 rebuilt against a current Go should be
re-measured and taken, and 13.1.x is still maintained, so staying here is not falling
behind.

**The rest is unchanged and still a watch rather than a task**: the fixes remain
upstream-only, exposure is unchanged (Access, single-email policy, no platform domain,
no public numbers passing through Grafana), and what would change the posture is still
a KEV entry, not a higher count.

## 3. The smoke probe cannot see a short outage — closed from the other repo

`smoke.yml` asks for `*/10` and gets, measured over its first twelve runs, an
interval between 38 and 93 minutes (median ~55): GitHub throttles frequent
schedules on public repositories. It reliably catches an outage that *lasts* — a
deploy that does not serve, a full volume, a Tunnel down — and will usually miss
a two-minute restart like the two on 2026-08-13.

**The prober that closes it shipped on 2026-08-14 in the site repo, not here**
(`marcobellingeri.dev` PR #203): a Cloudflare Cron Trigger at `*/2 * * * *` with a
`scheduled` handler that runs the same probe a visitor's request would. Two
properties are worth writing down, because they are why it works:

- **It adds a trigger, not logic.** The Worker already reported to Sentry when the
  hub did not answer; that code only ran if somebody visited, and at 4am nobody
  does — which is exactly when an outage has no witness. Reusing the visitor's path
  instead of writing a parallel one also means the probe measures **what a visitor
  actually gets**, not a twin road that can drift in silence.
- **It distinguishes the two ways of being degraded**, which is the part that
  matters here: `null` means "I could not read the hub", `stale` means "the hub is
  down and I am serving the last good numbers". The second one looks healthy from
  outside, and is the failure this project keeps removing.

So this section stays as the record of *why* the GitHub-side cadence is what it is
— `smoke.yml` is still throttled and still useful for outages that last — but the
gap it describes is no longer open. **The lesson is the cross-repo one**: a hole
named in one repo can be filled in another, and neither repo notices on its own.
This one sat here as "a decision rather than a task" for the fifteen hours after it
had already been decided and shipped.

## 4. The WAF rule passes two paths now, and closing one again is manual

Since 2026-08-20 the custom rule on `otel.marcobellingeri.dev` lets through
`POST /v1/logs` as well as `POST /v1/metrics`, both only with an `Authorization` header
present (never its value). That is **a change to the reachable surface**, not a config
detail: it was opened for one reason, Loki ingesting Claude Code's log events.

**If Loki ever leaves, `/v1/logs` has to be closed by hand, in the Cloudflare dashboard,
in the same breath.** Nothing in this repository does it and nothing would notice: the
rule lives outside git, and every gate would stay green in front of a route that no
longer serves anybody. An open path nobody needs is exactly the surface this project
spends a WAF rule reducing. `smoke.yml` catches the *opposite* mistake and only that one
— it requires an exact `401` on `/v1/logs`, so closing the route at the edge while the
assertion stands turns the probe red and names the moved surface. Leaving the route open
after the reason for it is gone stays silent. That asymmetry is why this is written down
as a task instead of trusted to a check.

## Still Marco's call

- **`SLACK_WEBHOOK_URL` on the `grafana` Railway service.** One variable, and it is the
  only thing left between "the alerts exist" and "the alerts reach somebody". Since
  2026-08-21 the rules, the contact point and the notification policy are provisioned
  from `docker/grafana/provisioning/alerting/`, and `scripts/prova-allarmi.sh` proves
  the whole chain by making a rule fire on a broken reality and requiring the
  notification to be **delivered** to a local receiver — so what is unproven is not the
  wiring, it is only that Slack's endpoint accepts it.
  **What happens meanwhile is declared, not discovered**: Grafana provisions fine, the
  rules evaluate, and the alerts are visible in its UI behind Access; the send fails and
  says so in Grafana's own logs. The system degrades to "the alarms exist and nobody
  carries them to you". Closing it needs no code.
- ~~Whether to add `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` /
  `STATUS_API_TOKEN` as GitHub secrets.~~ **Decided 2026-08-20: yes**, together with a
  Railway and a Sentry token. `sorveglianza.yml` is the workflow that uses the first
  three — it runs `verify-hub.sh` daily, extending the automatic check past the public
  endpoint to Grafana behind Access and the status API's own bearer. **Until the
  secrets exist the job is red and says so**, naming the missing variable rather than
  reporting an outage: a gate whose credential is missing stays red here, because a
  skipped check reads as green. The Railway and Sentry probes follow once their tokens
  exist — deliberately not written first, so each asserts the payload the vendor really
  returns.
- ~~Whether the hub stays past the first month.~~ **Decided 2026-08-16: it stays.**
  ~~The Railway plan changes on 2026-09-01.~~ **It changed on 2026-08-19** — twelve days
  early, and not by the calendar: the free plan had no volume left to give and the
  resize from 500 MB to 5 GB needed Hobby. Billing is now **on usage**, which is why
  several lines elsewhere in this repo stopped being about CPU and started being about
  money. Measured cost before the change, which is the number that made the decision
  easy: **€2.05 over 12 days**, so about €0.17/day or **~€5/month** for five services —
  roughly half the $10-13/month this project had estimated before anyone had a bill to
  read.

  What the early change removed: the arithmetic about the remaining Trial credit
  running out first, and the question of activating the plan early. What it did **not**
  remove is the silent-ending shape that made the question worth asking — a hub that
  stops for an empty balance produces no error, no red deploy and no failed check, only
  numbers that stop moving. Since 2026-08-20 there is a workspace usage limit ($15 soft
  / $30 hard) standing in front of that, set by hand in the dashboard: **no API this
  project can reach reads the limit back** — measured against Railway's published
  GraphQL collection, which has `usageLimitSet`/`usageLimitRemove` as mutations and no
  matching query. So it is a backstop nobody here can verify. **The balance is a
  different story and the earlier wording was wrong**: `usage`, `estimatedUsage` and
  `projectServiceUsage` are readable queries. Nothing watches the balance because no
  Railway token exists in CI, not because the platform hides it. See the table in
  `SECURITY.md`.
- ~~Grafana Loki as a Phase 1.5.~~ **Closed 2026-08-20: the design pass happened and
  the code is written.** The trigger had fired on 2026-08-19 with the plan change, and
  the pass produced `docs/superpowers/specs/2026-08-20-loki-fase-1.5-design.md` and
  `docs/superpowers/plans/2026-08-20-loki-fase-1.5.md`; what changed about the design
  while it was being built, and what measuring overturned in it, is in the Logs section
  of `DECISIONS.md`. The caveat this entry had carried is untouched and stays closed:
  **nothing log-derived goes on the public site**, the public surface is still exactly
  three aggregate numbers, and Loki has no Tunnel route at all. What is *not* closed is
  the infrastructure, and **all four items closed on 2026-08-21** — the R2 bucket and
  its S3 credentials, the `loki` service, the four `LOKI_R2_*` variables and the volume
  on `/loki`. What is left is one thing the platform cannot do for us: see below.

- **Nothing has been written to the log store yet, and that is the correct state.**
  `OTEL_LOGS_EXPORTER=otlp` is the switch on the *client*, not on the hub
  (`docs/CLAUDE_CODE_TELEMETRY.md`), and until an operator exports it Loki holds one
  object on R2 — `loki_cluster_seed.json`, written at startup — and no log records.
  Worth writing down because the empty store looks identical to a broken one: an
  operator who reads "Loki is up, R2 is empty" will go looking at the storage
  configuration, which is the half that has already been proven.

  Measured at the deploy rather than assumed: Loki reached `"Loki started"` in 957 ms
  with WAL recovery clean, wrote the cluster seed to R2 — so the credentials, the
  schemeless endpoint and `use_thanos_objstore: true` all hold in production — and the
  5 GB volume mounted with `RAILWAY_RUN_UID=0` set from the start, so the
  `permission denied` this list warned about never happened. **The one check that could
  not be made from outside is `up{job="loki"}`**: Prometheus has no public route and
  Grafana sits behind an email policy, so that number is read in Grafana Explore by a
  person, and no script here can assert it.

- **A service with a volume has a short outage on every redeploy** — Railway's own
  reference: it prevents two deployments from mounting the same volume, healthcheck or
  not. So `up{job="loki"}` dips on each deploy of that service. Written here so the
  first person to see the gap stops looking for a fault that is a documented property.

## Closed since the last revision of this file

- **The healthcheck that told the truth about nothing** — Railway ignores the
  Dockerfile `HEALTHCHECK` and probes its own `healthcheckPath`, and neither existed.
  Declaring it on 2026-08-20 (PR #96) made **every deploy fail**, because Railway
  probes the port named by the `PORT` service variable and that variable was not set:
  production stayed six days behind with every gate green. `PORT` was set first (8000
  on status-api, 2000 on cloudflared) and `healthcheckPath` came back after it
  (PR #98). The order is the lesson, and it is now written in `DECISIONS.md` and in
  `CLAUDE.md`. **Three services still have no path**, deliberately — see the note at
  the end of this section.

- **The OTLP ingest was reachable for anything, not just for ingest** — since
  2026-08-20 a Cloudflare WAF custom rule on `otel.marcobellingeri.dev` blocks
  everything except a `POST` to `/v1/metrics` (and, since the same day, `/v1/logs`)
  carrying an `Authorization` header, presence
  only, never the value. It is **defence in depth and not the authentication**: that
  stays `bearertokenauth` inside the Collector, and `smoke.yml` proves it with a
  *wrong* token, the only shape the edge still lets through (PRs #99, #100, #101). The
  rule lives in the Cloudflare dashboard, outside git — the six assertions in
  `smoke.yml` are the only thing that notices if it is switched off. **Since 2026-08-20
  it passes two paths, not one**: `POST /v1/logs` as well, for the Phase 1.5 log
  ingest — see §4 above, and note that only the *opposite* mistake is caught by a
  check.

- **The spend backstop was declared unverified** — a workspace usage limit of $15 soft
  / $30 hard has existed since 2026-08-20 (PR #99). It is confirmed by the operator and
  **not readable from here**: Railway's MCP surface does not expose billing, so no tool
  in this repository can re-read it and no gate notices if it disappears. Same class as
  `PORT`. This is not a rate limiter — the rate limiter is the site Worker's, per IP,
  60 requests / 60s, shipped 2026-08-16 and measured against production 2026-08-20.

- **The Sentry rule that notified once in six days** — the alert fired on the first
  event of a process and then went quiet, which is how a Prometheus compaction failure
  ran once a minute from 2026-08-13 to 2026-08-19 unseen (PR #86). The hourly throttle
  in the code was never the problem: the rule triggered on *priority transitions*, so
  four events produced one notification. Corrected 2026-08-20 with an `Every event`
  trigger beside it.

- **Two HIGH CVEs in the status-api base image that neither lever of the gate could
  reach** — `msgpack` and `pkg_resources`/setuptools live inside `pip/_vendor/`, so
  recompiling the lockfile did not move them and neither did bumping the Debian tag.
  pip is not needed at runtime (the image runs uvicorn), so it is uninstalled in the
  same `RUN` as the install — including the second copy under `ensurepip`, which Trivy
  reads and the first attempt left behind (PR #97). Measured: blocking library scan
  from 2 HIGH to zero.

- **Whether `docker/otel-collector-config.yaml` stays in the otel-collector's
  `watchPatterns`** — it stays, and the question dissolved rather than being decided
  (PR #67, 2026-08-14). It was only ever a question because a Collector restart
  dropped already-closed sessions from the three public numbers (measured on PR #29:
  3 sessions/9.1M tokens became 2/8.7M). The queries now read
  `max_over_time(...[25h])` and survive a restart — measured before and after on the
  same TSDB — so redeploying the Collector costs nothing that needs avoiding. The
  trade is written in `DECISIONS.md`: Prometheus' disk is now the only copy of the
  day. Both halves are held together by a step in the `compose` job, because they
  live in different services and no test suite sees them together.

- **Where the widget goes on the site page** — settled 2026-07-30 (site PR #160):
  a card under Projects with an `I NUMERI, LIVE →` chip pointing at
  `/{lang}/agentic-os`. Its degraded path improved again on 2026-08-13 (site
  PR #202): an outage now shows the last good numbers, labelled with their age,
  instead of three dashes.
- **The Prometheus volume check** — capping retention by size as well as time
  (PR #51) was necessary and not sufficient: the cap was `300MB` on a 500 MB volume
  and the volume filled anyway, with compaction failing once a minute from
  2026-08-13 to 2026-08-19 — six days, not hours, and invisible for all but the
  first because the alert fired once per process. Closed on 2026-08-19 by the plan
  change (volume 500 MB → 5 GB) and re-tuned to `30d` / `3GB`. The arithmetic, and
  what measuring corrected about it, is in `DECISIONS.md`.

## Deliberately still open, and why

- ~~Three of five services have no `healthcheckPath`.~~ **Decided 2026-08-20: they do
  not get one, and this is no longer an open item.** Railway wants a literal 200 with
  no credentials, so Prometheus (`/-/healthy`) and Grafana (`/api/health`) would each
  have to expose an *unauthenticated* route — and Grafana's hostname is public, behind
  Access. The Collector's only route is the ingest, which authenticates and answers
  `401`; giving it one means the `health_check` extension **and a new port on the one
  service that accepts writes from the internet**. Against that, a Prometheus `up`
  scrape watches each of them every 15s **for as long as they run**, which is more
  than a healthcheck does — Railway stops probing once a deploy is promoted. **The
  premise had a hole until 2026-08-20**: Grafana was never scraped, and the Loki job did
  not exist. Both were added rather than rewriting the decision they hold up — six jobs
  in `docker/prometheus.yml`, covering every service except `status-api`, which is the
  one that already has both a `healthcheckPath` and an outside witness in `smoke.yml`. Full
  reasoning in `DECISIONS.md`. If it is ever reopened, `PORT` goes first.

- ~~The dry run in `LOCAL_DRY_RUN.md` has no trigger.~~ **It has one since
  2026-08-20**: `telemetry-baseline.yml` asks the npm registry weekly what version
  `@anthropic-ai/claude-code` is on and compares its **minor** against
  `docs/telemetry-baseline.json`, the record of the run that last measured the labels.
  A new minor turns the run red with instructions; patches do not, because seven of
  them moved nothing between v2.1.220 and v2.1.227. **Read what this gate is
  carefully**: it does not see what a new release sends — no CI gate here can, that
  still takes the five-minute dry run on a laptop with the real client. It sees only
  that a release happened, which is the part that used to depend on somebody
  remembering. The baseline file is the record of a measurement: it gets updated after
  the dry run, never to turn the job green.
