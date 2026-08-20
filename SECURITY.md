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

The hub runs five services on Railway. **None of them has a public platform
domain.** The only ingress is a Cloudflare Tunnel with three hostnames:

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
  Collector**.

A public hostname is not an access control. This follows the CVE-2026-28798
pattern and is the reason this project's design starts from authentication at
the ingestion layer.

Prometheus never gets a hostname of its own. Its HTTP API has no authentication.

The public surface of the whole system is three aggregate numbers (sessions,
tokens, cost). No session content, no free-form PromQL, and no path from the
public widget to anything else.

**Aggregated over metrics *and* over time.** The three numbers alone were only half
the statement: sampled without limit they are also a presence feed — the second at
which a session starts, how intense it is, which model is running (the cost/token
ratio moves with the model mix). Since 2026-08-19 `/status` serves from a 60-second
cache, so the origin computes one pass per minute whatever the incoming rate, and
the resolution of that side channel is capped at the same minute. The endpoint is
still unthrottled (see `CLAUDE.md`); the cache bounds what unlimited sampling buys,
it does not throttle the caller.

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
