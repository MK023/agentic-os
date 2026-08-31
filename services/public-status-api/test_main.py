import json
import time

import httpx
import main
import pytest
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
# `{job="otel-collector"}` e' una parte del contratto, non decorazione: senza il
# filtro di provenienza qualunque target scrapato che serva una metrica CHIAMATA
# `claude_code_token_usage` finisce nella somma pubblica. Aggiunto il 2026-08-20,
# dopo che un'iniezione da un target diverso ha messo 1e12 token nella cifra
# pubblica di uno stack di prova. Queste costanti sono duplicate apposta rispetto a
# `QUERIES`: se qualcuno cambia la query in main.py e non qui, questi test vanno
# rossi — e' il loro mestiere, ed e' successo davvero il giorno del cambio.
# Il testimone del watchdog degli zeri, duplicato qui per la stessa ragione delle tre
# sopra. La finestra e' larga E DIVERSA da 25h apposta: se qualcuno allineasse le due,
# il testimone risponderebbe sempre come i tre numeri e smetterebbe di testimoniare
# senza che niente diventi rosso.
HISTORY_Q = 'count(present_over_time(claude_code_session_count{job="otel-collector"}[7d]))'
SESSIONS_Q = 'sum(max_over_time(claude_code_session_count{job="otel-collector"}[25h]))'
TOKENS_Q = 'sum(max_over_time(claude_code_token_usage{job="otel-collector"}[25h]))'
COST_Q = 'sum by (model, type) (max_over_time(claude_code_token_usage{job="otel-collector"}[25h]))'


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
    #
    # L'orologio avanza oltre la finestra della cache fra una richiesta e l'altra,
    # altrimenti questo test non misura piu' cio' che dice: dal 19/08/2026 /status
    # tiene una cache di 60s, quindi tre richieste ravvicinate arrivano al codice UNA
    # volta sola e il test resterebbe verde anche senza nessuna dedup. Era gia'
    # successo, ed e' stata la coverage a dirlo — la riga del ramo "gia' segnalato"
    # ha smesso di essere eseguita.
    orologio = _reset_stato_di_processo(monkeypatch)
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
        orologio.avanza(main.STATUS_CACHE_TTL_S + 1)

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


def test_opus_4_8_ha_un_prezzo_suo_e_non_il_fallback():
    # Regressione di AGENTI-OS-8, misurata in produzione il 2026-08-21: la produzione
    # ha emesso `claude-opus-4-8`, la tabella non lo aveva, e ogni suo token e' stato
    # prezzato alla tariffa piu' cara conosciuta. Un test sul solo nome, perche' e'
    # il nome che mancava — il ciclo generico sopra passerebbe anche se la riga
    # sparisse di nuovo, dato che itera la tabella e non un elenco atteso.
    assert "claude-opus-4-8" in main.PRICES_USD_PER_MTOK
    assert main.PRICES_USD_PER_MTOK["claude-opus-4-8"]["input"] < main._DEAREST_RATE


@respx.mock
def test_every_model_is_priced_from_its_own_row_not_the_fallback(monkeypatch):
    # One series per (model, type) in the table, one million tokens each: the
    # expected total is simply the sum of all twenty list rates (cinque modelli per
    # quattro tipi — erano sedici finche' la tabella non ha conosciuto Opus 4.8).
    # With several
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
    # fable 81.00 + opus-5 40.50 + opus-4-8 40.50 + sonnet-5 16.20 + haiku 8.10.
    # Si aggiorna A MANO quando la tabella cambia, ed e' il gesto che si vuole
    # rendere visibile: il 2026-08-21 questa riga e' diventata rossa da sola perche'
    # sonnet-5 e' passato da 24.30 a 16.20, cioe' il repository sovrastimava del 50%.
    assert response.json()["cost_usd_today"] == 186.30
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


@pytest.fixture(autouse=True)
def _stato_pulito(monkeypatch):
    """Ogni test parte con lo stato del watchdog azzerato.

    I limitatori vivono in dizionari a livello di modulo: senza questo, il primo
    test che segnala un guasto mette una chiave che silenzia i test successivi, e
    quelli falliscono (o passano) per ragioni che non hanno niente a che vedere con
    cio' che asseriscono. E' successo davvero il 19/08, quando la cattura del 502 e'
    passata dal limitatore: quattro test preesistenti sono diventati rossi in blocco.
    Autouse, perche' ricordarsene a mano e' esattamente il tipo di disciplina che
    smette di funzionare al primo test scritto di fretta.
    """
    _reset_stato_di_processo(monkeypatch)


def _reset_stato_di_processo(monkeypatch) -> _OrologioFinto:
    # TUTTO lo stato di processo si azzera insieme, sempre: e' condiviso fra i test, e
    # uno lasciato sporco fa passare (o fallire) il test successivo per ragioni che non
    # hanno niente a che vedere con cio' che asserisce. La cache delle risposte e'
    # arrivata dopo i due limitatori e dimenticarla qui ha fatto fallire trenta test in
    # blocco — nel modo giusto, cioe' subito e rumorosamente.
    monkeypatch.setattr(main, "_INFRA_ALERTS_SENT", {})
    monkeypatch.setattr(main, "_status_cache", None)
    monkeypatch.setattr(main, "_ZERO_PASSES", 0)
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
    _reset_stato_di_processo(monkeypatch)
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
    # Testo per intero, non un frammento: e' la frase che qualcuno leggera' alle 3 di
    # notte, e "check the volume" e' l'unica istruzione operativa che contiene.
    assert evento["exception"]["values"][0]["value"] == (
        "Prometheus failed 7 TSDB compactions in the last hour — it still answers "
        "queries, but it is not writing blocks; check the volume"
    )
    # Il tag e' come si trovano questi eventi fra tutti gli altri.
    assert evento["tags"]["endpoint"] == "status"


