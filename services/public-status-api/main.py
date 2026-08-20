"""Public-safe status endpoint.

The only read path a public consumer (the marcobellingeri.dev widget) may reach.
It runs exactly three fixed Prometheus queries and returns them as flat JSON:
no free-form query parameter, no pass-through to PromQL, no session content.
"""

import asyncio
import os
import secrets
import time
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from sentry import capture_exception, verifica_dsn

# Nessuna rotta oltre a /status. FastAPI monta /docs, /redoc e /openapi.json da
# solo, e `require_valid_token` è una dipendenza per-rotta: quelle tre
# risponderebbero senza token. Oggi Cloudflare Access copre tutto l'host (misurato
# il 13/08/2026: 401 su ogni path), ma la regola scritta in SECURITY.md è che un
# hostname non è un controllo d'accesso — è la stessa ragione per cui l'ingest OTLP
# autentica dentro il Collector invece di fidarsi del tunnel. Lo schema OpenAPI
# descriverebbe per giunta proprio l'autenticazione che protegge.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

PROMETHEUS_URL = os.environ["PROMETHEUS_URL"]


def _token_configurato(valore: str) -> str:
    """Un token vuoto e' peggio di un token mancante.

    `os.environ["X"]` solleva se la variabile MANCA, non se e' VUOTA. Con una stringa
    vuota il valore atteso diventa `Bearer ` — spazio finale compreso — cioe' una
    credenziale che si indovina alla prima richiesta, su un endpoint che dichiara di
    averne una. Il caso non e' teorico: la tabella di rotazione in docs/DECISIONS.md
    segnala gia' che questo token vive in due posti e che un disallineamento fallisce
    in silenzio. Meglio un deploy che non parte.
    """
    if not valore.strip():
        raise RuntimeError(
            "STATUS_API_TOKEN e' vuoto: senza un valore l'endpoint accetterebbe "
            "'Bearer ' come credenziale valida. Impostalo sul servizio Railway."
        )
    return valore


STATUS_API_TOKEN = _token_configurato(os.environ["STATUS_API_TOKEN"])

# All'import, non alla prima cattura: un reporter mal configurato deve impedire il
# deploy, non scoprirsi il giorno in cui serviva.
verifica_dsn(os.environ.get("SENTRY_DSN"))
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
# Summing each series' own total gives the right number instead: each series carries
# its own session's total. `max_over_time(...[25h])` is how that sum reaches series
# that are no longer being exported — on a counter that only ever grows, the max over
# the window IS the final value, so this reads the same as a plain sum for a live
# series and keeps reading it for a dead one.
#
# The bare `sum(...)` this replaced undercounted after every otel-collector restart:
# the sessions that had already ended vanished from the exporter's /metrics, and an
# instant query cannot see what is not currently exposed. Measured 2026-08-14 against
# Prometheus 3.13.2 with the real data shape — 3 sessions / 6600 tokens read back as
# 1 / 3300 after a restart, while this query held 3 / 6600.
#
# This query and the Collector's `metric_expiration` are ONE change, never two.
# Expiration used to be 25h purely so an instant query would still find the series
# alive; now that the query looks backwards in time, keeping them alive is the bug —
# a series the Collector keeps re-exporting stays in the TSDB, so a 25h window on top
# of 25h expiration counts a session for ~50h and OVERCOUNTS. Measured, same day.
# Expiration is now the 5m default: it decides when a session stops counting, this
# window decides how far back we look, and the two must not both be the window.
#
# What this trades away, deliberately: with 25h of expiration the Collector held a
# day of state, so a wiped or recreated `prometheus-data` volume healed itself — the
# next 15s scrape re-ingested every series. It no longer does. Prometheus' disk is now
# the only copy of today's numbers, and that disk is the component here with a failure
# on its record (the volume filled on 2026-08-13). Lose it and these three read ~0 for
# the rest of the day with nothing to recover from, answering 200 — indistinguishable
# from a quiet morning. Accepted, because the alternative is the double count above.
#
# So "today" here means "the last 25 hours of activity", not a calendar day. Honest,
# and stable when nothing is running.
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
    "sessions_today": "sum(max_over_time(claude_code_session_count[25h]))",
    "tokens_today": "sum(max_over_time(claude_code_token_usage[25h]))",
    "cost_usd_today": "sum by (model, type) (max_over_time(claude_code_token_usage[25h]))",
}

