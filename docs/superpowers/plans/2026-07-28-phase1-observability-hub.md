# Agentic OS Phase 1 — Claude Code Observability Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a Hostinger VPS (Terraform-provisioned) running OTel Collector +
Prometheus + Grafana behind a Cloudflare Tunnel, so Claude Code usage is visible live
in a private dashboard and as a sanitized public widget on marcobellingeri.dev.

**Architecture:** Docker Compose stack on a single VPS, zero Kubernetes. Ingestion is
OTLP-generic (Collector) so future producers can attach without redesign. A separate
minimal FastAPI service exposes a whitelisted, non-sensitive subset of metrics for
public consumption; the private Grafana dashboard is never exposed publicly.

**Tech Stack:** Terraform (`hostinger/hostinger` provider), Docker Compose,
`otel/opentelemetry-collector-contrib`, Prometheus, Grafana, `cloudflared`, Python
3.12 + FastAPI (status API), Astro/Cloudflare Workers (marcobellingeri.dev widget,
separate repo).

**Spec:** `docs/superpowers/specs/2026-07-28-agentic-os-phase1-design.md`

---

## Before you start

**Status as of 2026-07-28:** Cloudflare account exists already. Hostinger account
does not exist yet. Marco's target start date for real execution is **after
2026-08-10**. This plan was written in full now so it's ready to run when that
window opens — writing/validating code does not require the Hostinger account to
exist yet; only two things do (flagged inline where they occur):

- Task 1, Step 6 (look up real `data_center_id`/`template_id`) — needs a live
  Hostinger account + API token to query the provider's data sources. Everything
  else in Task 1 (the `.tf` files themselves) can be written and `terraform
  validate`-checked without it.
- `terraform apply` (actual VPS creation) and `cloudflared tunnel create` (actual
  tunnel/DNS routes) — real infrastructure, deliberately not part of this plan's
  tasks (see "Explicitly not in this plan" at the end). These happen whenever
  Marco is ready, independent of when the code/tests were written.

You need, from Marco, before Task 1 can fully close out (not before it can start):
- A Hostinger account with an API token (`HOSTINGER_API_TOKEN`) — not yet open.
- A Cloudflare account with a zone already on Cloudflare (for the Tunnel) and a
  `CLOUDFLARE_API_TOKEN` with Tunnel edit permission — **already have this one**.
- An SSH keypair to use for the VPS (path to the public key).

Tasks 2, 3, 4, 6, 8 need none of the above to be written and tested. Task 5 is
documentation of manual steps Marco runs later, against the Cloudflare account he
already has, whenever he chooses — not gated by Hostinger. Task 7 (site widget)
needs no live secrets to write/test; only `wrangler secret put` (Step 5) waits for
a real Cloudflare Access service token, which doesn't exist until Task 5 is run
for real.

---

## Task 1: Terraform module — provision the VPS

**Files:**
- Create: `terraform/hostinger-vps/versions.tf`
- Create: `terraform/hostinger-vps/variables.tf`
- Create: `terraform/hostinger-vps/main.tf`
- Create: `terraform/hostinger-vps/outputs.tf`
- Create: `terraform/hostinger-vps/terraform.tfvars.example`

- [ ] **Step 1: Write `versions.tf`**

```hcl
terraform {
  required_version = ">= 1.7"

  required_providers {
    hostinger = {
      source  = "hostinger/hostinger"
      version = "~> 0.1"
    }
  }
}

provider "hostinger" {
  # Reads HOSTINGER_API_TOKEN from the environment; do not hardcode the token here.
}
```

- [ ] **Step 2: Write `variables.tf`**

```hcl
variable "vps_plan" {
  type        = string
  description = "Hostinger VPS plan identifier (see: terraform console -> data.hostinger_vps_plans.all)"
  default     = "hostingercom-vps-kvm2-usd-1m"
}

variable "data_center_id" {
  type        = number
  description = "Hostinger data center ID. Pick an EU location (data residency). Look up via data.hostinger_vps_data_centers.all before setting."
}

variable "template_id" {
  type        = number
  description = "OS template ID for 'Ubuntu 24.04 with Docker'. Look up via data.hostinger_vps_templates.all before setting."
}

variable "ssh_public_key_path" {
  type        = string
  description = "Path to the local SSH public key to attach to the VPS"
  default     = "~/.ssh/id_ed25519.pub"
}
```

- [ ] **Step 3: Write `main.tf`**

```hcl
data "hostinger_vps_data_centers" "all" {}
data "hostinger_vps_templates" "all" {}

resource "hostinger_vps_ssh_key" "agentic_os" {
  name = "agentic-os-hub"
  key  = file(var.ssh_public_key_path)
}

resource "hostinger_vps_post_install_script" "docker_bootstrap" {
  name    = "agentic-os-bootstrap"
  content = file("${path.module}/../../scripts/bootstrap.sh")
}

resource "hostinger_vps" "hub" {
  plan                   = var.vps_plan
  data_center_id         = var.data_center_id
  template_id            = var.template_id
  hostname               = "agentic-os-hub.local"
  ssh_key_ids            = [hostinger_vps_ssh_key.agentic_os.id]
  post_install_script_id = hostinger_vps_post_install_script.docker_bootstrap.id
}
```

**Verified 2026-07-28 by actually running this against the real
`hostinger/hostinger` v0.1.22 provider** (not just read): two real bugs found
and fixed here. (1) The `=` alignment above failed `terraform fmt -check`
(exit 3) — the plan originally promised "no formatting diffs", which was
false as written; the block above is the corrected, `terraform fmt`-clean
version. (2) `terraform validate` **rejected** a bare `"agentic-os-hub"`
hostname with `invalid value for hostname (must be a valid FQDN)` — the
provider enforces dot-containing FQDN shape even though this hostname never
needs to resolve on public DNS. `agentic-os-hub.local` passes; any
dotted value does, this one was picked as a generic default. `terraform
init` (against the real registry) + `fmt -check` + `validate` all pass
clean on this exact code as of today.

- [ ] **Step 4: Write `outputs.tf`**

```hcl
output "vps_ip" {
  value       = hostinger_vps.hub.ipv4_address
  description = "Public IPv4 of the hub VPS. Used only for the initial SSH bootstrap check — no service is exposed directly on this IP."
}
```

- [ ] **Step 5: Write `terraform.tfvars.example`**