@respx.mock
def test_a_healthy_prometheus_is_silent(monkeypatch):
    # Zero must not report. Stated as its own test because the cheapest way to make
    # the test above pass is an unconditional capture, and that would page on every
    # single poll of a perfectly healthy hub.
    _reset_stato_di_processo(monkeypatch)
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
    _reset_stato_di_processo(monkeypatch)
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
    #
    # E come li', l'orologio deve avanzare oltre la cache fra una richiesta e l'altra:
    # altrimenti il limitatore non viene nemmeno raggiunto e questo test passerebbe
    # anche se non esistesse.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("7")

    for _ in range(3):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
        orologio.avanza(main.STATUS_CACHE_TTL_S + 1)

    assert sentry_call.call_count == 1


@respx.mock
def test_a_failure_that_lasts_keeps_calling(monkeypatch):
    # Il guasto vero, e la ragione di questa PR. Fino al 19/08/2026 la notifica era
    # una per VITA DEL PROCESSO: il 13/08 la compaction ha iniziato a fallire, Sentry
    # ha ricevuto un evento, e per sei giorni — con il guasto sempre in corso — non ne
    # ha ricevuti altri. Su Sentry l'issue mostrava "ultimo evento cinque giorni fa",
    # cioè esattamente l'aspetto di un guasto RIENTRATO. Un allarme che tace mentre la
    # condizione dura non è un allarme silenzioso: è un allarme che mente.
    orologio = _reset_stato_di_processo(monkeypatch)
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
    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 2


def test_the_alert_intervals_are_the_ones_we_decided():
    # I due intervalli sono POLITICA, non meccanismo, e l'incidente del 13/08 e' stato
    # un guasto di politica: il meccanismo funzionava, l'intervallo era di fatto
    # infinito. Ogni altro test qui usa `main.INFRA_ALERT_INTERVAL_S` come riferimento,
    # quindi resterebbe verde anche portandolo a un giorno. Questa e' l'unica riga che
    # si accorge di un cambio di valore.
    assert main.INFRA_ALERT_INTERVAL_S == 3600.0
    assert main.STATUS_CACHE_TTL_S == 60.0


def test_the_fake_clock_is_a_clock_and_not_just_one_attribute():
    # `_reset_stato_di_processo` sostituisce l'INTERO modulo time dentro main. Se il finto
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
    orologio = _reset_stato_di_processo(monkeypatch)
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
    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 1

    # E torna, ben dentro l'ora. Deve richiamare subito: e' un incidente nuovo.
    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "3"]}]}})
    )
    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 2


@respx.mock
def test_the_alert_repeats_exactly_at_the_interval_not_a_second_later(monkeypatch):
    # Il confine, esplicito: con `<` al posto di `<=` (o viceversa) questo cambia, e
    # senza un test sul valore esatto quel mutante sopravvive senza che nulla lo veda.
    orologio = _reset_stato_di_processo(monkeypatch)
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
def test_a_single_compaction_failure_is_already_worth_saying(monkeypatch):
    # `failures <= 0` e non `< 1`: una sola compaction fallita e' gia' il guasto del
    # 13/08 al suo primo minuto. La soglia che "aspetta di essere sicura" e' come si
    # arriva a scoprirlo sei giorni dopo.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("1")

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert sentry_call.call_count == 1
    assert "failed 1 TSDB compactions" in _evento_inviato(sentry_call)["exception"]["values"][0]["value"]


@respx.mock
def test_the_check_runs_again_exactly_at_the_cache_window(monkeypatch):
    # Il confine del controllo, gemello di quello sull'allarme: a intervallo esatto il
    # controllo DEVE ripartire. Senza, `<` e `<=` sono indistinguibili.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    sonda = _mock_persistence("0")

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    orologio.avanza(main.STATUS_CACHE_TTL_S)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert sonda.call_count == 2


@respx.mock
def test_the_blind_alert_has_its_own_memory(monkeypatch):
    # Le due condizioni hanno chiavi separate, e la chiave del cieco si dimentica da
    # sola quando la serie torna. Se le chiavi collidessero (o non si cancellassero),
    # un watchdog tornato cieco resterebbe muto per un'ora dopo essere guarito una
    # volta — cioe' il guasto del guasto, che e' il caso che questa sonda esiste per
    # non ripetere.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])

    cieco = {"data": {"result": []}}
    sano = {"data": {"result": [{"value": [0, "0"]}]}}

    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json=cieco)
    )
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 1

    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json=sano)
    )
    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 1

    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json=cieco)
    )
    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sentry_call.call_count == 2


@pytest.mark.parametrize(
    "serie",
    [
        {"metric": {}},  # 200 con un vettore non vuoto ma senza "value"
        {"value": [0]},  # "value" troncato: manca il campione
        {"value": [0, "non-un-numero"]},
        {"value": [0, "+Inf"]},  # float() lo accetta, int() no: OverflowError
    ],
    ids=["senza-value", "value-troncato", "non-numerico", "inf"],
)
@respx.mock
def test_a_malformed_persistence_answer_never_takes_status_down(monkeypatch, serie):
    # La docstring della sonda promette che NIENTE al suo interno puo' far cadere
    # /status, e il primo blocco lo manteneva: fetch e ["data"]["result"] dentro il try.
    # Il parse del campione stava FUORI, e la chiamata in status() e' fuori da ogni
    # try per scelta (il watchdog non deve condividere il percorso di guasto delle tre
    # query). Quindi un 200 di forma inattesa — Prometheus a meta' riavvio, una
    # versione futura dell'API — diventava un 500 senza cattura con i tre numeri gia'
    # in mano: la stessa classe chiusa per le tre query il 20/08, mai specchiata qui.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    respx.get("http://prometheus:9090/api/v1/query", params={"query": main.PERSISTENCE_QUERY}).mock(
        return_value=httpx.Response(200, json={"data": {"result": [serie]}})
    )

    risposta = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert risposta.status_code == 200
    assert risposta.json()["sessions_today"] == 3


@respx.mock
def test_the_watchdog_does_not_query_prometheus_on_every_public_request(monkeypatch):
    # Costo, non correttezza. L'endpoint pubblico non è throttlato e dal 19/08/2026 il
    # progetto è su un piano a consumo: senza intervallo, ogni richiesta pubblica
    # comprava QUATTRO query a Prometheus invece di tre, cioè un moltiplicatore di
    # spesa a disposizione di chiunque conosca l'URL.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    sonda = _mock_persistence("0")

    for _ in range(5):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert sonda.call_count == 1

    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert sonda.call_count == 2


