#!/usr/bin/env python3
"""Banco di prova della sonda sul consumo Railway. Nessun token, nessuna rete.

Perche' esiste. La sonda che questo file prova e' nata il 20/08/2026 e il 01/09 si e'
scoperto che misurava la cosa sbagliata — un cumulato contro un tetto fisso — per dodici
giorni, restando verde per cinque di essi e rossa per la ragione sbagliata al sesto.
Nessuno se n'era accorto perche' non c'era modo di guardarla senza il token di
produzione. Le risposte qui sotto sono finte apposta: rendono la sonda esaminabile su
questa macchina, e con lei i casi che in produzione capitano una volta l'anno.

E la meta' che di solito manca: ogni caso ROSSO viene provato anche nel verso opposto —
si toglie la ragione del rosso e si pretende il verde — altrimenti "rosso" puo' voler
dire soltanto che lo script esplode su qualunque ingresso.

Uso: `python3 scripts/prova-sonda-consumo.py`. Esce 0 se tutti i casi si comportano
come atteso. Serve solo la libreria standard.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent

# I due id sono quelli veri, presi dalla baseline: un id inventato proverebbe la
# traduzione id -> nome contro se stessa.
LOKI = "a95cf05d-a064-4fe9-b8d1-3c971e010d29"
GRAFANA = "0ba7ce79-a2bf-45a4-a948-eb0ee64fdbf3"


def _sonda(baseline: dict):
    """La sonda vera, con la baseline dirottata su un file temporaneo.

    Si importa il modulo dal disco invece di ricopiarne la logica: una seconda copia
    diverge, ed e' la regola di casa su ogni file di configurazione applicata al codice.
    """
    spec = importlib.util.spec_from_file_location("sonda", RADICE / "scripts" / "verifica-consumo-railway.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    temporaneo = Path(tempfile.mkstemp(suffix=".json")[1])
    temporaneo.write_text(json.dumps(baseline))
    modulo.BASE = temporaneo
    return modulo


def _risposta(righe: list[tuple[str, str, float]]) -> dict:
    return {
        "data": {"usage": [{"measurement": m, "value": v, "tags": {"serviceId": s}} for m, s, v in righe]}
    }


SANA = [
    ("CPU_USAGE", LOKI, 12.0),
    ("CPU_USAGE", GRAFANA, 18.0),
    ("MEMORY_USAGE_GB", LOKI, 400.0),
    ("MEMORY_USAGE_GB", GRAFANA, 500.0),
]

BASELINE = {
    "workspace_id": "w",
    "servizi": {LOKI: "loki", GRAFANA: "grafana"},
    "misurato_il": "2026-09-01",
    "osservato_al_giorno": {"CPU_USAGE": 30.0, "MEMORY_USAGE_GB": 900.0},
    "tetti_al_giorno": {"CPU_USAGE": 90, "MEMORY_USAGE_GB": 2700},
}


def _gira(baseline: dict, risposta: dict, modo: str = "") -> tuple[int, str]:
    sonda = _sonda(baseline)
    percorso = Path(tempfile.mkstemp(suffix=".json")[1])
    percorso.write_text(json.dumps(risposta))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        uscita = sonda.main(str(percorso), modo)
    return uscita, buffer.getvalue()


def _senza(baseline: dict, **cambi) -> dict:
    return {**baseline, **cambi}


# (nome, baseline, risposta, modo, uscita attesa, frammento che il messaggio deve avere)
CASI = [
    (
        "il consumo sotto i tetti: verde, e la ripartizione si vede lo stesso",
        BASELINE,
        _risposta(SANA),
        "",
        0,
        "loki",
    ),
    (
        "un servizio che scappa: rosso, e l'allarme NOMINA il servizio",
        BASELINE,
        _risposta([("CPU_USAGE", LOKI, 200.0), ("CPU_USAGE", GRAFANA, 18.0)] + SANA[2:]),
        "",
        1,
        "loki",
    ),
    (
        "tetti non ancora tarati: rosso, non verde per dizionario vuoto",
        _senza(BASELINE, tetti_al_giorno={}),
        _risposta(SANA),
        "",
        1,
        "NON ha verificato niente",
    ),
    (
        "un solo servizio nella risposta: estrazione mutila, non consumo basso",
        BASELINE,
        _risposta([("CPU_USAGE", LOKI, 1.0), ("MEMORY_USAGE_GB", LOKI, 1.0)]),
        "",
        1,
        "estrazione mutila",
    ),
    (
        "una misura assente dalla risposta: il suo tetto non e' stato verificato",
        BASELINE,
        _risposta([r for r in SANA if r[0] == "CPU_USAGE"]),
        "",
        1,
        "non e' stata restituita",
    ),
    (
        "lista vuota con un 200: nessuna misura, quindi nessuna verifica",
        BASELINE,
        {"data": {"usage": []}},
        "",
        1,
        "lista VUOTA",
    ),
    (
        "errori GraphQL: il token scaduto ha questo sintomo, non e' un consumo a posto",
        BASELINE,
        {"errors": [{"message": "Not Authorized"}]},
        "",
        1,
        "NON ha misurato niente",
    ),
    (
        "un id di servizio sconosciuto non fa fallire: si stampa com'e'",
        _senza(BASELINE, servizi={LOKI: "loki"}),
        _risposta(SANA),
        "",
        0,
        GRAFANA,
    ),
    (
        "--taratura stampa i tetti a 3x senza verificarne nessuno",
        _senza(BASELINE, tetti_al_giorno={}),
        _risposta(SANA),
        "--taratura",
        0,
        '"CPU_USAGE": 90',
    ),
]


def main() -> int:
    errori = 0
    for nome, baseline, risposta, modo, atteso, frammento in CASI:
        uscita, testo = _gira(baseline, risposta, modo)
        va_bene = uscita == atteso and frammento in testo
        if not va_bene:
            errori += 1
            perche = (
                f"exit {uscita} invece di {atteso}"
                if uscita != atteso
                else f"manca {frammento!r} dal messaggio"
            )
            print(f"  ERRORE {nome} — {perche}", file=sys.stderr)
        else:
            print(f"  ok   {nome}")

    # Il verso opposto del caso che conta di piu': stessa sonda, stessa forma di
    # risposta, solo il numero sotto il tetto. Senza questo, "rosso sul consumo alto"
    # potrebbe voler dire soltanto che la sonda e' rossa su qualunque cosa.
    uscita, _ = _gira(
        BASELINE, _risposta([("CPU_USAGE", LOKI, 40.0), ("CPU_USAGE", GRAFANA, 18.0)] + SANA[2:])
    )
    if uscita != 0:
        errori += 1
        print(
            "  ERRORE lo stesso caso sotto il tetto non e' verde: la sonda e' rossa e basta",
            file=sys.stderr,
        )
    else:
        print("  ok   lo stesso caso sotto il tetto e' verde: il rosso veniva dal numero")

    print(f"\n{'TUTTO A POSTO' if not errori else str(errori) + ' CASI FUORI POSTO'}")
    return 1 if errori else 0


if __name__ == "__main__":
    raise SystemExit(main())
