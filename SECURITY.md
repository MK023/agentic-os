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

The hub runs five services on Railway; **none of them takes a public platform
domain**. The only ingress is a Cloudflare Tunnel with three hostnames:

- `grafana.` — Cloudflare Access, single-email policy
- `status.` — Cloudflare Access with Service Auth, plus the application's own
  bearer token. Two independent layers
- `otel.` — no Access application, because Claude Code cannot send Access
  headers. Authentication is the `bearertokenauth` extension **inside** the OTel
  Collector. A public hostname is not an access control: that is the
  CVE-2026-28798 pattern, and it is the reason this project's design starts
  where it does

Prometheus never gets a hostname of its own — its HTTP API has no authentication.

The public surface of the whole system is three aggregate numbers (sessions,
tokens, cost). No session content, no free-form PromQL, no path from the public
widget to anything else.

## Data handling

Metric labels are an **allow-list** enforced in the Collector. Claude Code sends
identity — `user.email` with a real address, `user.id`, `user.account_id`,
`user.account_uuid`, `organization.id` — as *data point* attributes, so
`resource_to_telemetry_conversion: false` does not keep them out. This was
measured against the real client, not assumed. Everything not on the list is
dropped before storage, including attributes no version emits yet.

The reasoning behind every security decision, with what measuring changed about
it, is in [docs/DECISIONS.md](docs/DECISIONS.md).