@respx.mock
def test_a_watchdog_with_no_data_says_so_instead_of_going_quiet(monkeypatch):
    # The failure the watchdog itself can have. If Prometheus stops scraping itself,
    # the metric disappears; an empty result parses to 0.0 and a `<= 0` check reads
    # that as "healthy". Silent and blind become the same output, which is the exact
    # failure class this watchdog was added to end — and the lesson the Collector job
    # in docker/prometheus.yml already spells out: no series means no rule can fire.
    _reset_stato_di_processo(monkeypatch)
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
    assert evento["exception"]["values"][0]["value"] == (
        "prometheus_tsdb_compactions_failed_total returned no series — Prometheus is "
        "not scraping itself, so this watchdog is blind, not healthy; check the "
        "`prometheus` job in prometheus.yml"
    )
    assert evento["tags"]["endpoint"] == "status"


@respx.mock
def test_a_broken_upstream_does_not_hand_out_one_sentry_envelope_per_request(monkeypatch):
    # Il percorso di ERRORE costava piu' di quello di successo. Ogni 502 apriva un
    # client nuovo verso sentry.io, handshake TLS compreso, senza alcun limitatore —
    # mentre ogni altra cattura in questo file ne ha uno. Con Prometheus giu' e il
    # widget che interroga ogni 20s sono ~4.300 eventi al giorno senza nessun
    # attaccante: la quota Sentry muore da sola, e muore proprio quando serve.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))

    for _ in range(5):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 502

    # Il 502 resta su OGNI richiesta: si limita la segnalazione, non la risposta.
    assert sentry_call.call_count == 1

    orologio.avanza(main.INFRA_ALERT_INTERVAL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 502
    assert sentry_call.call_count == 2


def test_a_non_ascii_token_is_rejected_not_a_crash():
    # `secrets.compare_digest` su str solleva TypeError con qualunque byte >= 0x80, e
    # uvicorn decodifica gli header in latin-1: un byte qualsiasi in Authorization
    # diventava un 500 con traceback nei log, da chiamante NON autenticato. Nessun
    # bypass, ma una classe di risposta che il contratto dell'endpoint dichiara
    # inesistente (401/502), e volume di log non limitato su un piano a consumo.
    # Byte grezzi, non una str: httpx codifica gli header in ASCII e rifiuterebbe
    # prima di partire, mentre un attaccante scrive sul socket. uvicorn li decodifica
    # in latin-1, ed e' cosi' che il byte arriva alla comparazione come str non-ASCII.
    risposta = client.get("/status", headers={"Authorization": "Bearer \xe9".encode("latin-1")})

    assert risposta.status_code == 401


@respx.mock
def test_the_three_numbers_are_computed_once_a_minute_not_once_a_request(monkeypatch):
    # Denial-of-wallet, misurato: l'endpoint pubblico non e' throttlato e dal
    # 19/08/2026 si paga a consumo. Senza cache, ogni richiesta comprava tre query a
    # Prometheus e tre connessioni TCP nuove — un moltiplicatore a disposizione di
    # chiunque conosca l'URL. Con la cache, l'origine fa tre query al minuto
    # QUALUNQUE sia il traffico in ingresso, e il limitatore nel Worker del sito
    # diventa un secondo strato invece che l'unico.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    costo = _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("0")

    atteso = {"sessions_today": 3, "tokens_today": 48213, "cost_usd_today": 1.42}
    for _ in range(10):
        risposta = client.get("/status", headers={"Authorization": "Bearer test-token"})
        assert risposta.status_code == 200
        assert risposta.json() == atteso

    # Dieci richieste, una sola passata a Prometheus.
    assert costo.call_count == 1

    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert costo.call_count == 2


@respx.mock
def test_a_dead_upstream_is_never_served_from_cache(monkeypatch):
    # Il degrado deve dichiararsi. Una cache che, scaduta, continuasse a servire
    # l'ultimo valore buono renderebbe verde lo smoke test a hub morto — e' la
    # lezione gia' pagata sul Worker del sito. Scaduta la finestra, un upstream rotto
    # e' un 502, non un numero vecchio con la faccia di uno nuovo.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("0")

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    # Le stesse rotte, ri-puntate: respx da' precedenza a quelle registrate per
    # parametro, quindi un mock generico aggiunto dopo non vincerebbe mai.
    for query in (SESSIONS_Q, TOKENS_Q, COST_Q):
        respx.get("http://prometheus:9090/api/v1/query", params={"query": query}).mock(
            return_value=httpx.Response(500)
        )
    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 502


@respx.mock
def test_an_infinite_value_is_a_502_not_a_crash(monkeypatch):
    # `int(float('inf'))` solleva OverflowError, che e' un ArithmeticError e NON un
    # ValueError: prima di questa riga diventava il 500 senza cattura che il commento
    # accanto al try dichiarava chiuso. Non e' raggiungibile dall'esterno (nessuno
    # scrive metriche da fuori), ma la promessa "ogni guasto a monte e' un 502
    # controllato" o vale o non vale.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    respx.get("http://prometheus:9090/api/v1/query").mock(
        return_value=httpx.Response(200, json={"data": {"result": [{"value": [0, "+Inf"]}]}})
    )

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 502


@respx.mock
def test_a_cost_that_overflows_is_a_502_not_a_null(monkeypatch):
    # La moltiplicazione fra float NON solleva: trabocca a inf in silenzio, e
    # round(inf, 2) resta inf. Quindi il test qui sopra non copriva il costo da solo:
    # con "+Inf" su TUTTE le query era int(sessions_today) a scattare per primo. Un
    # campione finito ma enorme (4e306 e' un float64 valido, Prometheus lo conserva
    # 25h con max_over_time) passa int() sui token, trabocca sul prezzo, e la
    # risposta era 200 con "cost_usd_today": null — nessun 502, nessun evento Sentry,
    # e in cache per 60s. Misurato il 21/08/2026.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _mock_scalars()
    _mock_cost([_serie("claude-fable-5", "output", 4e306)])
    _mock_persistence("0")

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 502


