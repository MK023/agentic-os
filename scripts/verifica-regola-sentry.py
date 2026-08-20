#!/usr/bin/env python3
"""Verifica che la regola d'allarme Sentry di agentic-os sia ancora quella corretta.

Vive fuori da git, nella dashboard Sentry: se sparisce o torna alla forma vecchia,
niente in questo repository se ne accorge. Questo script e' quel niente.

Il 13/08/2026 un guasto e' rimasto invisibile SEI GIORNI perche' la regola scattava
solo sulle transizioni di priorita': quattro eventi, una notifica. La correzione del
20/08 e' `every_event` accanto alle altre due condizioni, piu' il throttle a 60
minuti, la stessa finestra su cui throttla l'applicazione.
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "docs" / "sorveglianza-baseline.json"


def main(percorso_risposta: str) -> int:
    atteso = json.loads(BASE.read_text())
    wid = atteso["sentry_workflow_id"]
    workflows = json.loads(Path(percorso_risposta).read_text())

    w = next((x for x in workflows if str(x.get("id")) == wid), None)
    if w is None:
        visti = [str(x.get("id")) for x in workflows]
        print(f"::error::nessun workflow Sentry con id {wid}.")
        print(f"::error::Trovati invece: {visti}. Se la regola e' stata ricreata ha un id nuovo: "
              "aggiornare docs/sorveglianza-baseline.json DOPO aver verificato che sia ancora giusta, "
              "non per far tornare verde questo job.")
        return 1

    problemi = []
    if not w.get("enabled"):
        problemi.append("il workflow e' DISABILITATO: nessuna notifica parte, e da fuori non si vede.")

    viste = sorted(c.get("type") for c in (w.get("triggers") or {}).get("conditions", []))
    attese = sorted(atteso["sentry_condizioni_attese"])
    if viste != attese:
        mancanti = sorted(set(attese) - set(viste))
        problemi.append(f"condizioni {viste}, attese {attese}.")
        if "every_event" in mancanti:
            problemi.append(
                "MANCA `every_event`: la regola torna a scattare solo sulle TRANSIZIONI di "
                "priorita', quindi una condizione che PERSISTE notifica una volta e poi tace. "
                "E' esattamente il guasto del 13/08, rimasto invisibile sei giorni."
            )

    freq = (w.get("config") or {}).get("frequency")
    if freq != atteso["sentry_frequency_attesa"]:
        problemi.append(
            f"frequency={freq}, atteso {atteso['sentry_frequency_attesa']}: e' il throttle, e "
            "deve restare la stessa finestra su cui throttla l'applicazione — due orologi per "
            "una cadenza divergono."
        )

    azioni = [a.get("type") for f in (w.get("actionFilters") or []) for a in f.get("actions", [])]
    if not azioni:
        problemi.append("nessuna AZIONE configurata: la regola valuta le condizioni "
                        "e non consegna niente a nessuno.")

    if problemi:
        for p in problemi:
            print(f"::error::{p}")
        return 1

    print(f"ok: workflow {wid} attivo, condizioni {viste}, throttle {freq}m, azioni {azioni}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
