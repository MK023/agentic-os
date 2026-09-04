#!/usr/bin/env python3
# I check healthchecks.io come codice: la tabella CHECKS qui sotto E' la configurazione.
#
# PERCHE' ESISTE QUESTO FILE. `notifica.yml` dichiara due buchi che non puo' chiudere
# da solo, e li nomina: un cron che non parte AFFATTO (workflow_run scatta quando un
# run finisce, quindi uno schedule morto non produce nessun evento) e chi notifica il
# notificatore (i due modi in cui `notifica` muore — segreto sparito, webhook 403 —
# sono gli stessi in cui il suo secondo tentativo fallirebbe identico). Entrambi
# chiedono la stessa cosa: qualcosa FUORI da GitHub che si accorga del silenzio.
#
# PERCHE' NON BASTA `?create=1`. L'URL di ping sa creare un check al primo battito,
# ma la doc e' esplicita: dall'URL non si possono impostare periodo e grace. I check
# nati cosi' prendono i default del progetto, che per quattro di questi cinque sono
# sbagliati in un verso o nell'altro. La configurazione passa quindi dalla Management
# API, che ha l'upsert idempotente su `unique: ["slug"]`: rilanciare `--apply` non
# duplica niente e riallinea cio' che qualcuno avesse cambiato dalla dashboard.
#
# DUE CHIAVI, DUE POTERI, E QUI NE VIVE UNA SOLA. La *ping key* sa dire "sono vivo" e
# nient'altro: sta nei secret di GitHub perche' il watcher deve pingare. La *API key*
# puo' allungare un `grace` fino a rendere cieco l'allarme, e sta SOLO in Doppler:
# `--apply` lo lancia una persona dalla macchina, mai la CI. E' deliberato che questo
# repository non abbia nessuno step che chiami `--apply`: la CI sorvegliata da questi
# check non deve poterli riconfigurare, e uno step "inerte finche' il secret non
# esiste" e' un invito ad aggiungerlo.
#
# DA DOVE VENGONO timeout E grace. Non sono stime: `gap_h` e' il divario massimo
# osservato fra due run SCHEDULATE consecutive, letto dall'API di GitHub il
# 2026-09-04. `timeout + grace` deve superarlo con margine, altrimenti l'allarme suona
# su un ritardo normale di GitHub e si impara a ignorarlo — lo stesso difetto che
# questo repository ha gia' argomentato altrove scegliendo `repeat_interval: 4h`
# invece di 1h. Lo scheduling di GitHub e' best-effort dichiarato, e su questo
# repository lo si vede: `smoke` e' scritto `*/10` e ha una MEDIANA di 1,3 ore.
#
#   python3 scripts/healthchecks.py --self-check   # niente rete, niente segreti
#   doppler run -p agentic-os -c prd -- python3 scripts/healthchecks.py --apply
#   doppler run -p agentic-os -c prd -- python3 scripts/healthchecks.py --apply --dry-run

from __future__ import annotations

import glob
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parent.parent
WORKFLOWS = RADICE / ".github" / "workflows"
WATCHER = WORKFLOWS / "healthchecks.yml"
API = "https://healthchecks.io/api/v3/checks/"

ORA = 3600
GIORNO = 24 * ORA

# Margine minimo fra la finestra dichiarata e il ritardo gia' osservato. Quattro ore
# non sono un numero tondo scelto per bellezza: sotto quella soglia il primo ritardo
# leggermente peggiore di quello misurato produce un allarme falso, e un allarme falso
# su un dead man's switch e' peggio dell'assenza — insegna a non guardarlo.
MARGINE_MINIMO_H = 4