def test_an_empty_token_is_refused_at_startup_not_served_open():
    # `os.environ["X"]` solleva se la variabile MANCA, non se e' VUOTA. Con un token
    # vuoto — una rotazione a meta', una variabile ripulita per sbaglio — il valore
    # atteso diventa "Bearer " con lo spazio finale, cioe' una credenziale valida che
    # si indovina alla prima richiesta. Meglio un deploy che non parte di un endpoint
    # che serve aperto: il guasto rumoroso e' quello che si nota.
    atteso = (
        "STATUS_API_TOKEN e' vuoto: senza un valore l'endpoint accetterebbe "
        "'Bearer ' come credenziale valida. Impostalo sul servizio Railway."
    )
    for vuoto in ("", "   ", "\t"):
        with pytest.raises(RuntimeError) as errore:
            main._token_configurato(vuoto)
        # Il messaggio per intero: e' l'unica istruzione operativa che riceve chi
        # trova il deploy fermo, e "Impostalo sul servizio Railway" e' la meta' che
        # dice dove andare.
        assert str(errore.value) == atteso

    assert main._token_configurato("un-token-vero") == "un-token-vero"


def test_every_route_either_requires_the_token_or_is_the_health_probe():
    # `require_valid_token` e' una dipendenza PER ROTTA, non un middleware: una
    # `@app.get("/qualcosa")` scritta domani nasce PUBBLICA, con il suo test verde e
    # la copertura ancora al 100%. Il commento in cima a main.py identifica proprio
    # questo rischio e poi si difende solo dalle tre rotte che FastAPI aggiunge da
    # solo. Questa e' l'asserzione sull'insieme, l'unica che vede una rotta nuova.
    from fastapi.routing import APIRoute

    senza_token = {
        rotta.path
        for rotta in app.routes
        if isinstance(rotta, APIRoute)
        and not any(d.call is main.require_valid_token for d in rotta.dependant.dependencies)
    }

    # /healthz e' deliberatamente aperta: Railway non manda header, e il suo
    # contenuto e' una costante. Ogni altra rotta senza token e' un errore.
    assert senza_token == {"/healthz"}


@respx.mock
def test_the_health_probe_answers_without_a_token_and_touches_nothing():
    # Railway promuove un deploy quando il processo parte, non quando serve: con
    # PROMETHEUS_URL sbagliato ma risolvibile l'import riesce, uvicorn ascolta, ogni
    # /status e' 502, il deploy e' SUCCESS e il container buono viene spento. Questa
    # rotta esiste perche' la piattaforma abbia qualcosa da interrogare — e Railway
    # non manda credenziali, quindi /status (401) non puo' esserlo.
    #
    # Nessuna chiamata a monte, e nessun dato: un probe che dipende da Prometheus
    # direbbe "malato" quando e' Prometheus a esserlo, spegnendo l'unica cosa che in
    # quel momento funziona ancora.
    risposta = client.get("/healthz")

    assert risposta.status_code == 200
    assert risposta.json() == {"status": "ok"}
    assert not respx.calls


def test_a_dsn_that_is_set_but_unparseable_refuses_to_boot():
    # Un DSN malformato spegne la segnalazione errori restando indistinguibile da
    # "nessun errore" — la lettura esatta che ha lasciato correre sei giorni il
    # guasto del 13/08. Stessa scelta gia' accettata per il token: meglio un deploy
    # che non parte di un reporter che qualcuno CREDE acceso.
    atteso = (
        "SENTRY_DSN e' impostato ma non ha la forma https://<chiave>@<host>/<progetto>: "
        "la segnalazione errori sarebbe spenta senza dirlo. Correggilo o rimuovilo."
    )
    for rotto in ("non-un-dsn", "https://KEY@host/9", "https://abc@host/prefisso/9"):
        with pytest.raises(RuntimeError) as errore:
            sentry.verifica_dsn(rotto)
        assert str(errore.value) == atteso

    # Assente e' un no-op deliberato, non un errore: e' il caso normale in locale.
    sentry.verifica_dsn(None)
    sentry.verifica_dsn("")
    sentry.verifica_dsn("https://abc123@example.sentry.io/9")


@respx.mock
def test_pricing_gaps_cannot_grow_without_bound(monkeypatch):
    # La chiave del dedup e' un VALORE che arriva da fuori (`model`, da Prometheus).
    # Chi ha il token di ingest puo' quindi far crescere il set senza limite e
    # comprare un evento Sentry per ogni modello inventato: la stessa esaurizione di
    # quota che il limitatore sul 502 e' appena servito a impedire.
    _reset_stato_di_processo(monkeypatch)
    pieno = {f"model {i!r}" for i in range(main.MAX_PRICING_GAPS)}
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", pieno)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars()
    _mock_cost([_serie("modello-mai-visto", "input", 1_000)])

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert not sentry_call.called
    assert len(main._PRICING_GAPS_REPORTED) == main.MAX_PRICING_GAPS


def test_only_status_is_routed_at_all():
    # SECURITY.md says the public surface is three aggregate numbers. FastAPI adds
    # /docs, /redoc and /openapi.json by default, and `require_valid_token` guards
    # only /status — so every one of those would answer without a token if anything
    # ever got past Cloudflare Access. Access does cover the whole host today
    # (measured: 401 on every path), but this repo's own rule is that a hostname is
    # not an access control, and the schema names the auth scheme it protects.
    for percorso in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(percorso).status_code == 404, percorso


