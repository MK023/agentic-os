# What is not done yet

Current as of 2026-08-13, **tagged v1.0.0**: the five services run, the Tunnel
serves its three hostnames, the public endpoint answers with real numbers, and
`smoke.yml` watches it from outside. Everything settled lives in `DECISIONS.md`;
everything verified lives in the commit that verified it. This file is only what
is still open.

**No engineering task blocks anything.** What follows is one thing to watch, one
thing nobody downstream can fix, and a handful of judgement calls.

## 1. Claude Code's metric names, against a future version

The names are measured, not assumed — but this telemetry is beta and versioned. If a
panel goes empty after a Claude Code upgrade, that is the first thing to check, and
`LOCAL_DRY_RUN.md` is how to check it in five minutes. The same dry run is also the
only check the label allow-list gets against a *new* client version: no CI gate sees
what a future release starts sending.

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

`grafana/grafana:13.1.3` carries 15 HIGH vulnerabilities that *do* have fixes —
in the upstream Go modules it vendors (`tempo`, Go stdlib, `x/net`, `x/text`),
not in any Grafana release that can be installed. The `images` job prints them
without failing, deliberately: a gate that cannot go green becomes a
`continue-on-error` within a month.

Exposure is low — Grafana has no platform domain and sits behind Cloudflare
Access with a single-email policy. **The action is to watch, not to work**: when
Dependabot proposes a newer `grafana/grafana`, check whether the count drops in
the job log. If it is still stuck in a few weeks, an upstream issue is worth
opening.

**Triaged 2026-08-14 by the criteria the model actually asks for — exploitability,
not existence.** "15 HIGH" is a count, and a count is not a risk assessment:

- **CISA KEV: none of the eleven.** Catalogue of 1665 entries, released
  2026-08-11. Nothing here is being exploited in the wild.
- **EPSS: the worst is 0.0078** — CVE-2026-33814, a 0.78% chance of exploitation
  in 30 days, 52nd percentile. Every other one is lower, down to 0.0016. This is
  the ordinary background of open-source CVEs, not a signal.
- **Reachability: the two `tempo` CVEs have no configured code path here.** The
  only provisioned datasource is Prometheus (`docker/grafana/provisioning/
  datasources/prometheus.yml`); there is no Tempo datasource and no S3 anywhere in
  `docker/grafana/`. They ship inside the binary as vendored modules and nothing
  calls them.
- **`13.1.3` is the newest release** (checked against the upstream release list on
  2026-08-14; it shipped 2026-08-07). So "wait for Dependabot" is currently waiting
  for something that does not exist yet — the absence of a bump is not neglect.

So the watch is not just cheap, it is **correct by the same criteria that would
have made it blocking**. What would change the posture is a **KEV entry**, not a
higher count — and the action then would not be "upgrade", since there would still
be no release to upgrade to. It would be to narrow or switch off the exposure:
Grafana is a convenience here, and the three public numbers do not pass through
it. That is worth knowing *before* the day it matters, because a decision that has
already been made is much cheaper at 3am.

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

## Still Marco's call

- Whether to add `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` /
  `STATUS_API_TOKEN` as GitHub secrets, which is what `verify-hub.sh` needs to run
  in CI instead of by hand. It would extend the automatic check past the public
  endpoint to Grafana behind Access and the status API's own bearer.
- ~~Whether the hub stays past the first month.~~ **Decided 2026-08-16: it stays**, and
  the Railway plan changes on **2026-09-01**. Measured cost, which is the number that made
  the decision easy: **€2.05 over 12 days**, so about €0.17/day or **~€5/month** for five
  services. That is roughly half the $10-13/month this project had estimated, and the
  estimate is what was written down before anyone had a bill to read.

  The arithmetic to 2026-09-01 is about €2.70 more at that rate, which is close enough to
  the remaining one-off Trial credit that it could run out first. **Marco's answer,
  2026-08-16: he activates the plan early if it does.** Worth having asked, because this
  is the silent kind of ending — a hub that stops for an empty balance produces no error,
  no red deploy and no failed check, only numbers that stop moving. The balance lives in
  the Railway dashboard; the API this project can reach does not expose it, so no gate
  here can watch it.
- Grafana Loki as a Phase 1.5 — **not now, and no longer waiting on a date.** The
  criterion was applied on 2026-08-14 after sixteen days of real use: the questions Phase
  1 actually left unanswered were about the hub's own containers, and a targeted metric
  plus a Sentry event answered each of them for the price of a query. It reopens on
  either of two triggers, whichever comes first: a question that recurs **twice** and no
  metric or Sentry event can answer, or **a paid Railway plan** — the free-credit budget
  and the deploy queue are what make a sixth service expensive, and those belong to the
  plan, not to Loki. Full reasoning, including why "richer data on the public site" is a
  design question rather than a free upgrade, in `DECISIONS.md`.

  **The second trigger now has a date: the plan changes on 2026-09-01** (decided
  2026-08-16). That is the trigger firing, not a date-based deferral coming back in
  through the window — the condition is still "a paid plan", it simply has a calendar
  now. So Loki moves from "not now" to "due for its design pass", and that pass starts
  from the caveat already written down: the public surface stays three aggregate numbers,
  so the question to answer first is *which log-derived aggregate can be public without
  becoming content*.

## Closed since the last revision of this file

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