# `gap_h`: divario massimo fra due run schedulate consecutive, misurato il 2026-09-04.
# `campioni`: quante run schedulate sono entrate nella misura. Con due campioni il
# divario e' UN solo intervallo, non una distribuzione: e' il caso dei due settimanali,
# ed e' scritto qui perche' stringere una finestra su quella base sarebbe indovinare.
# Rimisurare PRIMA di stringere un grace, non dopo.
CHECKS = [
    {
        "slug": "smoke",
        "workflow": "smoke.yml",
        "timeout": 6 * ORA,
        "grace": 12 * ORA,
        "gap_h": 12.3,
        "campioni": 100,
        "desc": "La sonda pubblica dell'hub. Dichiarata */10, mediana misurata 1,3h.",
    },
    {
        "slug": "mutation",
        "workflow": "mutation.yml",
        "timeout": GIORNO,
        "grace": 18 * ORA,
        "gap_h": 33.9,
        "campioni": 38,
        "desc": "Il gate di mutation testing. Tre fallimenti notturni invisibili nell'agosto 2026.",
    },
    {
        "slug": "sorveglianza",
        "workflow": "sorveglianza.yml",
        "timeout": GIORNO,
        "grace": 18 * ORA,
        "gap_h": 34.7,
        "campioni": 14,
        "desc": "Access, la regola Sentry e il consumo Railway: i controlli che non vivono in git.",
    },
    {
        "slug": "codeql",
        "workflow": "codeql.yml",
        "timeout": 7 * GIORNO,
        "grace": GIORNO,
        "gap_h": 173.7,
        "campioni": 2,
        "desc": "Analisi statica settimanale.",
    },
    {
        "slug": "telemetry-baseline",
        "workflow": "telemetry-baseline.yml",
        "timeout": 7 * GIORNO,
        "grace": GIORNO,
        "gap_h": 174.5,
        "campioni": 2,
        "desc": "Baseline settimanale della telemetria.",
    },
]


def _leggi(percorso: Path) -> str | None:
    """Il watcher legge se stesso, quindi il suo nome e' un presupposto.

    Se qualcuno lo rinomina, meglio un errore che dice cosa e' successo di un
    FileNotFoundError non gestito a meta' di un gate.
    """
    try:
        return percorso.read_text(encoding="utf-8")
    except OSError:
        return None


def nome_workflow(file: str) -> str | None:
    testo = _leggi(WORKFLOWS / file)
    if testo is None:
        return None
    return (yaml.safe_load(testo) or {}).get("name")


def elenco_workflow_run() -> list[str]:
    """I workflow che `healthchecks.yml` dichiara di sorvegliare.

    `on:` in YAML si legge come il booleano True, non come la stringa "on" — stessa
    trappola gia' documentata nei gate di lint.yml.
    """
    testo = _leggi(WATCHER)
    if testo is None:
        return []
    documento = yaml.safe_load(testo) or {}
    trigger = documento.get(True, documento.get("on")) or {}
    return list((trigger.get("workflow_run") or {}).get("workflows") or [])


def forma_watcher() -> list[str]:
    """Il tripwire sulla soppressione zizmor.

    `workflow_run` e' HIGH per categoria: gira sul ramo di default CON i segreti,
    svegliato da workflow che possono essere partiti da una PR di un fork. L'exploit
    noto e' in tre passi — la PR del fork produce un artefatto, il workflow
    privilegiato lo scarica, lo esegue con i segreti in mano.

    La soppressione in healthchecks.yml e' legittima perche' nessuno dei tre passi
    esiste li' dentro. Ma copre QUESTA FORMA, non il trigger: se un domani quel job
    acquisisse un checkout, un artefatto o dei permessi, la riga continuerebbe a
    tacere su un rischio diventato reale. Quindi la giustificazione si verifica invece
    di leggersi.
    """
    testo = _leggi(WATCHER)
    if testo is None:
        return [f"{WATCHER.name} non trovato: il watcher e' stato rinominato o rimosso"]

    documento = yaml.safe_load(testo) or {}
    errori: list[str] = []

    if documento.get("permissions") != {}:
        errori.append(f"{WATCHER.name}: manca `permissions: {{}}` a livello di workflow")

    jobs = documento.get("jobs") or {}
    # Un job solo, e si chiama battito. Non e' rigidita' fine a se stessa: ogni job in
    # piu' qui dentro gira sul ramo di default con i segreti del repository.
    if set(jobs) != {"battito"}:
        errori.append(f"{WATCHER.name}: i job non sono piu' esattamente {{'battito'}} ma {sorted(jobs)}")
        return errori

    battito = jobs["battito"] or {}

    if battito.get("permissions") != {}:
        errori.append("battito: non ha piu' `permissions: {}`")

    if "timeout-minutes" not in battito:
        errori.append("battito: non dichiara `timeout-minutes`")

    # Il filtro sullo schedule e' IL controllo che tiene in piedi il disegno.
    # `workflow_run` scatta per ogni trigger, non solo per lo schedule: senza questo
    # filtro un `workflow_dispatch` manuale manderebbe un battito e il monitor direbbe
    # "vivo" per un cron che non parte piu' da solo. E' il modo di fallire piu'
    # insidioso, perche' lascia il verde.
    condizione = str(battito.get("if") or "")
    if "workflow_run.event == 'schedule'" not in condizione:
        errori.append("battito: manca il filtro `workflow_run.event == 'schedule'` nella condizione del job")

    for passo in battito.get("steps") or []:
        passo = passo or {}
        if "uses" in passo:
            errori.append(f"battito: usa `{passo['uses']}`: qui dentro non gira codice di nessun altro")

    grezzo = testo
    if "download-artifact" in grezzo or "gh run download" in grezzo:
        errori.append("battito: scaricare artefatti e' il vettore dell'exploit workflow_run")

    return errori