def test_a_trailing_slash_is_a_404_not_a_redirect_that_echoes_the_host():
    # `redirect_slashes` e' acceso di default, quindi `/status/` rispondeva 307 PRIMA
    # che l'autenticazione venisse valutata, con una `Location` assoluta costruita
    # sull'header `Host` del chiamante — e in schema `http`, perche' uvicorn gira senza
    # `--forwarded-allow-ips` e ignora `X-Forwarded-Proto`. Non era una falla aperta
    # (h11 rifiuta CRLF negli header, quindi niente response splitting), ma era un
    # valore controllato da fuori riflesso senza credenziali, e chi avesse seguito quel
    # redirect avrebbe rispedito il bearer in chiaro verso un host scelto da altri.
    #
    # In produzione Access risponde 401 prima: cioe' e' il controllo di qualcun altro a
    # salvare questo, che e' esattamente il ragionamento che SECURITY.md rifiuta per
    # l'ingest OTLP. Nessun consumatore usa la barra finale — il Worker del sito
    # interroga `https://status.marcobellingeri.dev/status` e verify-hub.sh
    # `${STATUS_URL}/status` — quindi la rotta non ha varianti da servire.
    risposta = client.get("/status/", headers={"Host": "attaccante.example"}, follow_redirects=False)

    assert risposta.status_code == 404
    assert "location" not in risposta.headers


@respx.mock
def test_the_answer_declares_that_it_must_not_be_stored_or_sniffed():
    # Gli unici header di risposta erano `date`, `server`, `content-length` e
    # `content-type`: una risposta autenticata, vecchia fino a un minuto per via della
    # cache applicativa, che non diceva niente di se' stessa a chi la riceve.
    #
    # `no-store` e non un `max-age`: la finestra di freschezza di questi tre numeri e'
    # gia' STATUS_CACHE_TTL_S, e una seconda cache a valle la sommerebbe alla prima —
    # fino a due minuti di eta' per un numero che dichiara di valerne uno. E' la stessa
    # ragione per cui il watchdog non ha un orologio suo: due orologi per la stessa
    # cadenza si sfasano sempre. Il Worker del sito la pensa uguale e lo scrive
    # (`messo in cache al bordo diventerebbe vecchio due volte`), e la sua copia
    # last-known-good e' una Response che costruisce lui, non questa: `no-store` qui
    # non gliela tocca.
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])

    risposta = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert risposta.status_code == 200
    assert risposta.headers["cache-control"] == "no-store"
    assert risposta.headers["x-content-type-options"] == "nosniff"


@respx.mock
def test_the_refusal_and_the_502_declare_the_same_thing_as_the_200(monkeypatch):
    # HTTPException costruisce una Response NUOVA: gli header scritti sul parametro
    # `response` non la raggiungono, e fino al 21/08/2026 401 e 502 uscivano nudi —
    # misurato, i due test qui sopra guardavano solo il 200. Il contratto vale per i
    # tre codici dichiarati o non vale.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(503))

    rifiuto = client.get("/status", headers={"Authorization": "Bearer sbagliato"})
    guasto = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert (rifiuto.status_code, guasto.status_code) == (401, 502)
    for risposta in (rifiuto, guasto):
        assert risposta.headers["cache-control"] == "no-store"
        assert risposta.headers["x-content-type-options"] == "nosniff"


@respx.mock
def test_the_cached_answer_declares_the_same_thing_as_the_fresh_one(monkeypatch):
    # Gli header si mettono PRIMA del ramo che serve dalla cache, non dopo: una
    # risposta su due senza `no-store` e' peggio di nessuna, perche' rende il difetto
    # intermittente. Il 99% delle risposte in produzione esce da qui.
    _reset_stato_di_processo(monkeypatch)
    _mock_scalars()
    _mock_cost([_serie("claude-opus-5", "input", 284_000)])
    _mock_persistence("0")

    prima = client.get("/status", headers={"Authorization": "Bearer test-token"})
    seconda = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert seconda.json() == prima.json()
    assert len(respx.calls) == 4  # tre query + la sonda, una volta sola: la seconda e' cache
    assert seconda.headers["cache-control"] == "no-store"
    assert seconda.headers["x-content-type-options"] == "nosniff"


@respx.mock
def test_the_upstream_report_names_the_failure_and_not_the_internal_url(monkeypatch):
    # `str(httpx.HTTPStatusError)` contiene l'URL intero della richiesta, cioe' il DNS
    # interno di Railway e la PromQL completa: le due cose che SECURITY.md dichiara
    # private. Verso il chiamante non usciva niente (502 generico, corretto), ma
    # uscivano verso Sentry, che e' un terzo. Il tipo dell'eccezione e lo stato HTTP
    # bastano a sapere cosa e' successo; l'URL serviva solo a chi gia' lo conosce.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )

    risposta = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert risposta.status_code == 502
    evento = _evento_inviato(sentry_call)["exception"]["values"][0]
    # Il TIPO resta: e' l'unica meta' del racconto che non e' un segreto, ed e' anche
    # la chiave con cui il limitatore tiene separati due guasti diversi.
    assert evento["type"] == "HTTPStatusError"
    assert evento["value"] == "upstream query failed: HTTP 500"
    intero = json.dumps(_evento_inviato(sentry_call))
    assert "railway.internal" not in intero
    assert "prometheus" not in intero
    assert "claude_code" not in intero
    assert "max_over_time" not in intero


@respx.mock
def test_an_upstream_that_is_not_an_http_status_still_says_nothing_extra(monkeypatch):
    # L'altra meta' del ramo 502: KeyError/ValueError/ArithmeticError non hanno uno
    # stato HTTP, e il loro `str` non e' scritto da noi. Nessun messaggio altrui
    # nell'evento — il tipo lo dice gia'.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    respx.get("http://prometheus:9090/api/v1/query").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )

    risposta = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert risposta.status_code == 502
    evento = _evento_inviato(sentry_call)["exception"]["values"][0]
    assert evento["type"] == "KeyError"
    assert evento["value"] == "upstream query failed"