```hcl
data_center_id = 0  # REPLACE: run `terraform console`, then `data.hostinger_vps_data_centers.all`, pick an EU entry's id
template_id    = 0  # REPLACE: same console, `data.hostinger_vps_templates.all`, pick the Ubuntu 24.04 + Docker entry's id
```

- [ ] **Step 6: Look up real data center and template IDs**

Run:
```bash
cd terraform/hostinger-vps
terraform init
terraform console
```
At the console prompt, run `data.hostinger_vps_data_centers.all` and
`data.hostinger_vps_templates.all`, note the numeric `id` for an EU data center and
for the "Ubuntu 24.04 with Docker" template, then `exit`. Copy
`terraform.tfvars.example` to `terraform.tfvars` and fill in the two real IDs.

- [ ] **Step 7: Validate**

Run: `terraform fmt -check && terraform validate`
Expected: no formatting diffs, `Success! The configuration is valid.`

- [ ] **Step 8: Commit**

```bash
git add terraform/hostinger-vps/
git commit -m "feat(terraform): add Hostinger VPS provisioning module"
```

---

## Task 2: Docker Compose stack — OTel Collector, Prometheus, Grafana

**Files:**
- Create: `docker/docker-compose.yml`
- Create: `docker/otel-collector-config.yaml`
- Create: `docker/prometheus.yml`
- Create: `docker/grafana/provisioning/datasources/prometheus.yml`
- Create: `docker/grafana/provisioning/dashboards/dashboards.yml`
- Create: `docker/grafana/provisioning/dashboards/claude-code.json`

- [ ] **Step 1: Write `docker/otel-collector-config.yaml`**

The `otlp` receiver on a Docker network with no external route would be safe as-is,
but Task 5 puts a Cloudflare Tunnel hostname in front of it — an unauthenticated
ingest endpoint behind a public tunnel hostname is exactly the pattern behind
CVE-2026-28798 (ZimaOS: unauthenticated internal endpoint exposed via Cloudflare
Tunnel → SSRF into the internal network). The `bearertokenauth` extension closes
this: only requests carrying the configured token are accepted.

```yaml
extensions:
  bearertokenauth:
    scheme: "Bearer"
    tokens:
      - "${env:OTLP_INGEST_TOKEN}"

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
        auth:
          authenticator: bearertokenauth
      http:
        endpoint: 0.0.0.0:4318
        auth:
          authenticator: bearertokenauth

processors:
  batch: {}

exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
    resource_to_telemetry_conversion:
      enabled: true

service:
  extensions: [bearertokenauth]
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
```

- [ ] **Step 2: Write `docker/prometheus.yml`**

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: otel-collector
    static_configs:
      - targets: ["otel-collector:8889"]
```

- [ ] **Step 3: Write `docker/grafana/provisioning/datasources/prometheus.yml`**

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

- [ ] **Step 4: Write `docker/grafana/provisioning/dashboards/dashboards.yml`**

```yaml
apiVersion: 1

providers:
  - name: agentic-os
    folder: ""
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards
```

- [ ] **Step 5: Write `docker/grafana/provisioning/dashboards/claude-code.json`**

```json
{
  "title": "Claude Code — Live Usage",
  "timezone": "browser",
  "refresh": "10s",
  "panels": [
    {
      "id": 1,
      "title": "Sessions (rate, 5m)",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "targets": [
        { "expr": "sum(rate(claude_code_session_count_total[5m]))", "refId": "A" }
      ]
    },
    {
      "id": 2,
      "title": "Tokens in/out (rate, 5m)",
      "type": "timeseries",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "targets": [
        { "expr": "sum(rate(claude_code_token_usage_total[5m])) by (type)", "refId": "A" }
      ]
    },
    {
      "id": 3,
      "title": "Cost (USD, cumulative today)",
      "type": "stat",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "targets": [
        { "expr": "sum(increase(claude_code_cost_usage_total[24h]))", "refId": "A" }
      ]
    },
    {
      "id": 4,
      "title": "Cache hit rate",
      "type": "stat",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 8 },
      "targets": [
        { "expr": "sum(rate(claude_code_token_usage_total{type=\"cacheRead\"}[5m])) / sum(rate(claude_code_token_usage_total[5m]))", "refId": "A" }
      ]
    }
  ]
}
```

Metric names (`claude_code_session_count_total`, `claude_code_token_usage_total`,
`claude_code_cost_usage_total`) follow Claude Code's documented OTel metric naming.
Verify the exact names against your installed Claude Code version's telemetry docs
before Task 6 (they are versioned and can change between releases) — if they differ,
update this dashboard's `expr` fields to match, same file, same panels.

- [ ] **Step 6: Write `docker/docker-compose.yml`**

Image tags are pinned to a specific released version, never `:latest` — same
reasoning as SHA-pinning GitHub Actions (a mutable tag is a supply-chain risk, not
just a reproducibility one). **Before running this for real**, check each image's
current stable release tag (Docker Hub / GHCR) and update the four version numbers
below — they were current at spec time but this file will be executed months later.
Every service gets `security_opt: [no-new-privileges:true]` and `cap_drop: [ALL]`
(2026 Docker hardening baseline); none of these four services need any Linux
capability beyond default userspace networking.

```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:2025.10.1
    restart: unless-stopped
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    command: tunnel run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
    depends_on:
      - otel-collector
      - grafana
      - status-api

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.116.0
    restart: unless-stopped
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    environment:
      - OTLP_INGEST_TOKEN=${OTLP_INGEST_TOKEN}
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml
    command: ["--config=/etc/otelcol-contrib/config.yaml"]

  prometheus:
    image: prom/prometheus:v3.0.1
    restart: unless-stopped
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    # No Cloudflare Tunnel route to this service, ever (Task 5) — Prometheus's own
    # HTTP API has no authentication of its own, so its only safe exposure is
    # internal-to-the-compose-network, scraped by Grafana and queried by status-api.
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=30d

  grafana:
    image: grafana/grafana:11.4.0
    restart: unless-stopped
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
      - GF_AUTH_ANONYMOUS_ENABLED=false
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning
      - grafana-data:/var/lib/grafana

  status-api:
    build: ../services/public-status-api
    restart: unless-stopped
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    environment:
      - PROMETHEUS_URL=http://prometheus:9090
      - STATUS_API_TOKEN=${STATUS_API_TOKEN}
      - SENTRY_DSN=${SENTRY_DSN}

volumes:
  prometheus-data:
  grafana-data:
