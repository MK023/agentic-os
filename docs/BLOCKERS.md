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

- Whether `docker/otel-collector-config.yaml` stays in the otel-collector service's
  `watchPatterns`: today every merge touching it redeploys the Collector, and a
  Collector restart drops already-closed sessions from the three public numbers
  (measured on PR #29: 3 sessions/9.1M tokens became 2/8.7M). The alternative is
  removing it from the patterns and redeploying by hand. Raised in PR #27, which
  was closed for unrelated reasons — the question never landed in a doc until now.
- Whether to add `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` /
  `STATUS_API_TOKEN` as GitHub secrets, which is what `verify-hub.sh` needs to run
  in CI instead of by hand. It would extend the automatic check past the public
  endpoint to Grafana behind Access and the status API's own bearer.
- Whether the hub stays past the first month.
- Grafana Loki as a Phase 1.5, with its own spec — evaluated and recommended, but
  after a week of actually using Phase 1 (see `DECISIONS.md`).

## Closed since the last revision of this file

- **Where the widget goes on the site page** — settled 2026-07-30 (site PR #160):
  a card under Projects with an `I NUMERI, LIVE →` chip pointing at
  `/{lang}/agentic-os`. Its degraded path improved again on 2026-08-13 (site
  PR #202): an outage now shows the last good numbers, labelled with their age,
  instead of three dashes.
- **The Prometheus volume check** — closed by capping retention by size as well as
  time (PR #51), after the volume filled and compaction failed for hours. The
  arithmetic behind `300MB` is in `DECISIONS.md`.