@respx.mock
def test_a_label_from_the_ingest_side_cannot_write_freely_into_a_sentry_event(monkeypatch):
    # `model` e `type` arrivano dalla serie Prometheus, cioe' da chi possiede
    # OTLP_INGEST_TOKEN: finivano verbatim nel corpo di un evento Sentry. Il tetto
    # MAX_PRICING_GAPS impediva gia' l'esaurimento di quota, non la stringa arbitraria.
    # Cio' che serve sapere e' "esiste un modello sconosciuto", non i suoi duecento
    # caratteri: si tronca e si sanifica prima di costruire l'eccezione.
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.setattr(main, "_PRICING_GAPS_REPORTED", set())
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    veleno = "<script>alert(1)</script>\n" + "A" * 300
    _mock_scalars()
    _mock_cost([_serie(veleno, "input", 1_000)])
    _mock_persistence("0")

    risposta = client.get("/status", headers={"Authorization": "Bearer test-token"})

    assert risposta.status_code == 200
    valore = _evento_inviato(sentry_call)["exception"]["values"][0]["value"]
    assert "<" not in valore and ">" not in valore and "\n" not in valore
    assert "A" * (main.MAX_LABEL_CHARS + 1) not in valore
    # Sanificata, non cancellata: l'evento deve restare azionabile — dice ancora che
    # e' un `model` e quale, per quanto se ne puo' ripetere.
    assert valore.startswith("model 'scriptalert1scriptAAA")
    assert main._etichetta_sicura(veleno) in valore
    assert len(main._etichetta_sicura(veleno)) == main.MAX_LABEL_CHARS


def test_the_label_sanitiser_keeps_the_names_that_actually_exist():
    # Sanificare non deve rendere illeggibili i valori veri: i nomi dei modelli
    # emessi in produzione hanno lettere, cifre, punti e trattini, e devono
    # attraversare invariati — altrimenti il report che serve a correggere la
    # tabella dei prezzi nomina una chiave che non esiste.
    for vero in ("claude-opus-5", "claude-haiku-4-5-20251001", "cacheCreation", "input"):
        assert main._etichetta_sicura(vero) == vero
    # Un'etichetta assente resta distinguibile da una vuota.
    assert main._etichetta_sicura(None) == "None"


@respx.mock
def test_three_zeros_that_last_are_said_out_loud_instead_of_being_served_in_silence(monkeypatch):
    # Il guasto silenzioso che restava scoperto: un Prometheus VIVO con il TSDB
    # svuotato risponde 200 con result vuoto, i tre numeri leggono zero, e
    # `_check_persistence` trova la sua serie, legge zero fallimenti e tace. Verde
    # ovunque, volume perso.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars(sessions="0", tokens="0")
    _mock_cost([])
    _mock_persistence("0")
    # Nessuna serie nemmeno in sette giorni: il TSDB e' svuotato davvero, non e' una
    # pausa. Dal 31/08/2026 questo mock e' cio' che distingue questo test dal caso
    # innocuo — prima non c'era e il test non sapeva di stare asserendo entrambi.
    _mock_history([])

    for _ in range(main.ZERO_PASSES_BEFORE_ALERT - 1):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
        orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    # Il confine, non solo l'effetto: una soglia che scatta prima e' un allarme su una
    # mattina tranquilla, e un allarme che grida al lunedi' mattina si spegne.
    assert not sentry_call.called

    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    evento = _evento_inviato(sentry_call)
    assert evento["exception"]["values"][0]["type"] == "PublicNumbersAllZero"
    assert evento["exception"]["values"][0]["value"] == (
        "the three public numbers have read zero for 60 consecutive passes (~1h of "
        "polling) while Prometheus answered 200, and no series was found in the last "
        "7d either — so this is NOT simply a quiet stretch; check the prometheus-data "
        "volume first, then consider a fresh deploy with no history yet, or an "
        "absence longer than 7d"
    )
    assert evento["tags"]["endpoint"] == "status"


@respx.mock
def test_a_single_number_that_moves_puts_the_zero_counter_back_to_the_start(monkeypatch):
    # Il contatore misura zeri CONSECUTIVI. Senza l'azzeramento, una sessione ogni
    # cinquanta passate non impedirebbe l'allarme, e l'allarme direbbe una cosa falsa —
    # che i tre numeri sono fermi a zero — proprio mentre si muovono.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_persistence("0")
    _mock_scalars(sessions="0", tokens="0")
    vuota = _mock_cost([])

    for _ in range(main.ZERO_PASSES_BEFORE_ALERT - 1):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
        orologio.avanza(main.STATUS_CACHE_TTL_S + 1)

    # Una sessione. Il conteggio riparte da capo.
    _mock_scalars(sessions="1", tokens="10")
    vuota.mock(return_value=httpx.Response(200, json={"data": {"result": []}}))
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    orologio.avanza(main.STATUS_CACHE_TTL_S + 1)
    assert main._ZERO_PASSES == 0

    _mock_scalars(sessions="0", tokens="0")
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200

    assert not sentry_call.called


@respx.mock
def test_an_unauthenticated_caller_reaches_neither_prometheus_nor_sentry(monkeypatch):
    # Il commento accanto al ramo 502 affermava che quella cattura era "l'unica che un
    # chiamante NON autenticato puo' far scattare a volonta'". Misurato, e' falso: senza
    # token la risposta e' 401 e verso Prometheus non parte NIENTE, perche' quel ramo
    # sta dentro la rotta autenticata. Il limitatore resta giustificato — il widget
    # polla ogni 20s e i 502 non entrano in cache, quindi un Prometheus giu' compra
    # comunque un evento per richiesta — ma per un'altra ragione, e un rischio affermato
    # che non esiste manda a cercare nel posto sbagliato tanto quanto uno taciuto.
    #
    # (Il commento gemello sul 500 da latin-1 era invece vero: quel guasto scattava
    # DENTRO `require_valid_token`, prima di qualunque credenziale valida.)
    _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    monte = respx.get("http://prometheus:9090/api/v1/query").mock(return_value=httpx.Response(500))
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )

    for _ in range(5):
        assert client.get("/status").status_code == 401

    assert monte.call_count == 0
    assert sentry_call.call_count == 0


