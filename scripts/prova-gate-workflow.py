#!/usr/bin/env python3
"""Banco di prova dei due gate di `lint.yml` che sorvegliano i workflow.

Perche' esiste. Quei due gate sono nati rotti DUE volte in un giorno: il primo
matchava la parola `curl` nel proprio codice sorgente, il secondo non vedeva
`- run: curl ...` su una riga sola. Entrambe le volte erano stati dichiarati
"provati rossi su sei rotture" — a mano, in una shell che non esiste piu'. Una prova
che vive solo nella shell di chi l'ha scritta non e' un oracolo: e' un ricordo.

E i due "pavimenti" dentro il gate (`job_visti < 15`, `curl_visti < 20`) non bastano a
sostituirlo, perche' sono tarati su un TOTALE: dei curl reali la maggioranza sta in
`scripts/`, quindi un regex che smettesse di vedere TUTTI quelli dei workflow lascerebbe
il totale sopra il pavimento e il gate resterebbe verde.

Come funziona. Lo script del gate NON viene copiato qui — verrebbe una seconda copia, e
le seconde copie divergono. Viene ESTRATTO da `.github/workflows/lint.yml` a ogni
esecuzione, quindi il banco prova sempre il gate che spedisce davvero. Ogni caso parte
da una copia dell'albero VERO (i pavimenti vogliono l'ordine di grandezza reale) con una
sola cosa cambiata.

L'ultima sezione e' quella che conta di piu': muta il gate e pretende che un caso
preciso CAMBI colore. Un caso che resta verde anche quando il controllo che dovrebbe
esercitare e' stato rimosso non prova niente — e' esattamente il difetto che questa
giornata ha gia' pagato.

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

TETTI = "ogni job dichiara il proprio tetto, ogni curl i suoi due"
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
    (temp / "scripts").mkdir()
    for sh in (RADICE / "scripts").glob("*.sh"):
        shutil.copy(sh, temp / "scripts" / sh.name)
    return temp


def gira(codice: str, radice: Path) -> int:
    return subprocess.run(
        [sys.executable, "-c", codice], cwd=radice, capture_output=True, text=True
    ).returncode


# Ogni caso: (descrizione, esito atteso, come si sporca l'albero).
# `rosso=True` significa "il gate DEVE bocciare".
CASI = [
    ("l'albero reale, non toccato", False, lambda t: None),
    (
        "un job senza timeout-minutes",
        True,
        lambda t: (t / ".github/workflows/x.yml").write_text(
            "on: {}\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
    ),
    (
        "un job senza timeout-minutes in un file .yaml",
        True,
        lambda t: (t / ".github/workflows/x.yaml").write_text(
            "on: {}\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: []\n"
        ),
    ),
    (
        "un curl senza --max-time",
        True,
        lambda t: (t / "scripts/x.sh").write_text("curl --connect-timeout 5 -sS https://x\n"),
    ),
    (
        "un curl senza --connect-timeout",
        True,
        lambda t: (t / "scripts/x.sh").write_text("curl --max-time 30 -sS https://x\n"),
    ),
    (
        "due --max-time sulla stessa invocazione (vince l'ultimo)",
        True,
        lambda t: (t / "scripts/x.sh").write_text(
            "curl --connect-timeout 5 --max-time 30 --max-time 5 -sS https://x\n"
        ),
    ),
    (
        "`- run: curl ...` su una riga sola, senza tetti",
        True,
        lambda t: (t / ".github/workflows/x.yml").write_text(
            "on: {}\njobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n      - run: curl -sS https://x\n"
        ),
    ),
    (
        "un curl in una subshell `( ... )`, senza tetti",
        True,
        lambda t: (t / "scripts/x.sh").write_text("(curl -sS https://x)\n"),
    ),
    (
        "un curl in un gruppo `{ ... }`, senza tetti",
        True,
        lambda t: (t / "scripts/x.sh").write_text("{ curl -sS https://x; }\n"),
    ),
    (
        "un curl senza tetti in un workflow .yaml",
        True,
        lambda t: (t / ".github/workflows/x.yaml").write_text(
            "on: {}\njobs:\n  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n      - run: curl -sS https://x\n"
        ),
    ),
    (
        "un curl spezzato su piu' righe, senza tetti",
        True,
        lambda t: (t / "scripts/x.sh").write_text("curl -sS \\\n  https://x\n"),
    ),
    # --- i casi VERDI: falsi positivi che costerebbero un `continue-on-error` ---
    (
        "`-m 30`, la forma breve documentata di --max-time",
        False,
        lambda t: (t / "scripts/x.sh").write_text("curl -m 30 --connect-timeout 5 -sS https://x\n"),
    ),
    (
        "`--max-time=30` con l'uguale",
        False,
        lambda t: (t / "scripts/x.sh").write_text(
            "curl --max-time=30 --connect-timeout=5 -sS https://x\n"
        ),
    ),
    (
        "due curl completi sulla stessa riga logica",
        False,
        lambda t: (t / "scripts/x.sh").write_text(
            '[ "$(curl --connect-timeout 5 --max-time 30 -s A)" '
            '= "$(curl --connect-timeout 5 --max-time 30 -s B)" ]\n'
        ),
    ),
    (
        "un job che chiama una reusable workflow (non puo' avere timeout-minutes)",
        False,
        lambda t: (t / ".github/workflows/x.yml").write_text(
            "on: {}\njobs:\n  a:\n    uses: ./.github/workflows/interno.yml\n"
        ),
    ),
    (
        "il TESTO `run: curl ...` dentro un echo, non un comando",
        False,
        lambda t: (t / "scripts/x.sh").write_text(
            'echo "per una sonda si scrive cosi: run: curl -sS https://esempio"\n'
        ),
    ),
    (
        "`curl` dentro un commento",
        False,
        lambda t: (t / "scripts/x.sh").write_text("# curl -sS https://x\n"),
    ),
]

CASI_DUPLICATI = [
    ("l'albero reale, non toccato", False, lambda t: None),
    (
        "due step omonimi nello stesso job (.yml)",
        True,
        lambda t: (t / ".github/workflows/x.yml").write_text(
            "on: {}\njobs:\n  a:\n    timeout-minutes: 5\n    steps:\n"
            "      - name: uguale\n        run: 'true'\n"
            "      - name: uguale\n        run: 'true'\n"
        ),
    ),
    (
        "due step omonimi nello stesso job (.yaml)",
        True,
        lambda t: (t / ".github/workflows/x.yaml").write_text(
            "on: {}\njobs:\n  a:\n    timeout-minutes: 5\n    steps:\n"
            "      - name: uguale\n        run: 'true'\n"
            "      - name: uguale\n        run: 'true'\n"
        ),
    ),
]

# Ogni mutante toglie UN pezzo del gate e nomina il caso che deve accorgersene.
# Se quel caso non cambia colore, il caso e' vacuo: verde per un ramo diverso da
# quello che il suo nome dichiara.
MUTANTI = [
    (
        "l'ancora `run:` a inizio riga",
        r"|^\s*-?\s*run:\s*",
        "",
        "`- run: curl ...` su una riga sola, senza tetti",
    ),
    (
        "i delimitatori `(` e `{`",
        r"[|;&`!({]",
        r"[|;&`!]",
        "un curl in una subshell `( ... )`, senza tetti",
    ),
    (
        "la forma breve `-m`",
        r"(?<![\w-])(?:--max-time|-m)(?=[= ])",
        r"(?<![\w-])(?:--max-time)(?=[= ])",
        "`-m 30`, la forma breve documentata di --max-time",
    ),
    (
        "il conteggio per invocazione",
        "quanti = len(CURL.findall(riga))",
        "quanti = 1 if CURL.search(riga) else 0",
        "due curl completi sulla stessa riga logica",
    ),
    (
        "l'estensione .yaml sui curl",
        '\n    + glob.glob(".github/workflows/*.yaml")\n):',
        "\n):",
        "un curl senza tetti in un workflow .yaml",
    ),
]


def esegui(titolo, codice, casi) -> int:
    errori = 0
    print(f"\n== {titolo}")
    for descrizione, rosso, sporca in casi:
        temp = albero()
        try:
            sporca(temp)
            uscita = gira(codice, temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        atteso = 1 if rosso else 0
        esito = "ok  " if uscita == atteso else "ERRORE"
        if uscita != atteso:
            errori += 1
        print(f"  {esito} [{'rosso' if rosso else 'verde'}] {descrizione} -> exit {uscita}")
    return errori


def esegui_mutanti(codice) -> int:
    errori = 0
    print("\n== i casi non sono vacui: ogni mutante deve far cambiare colore al suo caso")
    per_nome = {d: (r, s) for d, r, s in CASI}
    for nome, cerca, sostituisci, caso in MUTANTI:
        if cerca not in codice:
            print(f"  ERRORE mutante '{nome}': il pezzo da mutare non esiste piu' nel gate")
            errori += 1
            continue
        mutato = codice.replace(cerca, sostituisci)
        rosso, sporca = per_nome[caso]
        temp = albero()
        try:
            sporca(temp)
            uscita = gira(mutato, temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        atteso = 1 if rosso else 0
        cambiato = uscita != atteso
        if not cambiato:
            errori += 1
        print(
            f"  {'ok  ' if cambiato else 'ERRORE'} tolto {nome}: "
            f"'{caso}' -> exit {uscita} (senza mutante: {atteso})"
        )
    return errori


def main() -> int:
    tetti = estrai(TETTI)
    duplicati = estrai(DUPLICATI)
    errori = esegui("tetti su job e curl", tetti, CASI)
    errori += esegui("step duplicati", duplicati, CASI_DUPLICATI)
    errori += esegui_mutanti(tetti)
    print(f"\n{'TUTTO A POSTO' if not errori else str(errori) + ' CASI FUORI POSTO'}")
    return 1 if errori else 0


if __name__ == "__main__":
    raise SystemExit(main())
