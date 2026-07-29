# What is not done yet

Current as of 2026-07-29, kept short on purpose: everything already settled lives in
`DECISIONS.md`, and everything already verified lives in the commit that verified it.
This file is only what stands between the repository and a working hub.

## 1. The Railway project does not exist

Five services to create, with the Root Directory, config path, variables and volume
listed per service in `../railway/README.md`. Starting on the Trial plan: $5 of
one-time credit, which is roughly a fortnight at this footprint, and volumes capped
at 0.5 GB. Nothing about the first deploy needs Hobby.

## 2. The Cloudflare Tunnel and the two Access applications

Manual, against the account that already exists, in `CLOUDFLARE_TUNNEL_SETUP.md`.
Needs the services deployed first, since the ingress points at their internal DNS
names. This was always a human step and still is.

## 3. The site's Worker secrets

`wrangler secret put` for `AGENTIC_OS_ACCESS_CLIENT_ID`,
`AGENTIC_OS_ACCESS_CLIENT_SECRET` and `AGENTIC_OS_STATUS_TOKEN`, plus
`AGENTIC_OS_STATUS_URL` as a plain variable. All three values only exist after step
2. Until then `/api/agentic-status` answers with three `null` fields and the widget
shows dashes — a tested path, not a broken one.

## 4. Three things only the first deploy can settle

Written out in `../railway/README.md` rather than left to be discovered from an empty
dashboard: whether Prometheus can write its volume as `nobody`, whether a stale
dashboard `startCommand` overrides the image's, and the fact that Railway ignores the
Dockerfile `HEALTHCHECK` — which is why `scripts/verify-hub.sh` stays the real
post-deploy check.

## 5. Claude Code's metric names, against a future version

The names are measured, not assumed — but this telemetry is beta and versioned. If a
panel goes empty after a Claude Code upgrade, that is the first thing to check, and
`LOCAL_DRY_RUN.md` is how to check it in five minutes.

## Still Marco's call

- Where the widget goes on the site page (the endpoint is live and degrades cleanly).
- Whether the hub stays past the first month.
- Grafana Loki as a Phase 1.5, with its own spec — evaluated and recommended, but
  after a week of actually using Phase 1 (see `DECISIONS.md`).
