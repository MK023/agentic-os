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

Le sezioni che contano di piu' sono quelle che MUTANO il gate e pretendono che un
caso preciso cambi colore: un caso che resta verde anche quando il controllo che
dovrebbe esercitare e' stato rimosso non prova niente. E ogni sabotatura pretende che
il gate sia ancora VIVO — uno che esplode esce 1 come uno che boccia, e senza quella
guardia una prova passa senza aver esercitato niente.

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
    """(exit code, stdout+stderr, compila). Il messaggio serve quanto il codice di uscita.

    S603: `codice` non e' input non fidato — e' lo script del gate estratto da
    `.github/workflows/lint.yml`, cioe' da un file versionato di questo repository, e
    girarlo davvero E' il punto del banco. La sola alternativa sarebbe copiarlo qui
    dentro, che vorrebbe dire una seconda copia del gate: la cosa che il progetto
    vieta per ogni file di configurazione, e per la stessa ragione.

    Il terzo valore e' il motivo per cui questa funzione e' stata toccata il
    01/09/2026. "Il gate e' morto prima di decidere?" si chiedeva leggendo il testo in
    cerca di `Traceback`, poi anche di `SyntaxError`: due forme della stessa classe,
    enumerate. La terza — `IndentationError`, che si ottiene togliendo una riga di
    intestazione `if` e lasciando il corpo rientrato — usciva 1 senza nessuna delle due
    stringhe, e il cadavere veniva contato come bocciatura. `compile()` chiude la CLASSE
    invece di aggiungere la terza forma: qualunque morte sintattica, presente o futura,
    e' una `SyntaxError` per il parser di Python e diventa un `False` qui, senza che
    nessuno debba prevederne il nome.
    """
    try:
        compile(codice, "<gate>", "exec")
    except SyntaxError as errore:  # IndentationError e TabError ne sono sottoclassi
        return 1, f"il gate non compila: {errore}", False
    esito = subprocess.run(  # noqa: S603
        [sys.executable, "-c", codice], cwd=radice, capture_output=True, text=True
    )
    return esito.returncode, esito.stdout + esito.stderr, True


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
        # NON `'if "uses" not in corpo and ' -> 'if ('`, che era la forma fino al
        # 01/09/2026: produceva `if ("timeout-minutes" not in corpo:`, cioe' codice
        # NON VALIDO, e quindi non modellava "il gate senza l'esenzione `uses:`" ma
        # "il gate che non compila". Toglie la sola condizione e lascia
        # `if "timeout-minutes" not in corpo:`, che e' il gate senza quel ramo.
        "il salto dei job `uses:`",
        '"uses" not in corpo and ',
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