```

`CLOUDFLARE_TUNNEL_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, `STATUS_API_TOKEN`,
`OTLP_INGEST_TOKEN`, `SENTRY_DSN` come from a local `.env` file next to this compose
file, never committed (Task 4 wires the `.gitignore`). `SENTRY_DSN` is optional —
Task 3's status API follows the same fail-open pattern as
`marcobellingeri.dev/engine/lib/sentry.mjs`: without a DSN it's a no-op, not an
error.

- [ ] **Step 7: Validate the compose file syntax**

Run: `docker compose -f docker/docker-compose.yml config --quiet`
Expected: no output, exit code 0 (fails loudly if YAML or interpolation is broken).

- [ ] **Step 8: Commit**

```bash
git add docker/
git commit -m "feat(docker): add OTel Collector + Prometheus + Grafana compose stack"
```

---

## Task 3: Public-safe status API

**Files:**
- Create: `services/public-status-api/main.py`
- Create: `services/public-status-api/sentry.py`
- Create: `services/public-status-api/conftest.py`
- Create: `services/public-status-api/requirements.txt`
- Create: `services/public-status-api/Dockerfile`
- Test: `services/public-status-api/test_main.py`

This is the only whitelisted, non-sensitive read path a public consumer (the
marcobellingeri.dev widget) may reach. It queries Prometheus for exactly three
metric names and returns them as flat JSON — no free-form query parameter, no
pass-through to Prometheus's own query language.

- [ ] **Step 1: Write the failing test**

```python
# services/public-status-api/test_main.py
import httpx
import respx
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


@respx.mock
def test_status_returns_whitelisted_fields_only():
    respx.get("http://prometheus:9090/api/v1/query", params={"query": "sum(claude_code_session_count_total)"}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "3"]}]}})
    )
    respx.get("http://prometheus:9090/api/v1/query", params={"query": "sum(claude_code_token_usage_total)"}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "48213"]}]}})
    )
    respx.get("http://prometheus:9090/api/v1/query", params={"query": "sum(increase(claude_code_cost_usage_total[24h]))"}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "1.42"]}]}})
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json() == {
        "sessions_today": 3,
        "tokens_today": 48213,
        "cost_usd_today": 1.42,
    }


def test_status_rejects_missing_token():
    response = client.get("/status")
    assert response.status_code == 401


def test_status_rejects_wrong_token():
    response = client.get("/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


@respx.mock
def test_status_reports_upstream_failure_to_sentry_and_returns_502(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502
    assert sentry_call.called


@respx.mock
def test_status_with_malformed_upstream_json_returns_502_not_500(monkeypatch):
    # Found 2026-07-28 by actually running this suite against the code below
    # before this test existed: a 200 response with an unexpected JSON shape
    # (e.g. Prometheus mid-restart, or a future API contract change) raised a
    # bare KeyError that FastAPI turned into an unhandled 500 with no Sentry
    # capture — not the controlled 502 this endpoint promises everywhere else.
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    respx.get("http://prometheus:9090/api/v1/query").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502
    assert sentry_call.called
```

- [ ] **Step 2: Write `conftest.py`**

```python
# services/public-status-api/conftest.py
# main.py reads these at import time; pytest loads conftest.py before test
# modules, so this must set them before `from main import app` runs anywhere.
import os

os.environ.setdefault("PROMETHEUS_URL", "http://prometheus:9090")
os.environ.setdefault("STATUS_API_TOKEN", "test-token")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/public-status-api && pip install -r requirements.txt httpx respx pytest && pytest test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 4: Write `requirements.txt`**

```
fastapi==0.115.0
uvicorn==0.32.0
httpx==0.27.2
```

- [ ] **Step 5: Write `sentry.py`**

Zero-dependency Sentry envelope client, ported from
`marcobellingeri.dev/engine/lib/sentry.mjs` — same fail-open contract: no DSN is a
no-op, and a failed delivery never raises past this module.

```python
# services/public-status-api/sentry.py
import json
import os
import re
import time
import uuid

import httpx

_DSN_RE = re.compile(r"^https://([a-f0-9]+)@([^/]+)/(\d+)$")


def _endpoint(dsn: str | None) -> str | None:
    if not dsn:
        return None
    match = _DSN_RE.match(dsn)
    return f"https://{match.group(2)}/api/{match.group(3)}/envelope/" if match else None


async def capture_exception(exc: Exception, *, tags: dict | None = None) -> None:
    url = _endpoint(os.environ.get("SENTRY_DSN"))
    if not url:
        return

    event_id = uuid.uuid4().hex
    event = {
        "event_id": event_id,
        "timestamp": time.time(),
        "platform": "python",
        "level": "error",
        "environment": "public-status-api",
        "tags": tags or {},
        "exception": {"values": [{"type": type(exc).__name__, "value": str(exc)}]},
    }
    envelope = "\n".join(
        json.dumps(part)
        for part in (
            {"event_id": event_id, "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "dsn": os.environ["SENTRY_DSN"]},
            {"type": "event"},
            event,
        )
    )
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                url,
                content=envelope,
                headers={"Content-Type": "application/x-sentry-envelope"},
                timeout=3.0,
            )
    except httpx.HTTPError:
        pass  # fail-open: a Sentry delivery failure must never break the request