def workflow_schedulati() -> list[str]:
    trovati = []
    for percorso in sorted(glob.glob(str(WORKFLOWS / "*.yml")) + glob.glob(str(WORKFLOWS / "*.yaml"))):
        documento = yaml.safe_load(Path(percorso).read_text(encoding="utf-8")) or {}
        trigger = documento.get(True, documento.get("on")) or {}
        if isinstance(trigger, dict) and "schedule" in trigger:
            trovati.append(Path(percorso).name)
    return trovati


def verifica() -> int:
    errori: list[str] = []
    visti: set[str] = set()

    for c in CHECKS:
        slug = c["slug"]
        if not all(ch.isalnum() and ch.islower() or ch in "-_" for ch in slug):
            errori.append(f"{slug}: slug non valido (ammessi a-z 0-9 - _)")
        if slug in visti:
            errori.append(f"{slug}: slug duplicato")
        visti.add(slug)

        # Il vincolo che conta: la finestra deve coprire il ritardo gia' osservato.
        finestra = (c["timeout"] + c["grace"]) / ORA
        margine = finestra - c["gap_h"]
        if margine <= 0:
            errori.append(f"{slug}: finestra {finestra:g}h non supera il divario misurato {c['gap_h']}h")
        elif margine < MARGINE_MINIMO_H:
            errori.append(f"{slug}: margine {margine:.1f}h troppo stretto (minimo {MARGINE_MINIMO_H}h)")

        # Limiti dichiarati dalla Management API.
        for campo in ("timeout", "grace"):
            if not 60 <= c[campo] <= 31536000:
                errori.append(f"{slug}: {campo} {c[campo]}s fuori dai limiti 60..31536000")

    # Un cron nuovo senza check e' il buco che questo script esiste per impedire.
    coperti = {c["workflow"] for c in CHECKS}
    for wf in workflow_schedulati():
        if wf not in coperti:
            errori.append(f"{wf}: ha uno schedule ma nessun check nella tabella")

    esistenti = {
        Path(p).name for p in glob.glob(str(WORKFLOWS / "*.yml")) + glob.glob(str(WORKFLOWS / "*.yaml"))
    }
    sorvegliati = elenco_workflow_run()

    for c in CHECKS:
        wf = c["workflow"]
        if wf not in esistenti:
            errori.append(f"{c['slug']}: il workflow {wf} non esiste")
            continue

        # La convenzione su cui si regge il ping: il watcher ricava lo slug da
        # `basename(path .yml)`. Se i due divergono il ping va su un check inesistente
        # e healthchecks risponde 404 senza che nessuno guardi.
        atteso = wf.removesuffix(".yaml").removesuffix(".yml")
        if c["slug"] != atteso:
            errori.append(f"{c['slug']}: lo slug deve essere il nome del file ({wf})")

        # IL LEGAME PIU' FRAGILE. `workflow_run` filtra per NOME del workflow, non per
        # file: rinominare un `name:` scollega il watcher e il cron smette di battere
        # in silenzio, lasciando il monitor verde. Qui i due lati vengono confrontati.
        nome = nome_workflow(wf)
        if not nome:
            errori.append(f"{wf}: manca la riga `name:`")
        elif nome not in sorvegliati:
            errori.append(f'{wf}: nome "{nome}" assente dall\'elenco workflow_run di {WATCHER.name}')

    # Un nome sorvegliato che non corrisponde a nessun check e' una riga che non fa
    # niente e sembra copertura.
    nomi_attesi = {nome_workflow(c["workflow"]) for c in CHECKS} - {None}
    for nome in sorvegliati:
        if nome not in nomi_attesi:
            errori.append(f'{WATCHER.name} sorveglia "{nome}", che non ha nessun check nella tabella')

    errori.extend(forma_watcher())

    for e in errori:
        print(f"::error::{e}")
    if errori:
        print(f"\ncheck: {len(errori)} problemi")
        return 1
    print(f"check: ok ({len(CHECKS)} check, {len(workflow_schedulati())} workflow schedulati)")
    return 0