# Anthropic list prices, USD per million tokens, as published on 2026-07-30. These
# are a hand-maintained copy of someone else's price list: when Anthropic changes a
# price, or a model lands that is not in here, this table is what must be edited —
# nothing downstream will notice on its own. The `type` keys are Claude Code's own
# attribute values, spelled exactly as it emits them (camelCase, not snake_case);
# renaming one to look tidier silently routes those tokens to the fallback below.
#
# Model keys are the labels production actually emitted, not the marketing names:
# Haiku arrives with its dated suffix (measured 2026-07-30, via the very
# UnknownPricingKey event this table exists to answer). A wrong or missing key is
# safe by construction — it falls through to the dearest rate and files a report.
# Sonnet is priced at the standard list, not the introductory price that runs to
# 2026-08-31: overstating for a month beats editing this table twice.
#
# `cacheCreation` is 2x input, not 1.25x. Anthropic publishes two cache-write
# rates -- 1.25x for the 5-minute TTL, 2x for the 1-hour one -- and
# `claude_code_token_usage` does NOT carry the TTL, so the metric cannot tell us
# which one applied. Until 2026-08-14 this table picked 1.25x, which is the
# CHEAPER of the two and contradicted the rule stated above: a gap we cannot
# measure is priced at the dearest rate we know, because a number quietly too low
# is one nobody investigates. Claude Code runs a 1-hour cache TTL, so 2x is also
# the likelier of the two, not merely the safer.
PRICES_USD_PER_MTOK = {
    "claude-fable-5": {
        "input": 10.00,
        "output": 50.00,
        "cacheCreation": 20.00,
        "cacheRead": 1.00,
    },
    "claude-opus-5": {
        "input": 5.00,
        "output": 25.00,
        "cacheCreation": 10.00,
        "cacheRead": 0.50,
    },
    "claude-sonnet-5": {
        "input": 3.00,
        "output": 15.00,
        "cacheCreation": 6.00,
        "cacheRead": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "output": 5.00,
        "cacheCreation": 2.00,
        "cacheRead": 0.10,
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


# La chiave del dedup e' un VALORE che arriva da fuori (`model`, letto dalla serie
# Prometheus), quindi il set e' un insieme che un produttore puo' far crescere. Chi
# tiene il token di ingest comprerebbe un evento Sentry per ogni modello inventato:
# la stessa esaurizione di quota che il limitatore sul 502 e' servito a impedire.
# Cinquanta e' molto oltre i modelli che esistono, e finito il tetto si tace: il
# primo evento e' quello che serve, il cinquantunesimo no.
MAX_PRICING_GAPS = 50


async def _report_pricing_gap(what: str) -> None:
    if what in _PRICING_GAPS_REPORTED or len(_PRICING_GAPS_REPORTED) >= MAX_PRICING_GAPS:
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


# `secrets.compare_digest` su due `str` solleva TypeError appena una delle due
# contiene un carattere non-ASCII, e uvicorn decodifica gli header in latin-1: un
# singolo byte >= 0x80 in Authorization diventava un 500 con traceback nei log,
# scatenabile da un chiamante NON autenticato. Non era un bypass — era una classe di
# risposta che il contratto dell'endpoint dichiara inesistente, e volume di log
# illimitato su un piano a consumo.
#
# `isascii()` invece di ri-codificare in byte: un token valido e' ASCII per
# costruzione, quindi qualunque cosa non lo sia e' gia' sbagliata e puo' uscire da
# qui senza toccare compare_digest. Ri-codificare funzionava, ma introduceva un nome
# di codec e un gestore di errori come letterali — e quelli generano mutanti
# EQUIVALENTI ("LATIN-1" e' un alias valido, e il gestore non viene mai consultato
# perche' una stringa decodificata da latin-1 si ri-codifica sempre): quattro
# sopravvissuti impossibili da uccidere sull'unica funzione dove il repo ne esige
# zero. Meno letterali, meno superficie, stessa proprieta'.
#
# L'ordine conta: `isascii()` prima, cosi' compare_digest riceve solo ASCII e resta a
# tempo costante sul confronto che conta.
_ATTESO = f"Bearer {STATUS_API_TOKEN}"


def require_valid_token(authorization: str = Header(default="")) -> None:
    if not authorization.isascii() or not secrets.compare_digest(authorization, _ATTESO):
        raise HTTPException(status_code=401, detail="unauthorized")


RequireToken = Annotated[None, Depends(require_valid_token)]


async def _query_one(client: httpx.AsyncClient, query: str) -> dict:
    response = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
    response.raise_for_status()
    return response.json()


# The only production failure this project has had was invisible from out here. On
# 2026-08-13 Prometheus's volume filled and compaction failed once a minute for hours
# while the three numbers above stayed correct and kept moving — the head block lives
# in memory, so ingestion outlives persistence right up until it doesn't. Nothing was
# down; something was doomed, and it surfaced only because someone read the service
# logs by hand. This is the smallest counter that would have said so out loud.
#
# `increase()` here, and plain sums above, for the same reason in reverse: this is an
# ordinary counter scraped every 15s from one long-lived process, so growth inside a
# window is exactly the question. The Claude Code counters are one series per session
# that never move again once the session ends, which is why increase() is structurally
# zero there. Same function, opposite verdict — the data shape decides, not the habit.
PERSISTENCE_QUERY = "sum(increase(prometheus_tsdb_compactions_failed_total[1h]))"

# Una passata al minuto, qualunque sia il traffico in ingresso. E' l'unico cancello di
# cadenza del servizio: lo condividono le tre query pubbliche e la sonda, che gira una
# volta per passata. Un secondo orologio per la stessa cadenza si sfasa dal primo — e
# lo ha fatto, la prima volta che ci ho provato.
STATUS_CACHE_TTL_S = 60.0
_status_cache: tuple[float, dict] | None = None


# Una notifica per vita del processo era troppo poco, e si è visto in produzione: il
# 13/08/2026 la compaction ha iniziato a fallire una volta al minuto, Sentry ha
# ricevuto UN evento, e per i sei giorni successivi — con il guasto sempre in corso —
# non ne ha ricevuti altri. Sull'issue si leggeva "ultimo evento cinque giorni fa",
# che è l'aspetto di un guasto RIENTRATO: l'allarme non taceva, mentiva. Si è riarmato
# il 19/08 solo perché un deploy non correlato ha riavviato il processo, per caso.
#
# Un'ora, non un minuto: la condizione si misura in giorni, e un evento all'ora basta
# a non farla sembrare risolta senza trasformare l'issue in un firehose. È anche il
# passo minimo perché una regola Sentry sulla FREQUENZA (non sulla creazione
# dell'issue) abbia qualcosa da contare — senza ripetizione non scatterebbe mai.
# Entrambi gli intervalli sono per-PROCESSO. Oggi combaciano con "per servizio"
# perché il Dockerfile lancia uvicorn senza --workers e Railway gira a una replica: se
# una delle due cose cambia, gli allarmi diventano N all'ora e i controlli N al
# minuto. Accettabile, ma è un accoppiamento fra un'impostazione di deploy e una
# politica di allerta, e non si vede da nessuna delle due parti.
INFRA_ALERT_INTERVAL_S = 3600.0

# monotonic, non time(): qui si misura una durata. Un orologio di sistema spostato
# all'indietro congelerebbe l'allarme finché il ritardo non è colmato.
_INFRA_ALERTS_SENT: dict[str, float] = {}


class PrometheusNotPersisting(Exception):
    """Prometheus answers queries but cannot write blocks. Reported, never raised."""


class PrometheusWatchdogBlind(Exception):
    """The watchdog's own input is missing. Reported, never raised."""


async def _report_infra_throttled(key: str, exc: Exception) -> None:
    last_sent = _INFRA_ALERTS_SENT.get(key)
    now = time.monotonic()
    if last_sent is not None and now - last_sent < INFRA_ALERT_INTERVAL_S:
        return
    # Segnato PRIMA dell'invio, deliberatamente: `capture_exception` inghiotte ogni
    # errore di consegna, quindi un Sentry irraggiungibile costerebbe comunque l'ora
    # di silenzio — ma segnare dopo trasformerebbe un suo 5xx in una raffica di
    # tentativi su ogni richiesta pubblica. Si preferisce perdere un evento che
    # amplificare un guasto altrui; è la stessa scelta fail-open di sentry.py.
    _INFRA_ALERTS_SENT[key] = now
    await capture_exception(exc, tags={"endpoint": "status"})


async def _check_persistence(client: httpx.AsyncClient) -> None:
    """Report a Prometheus that serves reads but has stopped persisting.

    Swallows every error on purpose. This is a watchdog bolted onto the read path of
    the one public endpoint, and a watchdog that can 502 the widget it exists to
    protect is worse than no watchdog: a broken probe must degrade to silence, never
    to an outage. It also means the probe can never be the reason /status fails, which
    is what lets it live on the hot path at all — the widget polls every 20s, so this
    is a cron nobody has to run and no scheduler has to own. It runs once per cache
    pass (STATUS_CACHE_TTL_S), so the polling rate sets the floor, not the bill.
    """
    # Nessun intervallo proprio: la sonda gira una volta per PASSATA, e le passate le
    # limita gia' la cache di /status (STATUS_CACHE_TTL_S). Averne uno suo era peggio
    # che inutile — era sbagliato: il timestamp della cache si prende PRIMA delle
    # query e quello della sonda DOPO, quindi al passaggio successivo la sonda
    # risultava indietro di quei millisecondi, saltava, e sarebbe girata una volta
    # ogni DUE finestre invece di una. Due orologi per la stessa cadenza si sfasano
    # sempre; qui ne resta uno solo, ed e' quello che paga le query.
    #
    # Resta dentro il try tutto cio' che puo' sollevare: la promessa e' che NIENTE in
    # questa funzione possa far cadere /status.
    try:
        series = (await _query_one(client, PERSISTENCE_QUERY))["data"]["result"]
    except Exception:  # noqa: BLE001 — see the docstring; silence is the contract
        return

    # An empty vector is NOT zero failures, and conflating the two is how a watchdog
    # goes blind without saying so: if Prometheus stops scraping itself the metric
    # disappears, `<= 0` reads that as healthy, and we are back to the 2026-08-13
    # blindness inside the very thing added to end it. The Collector job in
    # docker/prometheus.yml already spells out the general rule — no series means no
    # rule can fire — and this is that rule applied to the watchdog's own input.
    if not series:
        await _report_infra_throttled(
            "tsdb-watchdog-blind",
            PrometheusWatchdogBlind(
                "prometheus_tsdb_compactions_failed_total returned no series — "
                "Prometheus is not scraping itself, so this watchdog is blind, "
                "not healthy; check the `prometheus` job in prometheus.yml"
            ),
        )
        return

    # La serie c'è: qualunque cosa dica, il watchdog non è cieco. Dimenticare qui, e
    # non solo sul ramo sano più sotto, è ciò che rende accurata la "prima comparsa"
    # del prossimo incidente invece di lasciarla scivolare fino a un'ora dopo.
    _INFRA_ALERTS_SENT.pop("tsdb-watchdog-blind", None)

    failures = float(series[0]["value"][1])
    if failures <= 0:
        _INFRA_ALERTS_SENT.pop("tsdb-compaction", None)
        return
    await _report_infra_throttled(
        "tsdb-compaction",
        PrometheusNotPersisting(
            f"Prometheus failed {int(failures)} TSDB compactions in the last hour — "
            "it still answers queries, but it is not writing blocks; check the volume"
        ),
    )


@app.get("/healthz")
async def healthz() -> dict:
    """Sonda per la piattaforma: senza token, senza chiamate a monte, costante.

    Railway promuove un deploy quando il PROCESSO parte, non quando serve — con un
    `PROMETHEUS_URL` sbagliato ma risolvibile l'import riesce, uvicorn ascolta, ogni
    /status e' un 502, il deploy risulta SUCCESS e il container buono viene spento.
    `healthcheckPath` e' il meccanismo della piattaforma che lo impedisce, e vuole un
    200 letterale senza credenziali: /status, che risponde 401, non puo' esserlo.

    Deliberatamente NON interroga Prometheus. Una sonda che dipende da monte
    dichiara "malato" il servizio proprio quando e' monte a esserlo, spegnendo
    l'ultima cosa che ancora funziona — e i tre numeri hanno gia' il loro 502.
    """
    return {"status": "ok"}


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
    # Una passata al minuto, qualunque sia il traffico. L'endpoint pubblico non e'
    # throttlato e dal 19/08/2026 il progetto si paga a consumo: senza questa finestra
    # ogni richiesta comprava tre query a Prometheus e tre connessioni TCP nuove, cioe'
    # un moltiplicatore di spesa a disposizione di chiunque conosca l'URL. Con la
    # finestra, l'origine costa uguale sotto una richiesta al minuto e sotto mille al
    # secondo, e il limitatore nel Worker del sito torna a essere un SECONDO strato
    # invece dell'unico — cosa che conta, perche' quel Worker sta in un altro repo.
    #
    # Chiude anche una risoluzione, non solo un costo: tre aggregati campionabili
    # senza limite sono un segnale di presenza al secondo (l'ora in cui una sessione
    # comincia, quanto e' intensa, che modello gira). Aggregare sulle METRICHE e non
    # sul TEMPO lasciava aperta la seconda dimensione.
    #
    # Solo i successi entrano qui, e scaduta la finestra un guasto e' un 502: NON si
    # serve l'ultimo valore buono oltre la scadenza. Un degrado che si nasconde
    # renderebbe verde lo smoke test a hub morto, ed e' una lezione gia' pagata.
    #
    # ponytail: nessun lock. Due richieste che arrivano insieme a finestra scaduta
    # fanno due passate invece di una — costa una query in piu' ogni tanto, e un lock
    # su un singolo processo asyncio costerebbe piu' codice di quanto risparmi.
    global _status_cache
    now = time.monotonic()
    if _status_cache is not None and now - _status_cache[0] < STATUS_CACHE_TTL_S:
        return _status_cache[1]

    # KeyError/TypeError/ValueError are caught alongside httpx.HTTPError: a 200
    # response with an unexpected shape (Prometheus mid-restart, a future API
    # change) raises inside _parse_value or _parse_series and would otherwise become
    # an unhandled 500 with no Sentry capture, breaking this endpoint's "every
    # upstream failure is a controlled 502" promise. Parsing therefore stays inside
    # the try, next to the fetch it can only fail because of.
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            results = await asyncio.gather(*(_query_one(client, q) for q in QUERIES.values()))
            # strict=True: senza, uno zip fra chiavi e risultati di lunghezza diversa
            # si tronca in silenzio e l'endpoint risponde 200 con un numero mancante o
            # accoppiato alla query sbagliata. È la stessa famiglia di _parse_value che
            # leggeva result[0] e si fermava — un dato parziale che sembra sano.
            payloads = dict(zip(QUERIES.keys(), results, strict=True))
            values = {
                "sessions_today": int(_parse_value(payloads["sessions_today"])),
                "tokens_today": int(_parse_value(payloads["tokens_today"])),
                "cost_usd_today": round(await _cost_usd(payloads["cost_usd_today"]), 2),
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
            # Limitata, come ogni altra cattura di questo file. Era l'unica senza
            # limitatore, ed era anche l'unica che un chiamante NON autenticato puo'
            # far scattare a volonta': ogni 502 apriva un client nuovo verso
            # sentry.io, handshake TLS compreso. Con Prometheus giu' e il widget che
            # interroga ogni 20s sono ~4.300 eventi al giorno senza alcun attaccante,
            # cioe' la quota Sentry che si esaurisce da sola proprio mentre serve.
            #
            # La chiave e' il TIPO dell'eccezione, non un valore fisso: un guasto
            # nuovo parla subito invece di restare zitto per un'ora dietro a uno
            # vecchio. La RISPOSTA non e' limitata — 502 su ogni richiesta, sempre:
            # si limita cio' che raccontiamo, mai cio' che serviamo.
            #
            # ArithmeticError nella tupla per la stessa promessa: un `+Inf` da
            # Prometheus faceva `int(float('inf'))` -> OverflowError, che non e' un
            # ValueError e diventava il 500 senza cattura che il commento qui sopra
            # dichiarava chiuso.
            await _report_infra_throttled(f"upstream-{type(exc).__name__}", exc)
            raise HTTPException(status_code=502, detail="upstream unavailable") from exc

        _status_cache = (now, values)

        # After the three numbers are already in hand, and outside the try above: the
        # watchdog must not share a failure path with the contract it guards. Same
        # client, so it costs one round trip on the private network, not a connection.
        await _check_persistence(client)

    return values