def morto(compila: bool, testo: str) -> bool:
    """Il gate e' morto prima di decidere, invece di aver deciso.

    Un gate che ESPLODE esce 1 come uno che boccia: ogni controllo di questo banco
    deve saperlo distinguere, o conta un cadavere come una bocciatura.

    Due morti diverse, e solo la seconda si legge nel testo. La morte SINTATTICA la
    decide `compile()` dentro `gira_completo`, deterministicamente, per l'intera classe:
    un primo tentativo, lo stesso giorno, cercava le stringhe `Traceback` e
    `SyntaxError` e lasciava passare `IndentationError` — cioe' enumerava le forme
    credendo di chiudere la classe, che e' il difetto che stava correggendo. La morte a
    RUNTIME resta un `Traceback` nel testo, perche' e' quello che Python stampa e non
    c'e' un modo migliore da qui.

    E `SyntaxError` NON si cerca piu' nel testo, il che toglie anche un falso positivo:
    il gate degli step duplicati stampa i NOMI degli step nel proprio messaggio, quindi
    uno step chiamato "prova del SyntaxError" faceva leggere come esplosione una
    bocciatura perfettamente corretta.
    """
    return not compila or "Traceback" in testo


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
        uscita, testo, compila = gira_completo(codice, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    if morto(compila, testo):
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


def conta_job(radice: Path) -> dict:
    """L'oracolo: quanti job ha ogni workflow, contati QUI, fuori dal gate."""
    d = {}
    cartella = radice / ".github/workflows"
    for p in sorted(list(cartella.glob("*.yml")) + list(cartella.glob("*.yaml"))):
        d[p.relative_to(radice).as_posix()] = len((yaml.safe_load(p.read_text()) or {}).get("jobs") or {})
    return d


def legge_dichiarazione(testo: str) -> dict:
    """`letto: <percorso> (<n> job)` -> {percorso: n}. Un doppione e' un errore."""
    coppie = re.findall(r"^letto: (.+) \((\d+) job\)$", testo, re.M)
    d = {}
    for percorso, quanti in coppie:
        if percorso in d:
            return {"DOPPIONE " + percorso: -1}
        d[percorso] = int(quanti)
    return d


def prova_dichiarazione(codice, titolo) -> int:
    """Il gate deve DICHIARARE i file letti, e devono essere tutti quelli che ci sono.

    Perche' non basta il pavimento `job_visti < 15`: conta un TOTALE e non sa CHE COSA
    ha guardato. I job reali sono 21, quindi c'e' un margine di sei. Misurato il
    24/08/2026: un gate che smettesse di leggere `sonar.yml`, `smoke.yml` e
    `sorveglianza.yml` — fra i tre, l'unico gate obbligatorio del ruleset e due sonde — ne vedrebbe
    15, resterebbe VERDE sulla realta' e questo banco stampava TUTTO A POSTO.

    Alzare la soglia a 21 non chiude la classe: fra sei mesi la si riabbassa per far
    tornare il verde, ed e' la stessa mossa che qui e' gia' costata quattro giri.
    L'oracolo e' il glob calcolato QUI, fuori dal gate: il soggetto dichiara, il banco
    confronta. Un file che sparisce dalla vista del gate diventa rosso subito,
    qualunque sia il numero di job rimasti.
    """
    print(f"\n== {titolo}: dichiara cosa ha ISPEZIONATO")
    temp = albero()
    try:
        attesi = conta_job(temp)
        uscita, testo, _ = gira_completo(codice, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    dichiarati = legge_dichiarazione(testo)
    if uscita != 0:
        print(f"  ERRORE il gate non e' verde sull'albero reale (exit {uscita})")
        return 1
    if not dichiarati:
        print("  ERRORE il gate non dichiara niente: il pavimento e' l'unica difesa")
        return 1
    if dichiarati != attesi:
        print(f"  ERRORE dichiarati {len(dichiarati)} file, presenti {len(attesi)}")
        for f in sorted(set(attesi) | set(dichiarati)):
            if attesi.get(f) != dichiarati.get(f):
                print(
                    f"         {f}: ispezionati {dichiarati.get(f, 'MAI')} job, ce ne sono {attesi.get(f, 0)}"
                )
        return 1
    print(
        f"  ok   {len(dichiarati)} file e {sum(dichiarati.values())} job dichiarati, "
        f"uno per uno = quelli che ci sono"
    )

    # E la prova che questo controllo NON e' vacuo, che e' la meta' che di solito manca:
    # si acceca il gate su un insieme di workflow derivato dal pavimento e si pretende
    # che il confronto se ne accorga. Il pavimento, da solo, non se ne accorgerebbe: i
    # job scendono a 15 e 15 non e' minore di 15.
    # PRIMA sabotatura: un `continue` che salta l'ispezione. E' la forma esatta che una
    # revisione ha trovato il 24/08/2026 quando la dichiarazione stava PRIMA del ciclo:
    # allora il gate dichiarava 11 file, ne ispezionava 8 e usciva 0. Adesso la
    # dichiarazione sta dopo l'ispezione, quindi saltare l'ispezione salta anche la
    # dichiarazione — e questo controllo pretende che si veda.
    # UNA sola sostituzione, ancorata con `re` e con il rientro catturato. Erano due
    # `.replace` — una sui 14 spazi del YAML grezzo, una sui 4 del codice estratto — e
    # la guardia copriva la COPPIA invece di ciascuna: quella a 14 spazi non ha mai
    # morso e nessuno se ne accorgeva. La stessa classe che il giro prima diceva di aver
    # chiuso sulla mutazione del glob, rifatta venti righe sotto.
    salta, quante = re.subn(
        r"^(\s*)qui = 0$",
        r'\1if percorso.endswith(("sonar.yml","smoke.yml","sorveglianza.yml")): continue\n\1qui = 0',
        codice,
        flags=re.M,
    )
    if quante != 1:
        print(f"  ERRORE `qui = 0` trovato {quante} volte: la sabotatura col `continue` non ha girato")
        return 1
    temp = albero()
    try:
        uscita_salta, testo_salta, compila_salta = gira_completo(salta, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    # VIVO, non solo diverso: un saboteur che fa esplodere il gate produce dichiarazione
    # vuota, cioe' "diversa", e passerebbe per riuscito senza aver esercitato niente.
    if morto(compila_salta, testo_salta):
        print(f"  ERRORE il gate sabotato e' ESPLOSO (exit {uscita_salta}): la prova non esercita niente")
        return 1
    saltati = {f for f in attesi if f.endswith(("sonar.yml", "smoke.yml", "sorveglianza.yml"))}
    # I tre nomi sono INCHIODATI dentro la sabotatura, e questo e' il loro unico
    # guinzaglio: se nessuno dei tre esiste piu' — rinominato, spostato, unito a un
    # altro — il `continue` non scatta su niente, la dichiarazione non cala di un
    # file, e il confronto qui sotto diventa `attesi == attesi`, cioe' una tautologia
    # che stampa `ok`. La `quante != 1` sopra guarda che la SOSTITUZIONE sia
    # avvenuta, non che MORDA: sono due domande diverse, e fino al 01/09/2026 si
    # rispondeva solo alla prima.
    if not saltati:
        print(
            "  ERRORE nessuno fra sonar.yml, smoke.yml e sorveglianza.yml esiste piu': "
            "la sabotatura col `continue` non salta niente e il confronto e' una tautologia"
        )
        return 1
    if legge_dichiarazione(testo_salta) != {k: v for k, v in attesi.items() if k not in saltati}:
        print(
            "  ERRORE saltando l'ispezione la dichiarazione non cala esattamente dei file saltati: "
            "prova cio' che GLOBBA, non cio' che ISPEZIONA"
        )
        return 1
    print("  ok   un `continue` che salta l'ispezione salta anche la dichiarazione")

    # SECONDA sabotatura: il gate non vede tre file. Quali tre non e' inchiodato qui —
    # si deriva dal pavimento LETTO NEL GATE, altrimenti l'aritmetica scade: bastava
    # togliere un job dal repository perche' il gate accecato scendesse sotto la soglia,
    # uscisse rosso da solo, e questo banco — che e' un check obbligatorio del ruleset —
    # diventasse rosso puntando a se stesso invece che alla causa.
    trovato = re.search(r"job_visti < (\d+)", codice)
    if not trovato:
        # Il gate senza pavimento (gli step duplicati) non ha un'aritmetica da
        # rispettare: si acceca UN file solo, che basta a provare che il confronto
        # morde. `max(...)` invece era insicuro per fortuna, non per costruzione:
        # con un workflow da 7 job avrebbe accecato piu' del margine e reso il banco
        # rosso puntando a se stesso. E il parametro `con_pavimento` era una seconda
        # lista: `not trovato` lo deduce da solo dal codice del gate.
        pavimento, margine = 0, min(attesi.values())
    else:
        pavimento = int(trovato.group(1))
        margine = sum(attesi.values()) - pavimento
    ciechi, spesi = [], 0
    for percorso, quanti in sorted(attesi.items(), key=lambda kv: -kv[1]):
        if spesi + quanti <= margine:
            ciechi.append(Path(percorso).name)
            spesi += quanti
    if not ciechi:
        print("  ERRORE nessun file accecabile sotto il pavimento: la prova non puo' girare")
        return 1
    ciechi = tuple(ciechi)
    # Si muta il GLOB, non il print: modella "il gate non vede quei file". E si
    # sostituisce la CHIAMATA `glob.glob(...)`, non la riga del `for`, perche' i due
    # gate scrivono lo stesso glob in due forme diverse — una su una riga, l'altra su
    # tre. Legare la mutazione alla scrittura invece che alla chiamata l'aveva fatta
    # girare su un gate solo: la forma invece della classe, di nuovo.
    mutato = codice
    for modello in ('".github/workflows/*.yml"', '".github/workflows/*.yaml"'):
        mutato = mutato.replace(
            f"glob.glob({modello})",
            f"[p for p in glob.glob({modello}) if not p.endswith({ciechi!r})]",
        )
    if mutato == codice:
        print("  ERRORE non trovo la riga da mutare: la prova di cecita' non ha girato")
        return 1
    temp = albero()
    try:
        uscita_cieca, testo_cieco, compila_cieco = gira_completo(mutato, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    if morto(compila_cieco, testo_cieco):
        print("  ERRORE il gate accecato e' ESPLOSO: la mutazione non esercita niente")
        return 1
    visti_ciechi = legge_dichiarazione(testo_cieco)
    atteso_cieco = {k: v for k, v in attesi.items() if Path(k).name not in ciechi}
    # RIDUZIONE ESATTA, non "diverso": il confronto era fra una lista e un dizionario,
    # cioe' un `if` sempre falso con sopra un messaggio d'errore che rassicurava. Una
    # mutazione che cambiava il testo del gate senza accecare nessun file passava per
    # riuscita, e la riga di successo stampava "ne dichiara 11 invece di 11".
    if visti_ciechi != atteso_cieco:
        print(
            f"  ERRORE il gate accecato dichiara {len(visti_ciechi)} file, "
            f"ne servivano {len(atteso_cieco)}: la mutazione non ha morso come doveva"
        )
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
            uscita, testo, compila = gira_completo(mutato, temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        atteso = 1 if rosso else 0
        # Il mutante deve far cambiare colore al caso E lasciare il gate VIVO: un
        # mutante che lo fa esplodere uscirebbe 1 e verrebbe contato come "il caso se
        # ne accorge", mentre non ha esercitato proprio niente.
        vivo = not morto(compila, testo)
        cambiato = uscita != atteso and vivo
        if not cambiato:
            errori += 1
        motivo = "" if vivo else "  (il mutante ha fatto ESPLODERE il gate: non prova niente)"
        print(
            f"  {'ok  ' if cambiato else 'ERRORE'} tolto {nome}: "
            f"'{caso}' -> exit {uscita} (senza mutante: {atteso}){motivo}"
        )
    return errori


# Uno step il cui NOME contiene la parola, per provare che il predicato non ci casca.
NOME_TRABOCCHETTO = (
    "on: {}\njobs:\n  a:\n    timeout-minutes: 5\n    steps:\n"
    "      - name: prova del SyntaxError\n        run: 'true'\n"
    "      - name: prova del SyntaxError\n        run: 'true'\n"
)


def prova_il_predicato(codice: str) -> int:
    """`morto()` riconosce un cadavere? Provato, non creduto.

    Questa sezione esiste perche' la stessa classe ha morso DUE volte il 01/09/2026:
    prima con un mutante che produceva codice non valido e veniva contato come
    bocciatura, poi — dentro la correzione di quel difetto — con `IndentationError`,
    che non contiene ne' `Traceback` ne' `SyntaxError` e passava di nuovo. Un predicato
    che decide se una prova ha esercitato qualcosa e' l'ultima cosa che si puo'
    lasciare senza prova propria: se sbaglia, tutto il resto di questo file stampa `ok`
    senza aver misurato niente.
    """
    print("\n== `morto()` riconosce un cadavere, e non scambia per cadavere un vivo")
    errori = 0
    morti = [
        ("sintassi non valida", codice.replace('if "uses" not in corpo and ', "if (")),
        # La riga di intestazione sparisce e il corpo resta rientrato: e' la forma che
        # ha attraversato indenne il primo rimedio.
        (
            "rientro non valido",
            re.sub(
                r'^\s*if "uses" not in corpo and "timeout-minutes" not in corpo:\n', "", codice, flags=re.M
            ),
        ),
        (
            "morte a runtime",
            "import glob\n" + codice.replace("problemi = []", "problemi = []\nraise RuntimeError('boom')", 1),
        ),
    ]
    for nome, mutante in morti:
        temp = albero()
        try:
            _, testo, compila = gira_completo(mutante, temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        if morto(compila, testo):
            print(f"  ok   {nome}: riconosciuto come cadavere")
        else:
            errori += 1
            print(f"  ERRORE {nome}: `morto()` lo conta come bocciatura", file=sys.stderr)

    # E il verso opposto, che e' meta' del valore: una BOCCIATURA corretta non deve
    # essere letta come esplosione solo perche' il messaggio del gate cita il nome di
    # un'eccezione. Il gate degli step duplicati stampa i nomi degli step.
    temp = albero()
    try:
        (temp / ".github/workflows/x.yml").write_text(NOME_TRABOCCHETTO)
        uscita, testo, compila = gira_completo(estrai(DUPLICATI), temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    if uscita == 1 and not morto(compila, testo):
        print("  ok   uno step chiamato 'prova del SyntaxError' resta una bocciatura, non un'esplosione")
    else:
        errori += 1
        print("  ERRORE un nome di step fa leggere come esplosione una bocciatura corretta", file=sys.stderr)
    return errori


def main() -> int:
    tetti = estrai(TETTI)
    duplicati = estrai(DUPLICATI)
    errori = esegui("tetti sui job", tetti, CASI)
    errori += prova_dichiarazione(tetti, "tetti sui job")
    errori += esegui("step duplicati", duplicati, CASI_DUPLICATI)
    errori += prova_dichiarazione(duplicati, "step duplicati")
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
    errori += prova_il_predicato(tetti)

    print(f"\n{'TUTTO A POSTO' if not errori else str(errori) + ' CASI FUORI POSTO'}")
    return 1 if errori else 0


if __name__ == "__main__":
    raise SystemExit(main())
