import json
import time

import httpx
import main
import respx
import sentry
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# Query strings must stay in sync with main.QUERIES — spelled out here on purpose,
# not imported: a copy that has to be edited by hand is what makes a query change
# visible in review. Sums of `max_over_time`, because Claude Code emits one series per
# session and each is a counter that never grows again once the session ends, so
# `increase()` over those is structurally zero and a plain sum loses the ones the
# Collector has stopped exporting. See the comment in main.py; both halves were
# measured, not reasoned about.
SESSIONS_Q = "sum(max_over_time(claude_code_session_count[25h]))"
TOKENS_Q = "sum(max_over_time(claude_code_token_usage[25h]))"
COST_Q = "sum by (model, type) (max_over_time(claude_code_token_usage[25h]))"


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
    # answer 3.26 while looking perfectly healthy. That silent truncation is the
    # failure this test exists to catch, not the arithmetic.
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost(
        [
            _serie("claude-opus-5", "cacheCreation", 326_484),  # 3.264840
            _serie("claude-opus-5", "cacheRead", 515_573),  # 0.257787
            _serie("claude-opus-5", "input", 22_822),  # 0.114110
            _serie("claude-opus-5", "output", 4_582),  # 0.114550
        ]
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 3.75
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
    assert response.json()["cost_usd_today"] == 153.90  # fable 81.00 + opus 40.50 + sonnet 24.30 + haiku 8.10
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


class _OrologioFinto:
    """Il tempo come parametro, non come attesa.

    Il watchdog ha due intervalli (una notifica all'ora, un controllo al minuto) e
    testarli aspettando davvero significherebbe un test da un'ora. `main` chiama
    `time.monotonic()`, quindi basta sostituire il modulo: `avanza()` è il salto
    temporale che il test vuole misurare.
    """

    def __init__(self) -> None:
        self.adesso = 1_000.0

    def __getattr__(self, nome: str):
        # Delega tutto il resto al modulo vero. Senza, il giorno che main.py usasse
        # anche `time.time()` questo finto solleverebbe AttributeError da una riga del
        # percorso pubblico, e il test si presenterebbe come "/status risponde 500"
        # invece che come "l'orologio finto è incompleto".
        return getattr(time, nome)

    def monotonic(self) -> float:
        return self.adesso

    def avanza(self, secondi: float) -> None:
        self.adesso += secondi


def _reset_watchdog(monkeypatch) -> _OrologioFinto:
    # I due globali si azzerano insieme, sempre: sono stato di processo condiviso fra
    # i test, e uno lasciato sporco fa passare (o fallire) il test successivo per
    # ragioni che non hanno niente a che vedere con quello che asserisce.
    monkeypatch.setattr(main, "_INFRA_ALERTS_SENT", {})
    monkeypatch.setattr(main, "_persistence_checked_at", None)
    orologio = _OrologioFinto()
    monkeypatch.setattr(main, "time", orologio)
    return orologio


def _mock_persistence(value: str):
    return respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, value]}]}})
    )