```

- [ ] **Step 6: Write `main.py`**

Four decisions, the first two are bug fixes found by actually running this
code on 2026-07-28, not just reading it:

1. **Token check broadens `except httpx.HTTPError` to also catch
   `KeyError, TypeError, ValueError`.** Confirmed bug: a 200 response with an
   unexpected JSON shape (Prometheus mid-restart, a future API contract
   change) raised a bare `KeyError` inside `_parse_value` that was NOT an
   `httpx.HTTPError` — FastAPI turned it into an unhandled 500 with no Sentry
   capture, breaking the "every upstream failure is a controlled 502" promise
   this endpoint makes everywhere else. The new test above
   (`test_status_with_malformed_upstream_json_returns_502_not_500`) locks
   this in; it fails against the old exception clause and passes against
   this one — verified both ways.
2. **The three Prometheus queries run concurrently (`asyncio.gather`), not
   sequentially.** They're independent — no query depends on another's
   result — so awaiting them one at a time in a loop triples the worst-case
   latency for no benefit. `httpx.Timeout(10.0)` is now explicit (httpx
   defaults to 5.0s if unset, which was already fine, but leaving it implicit
   reads as an oversight rather than a decision).
3. **`secrets.compare_digest` instead of `!=`** for the token comparison — a
   plain string compare short-circuits on the first differing byte, leaking
   token length/prefix via response timing. Low realism at this traffic
   scale, but a one-line fix.
4. **Auth moved into a FastAPI dependency** (`require_valid_token` +
   `RequireToken` type alias, `Annotated[None, Depends(...)]`) instead of an
   inline check in the route body — matches the Annotated-DI pattern already
   used elsewhere in this portfolio (JobSearch, TorinoParking; see
   `~/GitHub/Atlas/entities/tools/fastapi.md`), separates the auth concern
   from the business logic, and makes the route itself read as "what this
   endpoint does" without the guard clause in front of it.

Rate limiting is still deliberately not built into this app: Cloudflare sits
in front of every public route (Task 5) and has its own rate-limiting rules,
which is where this belongs for a single-endpoint personal service —
`ponytail: no app-level limiter, add one here only if this API ever gets a
second, differently-sensitive endpoint that Cloudflare's per-hostname rule
can't distinguish`. One accepted edge case left as-is, not worth the added
complexity at this scale: if one of the three concurrent queries fails fast
while the others are still in flight, `asyncio.gather`'s default behavior
(`return_exceptions=False`) propagates that exception immediately without
cancelling the still-running ones, which can log a harmless
"exception never retrieved" warning for whichever query was still pending —
`ponytail: accepted at this traffic scale, revisit with
return_exceptions=True + manual result handling if this ever shows up in
Sentry`.

```python
import asyncio
import os
import secrets
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException

from sentry import capture_exception

app = FastAPI()

PROMETHEUS_URL = os.environ["PROMETHEUS_URL"]
STATUS_API_TOKEN = os.environ["STATUS_API_TOKEN"]
REQUEST_TIMEOUT = httpx.Timeout(10.0)

QUERIES = {
    "sessions_today": "sum(claude_code_session_count_total)",
    "tokens_today": "sum(claude_code_token_usage_total)",
    "cost_usd_today": "sum(increase(claude_code_cost_usage_total[24h]))",
}


def _parse_value(payload: dict) -> float:
    result = payload["data"]["result"]
    if not result:
        return 0.0
    return float(result[0]["value"][1])


def require_valid_token(authorization: str = Header(default="")) -> None:
    if not secrets.compare_digest(authorization, f"Bearer {STATUS_API_TOKEN}"):
        raise HTTPException(status_code=401, detail="unauthorized")


RequireToken = Annotated[None, Depends(require_valid_token)]


async def _query_one(client: httpx.AsyncClient, query: str) -> float:
    response = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
    response.raise_for_status()
    return _parse_value(response.json())


@app.get("/status")
async def status(_: RequireToken) -> dict:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            results = await asyncio.gather(*(_query_one(client, q) for q in QUERIES.values()))
        values = dict(zip(QUERIES.keys(), results))
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        await capture_exception(exc, tags={"endpoint": "status"})
        raise HTTPException(status_code=502, detail="upstream unavailable") from exc

    return {
        "sessions_today": int(values["sessions_today"]),
        "tokens_today": int(values["tokens_today"]),
        "cost_usd_today": round(values["cost_usd_today"], 2),
    }
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest test_main.py -v`
Expected: **5 passed** — verified for real on 2026-07-28 against this exact
code (Python 3.10 locally; the Docker image below targets 3.12, no
version-specific syntax used).

- [ ] **Step 8: Write `Dockerfile`**

`HEALTHCHECK` added — Docker Compose (Task 2) has no Kubernetes liveness/
readiness probe to lean on, so this is the only automatic signal that the
container is actually serving, not just running. It hits the container's own
`/status` using the runtime `STATUS_API_TOKEN` env var, which is why it's
shell form (`CMD` string, not exec-array form) — shell form expands
environment variables at check time, exec form does not.

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py sentry.py .

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS -H "Authorization: Bearer $STATUS_API_TOKEN" http://localhost:8000/status || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Not verified locally** (deliberately — this would need `docker run
python:3.12-slim` against the real daemon, and Marco asked to keep this
machine light since execution happens on another terminal): confirm `curl` is
present in `python:3.12-slim` before relying on the `HEALTHCHECK` above —
`docker run --rm python:3.12-slim sh -c "which curl"` on the executing
machine. If missing, add
`RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*`
before the `HEALTHCHECK` line.

- [ ] **Step 9: Commit**

```bash
git add services/public-status-api/
git commit -m "feat(status-api): whitelisted status endpoint, token auth, Sentry error capture"
```

---

## Task 4: Bootstrap script and secret wiring

**Files:**
- Create: `scripts/bootstrap.sh`
- Create: `docker/.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Write `scripts/bootstrap.sh`**

```bash
#!/bin/bash
set -euo pipefail

REPO_URL="https://github.com/MK023/agentic-os.git"
INSTALL_DIR="/opt/agentic-os"

if [ ! -d "$INSTALL_DIR" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR/docker"

if [ ! -f .env ]; then
  echo "Missing docker/.env on the VPS — copy .env.example, fill in real secrets, then re-run:" >&2
  echo "  cd $INSTALL_DIR/docker && docker compose up -d" >&2
  exit 1
fi

docker compose up -d
```

This runs once at VM creation (Terraform `post_install_script_id`). It clones the
repo but deliberately refuses to start the stack without a real `.env` — secrets are
never embedded in the Terraform-managed script content, they're placed on the VPS
by hand over SSH after the first boot (Step 3 below).

- [ ] **Step 2: Write `docker/.env.example`**

```
CLOUDFLARE_TUNNEL_TOKEN=
GRAFANA_ADMIN_PASSWORD=
STATUS_API_TOKEN=
```

- [ ] **Step 3: Add the real `.env` to `.gitignore`**

```
docker/.env
terraform/hostinger-vps/terraform.tfvars
```

- [ ] **Step 4: Commit**

```bash
git add scripts/bootstrap.sh docker/.env.example .gitignore
git commit -m "feat(bootstrap): add VPS bootstrap script, keep real secrets out of git"
```

---

## Task 5: Cloudflare Tunnel — ingress and Access policies

**Files:**
- Create: `docs/CLOUDFLARE_TUNNEL_SETUP.md`

This step is manual (Cloudflare dashboard/`cloudflared` CLI on Marco's machine, not
something Terraform-in-this-repo drives), so the plan documents the exact sequence
rather than code to run in CI.

