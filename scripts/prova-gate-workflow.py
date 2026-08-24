#!/usr/bin/env python3
"""Banco di prova dei due gate di `lint.yml` che sorvegliano i workflow.

Perche' esiste. Quei due gate sono nati rotti DUE volte in un giorno, ed entrambe le
volte erano stati dichiarati "provati rossi" a mano, in una shell che non esiste piu'.
Una prova che vive solo nella shell di chi l'ha scritta non e' un oracolo: e' un
ricordo.

E il "pavimento" dentro il gate (`job_visti < 15`) non lo sostituisce, perche' e' tarato
su un TOTALE: dice che il controllo ha girato su qualcosa, non che riconosce cio' che
deve riconoscere.

Come funziona. Lo script del gate NON viene copiato qui — verrebbe una seconda copia, e
le seconde copie divergono. Viene ESTRATTO da `.github/workflows/lint.yml` a ogni
esecuzione, quindi il banco prova sempre il gate che spedisce davvero. Ogni caso parte
da una copia dell'albero VERO (il pavimento vuole l'ordine di grandezza reale) con una
sola cosa cambiata.

L'ultima sezione e' quella che conta di piu': muta il gate e pretende che un caso
preciso CAMBI colore. Un caso che resta verde anche quando il controllo che dovrebbe
esercitare e' stato rimosso non prova niente.

Nota storica, perche' spiega perche' questo file e' meta' di quello che era: fino al
24/08/2026 c'era un terzo controllo, sui due tetti di ogni `curl`, ed e' stato rimosso
dopo quattro giri di correzioni — un regex non puo' decidere se una `&` separa due
comandi o sta dentro una query string senza interpretare il quoting. I casi che lo
esercitavano sono spariti con lui.

Uso:
    python3 scripts/prova-gate-workflow.py
Esce 0 se tutti i casi si comportano come atteso, 1 altrimenti. Serve PyYAML.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parent.parent
LINT = RADICE / ".github" / "workflows" / "lint.yml"

TETTI = "ogni job dichiara il proprio tetto"
DUPLICATI = "nessuno step duplicato dentro lo stesso job"


def estrai(nome_step: str) -> str:
    """Il corpo python del gate, preso dal workflow che gira in CI."""
    workflow = yaml.safe_load(LINT.read_text())
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps", []):
            if step.get("name") == nome_step:
                corpo = re.search(
                    r"python3 - <<'PY'\n(.*?)\n\s*PY\s*$",
                    step.get("run", ""),
                    re.S,
                )
                if not corpo:
                    raise SystemExit(f"lo step {nome_step!r} non contiene piu' un heredoc PY")
                # l'heredoc e' indentato dentro il YAML: si toglie il rientro comune
                righe = corpo.group(1).splitlines()
                margine = min(
                    (len(r) - len(r.lstrip()) for r in righe if r.strip()),
                    default=0,
                )
                return "\n".join(r[margine:] if r.strip() else "" for r in righe)
    raise SystemExit(f"nessuno step chiamato {nome_step!r} in {LINT}")


def albero() -> Path:
    """Una copia dell'albero reale: solo cio' che i due gate leggono."""
    temp = Path(tempfile.mkdtemp(prefix="prova-gate-"))
    (temp / ".github").mkdir()
    shutil.copytree(RADICE / ".github" / "workflows", temp / ".github" / "workflows")
    return temp


def gira_completo(codice: str, radice: Path):
    """(exit code, stdout+stderr). Il messaggio serve quanto il codice di uscita.

    S603: `codice` non e' input non fidato — e' lo script del gate estratto da
    `.github/workflows/lint.yml`, cioe' da un file versionato di questo repository, e
    girarlo davvero E' il punto del banco. La sola alternativa sarebbe copiarlo qui
    dentro, che vorrebbe dire una seconda copia del gate: la cosa che il progetto
    vieta per ogni file di configurazione, e per la stessa ragione.
    """
    esito = subprocess.run(  # noqa: S603
        [sys.executable, "-c", codice], cwd=radice, capture_output=True, text=True
    )
    return esito.returncode, esito.stdout + esito.stderr


SENZA_TETTO = "on: {}\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
DUE_STEP_UGUALI = (
    "on: {}\njobs:\n  a:\n    timeout-minutes: 5\n    steps:\n"
    "      - name: uguale\n        run: 'true'\n"
    "      - name: uguale\n        run: 'true'\n"
)

# Ogni caso: (descrizione, rosso, come si sporca l'albero[, frammento atteso]).
CASI = [
    ("l'albero reale, non toccato", False, lambda t: None),
    (
        "un job senza timeout-minutes",
        True,
        lambda t: (t / ".github/workflows/x.yml").write_text(SENZA_TETTO),
        "non dichiara `timeout-minutes`",
    ),
    (
        "un job senza timeout-minutes in un file .yaml",
        True,
        lambda t: (t / ".github/workflows/x.yaml").write_text(SENZA_TETTO),
        "non dichiara `timeout-minutes`",
    ),
    (
        "un job che chiama una reusable workflow (non puo' avere timeout-minutes)",
        False,
        lambda t: (t / ".github/workflows/x.yml").write_text(
            "on: {}\njobs:\n  a:\n    uses: ./.github/workflows/interno.yml\n"
        ),
    ),
]

CASI_DUPLICATI = [
    ("l'albero reale, non toccato", False, lambda t: None),
    (
        "due step omonimi nello stesso job (.yml)",
        True,
        lambda t: (t / ".github/workflows/x.yml").write_text(DUE_STEP_UGUALI),
        "ha 2 step chiamati",
    ),
    (
        "due step omonimi nello stesso job (.yaml)",
        True,
        lambda t: (t / ".github/workflows/x.yaml").write_text(DUE_STEP_UGUALI),
        "ha 2 step chiamati",
    ),
]

# Ogni mutante toglie UN pezzo del gate e nomina il caso che deve accorgersene.
# Se quel caso non cambia colore, il caso e' vacuo: verde per un ramo diverso da
# quello che il suo nome dichiara.
MUTANTI = [
    (
        "il salto dei job `uses:`",
        'if "uses" in (corpo or {}):\n            continue\n        ',
        "",
        "un job che chiama una reusable workflow (non puo' avere timeout-minutes)",
    ),
    (
        "l'estensione .yaml sui job",
        ' + glob.glob(".github/workflows/*.yaml")',
        "",
        "un job senza timeout-minutes in un file .yaml",
    ),
]

MUTANTI_DUPLICATI = [
    (
        "l'estensione .yaml sugli step duplicati",
        '\n    + glob.glob(".github/workflows/*.yaml")\n):',
        "\n):",
        "due step omonimi nello stesso job (.yaml)",
    ),
]


def prova_caso(codice, caso):
    """(va_bene, uscita, perche'). L'oracolo NON e' il solo codice di uscita.

    Un gate che ESPLODE esce 1 come uno che boccia, e con il solo exit code un caso
    rosso restava verde-di-nome anche quando il controllo che nomina era spento e a
    farlo fallire era un `KeyError`. Quindi per un caso rosso si pretende anche un
    `::error::` (il gate ha parlato), nessun `Traceback` (non e' morto) e, dove il
    caso lo dichiara, il FRAMMENTO del messaggio giusto: rosso per la ragione che il
    caso nomina, non per una qualunque.
    """
    rosso, sporca = caso[1], caso[2]
    frammento = caso[3] if len(caso) > 3 else None
    temp = albero()
    try:
        sporca(temp)
        uscita, testo = gira_completo(codice, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    if "Traceback" in testo:
        return False, uscita, "il gate e' ESPLOSO invece di decidere"
    if rosso:
        if uscita != 1:
            return False, uscita, "atteso rosso"
        if "::error::" not in testo:
            return False, uscita, "uscito 1 senza dire niente"
        if frammento and frammento not in testo:
            return False, uscita, f"rosso per un'altra ragione (manca {frammento!r})"
        return True, uscita, ""
    if uscita != 0:
        return False, uscita, "atteso verde"
    return True, uscita, ""


def prova_dichiarazione(codice) -> int:
    """Il gate deve DICHIARARE i file letti, e devono essere tutti quelli che ci sono.

    Perche' non basta il pavimento `job_visti < 15`: conta un TOTALE e non sa CHE COSA
    ha guardato. I job reali sono 21, quindi c'e' un margine di sei. Misurato il
    24/08/2026: un gate che smettesse di leggere `sonar.yml`, `smoke.yml` e
    `sorveglianza.yml` — l'unico gate obbligatorio del ruleset e due sonde — ne vedrebbe
    15, resterebbe VERDE sulla realta' e questo banco stampava TUTTO A POSTO.

    Alzare la soglia a 21 non chiude la classe: fra sei mesi la si riabbassa per far
    tornare il verde, ed e' la stessa mossa che qui e' gia' costata quattro giri.
    L'oracolo e' il glob calcolato QUI, fuori dal gate: il soggetto dichiara, il banco
    confronta. Un file che sparisce dalla vista del gate diventa rosso subito,
    qualunque sia il numero di job rimasti.
    """
    print("\n== il gate dichiara cosa ha letto")
    temp = albero()
    try:
        attesi = sorted(
            p.relative_to(temp).as_posix()
            for p in list((temp / ".github/workflows").glob("*.yml"))
            + list((temp / ".github/workflows").glob("*.yaml"))
        )
        uscita, testo = gira_completo(codice, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    dichiarati = sorted(re.findall(r"^letto: (.+)$", testo, re.M))
    if uscita != 0:
        print(f"  ERRORE il gate non e' verde sull'albero reale (exit {uscita})")
        return 1
    if not dichiarati:
        print("  ERRORE il gate non dichiara nessun file letto: il pavimento e' l'unica difesa")
        return 1
    if dichiarati != attesi:
        mancanti = set(attesi) - set(dichiarati)
        in_piu = set(dichiarati) - set(attesi)
        print(f"  ERRORE dichiarati {len(dichiarati)}, presenti {len(attesi)}")
        if mancanti:
            print(f"         NON letti: {sorted(mancanti)}")
        if in_piu:
            print(f"         letti ma inesistenti: {sorted(in_piu)}")
        return 1
    print(f"  ok   {len(dichiarati)} file dichiarati = {len(attesi)} file presenti")

    # E la prova che questo controllo NON e' vacuo, che e' la meta' che di solito manca:
    # si acceca il gate su tre workflow — esattamente i tre della misura — e si pretende
    # che il confronto se ne accorga. Il pavimento, da solo, non se ne accorgerebbe: i
    # job scendono a 15 e 15 non e' minore di 15.
    ciechi = ("sonar.yml", "smoke.yml", "sorveglianza.yml")
    # Si muta il GLOB, non il print: modella "il gate non vede quei file", che e' lo
    # scenario vero. E si muta su una riga sola perche' il codice estratto e' dedentato:
    # una mutazione multi-riga porterebbe l'indentazione sbagliata e farebbe fallire il
    # gate per IndentationError — rosso per il motivo sbagliato, cioe' una prova viziata.
    originale = (
        'for percorso in sorted(glob.glob(".github/workflows/*.yml")'
        ' + glob.glob(".github/workflows/*.yaml")):'
    )
    mutato = codice.replace(
        originale,
        'for percorso in [p for p in sorted(glob.glob(".github/workflows/*.yml")'
        ' + glob.glob(".github/workflows/*.yaml"))'
        f" if not p.endswith({ciechi!r})]:",
    )
    if mutato == codice:
        print("  ERRORE non trovo la riga da mutare: la prova di cecita' non ha girato")
        return 1
    temp = albero()
    try:
        uscita_cieca, testo_cieco = gira_completo(mutato, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    visti_ciechi = sorted(re.findall(r"^letto: (.+)$", testo_cieco, re.M))
    if visti_ciechi == attesi:
        print("  ERRORE il gate accecato dichiara gli stessi file: la mutazione non ha morso")
        return 1
    if uscita_cieca != 0:
        print(f"  ERRORE il gate accecato esce {uscita_cieca}: sarebbe rosso da solo, prova viziata")
        return 1
    print(
        f"  ok   accecato su {len(ciechi)} workflow ne dichiara {len(visti_ciechi)} invece di "
        f"{len(attesi)} — resta verde ({uscita_cieca}) e solo questo confronto lo vede"
    )
    return 0


def esegui(titolo, codice, casi) -> int:
    errori = 0
    print(f"\n== {titolo}")
    for caso in casi:
        va_bene, uscita, perche = prova_caso(codice, caso)
        if not va_bene:
            errori += 1
        colore = "rosso" if caso[1] else "verde"
        print(
            f"  {'ok  ' if va_bene else 'ERRORE'} [{colore}] {caso[0]} -> exit {uscita}"
            + (f"  ({perche})" if perche else "")
        )
    return errori


def esegui_mutanti(codice, mutanti, casi, titolo) -> int:
    errori = 0
    print(f"\n== {titolo}")
    per_nome = {c[0]: (c[1], c[2]) for c in casi}
    for nome, cerca, sostituisci, caso in mutanti:
        if cerca not in codice:
            print(f"  ERRORE mutante '{nome}': il pezzo da mutare non esiste piu' nel gate")
            errori += 1
            continue
        mutato = codice.replace(cerca, sostituisci)
        rosso, sporca = per_nome[caso]
        temp = albero()
        try:
            sporca(temp)
            uscita, testo = gira_completo(mutato, temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        atteso = 1 if rosso else 0
        # Il mutante deve far cambiare colore al caso E lasciare il gate VIVO: un
        # mutante che lo fa esplodere uscirebbe 1 e verrebbe contato come "il caso se
        # ne accorge", mentre non ha esercitato proprio niente.
        vivo = "Traceback" not in testo
        cambiato = uscita != atteso and vivo
        if not cambiato:
            errori += 1
        motivo = "" if vivo else "  (il mutante ha fatto ESPLODERE il gate: non prova niente)"
        print(
            f"  {'ok  ' if cambiato else 'ERRORE'} tolto {nome}: "
            f"'{caso}' -> exit {uscita} (senza mutante: {atteso}){motivo}"
        )
    return errori


def main() -> int:
    tetti = estrai(TETTI)
    duplicati = estrai(DUPLICATI)
    errori = esegui("tetti sui job", tetti, CASI)
    errori += prova_dichiarazione(tetti)
    errori += esegui("step duplicati", duplicati, CASI_DUPLICATI)
    errori += esegui_mutanti(
        tetti,
        MUTANTI,
        CASI,
        "i casi non sono vacui: ogni mutante deve far cambiare colore al suo caso",
    )
    errori += esegui_mutanti(
        duplicati,
        MUTANTI_DUPLICATI,
        CASI_DUPLICATI,
        "e lo stesso per il gate degli step duplicati",
    )
    print(f"\n{'TUTTO A POSTO' if not errori else str(errori) + ' CASI FUORI POSTO'}")
    return 1 if errori else 0


if __name__ == "__main__":
    raise SystemExit(main())