def _chiama(percorso: str, chiave: str, corpo: dict | None = None) -> tuple[int, object]:
    """Una sola porta verso healthchecks.io, cosi' l'URL non si compone altrove.

    L'URL nasce da una costante con schema https gia' dentro: non c'e' nessun punto in
    cui uno schema arrivi da fuori.
    """
    dati = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    richiesta = urllib.request.Request(  # noqa: S310 - URL costante, schema https letterale in API
        API + percorso,
        data=dati,
        headers={"X-Api-Key": chiave, "Content-Type": "application/json"},
        method="POST" if corpo is not None else "GET",
    )
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:  # noqa: S310
            return risposta.status, json.load(risposta)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def applica(dry_run: bool) -> int:
    chiave = os.environ.get("HEALTHCHECKS_API_KEY")
    if not chiave:
        print(
            "::error::HEALTHCHECKS_API_KEY assente: sta in Doppler, usa `doppler run -p agentic-os -c prd --`"
        )
        return 1

    # I check nascerebbero senza destinatario se non ci fosse nessuna integrazione, e
    # un allarme che non raggiunge nessuno e' indistinguibile da nessun allarme.
    codice, canali = _chiama("../channels/", chiave)
    if codice != 200 or not isinstance(canali, dict):
        print(f"::error::lettura dei canali fallita (HTTP {codice}): {canali}")
        return 1
    elenco = canali.get("channels") or []
    if not elenco:
        print("::error::nessuna integrazione su healthchecks.io: i check nascerebbero senza destinatario")
        return 1
    print(f"canali: {', '.join(repr(c.get('name')) for c in elenco)}")

    problemi = 0
    for c in CHECKS:
        corpo = {
            "name": f"agentic-os / {c['slug']}",
            "slug": c["slug"],
            "tags": "agentic-os cron",
            "desc": (
                f"{c['desc']} Finestra {(c['timeout'] + c['grace']) / ORA:g}h contro un divario "
                f"misurato di {c['gap_h']}h ({c['campioni']} campioni)."
            ),
            "timeout": c["timeout"],
            "grace": c["grace"],
            # Tutte le integrazioni del progetto: la ridondanza fra email e Slack e' il
            # punto: se il canale Slack e' il modo in cui il notificatore muore, un
            # allarme che passa solo di li' muore con lui.
            "channels": "*",
            "unique": ["slug"],
        }
        if dry_run:
            print(f"[dry-run] {c['slug']}: timeout={c['timeout']}s grace={c['grace']}s")
            continue
        codice, risposta = _chiama("", chiave, corpo)
        if codice == 201:
            print(f"creato    {c['slug']}")
        elif codice == 200:
            print(f"aggiornato {c['slug']}")
        else:
            print(f"::error::{c['slug']}: HTTP {codice} — {risposta}")
            problemi += 1
    return 1 if problemi else 0


def main() -> int:
    argomenti = sys.argv[1:]
    if "--self-check" in argomenti:
        return verifica()
    if "--apply" in argomenti:
        return applica("--dry-run" in argomenti)
    print(__doc__ or "uso: healthchecks.py --self-check | --apply [--dry-run]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
