import json

import httpx
import respx
from fastapi.testclient import TestClient

import main
import sentry
from main import app

client = TestClient(app)

# Query strings must stay in sync with main.QUERIES. Plain sums, because Claude Code
# emits one series per session and each is a counter that never grows again once the
# session ends — `increase()` over those is structurally zero. See the comment in
# main.py; it was measured in production, not reasoned about.
SESSIONS_Q = "sum(claude_code_session_count)"
TOKENS_Q = "sum(claude_code_token_usage)"
COST_Q = "sum by (model, type) (claude_code_token_usage)"


def _serie(model: str, tipo: str, value) -> dict:
    """One Prometheus instant-vector sample, labels included.

    The cost query groups `by (model, type)`, so its answer is N series, not one —
    the whole point of the parser this feeds.
    """
    return {"metric": {"model": model, "type": tipo}, "value": [0, str(value)]}


def _mock_scalars(sessions: str = "3", tokens: str = "48213") -> None:
    respx.get("http://prometheus:9090/api/v1/query", params={"query": SESSIONS_Q}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, sessions]}]}})
    )
    respx.get("http://prometheus:9090/api/v1/query", params={"query": TOKENS_Q}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, tokens]}]}})
    )


def _mock_cost(series: list[dict]):
    return respx.get("http://prometheus:9090/api/v1/query", params={"query": COST_Q}).mock(
        return_value=httpx.Response(200, json={"data": {"result": series}})
    )


@respx.mock
def test_status_returns_whitelisted_fields_only():
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json() == {
        "sessions_today": 3,
        "tokens_today": 48213,
        "cost_usd_today": 1.42,  # 284_000 input tokens at $5.00/Mtok
    }


@respx.mock
def test_cost_is_computed_from_token_counts_across_every_type(monkeypatch):
    # Real production numbers, read off the hub on 2026-07-30. They matter because
    # cache tokens dominate the bill here (cacheCreation alone is ~80% of it), and
    # any parser that reads `result[0]` and stops — as _parse_value does — would
    # answer 2.04 while looking perfectly healthy. That silent truncation is the
    # failure this test exists to catch, not the arithmetic.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost(
        [
            _serie("claude-opus-5", "cacheCreation", 326_484),  # 2.040525
            _serie("claude-opus-5", "cacheRead", 515_573),  # 0.257787
            _serie("claude-opus-5", "input", 22_822),  # 0.114110
            _serie("claude-opus-5", "output", 4_582),  # 0.114550
        ]
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 2.53
    # With a single model in the price table the fallback rates EQUAL that model's
    # rates, so a lookup that quietly falls through to the fallback produces the
    # right number for the wrong reason — the arithmetic above cannot see it. What
    # can: a known model and known types must report no pricing gap. This killed
    # six mutants (2026-07-30) that rerouted known models through the fallback.
    assert not sentry_call.called


@respx.mock
def test_costs_from_several_models_are_summed(monkeypatch):
    # One request can span models (a Task subagent on a cheaper one, a compaction
    # pass). Grouping by model and keeping only the first group would drop real
    # spend; the total must be the sum over every group.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost(
        [
            _serie("claude-opus-5", "input", 1_000_000),  # 5.00 from the table
            _serie("claude-sonnet-9-preview", "input", 1_000_000),  # 10.00: fallback = dearest input
        ]
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 15.00


@respx.mock
def test_unknown_model_is_priced_high_and_reported_not_dropped(monkeypatch):
    # A model missing from the price table must never be worth zero: that reads as
    # "cheap day" instead of "we have no idea", and nobody goes looking. It is
    # priced at the dearest rates we know — so the number can only ever overstate —
    # and Sentry is told, because the fix is a table update, not a code change.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-6-unreleased", "output", 1_000_000)])

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 50.00  # dearest known rate for `output`
    # The report is only useful if it says WHICH key is missing and where it came
    # from: a gap event with no model name, the wrong exception type or an
    # unsearchable tag is a report nobody can act on. Asserted on the wire, not on
    # the dict sentry.py built — same reasoning as _evento_inviato itself.
    evento = _evento_inviato(sentry_call)
    assert evento["exception"]["values"][0]["type"] == "UnknownPricingKey"
    assert "model 'claude-opus-6-unreleased'" in evento["exception"]["values"][0]["value"]
    assert evento["tags"] == {"endpoint": "status"}


@respx.mock
def test_unknown_token_type_is_priced_high_and_reported_not_dropped(monkeypatch):
    # Same contract one level down: Claude Code's telemetry is beta and its `type`
    # values are not a frozen set, so a new one (a second cache tier, say) must not
    # be quietly free. Dearest known rate, and a report.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "cacheRefresh", 1_000_000)])

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 50.00  # dearest rate on the table
    # Same wire-level check as the unknown-model test: the event must name the
    # missing key, or the price table cannot be fixed from the report alone.
    assert "token type 'cacheRefresh'" in _evento_inviato(sentry_call)["exception"]["values"][0]["value"]


@respx.mock
def test_a_pricing_gap_is_reported_once_not_on_every_request(monkeypatch):
    # The widget polls. An unreported gap is invisible, but one report per poll is
    # a Sentry quota burned on a message that never changes — the same
    # report-once rule sentry.py already applies to a malformed DSN.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-6-unreleased", "output", 1_000_000)])

    for _ in range(3):
        risposta = client.get("/status", headers={"Authorization": "Bearer test-token"})
        assert risposta.status_code == 200

    assert sentry_call.call_count == 1


@respx.mock
def test_cost_series_without_a_value_returns_502_not_500(monkeypatch):
    # The narrow version of the malformed-shape test below: only the cost query
    # answers strangely, so it pins the multi-series parser specifically rather
    # than passing because every query happened to be broken at once.
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([{"metric": {"model": "claude-opus-5", "type": "input"}}])

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502
    assert sentry_call.called


