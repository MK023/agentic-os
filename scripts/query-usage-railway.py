#!/usr/bin/env python3
"""Stampa il corpo JSON della query GraphQL sul consumo Railway.

Esiste come file e non come heredoc dentro il workflow per una ragione pratica:
un heredoc annidato dentro un blocco `run: |` rompe il parsing YAML, e la logica
in un file si prova in locale senza doverla estrarre dal workflow.

## Perche' `usage` e non piu' `estimatedUsage` — misurato il 01/09/2026

`estimatedUsage` e' CUMULATIVO sul periodo di fatturazione, e la doc del fornitore lo
dice: *"The chart shows the cumulative usage for the billing period"*. Un contatore che
sale per costruzione, confrontato con un tetto fisso, misura il TEMPO trascorso dentro
il ciclo, non il consumo — quindi diventa rosso a una data che dipende solo da quando e'
cominciato il ciclo, e torna verde da solo al reset.

Non e' teoria: la sonda e' diventata rossa il 01/09/2026 con `CPU_USAGE = 752.9` contro
un tetto di 750, e i tredici valori stampati dai run precedenti salgono in modo
monotono senza un solo azzeramento. I DELTA giornalieri, calcolati da quella stessa
serie, sono invece PIATTI da dieci giorni:

    21/08  CPU  31.8/giorno   MEM   754/giorno    <- prima di Loki
    22/08  CPU  48.6/giorno   MEM  1714/giorno    <- Phase 1.5 in produzione
    ...    CPU 36-48/giorno   MEM 1830-2730/giorno
    01/09  CPU  39.7/giorno   MEM  2297/giorno

Il consumo non stava scappando: era il metro a essere sbagliato. `usage` accetta
`startDate`/`endDate`, quindi una finestra FISSA da' un tasso per costruzione — nessuno
stato da conservare fra un run e l'altro, e nessuna deriva.

`groupBy: [SERVICE_ID]` perche' il file della baseline ordina di guardare QUALE servizio
e' cresciuto prima di toccare i tetti. Senza il raggruppamento quell'ordine e' un
consiglio che chi legge alle 5 del mattino non puo' eseguire: la sonda grida un totale e
l'indagine ricomincia da zero. Con il raggruppamento la risposta e' gia' dentro
l'allarme.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

QUERY = (
    "query($w: String!, $m: [MetricMeasurement!]!, $da: DateTime!, $a: DateTime!) { "
    "usage(workspaceId: $w, measurements: $m, startDate: $da, endDate: $a, "
    "groupBy: [SERVICE_ID]) { measurement value tags { serviceId } } }"
)

# Solo CPU e memoria: sono le due che un processo scappato fa esplodere. Disco e
# rete crescono per ragioni legittime e farebbero rumore.
MISURE = ["CPU_USAGE", "MEMORY_USAGE_GB"]

# Ventiquattro ore, e non un'ora. Il consumo di questo hub e' quasi tutto un fondo
# costante — sei container sempre accesi — ma i deploy e le compattazioni di Prometheus
# sono a scatti, e una finestra corta li leggerebbe come una fuga. Un giorno intero e'
# anche la finestra su cui e' misurata la serie qui sopra, quindi i tetti si tarano su
# numeri confrontabili invece che riscalati.
FINESTRA = timedelta(hours=24)


def corpo(workspace: str, adesso: datetime | None = None) -> dict:
    """Il corpo della richiesta. `adesso` e' un parametro per poterlo provare."""
    fine = adesso or datetime.now(UTC)
    return {
        "query": QUERY,
        "variables": {
            "w": workspace,
            "m": MISURE,
            "da": (fine - FINESTRA).isoformat().replace("+00:00", "Z"),
            "a": fine.isoformat().replace("+00:00", "Z"),
        },
    }


if __name__ == "__main__":
    print(json.dumps(corpo(sys.argv[1])))