- [ ] **Step 1: Write `docs/CLOUDFLARE_TUNNEL_SETUP.md`**

```markdown
# Cloudflare Tunnel setup (one-time, manual)

Run these from your local machine, with `cloudflared` installed and logged in
(`cloudflared tunnel login`):

1. Create the tunnel:
   `cloudflared tunnel create agentic-os-hub`
   Note the tunnel ID and the token printed — the token goes into the VPS's
   `docker/.env` as `CLOUDFLARE_TUNNEL_TOKEN` (Task 4, Step 3 wiring).

2. Add three DNS routes (replace `yourdomain.com`):
   - `cloudflared tunnel route dns agentic-os-hub grafana.yourdomain.com`
   - `cloudflared tunnel route dns agentic-os-hub status.yourdomain.com`
   - `cloudflared tunnel route dns agentic-os-hub otel.yourdomain.com`

3. In the Cloudflare dashboard, under the tunnel's Public Hostname configuration,
   map each hostname to the in-container service, using the container's own
   Docker network name (not localhost, since cloudflared runs as its own
   container on the same compose network — Task 2's compose file gives it
   `depends_on` on all three, and Docker Compose's default network makes each
   service reachable by its service name):
   - `grafana.yourdomain.com` -> `http://grafana:3000`
   - `status.yourdomain.com` -> `http://status-api:8000`
   - `otel.yourdomain.com` -> `http://otel-collector:4318`

4. Under Cloudflare Zero Trust -> Access -> Applications, create two
   applications:
   - **Grafana app** on `grafana.yourdomain.com`: policy = your own email only
     (interactive login).
   - **Status API app** on `status.yourdomain.com`: policy = Service Auth, issue
     a Service Token. The generated Client ID/Secret are what
     marcobellingeri.dev's Worker sends as `CF-Access-Client-Id` /
     `CF-Access-Client-Secret` headers (Task 7).

Leave `otel.yourdomain.com` without a Cloudflare Access **application** —
Claude Code's OTLP exporter doesn't support the Access service-token header
today. This does **not** mean the endpoint is unauthenticated: an unauthenticated
endpoint behind a public Tunnel hostname is exactly the pattern behind
CVE-2026-28798 (ZimaOS, found during the Phase 1 security review). Auth here is
the `bearertokenauth` extension inside the Collector itself (Task 2, Step 1) —
`OTLP_INGEST_TOKEN` is a random value you generate once
(`openssl rand -hex 32`) and put in both `docker/.env` (Task 4) and Claude Code's
local `OTEL_EXPORTER_OTLP_HEADERS` (Task 6). A request without the matching
bearer token is rejected by the Collector before it reaches any pipeline.
```

- [ ] **Step 2: Commit**

```bash
git add docs/CLOUDFLARE_TUNNEL_SETUP.md
git commit -m "docs: add manual Cloudflare Tunnel setup sequence"
```

---

## Task 6: Local Claude Code telemetry configuration (docs)

**Files:**
- Create: `docs/CLAUDE_CODE_TELEMETRY.md`

- [ ] **Step 1: Write `docs/CLAUDE_CODE_TELEMETRY.md`**

```markdown
# Enabling Claude Code telemetry toward the hub

Set these in your shell profile (or Claude Code's local settings.json `env`
block) on any machine you want observed:

    CLAUDE_CODE_ENABLE_TELEMETRY=1
    OTEL_METRICS_EXPORTER=otlp
    OTEL_LOGS_EXPORTER=otlp
    OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.yourdomain.com
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
    OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <the same OTLP_INGEST_TOKEN from docker/.env>
    OTEL_METRICS_EXPORTER_OTLP_TEMPORALITY_PREFERENCE=cumulative

The `OTEL_EXPORTER_OTLP_HEADERS` line is what the Collector's `bearertokenauth`
extension (Task 2) checks — without it every export is rejected with 401, not
silently dropped, so a typo here is easy to notice (check
`docker compose logs otel-collector` on the VPS if metrics never appear).

Before relying on these, check Claude Code's current OTel telemetry
documentation for your installed version — the metric names referenced in
`docker/grafana/provisioning/dashboards/claude-code.json` are versioned and
can change between releases (this is explicitly called out as a beta-stability
risk in the design spec, §10 is not the place — see spec §3 architecture note).
```

- [ ] **Step 2: Commit**

```bash
git add docs/CLAUDE_CODE_TELEMETRY.md
git commit -m "docs: document local Claude Code telemetry env vars"
```

---

## Task 7: marcobellingeri.dev — public widget (separate repo)

**Repo:** `MK023/marcobellingeri.dev` (not this repo — switch working directory)

**Files:**
- Create: `src/pages/api/agentic-status.ts` (Worker API route — follows
  the existing `/api/radar`, `/api/ask` pattern in that repo)
- Create: `src/components/AgenticOsWidget.astro`
- Test: `test/agentic-status.test.mjs` (follows existing Worker test conventions
  in that repo, e.g. `test/specchi.test.mjs`)

(An earlier draft of this task also listed a `src/lib/agenticOsStatus.ts` file
with no step that ever created it — found during the 2026-07-28 review pass.
Removed: the route's fetch/transform/fallback logic is ~15 lines and doesn't
earn a separate lib file, unlike `lib/sentry.mjs`/`lib/langfuse.mjs` which are
reused across many scripts. Don't recreate it just to match this old list.)

- [ ] **Step 1: Write the failing test**

```javascript
// test/agentic-status.test.mjs
import { describe, it, expect, vi, afterEach } from "vitest";
import worker from "../src/pages/api/agentic-status.ts";

