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

You need, from Marco, before Task 1:
- A Hostinger account with an API token (`HOSTINGER_API_TOKEN`).
- A Cloudflare account with a zone already on Cloudflare (for the Tunnel) and a
  `CLOUDFLARE_API_TOKEN` with Tunnel edit permission.
- An SSH keypair to use for the VPS (path to the public key).

None of these are placeholders to fill in later — the plan cannot proceed past
Task 1 without them, so confirm they exist before starting.

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
  data_center_id          = var.data_center_id
  template_id             = var.template_id
  hostname                = "agentic-os-hub"
  ssh_key_ids             = [hostinger_vps_ssh_key.agentic_os.id]
  post_install_script_id  = hostinger_vps_post_install_script.docker_bootstrap.id
}
```

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

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch: {}

exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
    resource_to_telemetry_conversion:
      enabled: true

service:
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

```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
    depends_on:
      - otel-collector
      - grafana
      - status-api

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    restart: unless-stopped
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml
    command: ["--config=/etc/otelcol-contrib/config.yaml"]

  prometheus:
    image: prom/prometheus:latest
    restart: unless-stopped
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=30d

  grafana:
    image: grafana/grafana:latest
    restart: unless-stopped
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
      - GF_AUTH_ANONYMOUS_ENABLED=false
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning
      - grafana-data:/var/lib/grafana

  status-api:
    build: ../services/public-status-api
    restart: unless-stopped
    environment:
      - PROMETHEUS_URL=http://prometheus:9090
      - STATUS_API_TOKEN=${STATUS_API_TOKEN}

volumes:
  prometheus-data:
  grafana-data:
```

`CLOUDFLARE_TUNNEL_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, `STATUS_API_TOKEN` come from a
local `.env` file next to this compose file, never committed (Task 4 wires the
`.gitignore`).

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/public-status-api && pip install -r requirements.txt httpx respx pytest && pytest test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Write `requirements.txt`**

```
fastapi==0.115.0
uvicorn==0.32.0
httpx==0.27.2
```

- [ ] **Step 4: Write `main.py`**

```python
import os

import httpx
from fastapi import FastAPI, Header, HTTPException

app = FastAPI()

PROMETHEUS_URL = os.environ["PROMETHEUS_URL"]
STATUS_API_TOKEN = os.environ["STATUS_API_TOKEN"]

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


@app.get("/status")
async def status(authorization: str = Header(default="")) -> dict:
    if authorization != f"Bearer {STATUS_API_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")

    async with httpx.AsyncClient() as client:
        values = {}
        for field, query in QUERIES.items():
            response = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
            values[field] = _parse_value(response.json())

    return {
        "sessions_today": int(values["sessions_today"]),
        "tokens_today": int(values["tokens_today"]),
        "cost_usd_today": round(values["cost_usd_today"], 2),
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest test_main.py -v`
Expected: 3 passed

- [ ] **Step 6: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 7: Commit**

```bash
git add services/public-status-api/
git commit -m "feat(status-api): add whitelisted public status endpoint with token auth"
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

Leave `otel.yourdomain.com` without a Cloudflare Access application for now —
Claude Code's OTLP exporter doesn't support the Access service-token header
today, so ingestion auth is the `STATUS_API_TOKEN`-style bearer check inside the
Collector's own config being deferred to Phase 2 when a second real producer
needs it; today it is a single trusted producer (you) on a non-guessable
subdomain, which is the accepted risk for a one-month experiment.
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
    OTEL_METRICS_EXPORTER_OTLP_TEMPORALITY_PREFERENCE=cumulative

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
- Create: `src/lib/agenticOsStatus.ts`
- Modify: `src/pages/api/agentic-status.ts` (new file, Worker API route — follows
  the existing `/api/radar`, `/api/ask` pattern in that repo)
- Create: `src/components/AgenticOsWidget.astro`
- Test: `test/agentic-status.test.mjs` (follows existing Worker test conventions
  in that repo, e.g. `test/specchi.test.mjs`)

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

**Files:**
- Create: `scripts/verify-hub.sh`
- Create: `.github/workflows/validate.yml`

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

```yaml
name: validate

on:
  pull_request:
  push:
    branches: [main]

jobs:
  terraform:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
      - run: terraform -chdir=terraform/hostinger-vps fmt -check
      - run: terraform -chdir=terraform/hostinger-vps init -backend=false
      - run: terraform -chdir=terraform/hostinger-vps validate

  compose:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker/docker-compose.yml config --quiet
        env:
          CLOUDFLARE_TUNNEL_TOKEN: dummy
          GRAFANA_ADMIN_PASSWORD: dummy
          STATUS_API_TOKEN: dummy

  status-api-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r services/public-status-api/requirements.txt httpx respx pytest
      - run: pytest services/public-status-api/test_main.py -v
```

- [ ] **Step 4: Commit**

```bash
git add scripts/verify-hub.sh .github/workflows/validate.yml
git commit -m "ci: add validate workflow (terraform, compose config, status API tests)"
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
