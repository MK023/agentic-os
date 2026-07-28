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
