#!/usr/bin/env python3
"""Verifica che il consumo Railway resti sotto i tetti dichiarati.

NON calcola soldi, e la differenza conta. L'enum `MetricMeasurement` non ha nessuna
misura in denaro (verificato per introspezione il 20/08/2026: solo CPU, memoria,
disco, rete) e le unita' di `estimatedUsage` non sono documentate — MEMORY_USAGE_GB
diviso le ore di un mese darebbe ~$169 contro i ~5 euro della fattura vera.
Convertire vorrebbe dire indovinare l'unita' E replicare il prezzario in un quarto
posto.

Quello che intercetta e' la FUGA: un processo che consuma e non dovrebbe. Il limite
di spesa del workspace lo fermerebbe solo DOPO, e nessun tool di questo repository
puo' rileggerlo.
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "docs" / "sorveglianza-baseline.json"


def main(percorso_risposta: str) -> int:
    base = json.loads(BASE.read_text())
    risposta = json.loads(Path(percorso_risposta).read_text())

    if risposta.get("errors"):
        messaggi = [e.get("message") for e in risposta["errors"]]
        print(f"::error::GraphQL ha risposto con errori: {messaggi}")
        print("::error::Questo job NON ha misurato niente. Un token scaduto o revocato ha questo sintomo.")
        return 1

    letti = {
        r["measurement"]: r["estimatedValue"]
        for r in (risposta.get("data") or {}).get("estimatedUsage") or []
    }
    if not letti:
        print(
            "::error::estimatedUsage ha risposto 200 con una lista VUOTA: "
            "nessuna misura, quindi nessuna verifica."
        )
        return 1

    sforati = []
    for misura, tetto in base["tetti"].items():
        valore = letti.get(misura)
        if valore is None:
            sforati.append(f"{misura} non e' stata restituita: il suo tetto non e' stato verificato.")
            continue
        osservato = base["osservato"].get(misura)
        print(f"{misura}: {valore:.1f}  (tetto {tetto} — osservato il {base['misurato_il']}: {osservato})")
        if valore > tetto:
            sforati.append(f"{misura} = {valore:.1f}, sopra il tetto di {tetto}.")

    if sforati:
        for s in sforati:
            print(f"::error::{s}")
        print(
            "::error::Sono unita' di risorsa, non una fattura: molto sopra il tetto significa che "
            "qualcosa consuma e non dovrebbe."
        )
        print(
            "::error::Se invece e' crescita legittima, si aggiorna docs/sorveglianza-baseline.json "
            "DOPO aver guardato QUALE servizio e' cresciuto — non per far tornare verde il job."
        )
        return 1

    print("ok: consumo sotto i tetti dichiarati")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
