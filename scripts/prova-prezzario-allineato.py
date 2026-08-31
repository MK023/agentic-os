#!/usr/bin/env python3
"""I nomi dei modelli vivono in TRE posti, e devono nominare gli stessi.

Perche' esiste, con la data e il costo. Il 31/08/2026 la produzione ha emesso
`claude-opus-5[1m]`, assente da tutti e tre. Aggiungerlo ha prodotto due errori di
segno OPPOSTO che si sono cancellati a vicenda nello sguardo di chi guardava:

  - `main.py` prezzava quei token alla tariffa di ripiego  -> il numero pubblico
    SOVRASTIMAVA, e lo diceva (evento Sentry `UnknownPricingKey`);
  - la dashboard non lo nominava affatto -> il pannello dei costi SOTTOSTIMAVA, e non
    lo diceva a nessuno;
  - il test promtool, che si presenta come "la query del pannello messa alla prova",
    testava la PROPRIA copia della query e restava verde mentre la dashboard divergeva.

Solo il primo dei tre aveva un allarme. Gli altri due sono stati trovati a mano, e la
seconda volta da una revisione avversaria: senza questo file, il prossimo modello
seguirebbe la stessa strada.

## Perche' il gate che esisteva non bastava

`images.yml` confronta gia' i tre posti, ma sull'insieme delle TARIFFE. Un modello
nuovo che riusa tariffe gia' presenti — ed e' il caso normale, le tariffe sono cinque
e i modelli sei — attraversa quel confronto senza muovere niente. La dimensione
sbagliata non e' un gate debole: e' un gate che dichiara allineati tre file che non lo
sono, cioe' peggio di nessun gate, perche' chi legge smette di guardare.

## Cosa NON copre, dichiarato

Che i nomi siano quelli che la produzione emette davvero. Questo confronta i tre file
FRA LORO; se sbagliassero tutti e tre allo stesso modo, resterebbe verde. Quel lato lo
copre l'evento `UnknownPricingKey`, che parla quando arriva un modello sconosciuto —
ed e' il motivo per cui va tenuto rumoroso.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent

MAIN = RADICE / "services/public-status-api/main.py"
DASHBOARD = RADICE / "docker/grafana/dashboards/claude-code.json"
PROMQL = RADICE / "scripts/cost-per-session.promql-test.yml"

# I quattro tipi di token che ogni modello deve avere in ogni pannello. Un modello
# aggiunto a META' — nomi allineati, un tipo dimenticato — passerebbe un confronto di
# soli nomi, e sarebbe una sottostima silenziosa esattamente come l'assenza.
TIPI = {"input", "output", "cacheCreation", "cacheRead"}


def chiavi_del_prezzario() -> set[str]:
    testo = MAIN.read_text()
    blocco = re.search(r"PRICES_USD_PER_MTOK\s*=\s*\{(.*?)\n\}", testo, re.S)
    if blocco is None:
        raise SystemExit(f"FAIL: PRICES_USD_PER_MTOK non trovato in {MAIN}.")
    # Solo le chiavi di primo livello: quelle dei tipi sono rientrate di otto spazi.
    return set(re.findall(r'^\s{4}"([^"]+)":\s*\{', blocco.group(1), re.M))


def modelli_della_dashboard() -> tuple[set[str], dict[tuple[str, str], set[str]]]:
    """I tipi si contano PER PANNELLO, non sull'unione.

    Misurato il 31/08/2026 provando questo gate a rompersi: aggregando i tipi su tutti
    i pannelli, togliere `cacheRead` da UNO dei due lasciava il gate verde, perche'
    l'altro pannello lo copriva. I due pannelli calcolano due cifre diverse — il costo
    totale e quello per sessione — e un tipo che manca a uno solo fa divergere le due
    fra loro, che e' peggio di un'assenza in entrambi: una delle due sembra confermare
    l'altra.
    """
    dashboard = json.loads(DASHBOARD.read_text())
    modelli: set[str] = set()
    per_pannello: dict[tuple[str, str], set[str]] = {}
    for pannello in dashboard.get("panels", []):
        titolo = pannello.get("title", "(senza titolo)")
        for target in pannello.get("targets") or []:
            expr = target.get("expr", "")
            for modello, tipo in re.findall(r'model="([^"]+)",type="([^"]+)"', expr):
                modelli.add(modello)
                per_pannello.setdefault((titolo, modello), set()).add(tipo)
    return modelli, per_pannello


def modelli_del_promql() -> set[str]:
    return set(re.findall(r'model="([^"]+)"', PROMQL.read_text()))


def main() -> int:
    for percorso in (MAIN, DASHBOARD, PROMQL):
        if not percorso.is_file():
            print(f"FAIL: manca {percorso}.", file=sys.stderr)
            return 1

    tabella = chiavi_del_prezzario()
    dashboard, tipi_per_pannello = modelli_della_dashboard()
    promql = modelli_del_promql()

    # IL PAVIMENTO, prima dei confronti. Senza, un `re` che smette di corrispondere —
    # perche' qualcuno riformatta la tabella, o rinomina il pannello — darebbe tre
    # insiemi VUOTI, tre confronti soddisfatti e un gate verde che non ha guardato
    # niente. E' la stessa lezione del pavimento dei 100 mutanti in mutation.yml:
    # "nessuna differenza" e "nessun dato" hanno lo stesso aspetto.
    if len(tabella) < 2:
        print(
            f"FAIL: lette {len(tabella)} chiavi da PRICES_USD_PER_MTOK, attese almeno 2.\n"
            "Non e' un prezzario piccolo: e' un'estrazione che non ha funzionato.",
            file=sys.stderr,
        )
        return 1

    problemi: list[str] = []

    if dashboard != tabella:
        problemi.append(
            "la dashboard e il prezzario non nominano gli stessi modelli:\n"
            f"    solo in main.py   : {sorted(tabella - dashboard) or '—'}\n"
            f"    solo in dashboard : {sorted(dashboard - tabella) or '—'}\n"
            "  Un modello in tabella e non in dashboard fa SOTTOSTIMARE il pannello;\n"
            "  uno in dashboard e non in tabella e' un moltiplicatore senza prezzario."
        )

    if promql != tabella:
        problemi.append(
            "il test promtool e il prezzario non nominano gli stessi modelli:\n"
            f"    solo in main.py     : {sorted(tabella - promql) or '—'}\n"
            f"    solo in promql-test : {sorted(promql - tabella) or '—'}\n"
            "  Quel file dice di se' stesso che mette alla prova la query del pannello:\n"
            "  se diverge, la CI esercita una query che non e' quella che gira."
        )

    for (titolo, modello), tipi in sorted(tipi_per_pannello.items()):
        mancanti = TIPI - tipi
        if mancanti:
            problemi.append(
                f"nel pannello {titolo!r} il modello {modello!r} non ha i tipi "
                f"{sorted(mancanti)}: quei token non entrano in quella cifra, in\n"
                "  silenzio, e le due cifre della dashboard divergono fra loro."
            )

    if problemi:
        print("FAIL: il prezzario vive in tre posti e questi non concordano.\n", file=sys.stderr)
        for problema in problemi:
            print(f"  - {problema}\n", file=sys.stderr)
        print(
            "Aggiungere un modello vuol dire toccarli TUTTI E TRE:\n"
            f"  1. {MAIN.relative_to(RADICE)} — la riga di tariffe\n"
            f"  2. {DASHBOARD.relative_to(RADICE)} — quattro termini per ciascun pannello\n"
            f"  3. {PROMQL.relative_to(RADICE)} — i termini E una serie che li eserciti",
            file=sys.stderr,
        )
        return 1

    print(f"OK: i tre posti nominano gli stessi {len(tabella)} modelli, quattro tipi ciascuno.")
    print(f"    {sorted(tabella)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
