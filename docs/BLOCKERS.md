# What is not done yet

Current as of 2026-07-29, **post go-live**: the Railway project exists, the Tunnel
serves its three hostnames, the Worker secrets are set, and the public endpoint
answers with real numbers. Everything settled lives in `DECISIONS.md`; everything
verified lives in the commit that verified it. This file is only what is still open.

## 1. The Prometheus volume, a day after go-live

The volume reported `0.0 MB` right after the first successful mount — consistent
with a TSDB that had just started (a few KB of WAL round to zero), but worth a
second look rather than an assumption:

```bash
railway volume list --json | python3 -c "import sys,json;v=json.load(sys.stdin)['volumes'][0];print(v['currentSizeMB'],'MB')"
```

If it is still exactly `0.0` after a day of real traffic, the data is not landing on
the volume and the retention promise is empty. The log line that settles it is
`filesystem information`: `EXT4_SUPER_MAGIC` is the volume, `OVERLAYFS_SUPER_MAGIC`
is ephemeral storage.

## 2. Claude Code's metric names, against a future version

The names are measured, not assumed — but this telemetry is beta and versioned. If a
panel goes empty after a Claude Code upgrade, that is the first thing to check, and
`LOCAL_DRY_RUN.md` is how to check it in five minutes. The same dry run is also the
only check the label allow-list gets against a *new* client version: no CI gate sees
what a future release starts sending.

## Still Marco's call

- **Whether `docker/otel-collector-config.yaml` stays in the otel-collector
  service's `watchPatterns`.** As it stands, any change to the file — a comment
  included — redeploys the collector in production, and a collector restart has a
  measured price (see "collector redeploy" in `DECISIONS.md`: closed sessions drop
  out of "today", a live client can go silent). The alternative is removing it from
  the patterns and redeploying by hand when the config really changes — trading an
  automatic deploy for a deliberate one.
- Where the widget goes on the site page (the endpoint is live and returns real
  numbers; the degraded path stays tested).
- Whether the hub stays past the first month.
- Grafana Loki as a Phase 1.5, with its own spec — evaluated and recommended, but
  after a week of actually using Phase 1 (see `DECISIONS.md`).
