# What is not done yet

Current as of 2026-07-30, **post go-live**: the Railway project exists, the Tunnel
serves its three hostnames, the Worker secrets are set, and the public endpoint
answers with real numbers. Everything settled lives in `DECISIONS.md`; everything
verified lives in the commit that verified it. This file is only what is still open.

## 1. Claude Code's metric names, against a future version

The names are measured, not assumed — but this telemetry is beta and versioned. If a
panel goes empty after a Claude Code upgrade, that is the first thing to check, and
`LOCAL_DRY_RUN.md` is how to check it in five minutes. The same dry run is also the
only check the label allow-list gets against a *new* client version: no CI gate sees
what a future release starts sending.

## Still Marco's call

- Whether `docker/otel-collector-config.yaml` stays in the otel-collector service's
  `watchPatterns`: today every merge touching it redeploys the Collector, and a
  Collector restart drops already-closed sessions from the three public numbers
  (measured on PR #29: 3 sessions/9.1M tokens became 2/8.7M). The alternative is
  removing it from the patterns and redeploying by hand. Raised in PR #27, which
  was closed for unrelated reasons — the question never landed in a doc until now.
- Where the widget goes on the site page (the endpoint is live and returns real
  numbers; the degraded path stays tested).
- Whether the hub stays past the first month.
- Grafana Loki as a Phase 1.5, with its own spec — evaluated and recommended, but
  after a week of actually using Phase 1 (see `DECISIONS.md`).