@respx.mock
def test_the_zero_alert_forgets_as_soon_as_a_number_moves_again(monkeypatch):
    # La chiave del limitatore ("zero-volume") compare due volte: quando si dimentica
    # sul ramo sano e quando si segnala. Se le due divergono, il primo periodo di zeri
    # zittisce il secondo per un'ora — e il secondo e' quello in cui il volume e'
    # davvero sparito. Nessuna asserzione la sorvegliava: sei mutanti di quella
    # stringa sopravvivevano al run di mutmut del 20/08/2026.
    #
    # La soglia si abbassa a due apposta: il punto qui e' il limitatore, e sessanta
    # passate da 61 secondi supererebbero da sole l'ora di INFRA_ALERT_INTERVAL_S,
    # facendo passare il test per il motivo sbagliato.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setattr(main, "ZERO_PASSES_BEFORE_ALERT", 2)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_persistence("0")
    vuota = _mock_cost([])
    # SENZA QUESTA RIGA il test drifta di ramo, misurato il 31/08/2026. Da quando esiste
    # il testimone, non mockarlo lo fa fallire, l'esito e' `None`, e l'evento esce sulla
    # chiave "zero-volume-cieco": il `pop("zero-volume")` — cioe' cio' che questo test
    # dichiara di sorvegliare — non veniva piu' esercitato da nessuno, e tre suoi mutanti
    # sopravvivevano. Un test che cambia ramo sotto silenzio e' peggio di uno assente:
    # continua a passare col nome di prima.
    _mock_history([])

    def passata(sessioni: str) -> None:
        _mock_scalars(sessions=sessioni, tokens=sessioni)
        vuota.mock(return_value=httpx.Response(200, json={"data": {"result": []}}))
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
        orologio.avanza(main.STATUS_CACHE_TTL_S + 1)

    for _ in range(4):
        passata("0")
    # Quattro passate, un evento solo: il limitatore fa il suo mestiere.
    assert sentry_call.call_count == 1
    # La chiave e' quella della conclusione DEFINITIVA, non quella del ramo cieco.
    assert "zero-volume" in main._INFRA_ALERTS_SENT

    passata("1")  # i numeri si muovono: il limitatore deve dimenticare
    assert "zero-volume" not in main._INFRA_ALERTS_SENT
    for _ in range(2):
        passata("0")

    # Il secondo evento arriva SUBITO, non un'ora dopo — e siamo ancora dentro
    # INFRA_ALERT_INTERVAL_S, quindi puo' essere arrivato solo dal `pop`.
    assert orologio.adesso - 1_000.0 < main.INFRA_ALERT_INTERVAL_S
    assert sentry_call.call_count == 2


def _mock_history(series: list[dict]):
    """La seconda domanda del watchdog degli zeri: esiste storia oltre la finestra?

    `series` vuota = vettore vuoto = nessuna serie in sette giorni. NON e' zero, ed e'
    esattamente la distinzione che il testimone esiste per fare.
    """
    return respx.get("http://prometheus:9090/api/v1/query", params={"query": HISTORY_Q}).mock(
        return_value=httpx.Response(200, json={"data": {"result": series}})
    )


def _passate_a_zero(orologio, quante: int) -> None:
    for _ in range(quante):
        assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
        orologio.avanza(main.STATUS_CACHE_TTL_S + 1)


@respx.mock
def test_a_quiet_stretch_longer_than_the_window_is_not_reported_as_a_lost_volume(monkeypatch):
    # IL CASO PER CUI ESISTE IL TESTIMONE. Le tre query guardano indietro 25h, quindi
    # qualunque pausa piu' lunga di 25h le porta a zero legittimamente: un weekend
    # basta. Misurato in produzione il 31/08/2026 — otto eventi in otto ore, tutti
    # innocui, l'ultima attivita' reale era a mezzanotte del giorno prima.
    #
    # Un allarme che grida a ogni weekend e' un allarme che qualcuno silenzia, e un
    # watchdog silenziato e' peggio di uno assente: risulta acceso.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars(sessions="0", tokens="0")
    _mock_cost([])
    _mock_persistence("0")
    # La storia c'e': quattro serie viste negli ultimi sette giorni. Il volume e'
    # pieno, i dati ci sono, Marco era via.
    _mock_history([{"metric": {}, "value": [0, "4"]}])

    _passate_a_zero(orologio, main.ZERO_PASSES_BEFORE_ALERT + 1)

    assert not sentry_call.called


@respx.mock
def test_zeros_with_no_history_behind_them_are_still_reported(monkeypatch):
    # L'altra meta': se NEMMENO la finestra larga trova una serie, le ipotesi restano
    # tre — volume perso, deploy nuovo senza storia, assenza oltre i sette giorni — e
    # solo la prima e' un guasto. Si grida lo stesso, dicendo che non si sa quale.
    # Tacere qui rimetterebbe il volume perso esattamente dov'era: invisibile.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars(sessions="0", tokens="0")
    _mock_cost([])
    _mock_persistence("0")
    _mock_history([])  # vettore vuoto: nessuna serie in sette giorni

    _passate_a_zero(orologio, main.ZERO_PASSES_BEFORE_ALERT)

    evento = _evento_inviato(sentry_call)
    assert evento["exception"]["values"][0]["type"] == "PublicNumbersAllZero"
    valore = evento["exception"]["values"][0]["value"]
    # `"7d" in valore` NON basta, misurato il 31/08/2026: anche il messaggio del ramo
    # ambiguo nomina 7d, quindi un mutante che fa sollevare la sonda sul vettore vuoto
    # (IndexError inghiottita, esito None) passava questo test. L'asserzione deve
    # distinguere i due messaggi, non solo constatare che un evento e' partito.
    assert "no series was found in the last 7d" in valore
    assert "could not answer" not in valore
    # Le altre due ipotesi nominate, perche' chi legge l'evento alle tre di notte
    # sappia cosa guardare invece di dedurlo.
    assert "prometheus-data volume" in valore
    assert "fresh deploy" in valore


