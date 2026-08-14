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

## 3. The smoke probe cannot see a short outage

`smoke.yml` asks for `*/10` and gets, measured over its first twelve runs, an
interval between 38 and 93 minutes (median ~55): GitHub throttles frequent
schedules on public repositories. It reliably catches an outage that *lasts* — a
deploy that does not serve, a full volume, a Tunnel down — and will usually miss
a two-minute restart like the two on 2026-08-13.

Closing that needs a prober that does not run on GitHub Actions. The obvious
candidate is a Cron Trigger on the site's Worker, which already sits at the edge
with the Sentry channel wired. That is new infrastructure, so it is a decision
rather than a task.

## Still Marco's call

- Whether to add `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` /
  `STATUS_API_TOKEN` as GitHub secrets, which is what `verify-hub.sh` needs to run
  in CI instead of by hand. It would extend the automatic check past the public
  endpoint to Grafana behind Access and the status API's own bearer.
- Whether the hub stays past the first month.
- Grafana Loki as a Phase 1.5 — **not now, and no longer waiting on a date.** The
  criterion was applied on 2026-08-14 after sixteen days of real use: the questions Phase
  1 actually left unanswered were about the hub's own containers, and a targeted metric
  plus a Sentry event answered each of them for the price of a query. It reopens on
  either of two triggers, whichever comes first: a question that recurs **twice** and no
  metric or Sentry event can answer, or **a paid Railway plan** — the free-credit budget
  and the deploy queue are what make a sixth service expensive, and those belong to the
  plan, not to Loki. Full reasoning, including why "richer data on the public site" is a
  design question rather than a free upgrade, in `DECISIONS.md`.

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
- **The Prometheus volume check** — closed by capping retention by size as well as
  time (PR #51), after the volume filled and compaction failed for hours. The
  arithmetic behind `300MB` is in `DECISIONS.md`.
