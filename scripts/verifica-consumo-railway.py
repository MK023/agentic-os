#!/usr/bin/env python3
"""Verifica che il TASSO di consumo Railway resti sotto i tetti dichiarati.

NON calcola soldi, e la differenza conta. L'enum `MetricMeasurement` non ha nessuna
misura in denaro (verificato per introspezione il 20/08/2026, riverificato il 01/09:
solo CPU, memoria, disco, rete, e i rispettivi limiti) e le unita' di queste misure non
sono documentate. Convertire vorrebbe dire indovinare l'unita' E replicare il prezzario
in un quarto posto. Quello che questa sonda intercetta e' la FUGA: un processo che
consuma e non dovrebbe. Il limite di spesa del workspace lo fermerebbe solo DOPO, e
nessun tool di questo repository puo' rileggerlo.

## Un TASSO, non un totale — cambiato il 01/09/2026

Fino a quel giorno leggeva `estimatedUsage`, che e' cumulativo sul ciclo di
fatturazione: un numero che sale per costruzione, confrontato con un tetto fisso.
Diventava rosso a una data decisa da quando comincia il ciclo, non dal consumo — ed e'
successo. Il perche', con la serie misurata, sta in `scripts/query-usage-railway.py`.

Ora la domanda e' "quanto ha consumato questo hub NELLE ULTIME 24 ORE", che e' stabile
finche' la realta' e' stabile e sale davvero solo quando sale il consumo.

## E NOMINA il servizio

Il file della baseline ordina, da sempre, di guardare quale servizio e' cresciuto prima
di toccare i tetti. Un allarme che grida un totale rende quell'ordine ineseguibile
proprio nel momento in cui va eseguito, quindi la ripartizione per servizio si stampa
sempre — anche quando e' tutto a posto, perche' e' li' che si legge la crescita mentre
e' ancora sotto il tetto.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "docs" / "sorveglianza-baseline.json"

# La forma di un serviceId Railway. Un FILTRO POSITIVO, non una fuga dei caratteri
# pericolosi, e la differenza e' la solita: fuggire `\n` e `\r` chiude due FORME e
# lascia aperta la classe — restano gli escape ANSI, i separatori Unicode, e la forma
# che il fornitore inventera' domani. Cio' che passa di qui e' invece dichiarato: 36
# caratteri esadecimali e trattini, che e' quello che un id e'.
#
# Perche' serve, misurato il 01/09/2026 su questo stesso script: la sonda stampa il
# nome del servizio piu' consumante, e quel nome viene dalla RISPOSTA dell'API, non dal
# repository. Un id contenente `\n::stop-commands::<token>` usciva a inizio riga e
# GitHub Actions smetteva di interpretare le annotazioni successive: il job restava
# rosso — il verdetto e' il codice di uscita, non un comando di workflow — ma perdeva
# la riga che dice QUALE servizio e' cresciuto. Cioe' esattamente cio' per cui questa
# sonda e' stata riscritta. Un allarme che si puo' zittire e' peggio di uno assente.
FORMA_ID = re.compile(r"^[0-9a-f-]{36}$")

# Il pavimento. "Nessuna differenza" e "nessun dato" hanno lo stesso aspetto: una
# risposta che elenca un servizio solo produce una somma piccola, sotto ogni tetto, e
# un verde che non ha misurato niente. Due, e non sei, perche' sei sarebbe un secondo
# posto in cui vive il numero dei servizi e diventerebbe rosso il giorno che se ne
# spegne uno legittimamente — un rosso su cui non puoi agire e' un `continue-on-error`
# con passi in piu'. Quanti se ne sono visti si STAMPA, cosi' un calo si legge.
SERVIZI_MINIMI = 2

# Sotto un decimo dell'osservato la misura e' rotta, non bassa. Dieci e non due perche'
# il consumo vero oscilla — un deploy, una compattazione di Prometheus — e un pavimento
# stretto griderebbe a ogni giornata tranquilla, cioe' verrebbe disattivato. Dieci e non
# cento perche' il caso che conta e' lo zero e i suoi dintorni: una finestra che torna
# vuota, una query che cambia semantica sotto di noi, l'hub fermo.
PAVIMENTO_BASSO = 10


def _nome(servizio: str | None, nomi: dict[str, str]) -> str:
    """Il nome stampabile di un servizio. Mai una stringa arbitraria dell'API.

    Tre esiti e non due. Un id NOTO diventa il suo nome — e' il caso normale. Un id
    sconosciuto ma ben formato resta l'id, che e' la scelta gia' dichiarata nella
    baseline: un servizio nuovo non deve far fallire la sonda, deve solo comparire con
    un nome brutto. Un id che non ha la forma di un id non si stampa affatto: non e'
    un servizio che non conosciamo, e' una risposta che non e' quella che l'API dichiara
    di dare, e ripeterla nel log significa lasciar scrivere nel log a chi l'ha mandata.
    """
    if servizio in nomi:
        return nomi[servizio]
    if servizio and FORMA_ID.match(servizio):
        return servizio
    return "(id malformato)"


def _per_misura(risposta: dict, nomi: dict[str, str]) -> dict[str, dict[str, float]]:
    """{misura: {nome del servizio: valore}}."""
    fuori: dict[str, dict[str, float]] = {}
    for riga in (risposta.get("data") or {}).get("usage") or []:
        nome = _nome((riga.get("tags") or {}).get("serviceId"), nomi)
        # Si SOMMA invece di sovrascrivere: piu' id malformati collassano sullo stesso
        # nome, e un `=` li farebbe sparire tutti tranne l'ultimo — una sottostima
        # silenziosa del totale, cioe' il modo in cui questa sonda smetterebbe di
        # suonare senza dirlo.
        misura = fuori.setdefault(riga["measurement"], {})
        misura[nome] = misura.get(nome, 0.0) + riga["value"]
    return fuori


def taratura(letti: dict[str, dict[str, float]], oggi: str) -> str:
    """Il blocco da incollare nella baseline, coi tetti a 3x il misurato.

    Esiste perche' i tetti non si possono indovinare da qui: le unita' di `usage` non
    sono documentate e nessuno le ha mai lette su una finestra di 24 ore. Scriverli a
    stima sarebbe la stessa mossa che questo file vieta al contrario — un numero non
    misurato che decide se un allarme suona.

    3x come prima, e per la stessa ragione: sotto e' rumore di crescita normale, sopra
    e' qualcosa che non dovrebbe girare. Ma ora il 3x sta su un TASSO, quindi vuol dire
    "tre volte il consumo di oggi" invece di "tre volte quello che il contatore aveva
    accumulato quel martedi'".
    """
    osservato = {misura: round(sum(per.values()), 1) for misura, per in sorted(letti.items())}
    return json.dumps(
        {
            "misurato_il": oggi,
            "osservato_al_giorno": osservato,
            "tetti_al_giorno": {misura: round(valore * 3) for misura, valore in osservato.items()},
        },
        indent=2,
    )


def main(percorso_risposta: str, modo: str = "") -> int:
    base = json.loads(BASE.read_text())
    risposta = json.loads(Path(percorso_risposta).read_text())

    if risposta.get("errors"):
        messaggi = [e.get("message") for e in risposta["errors"]]
        # `json.dumps` e non un f-string, e non e' pedanteria: anche questi messaggi
        # arrivano dall'API, e su una riga `::error::` un `\n` restituisce a chi
        # risponde la possibilita' di scrivere annotazioni. Oggi l'f-string sarebbe
        # salva per caso — interpolando una LISTA, Python passa da `repr()` e i newline
        # diventano letterali — ma e' una protezione che sparisce alla prima persona
        # che scrive `', '.join(messaggi)` per leggibilita', senza che niente diventi
        # rosso. La fuga e' il contratto dichiarato di `json.dumps`; quella del `repr`
        # di una lista e' un effetto collaterale.
        print("::error::GraphQL ha risposto con errori: " + json.dumps(messaggi))
        print("::error::Questo job NON ha misurato niente. Un token scaduto o revocato ha questo sintomo.")
        return 1

    letti = _per_misura(risposta, base.get("servizi") or {})
    if not letti:
        print("::error::usage ha risposto 200 con una lista VUOTA: nessuna misura, quindi nessuna verifica.")
        return 1

    if modo == "--taratura":
        from datetime import UTC, datetime

        print("TARATURA — nessun tetto verificato. Da incollare in docs/sorveglianza-baseline.json:")
        for misura, per_servizio in sorted(letti.items()):
            print(f"\n{misura}: {sum(per_servizio.values()):.1f} al giorno")
            for nome, valore in sorted(per_servizio.items(), key=lambda kv: -kv[1]):
                print(f"    {valore:9.2f}  {nome}")
        print()
        print(taratura(letti, datetime.now(UTC).date().isoformat()))
        return 0

    # I tetti non tarati NON passano per "nessuno sforamento". Un dizionario vuoto qui
    # renderebbe il ciclo qui sotto una passeggiata a vuoto e il job verde: la forma
    # esatta del guasto silenzioso che questa sonda esiste per impedire.
    if not base.get("tetti_al_giorno"):
        print(
            "::error::docs/sorveglianza-baseline.json non ha `tetti_al_giorno`: "
            "questo job NON ha verificato niente."
        )
        print("::error::Si tarano una volta con `--taratura` e si incolla il blocco che stampa.")
        return 1

    sforati = []
    for misura, tetto in base["tetti_al_giorno"].items():
        per_servizio = letti.get(misura)
        if per_servizio is None:
            sforati.append(f"{misura} non e' stata restituita: il suo tetto non e' stato verificato.")
            continue
        if len(per_servizio) < SERVIZI_MINIMI:
            sforati.append(
                f"{misura} e' arrivata da {len(per_servizio)} servizio/i, attesi almeno "
                f"{SERVIZI_MINIMI}: e' un'estrazione mutila, non un consumo basso."
            )
            continue
        totale = sum(per_servizio.values())
        osservato = base["osservato_al_giorno"].get(misura)
        if osservato is None:
            sforati.append(
                f"{misura} ha un tetto ma nessun valore osservato: senza quello il "
                "pavimento sul basso non si puo' calcolare, e mezzo controllo si legge "
                "come un controllo intero."
            )
            continue
        print(
            f"{misura}: {totale:.1f} al giorno  (tetto {tetto} — "
            f"misurato il {base['misurato_il']}: {osservato})"
        )
        # SEMPRE, non solo quando sfora: e' la riga su cui si vede una crescita mentre
        # e' ancora sotto il tetto, cioe' finche' costa poco guardarla.
        for nome, valore in sorted(per_servizio.items(), key=lambda kv: -kv[1]):
            print(f"    {valore:9.2f}  {nome}")
        if totale > tetto:
            sforati.append(f"{misura} = {totale:.1f} al giorno, sopra il tetto di {tetto}.")
        # E il PAVIMENTO SUL BASSO, che e' l'altra meta' e mancava. `SERVIZI_MINIMI`
        # conta quanti servizi hanno risposto, mai QUANTO hanno misurato: una risposta
        # con la forma giusta e tutti i valori a zero attraversava il pavimento, stava
        # sotto ogni tetto e stampava "consumo a posto". Sei container sempre accesi non
        # consumano zero CPU in ventiquattro ore — uno zero e' una misura rotta, non un
        # consumo virtuoso, e questa sonda e' stata appena riscritta proprio perche'
        # misurava la cosa sbagliata sembrando sana.
        elif totale < osservato / PAVIMENTO_BASSO:
            sforati.append(
                f"{misura} = {totale:.1f} al giorno, sotto un {PAVIMENTO_BASSO}esimo "
                f"dell'osservato ({osservato}). Un consumo che crolla cosi' non e' un "
                "risparmio: o l'hub e' fermo, o la finestra della query non ha "
                "restituito il periodo che dichiara."
            )

    if sforati:
        for s in sforati:
            print(f"::error::{s}")
        print(
            "::error::E' un TASSO su 24 ore, non un totale che sale da solo: sopra il tetto "
            "significa che qualcosa consuma adesso e non dovrebbe."
        )
        print(
            "::error::La ripartizione per servizio qui sopra dice QUALE. Se e' crescita "
            "legittima si aggiorna docs/sorveglianza-baseline.json dopo averla guardata, "
            "mai per far tornare verde il job."
        )
        return 1

    print("ok: il consumo giornaliero resta sotto i tetti dichiarati")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
