#!/usr/bin/env python3
"""I nomi dei modelli vivono in TRE posti, e devono nominare gli stessi.

Perche' esiste, con la data e il costo. Il 31/08/2026 la produzione ha emesso
`claude-opus-5[1m]`, assente da tutti e tre. I tre errori avevano segni OPPOSTI e si
mascheravano a vicenda:

  - `main.py` prezzava quei token alla tariffa di ripiego  -> il numero pubblico
    SOVRASTIMAVA (misurato: $108.81 dove il vero era $73.47), e lo diceva, con
    l'evento Sentry `UnknownPricingKey`;
  - la dashboard non lo nominava -> il pannello dei costi SOTTOSTIMAVA, in silenzio;
  - il test promtool, che si presenta come "la query del pannello messa alla prova",
    testava la PROPRIA copia della query e restava verde mentre la dashboard divergeva.

Solo il primo dei tre aveva un allarme.

## Perche' il gate che gia' esisteva non bastava

`images.yml` confronta i tre posti sull'insieme delle TARIFFE. Un modello nuovo che
riusa tariffe gia' presenti — il caso normale, cinque tariffe per sei modelli —
attraversa quel confronto senza muovere niente. La dimensione sbagliata non e' un gate
debole: dichiara allineati tre file che non lo sono, e chi legge smette di guardare.

## Cosa NON copre, dichiarato invece che sperato

1. Che i nomi siano quelli che la produzione emette DAVVERO. Confronta i tre file fra
   loro; se sbagliassero tutti e tre allo stesso modo resterebbe verde. Quel lato lo
   copre `UnknownPricingKey`, ed e' il motivo per cui va tenuto rumoroso.
2. Che il MOLTIPLICATORE di un modello sia quello della sua riga di tariffe. Un
   modello prezzato con le tariffe di un altro passa questo gate (i nomi ci sono) E
   quello delle tariffe (il moltiplicatore esiste, su un altro modello). Serve un
   confronto per COPPIA (modello, tariffa) che nessuno dei due fa.
3. Le righe Grafana collassate: i pannelli dentro `row.panels` vengono attraversati,
   ma se un domani comparisse un terzo livello di annidamento andrebbe aggiunto qui.

Uso: `python3 scripts/prova-prezzario-allineato.py`, oppure con `--prova` per
verificare che il gate sappia diventare rosso su sei mutanti. Le due invocazioni
insieme: **0,45s misurati** il 31/08/2026.
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

# I quattro tipi di token che ogni modello deve avere in ogni pannello che prezza. Un
# modello aggiunto a META' — nomi allineati, un tipo dimenticato — passerebbe un
# confronto di soli nomi, ed e' una sottostima silenziosa quanto l'assenza.
#
# Hardcoded, e dichiarato: e' una QUARTA copia dei tipi, che vivono anche nelle righe
# di `PRICES_USD_PER_MTOK`. Non li si legge da li' di proposito — leggerli dal file
# sotto esame renderebbe il controllo una tautologia, che e' il difetto che questo gate
# esiste per impedire. Il prezzo e' che un tipo NUOVO del fornitore va aggiunto qui a
# mano; il guadagno e' che l'oracolo non vive nel file esaminato.
TIPI = {"input", "output", "cacheCreation", "cacheRead"}

# Quanti pannelli devono prezzare. Due: "Cost USD" e "Token e costo per sessione".
# NON e' pedanteria — e' il pavimento sui pannelli. Senza, un pannello che smette di
# corrispondere (chiave rinominata, `expr` spostata) sparisce dalla mappa, l'altro
# fornisce comunque tutti i modelli, gli insiemi tornano e il gate dichiara "quattro
# tipi ciascuno" su un pannello che non ha mai guardato. E' il difetto gia' pagato in
# questo repo (#161): un gate dichiara cosa ha ISPEZIONATO, non cosa gli ha restituito
# il glob.
PANNELLI_CHE_PREZZANO = 2

# Un pannello "prezza" se la sua espressione moltiplica per una costante. Stesso
# criterio del gate delle tariffe qui accanto in images.yml, e serve a non costringere
# ai quattro tipi un pannello che nomina un modello per altre ragioni (un conteggio di
# token, un filtro) — sarebbe un rosso falso, e un gate che grida a torto viene
# disattivato.
_MOLTIPLICATORE = re.compile(r"\*\s*[0-9.]")


def chiavi_del_prezzario(sorgente: str) -> set[str]:
    blocco = re.search(r"PRICES_USD_PER_MTOK\s*=\s*\{(.*?)\n\}", sorgente, re.S)
    if blocco is None:
        return set()
    # Solo le chiavi di primo livello: quelle dei tipi sono rientrate di otto spazi.
    return set(re.findall(r'^\s{4}"([^"]+)":\s*\{', blocco.group(1), re.M))


def _pannelli(dashboard: dict) -> list[dict]:
    """Anche quelli dentro una riga collassata, che Grafana sposta in `row.panels`."""
    fuori = []
    for pannello in dashboard.get("panels", []):
        fuori.append(pannello)
        fuori.extend(pannello.get("panels") or [])
    return fuori


def modelli_della_dashboard(dashboard: dict) -> tuple[set[str], dict[tuple[str, str], set[str]]]:
    """I tipi si contano PER PANNELLO, non sull'unione.

    Misurato il 31/08/2026 provando questo gate a rompersi: aggregando i tipi su tutti
    i pannelli, togliere `cacheRead` da UNO dei due lasciava il gate verde, perche'
    l'altro lo copriva. I due pannelli calcolano cifre diverse, e un tipo che manca a
    uno solo le fa divergere fra loro — peggio di un'assenza in entrambi, perche' una
    sembra confermare l'altra.
    """
    modelli: set[str] = set()
    per_pannello: dict[tuple[str, str], set[str]] = {}
    for pannello in _pannelli(dashboard):
        titolo = pannello.get("title", "(senza titolo)")
        for target in pannello.get("targets") or []:
            expr = target.get("expr", "")
            if not _MOLTIPLICATORE.search(expr):
                continue
            # Le due chiavi in ordine qualunque: in PromQL `{type=...,model=...}` e'
            # equivalente, e presumere un ordine renderebbe il gate cieco a una
            # riscrittura innocua.
            for coppia in re.findall(r"\{([^}]*)\}", expr):
                modello = re.search(r'model="([^"]+)"', coppia)
                tipo = re.search(r'type="([^"]+)"', coppia)
                if modello and tipo:
                    modelli.add(modello.group(1))
                    per_pannello.setdefault((titolo, modello.group(1)), set()).add(tipo.group(1))
    return modelli, per_pannello


def modelli_del_promql(sorgente: str) -> set[str]:
    """SOLO dalle espressioni sotto prova, non da tutto il file.

    Il file porta anche `input_series` e commenti che nominano modelli. Contarli
    renderebbe il gate soddisfatto da un modello presente fra i dati di ingresso ma
    ASSENTE dalla query — cioe' dall'unica cosa che questo file dice di mettere alla
    prova. Sarebbe di nuovo l'oracolo dentro il file esaminato.
    """
    modelli: set[str] = set()
    for expr in re.findall(r"^\s*- expr: \|\n((?:^[ \t]+.*\n|^\s*\n)+)", sorgente, re.M):
        if _MOLTIPLICATORE.search(expr):
            modelli |= set(re.findall(r'model="([^"]+)"', expr))
    return modelli


def verifica(sorgente_main: str, dashboard: dict, sorgente_promql: str) -> list[str]:
    tabella = chiavi_del_prezzario(sorgente_main)
    modelli_dash, tipi_per_pannello = modelli_della_dashboard(dashboard)
    promql = modelli_del_promql(sorgente_promql)

    # I PAVIMENTI, prima dei confronti. "Nessuna differenza" e "nessun dato" hanno lo
    # stesso aspetto: e' la lezione del pavimento dei 100 mutanti in mutation.yml.
    if len(tabella) < 2:
        return [
            f"lette {len(tabella)} chiavi da PRICES_USD_PER_MTOK, attese almeno 2.\n"
            "  Non e' un prezzario piccolo: e' un'estrazione che non ha funzionato."
        ]

    pannelli_visti = {titolo for titolo, _ in tipi_per_pannello}
    if len(pannelli_visti) != PANNELLI_CHE_PREZZANO:
        return [
            f"ispezionati {len(pannelli_visti)} pannelli che prezzano, attesi "
            f"{PANNELLI_CHE_PREZZANO}: {sorted(pannelli_visti) or '—'}.\n"
            "  Un pannello che non produce estrazioni non e' un pannello senza modelli:\n"
            "  e' un pannello che questo gate non ha guardato, e su cui non puo' dire niente."
        ]

    problemi: list[str] = []

    if modelli_dash != tabella:
        problemi.append(
            "la dashboard e il prezzario non nominano gli stessi modelli:\n"
            f"    solo in main.py   : {sorted(tabella - modelli_dash) or '—'}\n"
            f"    solo in dashboard : {sorted(modelli_dash - tabella) or '—'}\n"
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
                f"{sorted(mancanti)}:\n"
                "  quei token non entrano in quella cifra, in silenzio, e le due cifre\n"
                "  della dashboard divergono fra loro."
            )

    return problemi


def _stato_reale() -> tuple[str, dict, str]:
    return MAIN.read_text(), json.loads(DASHBOARD.read_text()), PROMQL.read_text()


def _prova() -> int:
    """Il gate sa diventare rosso? Sei mutanti, in memoria, nessun file toccato.

    Esiste perche' un gate che nessuno ha visto fallire e' una promessa, non un
    controllo — e in questo repo tre gate su sei erano nati ciechi.
    """
    main_src, dash, promql_src = _stato_reale()

    def senza_modello_nel_promql(s: str) -> str:
        return re.sub(r'^.*model="claude-opus-5\[1m\]".*\n', "", s, flags=re.M)

    def senza_un_tipo(d: dict) -> dict:
        copia = json.loads(json.dumps(d))
        for pannello in _pannelli(copia):
            for target in pannello.get("targets") or []:
                if "cacheRead" in target.get("expr", ""):
                    target["expr"] = re.sub(
                        r'[^+]*model="claude-opus-5\[1m\]",type="cacheRead"[^+]*\+',
                        "",
                        target["expr"],
                        count=1,
                    )
                    return copia
        return copia

    def un_pannello_muto(d: dict) -> dict:
        copia = json.loads(json.dumps(d))
        for pannello in _pannelli(copia):
            for target in pannello.get("targets") or []:
                if _MOLTIPLICATORE.search(target.get("expr", "")):
                    target["expr"] = "up"  # nessun modello, nessun moltiplicatore
                    return copia
        return copia

    mutanti = [
        ("modello tolto dal solo test promtool", main_src, dash, senza_modello_nel_promql(promql_src)),
        (
            "modello tolto dalla sola dashboard",
            main_src,
            json.loads(json.dumps(dash).replace("claude-opus-5[1m]", "claude-opus-5")),
            promql_src,
        ),
        ("un tipo tolto da UN SOLO pannello", main_src, senza_un_tipo(dash), promql_src),
        ("un pannello smette di corrispondere", main_src, un_pannello_muto(dash), promql_src),
        (
            "costante del prezzario rinominata",
            main_src.replace("PRICES_USD_PER_MTOK = {", "X = {", 1),
            dash,
            promql_src,
        ),
        (
            "modello solo fra le input_series del promtool",
            main_src,
            dash,
            senza_modello_nel_promql(promql_src) + '\n# series model="claude-opus-5[1m]"\n',
        ),
    ]

    esito = 0
    if verifica(main_src, dash, promql_src):
        print("FAIL: il gate e' rosso sullo stato reale, prima ancora dei mutanti.", file=sys.stderr)
        return 1
    print("verde sullo stato reale: OK")
    for nome, m, d, p in mutanti:
        problemi = verifica(m, d, p)
        if problemi:
            print(f"  rosso su «{nome}»: OK")
        else:
            print(f"  SOPRAVVISSUTO: «{nome}» — il gate non lo vede", file=sys.stderr)
            esito = 1
    return esito


def main(argv: list[str]) -> int:
    if "--prova" in argv:
        return _prova()

    for percorso in (MAIN, DASHBOARD, PROMQL):
        if not percorso.is_file():
            print(f"FAIL: manca {percorso}.", file=sys.stderr)
            return 1

    main_src, dash, promql_src = _stato_reale()
    problemi = verifica(main_src, dash, promql_src)

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

    tabella = chiavi_del_prezzario(main_src)
    _, tipi_per_pannello = modelli_della_dashboard(dash)
    pannelli = sorted({titolo for titolo, _ in tipi_per_pannello})
    # Si dichiara cosa e' stato ISPEZIONATO, coi nomi, non un conteggio che potrebbe
    # venire da un glob vuoto.
    print(f"OK: {len(tabella)} modelli identici nei tre posti — {sorted(tabella)}")
    print(f"    quattro tipi ciascuno in entrambi i pannelli ispezionati: {pannelli}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