@respx.mock
def test_status_returns_zeros_when_no_data_yet():
    # Empty `result` is what Prometheus returns before the first session is ever
    # recorded, or after the volume is recreated — a legitimate 200 with zeros, not a
    # 502. Locks in _parse_value's empty-result branch.
    respx.get("http://prometheus:9090/api/v1/query").mock(
        return_value=httpx.Response(200, json={"data": {"result": []}})
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json() == {
        "sessions_today": 0,
        "tokens_today": 0,
        "cost_usd_today": 0.0,
    }
    # `0 == 0.0` is True, so the dict comparison above cannot tell them apart —
    # but JSON can: without the explicit 0.0 start value, `sum([])` is the int 0
    # and the quiet-day payload changes type under the widget's feet.
    assert isinstance(response.json()["cost_usd_today"], float)


@respx.mock
def test_every_model_is_priced_from_its_own_row_not_the_fallback(monkeypatch):
    # One series per (model, type) in the table, one million tokens each: the
    # expected total is simply the sum of all sixteen list rates. With several
    # models in the table the fallback rates no longer equal any single model's
    # rates, so this pins each row's numbers individually — a mutated price, a
    # misspelled model key or a silent fall-through changes the total or fires
    # a Sentry gap report, and either fails the test.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost(
        [
            _serie(model, tipo, 1_000_000)
            for model, prezzi in main.PRICES_USD_PER_MTOK.items()
            for tipo in prezzi
        ]
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 139.65  # fable 73.50 + opus 36.75 + sonnet 22.05 + haiku 7.35
    assert not sentry_call.called


@respx.mock
def test_a_month_of_tokens_does_not_hide_a_wrong_divisor(monkeypatch):
    # `round(..., 2)` forgives a divisor that is off by one (1_000_001 instead of
    # 1_000_000) on any day cheap enough — the error only clears a cent past ~$5k.
    # Token counts big enough to make the difference visible pin the divisor
    # exactly; the daily-sized tests above never can.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 2_000_000_000_000)])

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 10_000_000.0


def test_status_rejects_missing_token():
    response = client.get("/status")
    assert response.status_code == 401


def test_status_rejects_wrong_token():
    response = client.get("/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    # The body is asserted, not just the code: with only the status checked, four
    # mutants of this rejection survived the 2026-07-28 mutation run (detail=None,
    # detail removed, detail retyped) — a 401 that says nothing distinguishable is
    # a rejection nobody can debug.
    assert response.json() == {"detail": "unauthorized"}


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


@respx.mock
def test_without_sentry_dsn_capture_is_a_noop(monkeypatch):
    # The no-DSN branch is the default wherever SENTRY_DSN is left empty
    # in docker/.env — it must stay a silent no-op, not an error path.
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502
    assert not any(call.request.url.host == "example.sentry.io" for call in respx.calls)


@respx.mock
def test_malformed_sentry_dsn_is_reported_not_swallowed(monkeypatch, capsys):
    # A DSN that is set but unparseable means someone believes error reporting
    # works. Silence there is the same failure this endpoint exists to avoid.
    #
    # The report-once flag is module state that outlives a test: anything that
    # ran the malformed path earlier in the same process leaves it True and this
    # test would then pass vacuously (a mutant of the once-check survived the
    # 2026-07-30 mutation run for exactly that reason). Reset it explicitly.
    monkeypatch.setattr(sentry, "_dsn_malformato_segnalato", False)
    monkeypatch.setenv("SENTRY_DSN", "questo-non-e-un-dsn")
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502  # fail-open still holds
    errori = capsys.readouterr().err
    assert "does not parse" in errori
    assert "questo-non-e-un-dsn" not in errori  # mai il valore nei log


@respx.mock
def test_sentry_delivery_failure_does_not_change_the_response(monkeypatch):
    # Fail-open contract, ported from marcobellingeri.dev/engine/lib/sentry.mjs:
    # a broken Sentry must never turn a controlled 502 into an unhandled 500.
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))
    respx.post("https://example.sentry.io/api/9/envelope/").mock(
        side_effect=httpx.ConnectError("sentry down")
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502


def _evento_inviato(sentry_call) -> dict:
    """The event out of the envelope Sentry actually received.

    An envelope is newline-delimited JSON — header, item header, payload — so
    these assertions read what went over the wire instead of trusting the dict
    built in sentry.py. The payload is found by shape rather than by line number:
    the envelope layout is ours, and a test pinned to an index would fail for a
    reordering that broke nothing.
    """
    righe = sentry_call.calls.last.request.content.decode().splitlines()
    return next(json.loads(riga) for riga in righe if riga and '"exception"' in riga)


@respx.mock
def test_sentry_event_carries_the_deploy_sha_as_release(monkeypatch):
    # Without a release every error in Sentry belongs to the same unnamed
    # version, and "is this still happening after the fix?" has no answer.
    # Dormant in production — Railway sets no git variable at runtime (measured
    # 2026-07-30) — so this guards the contract for the day a platform does.
    SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", SHA)
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502
    assert _evento_inviato(sentry_call)["release"] == SHA


@respx.mock
def test_sentry_event_omits_release_when_the_deploy_sha_is_absent(monkeypatch):
    # Railway sets RAILWAY_GIT_COMMIT_SHA on every deployment; nothing off the
    # platform does — not local runs, not `docker compose`. This is that case.
    # Sending an empty release would file those errors under a version that does
    # not exist, which is worse than sending none — missing, not blank.
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 502
    assert "release" not in _evento_inviato(sentry_call)