@respx.mock
def test_a_witness_that_cannot_answer_never_buys_silence(monkeypatch):
    # Il modo in cui questa modifica poteva peggiorare le cose invece di migliorarle:
    # un testimone che degrada al silenzio rende MUTO il watchdog proprio quando
    # Prometheus e' mezzo rotto — risponde alle tre query e non alla quarta. Sarebbe
    # aggiungere un guasto silenzioso mentre se ne cura uno rumoroso.
    #
    # Percio' sul ramo d'errore si torna al comportamento di prima: si grida col
    # messaggio ambiguo. Peggio essere ambigui che zitti.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars(sessions="0", tokens="0")
    _mock_cost([])
    _mock_persistence("0")
    respx.get("http://prometheus:9090/api/v1/query", params={"query": HISTORY_Q}).mock(
        return_value=httpx.Response(500)
    )

    _passate_a_zero(orologio, main.ZERO_PASSES_BEFORE_ALERT)

    assert sentry_call.called
    evento = _evento_inviato(sentry_call)
    assert evento["exception"]["values"][0]["type"] == "PublicNumbersAllZero"
    # Il messaggio si asserisce PER INTERO, non a pezzi. Due ragioni. La prima e' la
    # stessa gia' scritta in mutation.yml: quel testo e' l'unica istruzione operativa
    # che riceve chi trova il guasto, quindi appartiene al contratto. La seconda e'
    # misurata il 31/08/2026 — con `assert "could not answer" in valore` cinque
    # mutanti di questi letterali sopravvivevano, maiuscolatura compresa.
    #
    # E soprattutto NON deve affermare cio' che non e' stato misurato: un evento che
    # dicesse "no series in the last 7d" quando la query non ha risposto manderebbe
    # chi lo legge a cercare un volume perso sulla base di niente.
    assert evento["exception"]["values"][0]["value"] == (
        "the three public numbers have read zero for 60 consecutive passes (~1h of "
        "polling) while Prometheus answered 200, and the 7d history probe could not "
        "answer, so the quiet-stretch case could NOT be ruled out — this event is as "
        "ambiguous as it was before the probe existed; check the prometheus-data "
        "volume, and check why the probe failed while the three queries did not"
    )


@respx.mock
def test_the_definitive_verdict_is_never_throttled_behind_the_ambiguous_one(monkeypatch):
    # Due conclusioni diverse non condividono una chiave di limitazione. Se il
    # testimone prima non risponde (evento AMBIGUO) e poi risponde "nessuna serie"
    # (evento DEFINITIVO, il guasto vero), il secondo deve partire subito: con una
    # chiave sola resterebbe zitto un'ora dietro al primo, cioe' il limitatore
    # comprerebbe silenzio proprio alla conclusione che vale di piu'.
    #
    # E' la stessa classe gia' pagata su "zero-volume" il 20/08/2026, quando il `pop`
    # e la segnalazione usavano chiavi divergenti.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setattr(main, "ZERO_PASSES_BEFORE_ALERT", 2)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    sentry_call = respx.post("https://example.sentry.io/api/9/envelope/").mock(
        return_value=httpx.Response(200)
    )
    _mock_scalars(sessions="0", tokens="0")
    _mock_cost([])
    _mock_persistence("0")

    # Prima: il testimone non risponde -> evento ambiguo.
    cieco = respx.get("http://prometheus:9090/api/v1/query", params={"query": HISTORY_Q}).mock(
        return_value=httpx.Response(500)
    )
    _passate_a_zero(orologio, main.ZERO_PASSES_BEFORE_ALERT)
    assert sentry_call.call_count == 1
    assert "could not answer" in _evento_inviato(sentry_call)["exception"]["values"][0]["value"]
    # QUALE chiave, non solo "una chiave diversa". Senza questa asserzione un mutante
    # che scambia le due (`storia is not False`) resta vivo: le chiavi restano distinte,
    # i due eventi partono lo stesso, e il test passa mentre l'etichetta del limitatore
    # dice il contrario di cio' che e' successo.
    assert "zero-volume-cieco" in main._INFRA_ALERTS_SENT
    assert "zero-volume" not in main._INFRA_ALERTS_SENT

    # Poi: il testimone risponde, e dice che storia non ce n'e'. Siamo ancora ben
    # dentro INFRA_ALERT_INTERVAL_S, quindi se il secondo evento arriva puo' essere
    # arrivato solo perche' la chiave e' un'altra.
    cieco.mock(return_value=httpx.Response(200, json={"data": {"result": []}}))
    _passate_a_zero(orologio, main.ZERO_PASSES_BEFORE_ALERT)

    assert orologio.adesso - 1_000.0 < main.INFRA_ALERT_INTERVAL_S
    assert sentry_call.call_count == 2
    assert "no series was found" in _evento_inviato(sentry_call)["exception"]["values"][0]["value"]
    # Adesso ci sono ENTRAMBE: due conclusioni, due limitatori indipendenti.
    assert "zero-volume" in main._INFRA_ALERTS_SENT
    assert "zero-volume-cieco" in main._INFRA_ALERTS_SENT

    # E il ramo sano deve dimenticarle TUTTE E DUE. Dimenticarne una sola farebbe
    # scivolare la "prima comparsa" del prossimo periodo di zeri fino a un'ora dopo,
    # per la conclusione dimenticata a meta' — che e' il difetto gia' pagato il
    # 20/08/2026 su una chiave sola. Senza queste due righe i tre mutanti del
    # `pop("zero-volume-cieco")` sopravvivevano: nessun test tornava alla salute
    # partendo dal ramo cieco.
    _mock_scalars(sessions="1", tokens="1")
    assert client.get("/status", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert "zero-volume" not in main._INFRA_ALERTS_SENT
    assert "zero-volume-cieco" not in main._INFRA_ALERTS_SENT


@respx.mock
def test_the_witness_is_not_asked_before_the_threshold(monkeypatch):
    # Il testimone e' una query in piu' su un percorso caldo: il widget interroga ogni
    # 20s. Se partisse a ogni passata a zero invece che alla soglia, sarebbero
    # millequattrocento query al giorno di finestra a sette giorni per non dire nulla.
    # Il costo va pagato quando la domanda serve, cioe' quando si sta per gridare.
    orologio = _reset_stato_di_processo(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", "https://abc123@example.sentry.io/9")
    respx.post("https://example.sentry.io/api/9/envelope/").mock(return_value=httpx.Response(200))
    _mock_scalars(sessions="0", tokens="0")
    _mock_cost([])
    _mock_persistence("0")
    storia = _mock_history([{"metric": {}, "value": [0, "4"]}])

    _passate_a_zero(orologio, main.ZERO_PASSES_BEFORE_ALERT - 1)

    assert not storia.called
