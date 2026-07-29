"""Public-safe status endpoint.

The only read path a public consumer (the marcobellingeri.dev widget) may reach.
It runs exactly three fixed Prometheus queries and returns them as flat JSON:
no free-form query parameter, no pass-through to PromQL, no session content.
"""

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

# Plain sums, not `increase()` over a range — and that is not a shortcut, it is what
# the data shape requires. Claude Code emits ONE SERIES PER SESSION (`session_id` is
# a label), and each series is a cumulative counter for that process alone. It is
# born, it grows while the session runs, and it stays flat forever after.
#
# `increase(...[24h])` measures growth *inside* the window, so on
# claude_code_session_count — incremented exactly once, at session start — it is
# structurally zero: the series appears at 1 and never moves again. Measured against
# production on 2026-07-29 with two real sessions, which is the only way this shows
# up; a synthetic payload that re-sends the same session_id with a higher value
# hides it perfectly, and that is exactly what an earlier local test did.
#
# Summing the current value of every live series gives the right number instead:
# each series carries its own session's total. The time window comes from the
# Collector's `metric_expiration: 25h`, which stops exposing a series a day after its
# last update — so "today" here means "the last ~25 hours of activity", not a
# calendar day. Honest, and stable when nothing is running.
#
# Metric names assume the Collector's UnderscoreEscapingWithoutSuffixes strategy
# (docker/otel-collector-config.yaml) — keep the two files in sync.
QUERIES = {
    "sessions_today": "sum(claude_code_session_count)",
    "tokens_today": "sum(claude_code_token_usage)",
    "cost_usd_today": "sum(claude_code_cost_usage)",
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


@app.get(
    "/status",
    # Documented, not just raised: these two are the endpoint's contract for whoever
    # reads its OpenAPI (python:S8415).
    responses={
        401: {"description": "Missing or invalid bearer token"},
        502: {"description": "Prometheus unreachable or answering an unexpected shape"},
    },
)
async def status(_: RequireToken) -> dict:
    # KeyError/TypeError/ValueError are caught alongside httpx.HTTPError: a 200
    # response with an unexpected shape (Prometheus mid-restart, a future API
    # change) raises inside _parse_value and would otherwise become an unhandled
    # 500 with no Sentry capture, breaking this endpoint's "every upstream
    # failure is a controlled 502" promise.
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