describe("GET /api/agentic-status", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the hub's sanitized status fields", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ sessions_today: 3, tokens_today: 48213, cost_usd_today: 1.42 }),
        { status: 200 }
      )
    );

    const request = new Request("https://marcobellingeri.dev/api/agentic-status");
    const env = {
      AGENTIC_OS_STATUS_URL: "https://status.yourdomain.com/status",
      AGENTIC_OS_ACCESS_CLIENT_ID: "test-id",
      AGENTIC_OS_ACCESS_CLIENT_SECRET: "test-secret",
    };

    const response = await worker.fetch(request, env);
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body).toEqual({ sessionsToday: 3, tokensToday: 48213, costUsdToday: 1.42 });
  });

  it("degrades to null fields if the hub is unreachable", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("network down"));

    const request = new Request("https://marcobellingeri.dev/api/agentic-status");
    const env = {
      AGENTIC_OS_STATUS_URL: "https://status.yourdomain.com/status",
      AGENTIC_OS_ACCESS_CLIENT_ID: "test-id",
      AGENTIC_OS_ACCESS_CLIENT_SECRET: "test-secret",
    };

    const response = await worker.fetch(request, env);
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body).toEqual({ sessionsToday: null, tokensToday: null, costUsdToday: null });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run test/agentic-status.test.mjs`
Expected: FAIL — `Cannot find module '../src/pages/api/agentic-status.ts'`

- [ ] **Step 3: Write `src/pages/api/agentic-status.ts`**

```typescript
export default {
  async fetch(request: Request, env: Record<string, string>): Promise<Response> {
    const fallback = { sessionsToday: null, tokensToday: null, costUsdToday: null };

    try {
      const upstream = await fetch(env.AGENTIC_OS_STATUS_URL, {
        headers: {
          "CF-Access-Client-Id": env.AGENTIC_OS_ACCESS_CLIENT_ID,
          "CF-Access-Client-Secret": env.AGENTIC_OS_ACCESS_CLIENT_SECRET,
        },
      });

      if (!upstream.ok) {
        return new Response(JSON.stringify(fallback), {
          headers: { "content-type": "application/json", "cache-control": "no-store" },
        });
      }

      const data = (await upstream.json()) as {
        sessions_today: number;
        tokens_today: number;
        cost_usd_today: number;
      };

      return new Response(
        JSON.stringify({
          sessionsToday: data.sessions_today,
          tokensToday: data.tokens_today,
          costUsdToday: data.cost_usd_today,
        }),
        { headers: { "content-type": "application/json", "cache-control": "public, max-age=30" } }
      );
    } catch {
      return new Response(JSON.stringify(fallback), {
        headers: { "content-type": "application/json", "cache-control": "no-store" },
      });
    }
  },
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run test/agentic-status.test.mjs`
Expected: 2 passed

- [ ] **Step 5: Add the two Worker secrets**

Run:
```bash
wrangler secret put AGENTIC_OS_ACCESS_CLIENT_ID
wrangler secret put AGENTIC_OS_ACCESS_CLIENT_SECRET
```
And add `AGENTIC_OS_STATUS_URL` as a plain (non-secret) var in `wrangler.toml`
under `[vars]`, since it's a public hostname, not a credential.

- [ ] **Step 6: Write `src/components/AgenticOsWidget.astro`**

```astro
---
const response = await fetch(new URL("/api/agentic-status", Astro.url));
const { sessionsToday, tokensToday, costUsdToday } = await response.json();
---
<div class="agentic-os-widget">
  <p>Claude Code oggi: {sessionsToday ?? "—"} sessioni · {tokensToday ?? "—"} token · ${costUsdToday ?? "—"}</p>
</div>
```

Styling and placement in the page layout follow the same conventions as the
existing `Newsstand.astro` component in that repo — not respecified here since
it's a visual/CSS decision for that repo's own design system, not an
architectural one.

- [ ] **Step 7: Commit**

```bash
git add src/pages/api/agentic-status.ts src/components/AgenticOsWidget.astro test/agentic-status.test.mjs
git commit -m "feat: add Agentic OS live status widget"
```

---

## Task 8: Smoke test and CI gate (agentic-os repo)

Baseline copied from `marcobellingeri.dev`'s CI (Livello 3 on several axes already
— see `~/GitHub/Atlas/concepts/pipeline-cicd.md`), scaled down to what a
declared-**Livello 1** personal project actually needs: automated gates, no
approval workflow, no canary. What's kept from that baseline regardless of level,
because it's cheap and non-negotiable per the security baseline (not gated by
maturity level): SHA-pinned actions, minimal per-job `permissions:`, gitleaks
(zero tolerance), zizmor (workflow SAST), dependency-review, SonarCloud. What's
explicitly **not** copied: approval gates, canary/progressive delivery, SBOM +
attestation (3 Python dependencies, no distributed artifact — would be Livello 4
cargo-cult on a project this size, the exact antipattern the model warns against).

**Files:**
- Create: `scripts/verify-hub.sh`
- Create: `.github/workflows/validate.yml`
- Create: `sonar-project.properties`
- Create: `.gitleaks.toml`

- [ ] **Step 1: Write `scripts/verify-hub.sh`**

```bash
#!/bin/bash
set -euo pipefail

GRAFANA_URL="${1:?Usage: verify-hub.sh <grafana-url> <status-url> <status-token>}"
STATUS_URL="${2:?}"
STATUS_TOKEN="${3:?}"

fail=0

if ! curl -fsS "${GRAFANA_URL}/api/health" > /dev/null; then
  echo "FAIL: Grafana health check" >&2
  fail=1
fi

if ! curl -fsS -H "Authorization: Bearer ${STATUS_TOKEN}" "${STATUS_URL}/status" > /dev/null; then
  echo "FAIL: status API check" >&2
  fail=1
fi

if [ "$fail" -eq 1 ]; then
  exit 1
fi

echo "OK: hub is reachable and healthy"
```

- [ ] **Step 2: Run it manually to verify the failure path**

Run: `bash scripts/verify-hub.sh http://localhost:1234 http://localhost:1234 wrong-token`
Expected: two `FAIL:` lines printed to stderr, exit code 1 (nothing is running on
that port yet — this is the expected-fail check before the hub exists).

- [ ] **Step 3: Write `.github/workflows/validate.yml`**

`actions/checkout`, `actions/dependency-review-action`, `gitleaks/gitleaks-action`
and `SonarSource/sonarqube-scan-action` are pinned to the exact SHAs already
verified live in `marcobellingeri.dev`'s workflows — reusing a pin that's already
running in production beats trusting a freshly-typed one.
`actions/setup-python` and `hashicorp/setup-terraform` have **no verified pin from
an existing repo** (marcobellingeri.dev is Node-only) — look up each action's
current release SHA on its GitHub Releases page before running this for real, the
same way Task 1 looks up Hostinger's data center/template IDs. Do not invent one.

```yaml
name: validate

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch: {}

permissions:
  contents: read

concurrency:
  group: validate-${{ github.ref }}
  cancel-in-progress: true

jobs:
  terraform:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - uses: hashicorp/setup-terraform@REPLACE_WITH_LOOKED_UP_SHA # look up current release before running
      - run: terraform -chdir=terraform/hostinger-vps fmt -check
      - run: terraform -chdir=terraform/hostinger-vps init -backend=false
      - run: terraform -chdir=terraform/hostinger-vps validate

  compose:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - run: docker compose -f docker/docker-compose.yml config --quiet
        env:
          CLOUDFLARE_TUNNEL_TOKEN: dummy
          GRAFANA_ADMIN_PASSWORD: dummy
          STATUS_API_TOKEN: dummy
          OTLP_INGEST_TOKEN: dummy
          SENTRY_DSN: ""

  status-api-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - uses: actions/setup-python@REPLACE_WITH_LOOKED_UP_SHA # look up current release before running
        with:
          python-version: "3.12"
      - run: pip install -r services/public-status-api/requirements.txt httpx respx pytest pytest-cov
      - run: pytest services/public-status-api/test_main.py -v --cov=services/public-status-api --cov-report=xml:services/public-status-api/coverage.xml

  # Pipeline-as-attack-surface (Atlas pipeline-cicd model v2): zizmor is SAST for
  # the workflows themselves — template injection, dangerous triggers, overly
  # broad permissions. Blocks on HIGH, same policy and pinned version as
  # marcobellingeri.dev's site-ci.yml (a linter that changes its own rules
  # silently is a non-deterministic gate).
  workflow-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - name: zizmor sui workflow (blocca su HIGH)
        run: |
          pip install --quiet zizmor==1.25.0
          zizmor --min-severity=high .github/workflows/

  # Blocks on new HIGH-severity CVEs introduced by this PR's dependency diff —
  # does not see what's already on main (Dependabot alerts cover that). No
  # secrets used, so it runs on Dependabot's own PRs too — same as
  # marcobellingeri.dev's site-ci.yml.
  dependency-review:
    if: github.event_name == 'pull_request'
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
      - uses: actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294 # v5.0.0
        with:
          fail-on-severity: high

  # Zero tolerance, full history. Checked against the author of the PR
  # (`pull_request.user.login`), not `github.actor` — the latter changes on an
  # `update-branch` push and would silently reopen a check meant to stay closed
  # for Dependabot. `pull-requests: read` is required by the action itself to
  # list which commits to scan; without it the job 403s on every PR.
  gitleaks:
    if: github.event.pull_request.user.login != 'dependabot[bot]'
    permissions:
      contents: read
      pull-requests: read
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          fetch-depth: 0
      - uses: gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e # v3.0.0
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITLEAKS_CONFIG: .gitleaks.toml

  # Excluded for Dependabot PRs: GitHub doesn't pass repository secrets to them,
  # so SONAR_TOKEN arrives empty and the scan would die with "Not authorized" on
  # every single dependency bump — not a weakened gate, a gate that structurally
  # cannot pass there. Same policy as marcobellingeri.dev's site-ci.yml.
  sonar:
    if: github.event_name == 'pull_request' && github.event.pull_request.user.login != 'dependabot[bot]'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          fetch-depth: 0
      - uses: actions/setup-python@REPLACE_WITH_LOOKED_UP_SHA # look up current release before running
        with:
          python-version: "3.12"
      - run: pip install -r services/public-status-api/requirements.txt httpx respx pytest pytest-cov
      - run: pytest services/public-status-api/test_main.py -v --cov=services/public-status-api --cov-report=xml:services/public-status-api/coverage.xml
      - uses: SonarSource/sonarqube-scan-action@22918119ff8e1ca75a623e15c8296b6ea4fbe28f # v8.2.1
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

- [ ] **Step 4: Write `sonar-project.properties`**

Sonar has no meaningful Terraform analysis without a paid plugin — IaC static
analysis for this repo is Checkov's job (Step 6 below), reused from
`langfuse-devops-lab/.checkov.yml`. Sonar's scope here is the one real
application-logic component: the status API.

```
sonar.projectKey=MK023_agentic-os
sonar.organization=mk023

sonar.sources=services/public-status-api
sonar.tests=services/public-status-api
sonar.test.inclusions=services/public-status-api/test_main.py,services/public-status-api/conftest.py
sonar.exclusions=services/public-status-api/test_main.py,services/public-status-api/conftest.py,**/.terraform/**

sonar.python.version=3.12
sonar.python.coverage.reportPaths=services/public-status-api/coverage.xml
```

- [ ] **Step 5: Write `.gitleaks.toml`**

Start from gitleaks' own default ruleset — no exceptions yet, unlike
`marcobellingeri.dev` which needed exactly one (a non-secret Sonar project key
that pattern-matched a rule). Add an exception here only when a real false
positive shows up, same discipline, not preemptively.

```toml
# Empty extends the default gitleaks ruleset with no local exceptions.
# Add a [[rules]] override here only in response to a real false positive —
# see marcobellingeri.dev/.gitleaks.toml for the one precedent in this portfolio.
[extend]
useDefault = true
```

- [ ] **Step 6: Write `.checkov.yml`, adapted from langfuse-devops-lab**

Read `../langfuse-devops-lab/.checkov.yml` on 2026-07-28 rather than assuming
its shape: every one of its `skip-check` entries is Kubernetes/Helm-specific
(`CKV_K8S_*`, `CKV2_K8S_*`), Supabase-specific (`CKV_SUPABASE_1`), or
references a `rollback.yml` workflow (`CKV_GHA_7`) that doesn't exist in this
repo — **none of them apply here**, this repo has no Kubernetes/Helm/Supabase.
Don't `cp` it — write a fresh file that reuses only the `framework`,
`skip-path`, `soft-fail-on`, and output settings, trimmed to what this repo
actually has (Terraform + a Dockerfile + GitHub Actions, no Kubernetes/Helm):

```yaml
# Checkov config for Agentic OS. Adapted from langfuse-devops-lab/.checkov.yml
# 2026-07-28 — that file's skip-check entries are all Kubernetes/Helm/Supabase
# specific and don't apply to this repo's stack (Terraform VPS + Dockerfile +
# GitHub Actions only). Starting with an empty skip-check, not a copied one:
# add an entry here only in response to a real finding, with the same
# comment-per-skip discipline as the source file.

framework:
  - terraform
  - dockerfile
  - github_actions

skip-path:
  - .terraform/

skip-check: []

soft-fail-on:
  - LOW
  - MEDIUM

compact: true
quiet: true
```

- [ ] **Step 7: Commit**

```bash
git add scripts/verify-hub.sh .github/workflows/validate.yml sonar-project.properties .gitleaks.toml .checkov.yml
git commit -m "ci: add validate workflow (terraform, compose, status API tests, zizmor, gitleaks, dependency-review, SonarCloud)"
```

---

## Self-review notes (completed during planning)

- **Spec coverage:** §3 architecture → Tasks 1-2, 5; §4 components → Tasks 1-3, 5;
  §5 data flow → Tasks 2-3 (Collector/Prometheus/status-api wiring), Task 7 (site
  side); §6 security → Task 3 (token auth), Task 5 (Access policies), Task 4
  (secrets never committed); §8 testing → Task 3 (pytest), Task 7 (vitest), Task 8
  (smoke script + CI); §9 cost/duration → not a code task, stays a manual decision
  at `terraform apply`/`destroy` time, correctly out of this plan's scope.
- Fixed during review: `docker-compose.yml`'s `status-api` build path corrected to
  `../services/public-status-api` (relative to `docker/`, matching where Task 3
  actually creates the service directory).
- Metric names in the Grafana dashboard JSON (Task 2, Step 5) and the status API's
  `QUERIES` dict (Task 3, Step 4) must stay in sync if Claude Code's telemetry names
  change — flagged explicitly in Task 6 rather than assumed stable.
- **Second pass (same day), after adopting marcobellingeri.dev's CI as baseline
  and a web sweep for anything missed:** three real gaps found and closed —
  (1) the OTLP ingest endpoint behind a public Tunnel hostname with no auth was
  the exact pattern behind CVE-2026-28798, closed with the Collector's
  `bearertokenauth` extension (Task 2, Task 5, Task 6); (2) Docker images pinned
  to `:latest` and containers with default privileges, closed with version pins
  + `no-new-privileges` + `cap_drop: [ALL]` (Task 2); (3) status API's token
  check used `!=` (timing side-channel) instead of `secrets.compare_digest`
  (Task 3). Also added: Sentry error capture in the status API (zero-dep,
  ported from `marcobellingeri.dev/engine/lib/sentry.mjs`), and the full
  CI gate set from that repo scaled to Livello 1 (zizmor, gitleaks,
  dependency-review, SonarCloud) — see Atlas `concepts/pipeline-cicd.md` and
  `concepts/testing-pyramid.md` for the declared level/contract this maps to.
- Langfuse was considered for this phase and deliberately **not** added: Phase 1
  makes no LLM inference call of its own (it only relays Claude Code's own
  reported usage), so there is nothing for Langfuse to trace yet. It's a
  standing decision for Phase 4 (session RAG), not a gap in Phase 1.
- **Third pass (same day): every code block in this plan actually executed,
  not just read**, ahead of Marco starting real work on this in the coming
  days. Reconstructed every file from Tasks 1, 2, 3, 7, 8 in a scratch
  directory and ran the real tools against them (Terraform v1.14.8 against
  the real `hostinger/hostinger` v0.1.22 provider, Docker Compose v2 config
  resolution, pytest 5 tests, vitest 2 tests, `terraform fmt`, YAML/TOML
  parsing). Found and fixed:
  - **`terraform fmt -check` failed** (exit 3) on `main.tf`'s manually-aligned
    `=` signs — the plan claimed "no formatting diffs", which was false as
    written. Fixed to the actual `terraform fmt` output (Task 1).
  - **`terraform validate` rejected the hostname** — the provider requires an
    FQDN-shaped value; `"agentic-os-hub"` (no dot) failed with `invalid value
    for hostname (must be a valid FQDN)`. This is a real provider-schema
    constraint no amount of reading would have surfaced without the provider
    actually installed. Fixed to `"agentic-os-hub.local"` (Task 1).
  - **Status API let a malformed upstream response become an unhandled 500**
    — `except httpx.HTTPError` didn't catch the `KeyError` a 200-with-wrong-
    shape response raises inside `_parse_value`. Confirmed by writing a
    regression test, watching it fail against the old code, then fixing it
    (Task 3) — this is the gap Marco asked about directly ("gestione degli
    errori da aggiungere").
  - **Orphan file in Task 7's file list**: `src/lib/agenticOsStatus.ts` was
    listed as a file to create but no step ever wrote it. Removed the
    reference rather than inventing an unneeded file (Task 7).
  - **Task 8's Checkov instruction was vague** ("strip what doesn't apply") —
    reading the actual `langfuse-devops-lab/.checkov.yml` showed *every*
    existing skip is Kubernetes/Helm/Supabase-specific and none apply here;
    replaced with a concrete, minimal file instead of a `cp` + hand-wave
    (Task 8).
  - Confirmed working, not just written: all 5 pytest cases, both vitest
    cases, `docker compose config`, all pinned image tags exist on their
    registries (checked via `docker manifest inspect`), `scripts/verify-hub.sh`
    produces exactly the two-line-FAIL/exit-1 output the plan promises.
  - Also applied while in there, matching Marco's ask for FastAPI
    architectural patterns: the three Prometheus queries now run concurrently
    (`asyncio.gather`) instead of sequentially, an explicit `httpx.Timeout`
    replaces the previously-implicit 5s default, and token auth moved into a
    proper FastAPI dependency (`Annotated[None, Depends(...)]`) matching the
    DI pattern already used in JobSearch/TorinoParking (Task 3).
  - **Not fixed, deliberately**: Docker/Dockerfile execution itself wasn't run
    against a real daemon (`docker run`, image pulls) — Marco asked to keep
    this machine light since execution happens elsewhere. Everything
    Docker-related that doesn't need the daemon (`compose config`,
    `manifest inspect`, YAML parsing) was still verified for real.

---

## Explicitly not in this plan

- No Kubernetes/K3s anywhere (spec §10).
- No changes to `langfuse-devops-lab` (separate repo, untouched).
- No Phase 2 (marcobellingeri.dev agent metrics push, monferrinoAI), Phase 3
  (Personal Portal), or Phase 4 (session RAG) work — sketch-only per spec §7, each
  gets its own future spec → plan cycle.
- No `terraform apply` / actual VPS creation / actual Cloudflare Tunnel creation —
  this plan produces reviewable, testable code and docs; running it against real
  infrastructure is a deliberate, separate step Marco takes when ready (matches the
  "autonomia si ferma alla produzione" pattern already established on his other
  projects).
