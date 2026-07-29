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
| `grafana.yourdomain.com` | `http://grafana.railway.internal:3000` |
| `status.yourdomain.com` | `http://status-api.railway.internal:8000` |
| `otel.yourdomain.com` | `http://otel-collector.railway.internal:4318` |

None of these services takes a public Railway domain: the tunnel is the only way in,
so there is no platform hostname sitting unprotected beside it.

Prometheus deliberately gets **no** hostname: its HTTP API has no authentication
of its own, so its only safe exposure is the project's private network.

## 3. Two Access applications

Zero Trust → **Access → Applications**:

- **Grafana** on `grafana.yourdomain.com` — policy: your own email only
  (interactive login).
- **Status API** on `status.yourdomain.com` — policy action **Service Auth**
  (anything else makes Access prompt for an identity-provider login, which a
  Worker cannot complete), with a Service Token issued for it. The generated
  Client ID/Secret are what marcobellingeri.dev's Worker sends as the
  `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers (Task 7).

An Access application with **no** policy is reachable by anyone authenticated in
the org — check the policy explicitly on both apps after creating them.

## 4. Why `otel.yourdomain.com` has no Access application

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

## 5. Verify

```bash
bash scripts/verify-hub.sh https://grafana.yourdomain.com https://status.yourdomain.com "$STATUS_API_TOKEN"
```

Note that the status check goes through Access, so it only passes from a context
that carries valid Access credentials — from your laptop, expect the Access login
redirect rather than a 200.
