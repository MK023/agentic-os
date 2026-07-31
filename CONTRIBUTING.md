# Contributing

This is a personal operational project, not a product looking for contributors,
but the rules below are what keeps it reviewable, so they apply to anyone
touching it, including future me.

## The loop

Branch, PR, green gates, merge. Never straight to `main`.

Every gate blocks: `compose`, `images`, `image-users`, `status-api-tests`,
`checkov`, `workflow-lint`, `gitleaks`, `dependency-audit`, `dependency-review`,
`sonar`. Nothing runs with `continue-on-error` — a gate either blocks or is
removed on purpose.

A failing gate is never re-run until green. Either it found something, or it is
flaky and gets fixed (see the flaky policy in the README).

## Before writing code

Read `docs/DECISIONS.md`. It lists what is already settled and why, with what
measuring changed about each one. Reopening a closed decision needs new evidence,
not a new opinion.

## The house rule

Three layers of verification, each catching what the previous one cannot:

1. **Re-reading** the plan found 5 problems.
2. **Running** the tools found 12 more.
3. **Measuring the real behaviour** found what no documentation states, a real
   email address arriving as a Prometheus label, and the obvious fix for it
   silently losing data.

`docs/LOCAL_DRY_RUN.md` is layer 3 made repeatable: the whole stack on a laptop,
fed by the real client. Use it whenever a producer, an image or a Claude Code
version changes.

## Configuration lives in one place

Every configuration file has exactly one copy, in `docker/`. The Railway images
copy them at build time; the local compose file mounts them and gives each
container a network alias equal to its Railway internal DNS name. Never fork a
config "just for local".

## Secrets

Never in git. Railway service variables in production, Doppler as the source of
truth, `docker/.env` with fake values locally. `gitleaks` runs on every PR over
full history, and a pre-commit hook catches it earlier, enable it with:

```bash
git config core.hooksPath .githooks
```