@respx.mock
def test_a_prometheus_that_cannot_persist_is_reported(monkeypatch):
    # The failure this watchdog exists for: on 2026-08-13 compaction failed once a
    # minute for hours while /status kept answering correct, moving numbers, because
    # the head block lives in RAM. The assertion that matters is BOTH halves — the
    # alert fires AND the three numbers are still served. A watchdog that degraded
    # the endpoint would be a worse bug than the one it reports.
    _reset_watchdog(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("7")

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["cost_usd_today"] == 1.42
    evento = _evento_inviato(sentry_call)
    assert evento["exception"]["values"][0]["type"] == "PrometheusNotPersisting"
    assert "failed 7 TSDB compactions" in evento["exception"]["values"][0]["value"]


@respx.mock
def test_a_healthy_prometheus_is_silent(monkeypatch):
    # Zero must not report. Stated as its own test because the cheapest way to make
    # the test above pass is an unconditional capture, and that would page on every
    # single poll of a perfectly healthy hub.
    _reset_watchdog(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("0")

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert not sentry_call.called


@respx.mock
def test_a_broken_watchdog_never_breaks_the_endpoint(monkeypatch):
    # The probe's own query fails. The endpoint must still answer 200 with the three
    # numbers: silence is the contract, an outage is not. This is the test that lets
    # the watchdog live on the read path of a public endpoint at all.
    _reset_watchdog(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(500)
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json() == {
        "sessions_today": 3,
        "tokens_today": 48213,
        "cost_usd_today": 1.42,
    }


@respx.mock
def test_the_persistence_alert_is_not_repeated_on_every_poll(monkeypatch):
    # Same reasoning as the pricing gaps: the widget polls every 20s, and a condition
    # that stays true until a human fixes a volume would otherwise burn the Sentry
    # quota on one identical event per poll, forever.
    _reset_watchdog(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("7")

    for _ in range(3):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert sentry_call.call_count == 1


@respx.mock
def test_a_failure_that_lasts_keeps_calling(monkeypatch):
    # Il guasto vero, e la ragione di questa PR. Fino al 19/08/2026 la notifica era
    # una per VITA DEL PROCESSO: il 13/08 la compaction ha iniziato a fallire, Sentry
    # ha ricevuto un evento, e per sei giorni — con il guasto sempre in corso — non ne
    # ha ricevuti altri. Su Sentry l'issue mostrava "ultimo evento cinque giorni fa",
    # cioè esattamente l'aspetto di un guasto RIENTRATO. Un allarme che tace mentre la
    # condizione dura non è un allarme silenzioso: è un allarme che mente.
    orologio = _reset_watchdog(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("7")

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 1

    # Un secondo prima della scadenza non deve richiamare: senza questa metà, il
    # test passerebbe anche con l'intervallo rimosso del tutto.
    orologio.avanza(main.INFRA_ALERT_INTERVAL_S - 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 1

    # Oltre l'ora, e oltre la finestra del controllo: i due intervalli sono annidati,
    # perché la notifica può ripartire solo su un controllo che è davvero avvenuto.
    # Scritto prima con un salto di 2 secondi, questo test falliva — e aveva ragione
    # lui: quel salto descrive una sequenza che in produzione non esiste, dove il
    # widget interroga ogni 20s. La conseguenza vera è che l'allarme riparte entro
    # PERSISTENCE_CHECK_INTERVAL_S dallo scadere dell'ora, non nell'istante esatto.
    orologio.avanza(main.PERSISTENCE_CHECK_INTERVAL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 2


def test_the_alert_intervals_are_the_ones_we_decided():
    # I due intervalli sono POLITICA, non meccanismo, e l'incidente del 13/08 e' stato
    # un guasto di politica: il meccanismo funzionava, l'intervallo era di fatto
    # infinito. Ogni altro test qui usa `main.INFRA_ALERT_INTERVAL_S` come riferimento,
    # quindi resterebbe verde anche portandolo a un giorno. Questa e' l'unica riga che
    # si accorge di un cambio di valore.
    assert main.INFRA_ALERT_INTERVAL_S == 3600.0
    assert main.PERSISTENCE_CHECK_INTERVAL_S == 60.0


def test_the_fake_clock_is_a_clock_and_not_just_one_attribute():
    # `_reset_watchdog` sostituisce l'INTERO modulo time dentro main. Se il finto
    # esponesse solo monotonic(), il giorno che main.py usasse time.time() il test
    # fallirebbe con AttributeError su una riga del percorso pubblico — cioe' come
    # "/status risponde 500" invece che come "l'orologio finto e' incompleto".
    orologio = _OrologioFinto()
    assert orologio.monotonic() == orologio.adesso
    assert orologio.strftime is time.strftime


@respx.mock
def test_a_recovered_fault_alerts_again_immediately(monkeypatch):
    # Senza questo, il timestamp sopravvive alla guarigione: un incidente NUOVO che
    # comincia venti minuti dopo l'allarme precedente resta muto per quaranta, e la
    # sua "prima comparsa" su Sentry e' un'ora piu' tardi di quando e' iniziato. Il
    # ricordo va cancellato quando la condizione rientra, altrimenti l'intervallo
    # protegge dal rumore ma falsifica la cronologia.
    orologio = _reset_watchdog(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])

    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "7"]}]}})
    )
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 1

    # Il guasto rientra: qualcuno ha allargato il volume.
    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "0"]}]}})
    )
    orologio.avanza(main.PERSISTENCE_CHECK_INTERVAL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 1

    # E torna, ben dentro l'ora. Deve richiamare subito: e' un incidente nuovo.
    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "3"]}]}})
    )
    orologio.avanza(main.PERSISTENCE_CHECK_INTERVAL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 2


@respx.mock
def test_the_alert_repeats_exactly_at_the_interval_not_a_second_later(monkeypatch):
    # Il confine, esplicito: con `<` al posto di `<=` (o viceversa) questo cambia, e
    # senza un test sul valore esatto quel mutante sopravvive senza che nulla lo veda.
    orologio = _reset_watchdog(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("7")

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    orologio.avanza(main.INFRA_ALERT_INTERVAL_S)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert sentry_call.call_count == 2


@respx.mock
def test_the_watchdog_does_not_query_prometheus_on_every_public_request(monkeypatch):
    # Costo, non correttezza. L'endpoint pubblico non è throttlato e dal 19/08/2026 il
    # progetto è su un piano a consumo: senza intervallo, ogni richiesta pubblica
    # comprava QUATTRO query a Prometheus invece di tre, cioè un moltiplicatore di
    # spesa a disposizione di chiunque conosca l'URL.
    orologio = _reset_watchdog(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    sonda = _mock_persistence("0")

    for _ in range(5):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert sonda.call_count == 1

    orologio.avanza(main.PERSISTENCE_CHECK_INTERVAL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sonda.call_count == 2


@respx.mock
def test_a_watchdog_with_no_data_says_so_instead_of_going_quiet(monkeypatch):
    # The failure the watchdog itself can have. If Prometheus stops scraping itself,
    # the metric disappears; an empty result parses to 0.0 and a `<= 0` check reads
    # that as "healthy". Silent and blind become the same output, which is the exact
    # failure class this watchdog was added to end — and the lesson the Collector job
    # in docker/prometheus.yml already spells out: no series means no rule can fire.
    _reset_watchdog(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    # Prometheus answers, correctly, with an empty vector: the metric is not there.
    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json={"data": {"result": []}})
    )

    response = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    evento = _evento_inviato(sentry_call)
    assert evento["exception"]["values"][0]["type"] == "PrometheusWatchdogBlind"
    assert "not scraping itself" in evento["exception"]["values"][0]["value"]


def test_only_status_is_routed_at_all():
    # SECURITY.md says the public surface is three aggregate numbers. FastAPI adds
    # /docs, /redoc and /openapi.json by default, and `require_valid_token` guards
    # only /status — so every one of those would answer without a token if anything
    # ever got past Cloudflare Access. Access does cover the whole host today
    # (measured: 401 on every path), but this repo's own rule is that a hostname is
    # not an access control, and the schema names the auth scheme it protects.
    for percorso in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(percorso).status_code == 404, percorso
