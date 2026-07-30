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
#
# `cost_usd_today` is NOT claude_code_cost_usage. That metric is Claude Code's own
# estimate and it undercounts (checked against the real spend on 2026-07-30); the
# token counters next to it are measured, so the cost is recomputed here from tokens
# × list price. Grouping `by (model, type)` is what makes that possible — price
# depends on both — and it is also why this query needs a different parser from the
# other two.
QUERIES = {
    "sessions_today": "sum(claude_code_session_count)",
    "tokens_today": "sum(claude_code_token_usage)",
    "cost_usd_today": "sum by (model, type) (claude_code_token_usage)",
}

# Anthropic list prices, USD per million tokens, as published on 2026-07-30. These
# are a hand-maintained copy of someone else's price list: when Anthropic changes a
# price, or a model lands that is not in here, this table is what must be edited —
# nothing downstream will notice on its own. The `type` keys are Claude Code's own
# attribute values, spelled exactly as it emits them (camelCase, not snake_case);
# renaming one to look tidier silently routes those tokens to the fallback below.
PRICES_USD_PER_MTOK = {
    "claude-opus-5": {
        "input": 5.00,
        "output": 25.00,
        "cacheCreation": 6.25,
        "cacheRead": 0.50,
    },
}

# What an unknown model or an unknown token type costs. Pricing a gap at zero is the
# dangerous answer: it reads as a cheap day rather than as missing knowledge, and a
# number that is quietly too low is one nobody investigates. So a gap is priced at
# the dearest rate we know — per type where the type is known, overall otherwise —
# which can only ever overstate, and it is reported once to Sentry so the table gets
# fixed. Derived, not hardcoded: a new row in the table above updates these too.
_FALLBACK_RATES = {
    tipo: max(tabella.get(tipo, 0.0) for tabella in PRICES_USD_PER_MTOK.values())
    for prezzi in PRICES_USD_PER_MTOK.values()
    for tipo in prezzi
}
_DEAREST_RATE = max(_FALLBACK_RATES.values())

# Report-once, like sentry.py does for a malformed DSN: the widget polls, and a gap
# that never changes would otherwise cost one Sentry event per poll forever.
_PRICING_GAPS_REPORTED: set[str] = set()


def _parse_value(payload: dict) -> float:
    result = payload["data"]["result"]
    if not result:
        return 0.0
    return float(result[0]["value"][1])


def _parse_series(payload: dict) -> list[tuple[dict, float]]:
    """Every series in an instant vector, labels kept.

    _parse_value reads `result[0]` and stops, which is correct for `sum(...)` — one
    series by construction — and silently wrong for `sum by (...)`: it would bill one
    group out of N and still return a plausible number. Grouped queries come here.
    """
    return [(serie["metric"], float(serie["value"][1])) for serie in payload["data"]["result"]]


class UnknownPricingKey(Exception):
    """A model or token type with no list price. Reported, never raised."""


async def _rate_usd_per_mtok(model: str | None, tipo: str | None) -> float:
    # `None` reaches here when the label itself is missing from a series, which is
    # the same situation as an unrecognised value: unknown, so priced high and said
    # out loud rather than assumed to be free.
    prezzi = PRICES_USD_PER_MTOK.get(model)
    if prezzi is None:
        await _report_pricing_gap(f"model {model!r}")
        prezzi = _FALLBACK_RATES
    rate = prezzi.get(tipo)
    if rate is None:
        await _report_pricing_gap(f"token type {tipo!r}")
        rate = _DEAREST_RATE
    return rate


async def _report_pricing_gap(what: str) -> None:
    if what in _PRICING_GAPS_REPORTED:
        return
    _PRICING_GAPS_REPORTED.add(what)
    await capture_exception(
        UnknownPricingKey(f"{what} has no list price — billed at the dearest known rate"),
        tags={"endpoint": "status"},
    )


async def _cost_usd(payload: dict) -> float:
    # A list comprehension, not a generator: `await` inside a generator expression
    # makes it an async generator, which sum() refuses to consume. The 0.0 start
    # keeps an empty result a float — `sum([])` is the int 0, and the endpoint would
    # answer `"cost_usd_today": 0` instead of `0.0` on a quiet day.
    return sum(
        [
            tokens * await _rate_usd_per_mtok(metric.get("model"), metric.get("type")) / 1_000_000
            for metric, tokens in _parse_series(payload)
        ],
        0.0,
    )


def require_valid_token(authorization: str = Header(default="")) -> None:
    if not secrets.compare_digest(authorization, f"Bearer {STATUS_API_TOKEN}"):
        raise HTTPException(status_code=401, detail="unauthorized")


RequireToken = Annotated[None, Depends(require_valid_token)]


async def _query_one(client: httpx.AsyncClient, query: str) -> dict:
    response = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
    response.raise_for_status()
    return response.json()


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
    # change) raises inside _parse_value or _parse_series and would otherwise become
    # an unhandled 500 with no Sentry capture, breaking this endpoint's "every
    # upstream failure is a controlled 502" promise. Parsing therefore stays inside
    # the try, next to the fetch it can only fail because of.
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            results = await asyncio.gather(*(_query_one(client, q) for q in QUERIES.values()))
        payloads = dict(zip(QUERIES.keys(), results))
        values = {
            "sessions_today": int(_parse_value(payloads["sessions_today"])),
            "tokens_today": int(_parse_value(payloads["tokens_today"])),
            "cost_usd_today": round(await _cost_usd(payloads["cost_usd_today"]), 2),
        }
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        await capture_exception(exc, tags={"endpoint": "status"})
        raise HTTPException(status_code=502, detail="upstream unavailable") from exc

    return values
