# Cloudflare Tunnel setup (one-time, manual)

Manual work against the real Cloudflare account — not something this repo's CI
drives. Run it once the five Railway services are deployed and running.

The compose file runs `cloudflared tunnel run` with a `TUNNEL_TOKEN`, which means
a **remotely-managed** tunnel: it is created in the dashboard, and its routes
live in the dashboard too. (A tunnel created with `cloudflared tunnel create`
instead is *locally* managed — it gets a credentials file and a local
`config.yml` ingress, and its hostnames cannot be configured from the dashboard.
Don't mix the two models.)

## 1. Create the tunnel

Cloudflare dashboard → **Networking → Tunnels → Create a tunnel** → name it
`agentic-os-hub`. Copy the connector token from the installation command it
shows; that value becomes the `cloudflared` service's `TUNNEL_TOKEN` variable
(see `docker/.env.example`). It is a credential — treat it like a password.

## 2. Add three public hostnames

In the tunnel's **Routes** tab, add a published-application route per hostname,
pointing at Railway's internal DNS name (not `localhost` — `cloudflared` is its own
service, reaching the others over the project's private network):

| Hostname | Service |
|---|---|
| `grafana.marcobellingeri.dev` | `http://grafana.railway.internal:3000` |
| `status.marcobellingeri.dev` | `http://status-api.railway.internal:8000` |
| `otel.marcobellingeri.dev` | `http://otel-collector.railway.internal:4318` |

None of these services takes a public Railway domain: the tunnel is the only way in,
so there is no platform hostname sitting unprotected beside it.

Prometheus deliberately gets **no** hostname: its HTTP API is not configured with
authentication (it supports it — see `SECURITY.md` — it is simply not turned on)
of its own, so its only safe exposure is the project's private network.

## 3. Two Access applications

Zero Trust → **Access → Applications**:

- **Grafana** on `grafana.marcobellingeri.dev` — policy: your own email only
  (interactive login).
- **Status API** on `status.marcobellingeri.dev` — policy action **Service Auth**
  (anything else makes Access prompt for an identity-provider login, which a
  Worker cannot complete), with a Service Token issued for it. The generated
  Client ID/Secret are what marcobellingeri.dev's Worker sends as the
  `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers (Task 7).

An Access application with **no** policy is reachable by anyone authenticated in
the org — check the policy explicitly on both apps after creating them.

## 4. Why the hostnames are written down here

They are subdomains of a site that is already public, and naming them costs nothing
this design ever relied on: the OTLP endpoint authenticates inside the Collector, the
other two sit behind Cloudflare Access. If knowing a hostname were enough to reach
any of them, that would be the finding — not the fact that it is written in a repo.
This is the same reasoning that made us reject "the hostname is hard to guess" as an
access control in the first place.

## 5. Why `otel.marcobellingeri.dev` has no Access application

Claude Code's OTLP exporter cannot send the Access service-token headers, so an
Access app in front of it would simply break ingestion. That does **not** make
the endpoint unauthenticated: a public Tunnel hostname with no auth of its own is
exactly the CVE-2026-28798 pattern (ZimaOS). Authentication here lives inside the
Collector — the `bearertokenauth` extension in
`docker/otel-collector-config.yaml`. Generate the token once:

```bash
openssl rand -hex 32
```

and put the same value in `docker/.env` as `OTLP_INGEST_TOKEN` and in Claude
Code's `OTEL_EXPORTER_OTLP_HEADERS` (see `docs/CLAUDE_CODE_TELEMETRY.md`). A
request without a matching bearer token is rejected by the Collector before it
reaches any pipeline.

## 6. Verify

```bash
export CF_ACCESS_CLIENT_ID=...      # the Service Token from step 3
export CF_ACCESS_CLIENT_SECRET=...
bash scripts/verify-hub.sh https://grafana.marcobellingeri.dev https://status.marcobellingeri.dev "$STATUS_API_TOKEN"
```

The script knows it is running behind Access and checks each hostname for what it
should actually do:

- **Grafana** must answer with an Access challenge (302/401/403). A plain `200`
  there is treated as a **failure**, not a success — it would mean the dashboard is
  reachable without logging in.
- **the status API** must return the three fields, using both credentials at once:
  the Access service token *and* its own bearer token. That is exactly the pair the
  site's Worker sends, so a pass here means the Worker will work too.

Without the two `CF_ACCESS_*` variables the status check fails on purpose, and says
why.
