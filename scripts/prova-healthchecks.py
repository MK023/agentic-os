#!/usr/bin/env python3
# Il banco del self-check di scripts/healthchecks.py.
#
# PERCHE' ESISTE. Il self-check e' l'unica cosa che tiene onesta la soppressione
# zizmor in healthchecks.yml e l'unica che si accorge di un cron nuovo senza check.
# Un gate che non e' mai stato visto diventare rosso non e' un gate: e' una riga che
# passa. Qui ogni controllo viene esercitato su una realta' rotta apposta, in una
# copia usa-e-getta dei workflow, e si pretende il rosso.
#
# E NON BASTA IL ROSSO. Due casi — il margine della finestra e il filtro sullo
# schedule — vengono provati anche al contrario: si spegne il controllo e si pretende
# che il caso torni VERDE. Un caso che resta rosso anche senza il controllo che
# dovrebbe esercitarlo sta misurando qualcos'altro, e non se ne accorgerebbe nessuno.
#
# Niente rete, niente segreti: gira su ogni PR dentro lint.yml.
#
#   python3 scripts/prova-healthchecks.py

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import shutil
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent


def carica_modulo():
    percorso = RADICE / "scripts" / "healthchecks.py"
    spec = importlib.util.spec_from_file_location("healthchecks_sotto_esame", percorso)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def esegui(modulo, workflows: Path, checks) -> tuple[int, str]:
    """Lancia verifica() contro una copia dei workflow, catturando cio' che dice."""
    modulo.WORKFLOWS = workflows
    modulo.WATCHER = workflows / "healthchecks.yml"
    modulo.CHECKS = checks
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        esito = modulo.verifica()
    return esito, buffer.getvalue()


def copia_workflow(base: Path) -> Path:
    destinazione = base / "workflows"
    shutil.copytree(RADICE / ".github" / "workflows", destinazione)
    return destinazione


ESITI: list[tuple[str, bool]] = []


def pretendi(condizione: bool, messaggio: str) -> None:
    """La mutazione DEVE applicarsi.

    Se il testo da rompere non c'e' piu' — un refactor del watcher, una riga
    riformattata — il caso girerebbe su un file intatto e passerebbe verde senza aver
    provato niente. Meglio un'esplosione che nomina il punto.
    """
    if not condizione:
        raise RuntimeError(messaggio)


def caso(nome: str, condizione: bool) -> None:
    ESITI.append((nome, condizione))
    print(f"  {'ok  ' if condizione else 'ROTTO'} {nome}")


def main() -> int:
    modulo = carica_modulo()
    originali = copy.deepcopy(modulo.CHECKS)

    print("banco del self-check di healthchecks.py\n")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        wf = copia_workflow(base)

        # --- il pavimento: la realta' vera deve passare -------------------------
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso("la configurazione reale passa il self-check", esito == 0)
        if esito != 0:
            print(uscita)

        # --- un cron nuovo senza check ------------------------------------------
        (wf / "nuovo-cron.yml").write_text(
            'name: nuovo-cron\non:\n  schedule:\n    - cron: "0 1 * * *"\njobs:\n'
            "  fai:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n"
            "      - run: 'true'\n",
            encoding="utf-8",
        )
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "un workflow schedulato senza check e' una bocciatura",
            esito == 1 and "nuovo-cron.yml: ha uno schedule ma nessun check" in uscita,
        )
        (wf / "nuovo-cron.yml").unlink()

        # --- finestra piu' stretta del ritardo gia' misurato ---------------------
        stretti = copy.deepcopy(originali)
        stretti[0]["grace"] = 60
        stretti[0]["timeout"] = 60
        esito, uscita = esegui(modulo, wf, stretti)
        caso(
            "una finestra sotto il divario misurato e' una bocciatura",
            esito == 1 and "non supera il divario misurato" in uscita,
        )

        # --- margine presente ma troppo sottile ----------------------------------
        sottili = copy.deepcopy(originali)
        # Divario 12,3h: una finestra di 13h passa il primo controllo e deve cadere
        # sul margine minimo, altrimenti il primo ritardo appena peggiore suona falso.
        sottili[0]["timeout"] = 12 * modulo.ORA
        sottili[0]["grace"] = int(1.0 * modulo.ORA)
        esito, uscita = esegui(modulo, wf, sottili)
        caso(
            "un margine sotto le 4h e' una bocciatura",
            esito == 1 and "troppo stretto" in uscita,
        )

        # --- lo slug non e' piu' il nome del file --------------------------------
        rinominati = copy.deepcopy(originali)
        rinominati[0]["slug"] = "fumo"
        esito, uscita = esegui(modulo, wf, rinominati)
        caso(
            "uno slug diverso dal nome del file e' una bocciatura",
            esito == 1 and "lo slug deve essere il nome del file" in uscita,
        )

        # --- il name: di un workflow sorvegliato cambia --------------------------
        # E' il legame piu' fragile del disegno: workflow_run filtra per NOME, non per
        # file. Rinominare il `name:` scollega il watcher in silenzio.
        testo = (wf / "smoke.yml").read_text(encoding="utf-8")
        (wf / "smoke.yml").write_text(testo.replace("name: smoke", "name: fumo", 1), encoding="utf-8")
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "un `name:` rinominato scollega il watcher ed e' una bocciatura",
            esito == 1 and "assente dall'elenco workflow_run" in uscita,
        )
        (wf / "smoke.yml").write_text(testo, encoding="utf-8")

        # --- il watcher perde il filtro sullo schedule ---------------------------
        watcher = (wf / "healthchecks.yml").read_text(encoding="utf-8")
        rotto = watcher.replace(
            "if: github.event.workflow_run.event == 'schedule'",
            "if: always()",
            1,
        )
        pretendi(rotto != watcher, "il banco non ha trovato la riga da rompere")
        (wf / "healthchecks.yml").write_text(rotto, encoding="utf-8")
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "il watcher senza filtro sullo schedule e' una bocciatura",
            esito == 1 and "manca il filtro" in uscita,
        )

        # --- MUTAZIONE: spento il controllo, quel caso deve tornare verde ---------
        # Se restasse rosso, il caso qui sopra starebbe misurando qualcos'altro.
        sorgente = modulo.forma_watcher
        modulo.forma_watcher = lambda: []
        esito, _ = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "spento forma_watcher(), il caso del filtro torna verde (il banco misura QUEL controllo)",
            esito == 0,
        )
        modulo.forma_watcher = sorgente
        (wf / "healthchecks.yml").write_text(watcher, encoding="utf-8")

        # --- il watcher acquista un `uses:` --------------------------------------
        # E' meta' dell'exploit che la soppressione zizmor dichiara impossibile.
        con_uses = watcher.replace(
            "      - name: Ping",
            "      - uses: actions/checkout@v5\n      - name: Ping",
            1,
        )
        pretendi(con_uses != watcher, "il banco non ha trovato il punto in cui iniettare lo step")
        (wf / "healthchecks.yml").write_text(con_uses, encoding="utf-8")
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "uno step `uses:` nel watcher e' una bocciatura",
            esito == 1 and "qui dentro non gira codice di nessun altro" in uscita,
        )
        (wf / "healthchecks.yml").write_text(watcher, encoding="utf-8")

        # --- il watcher perde `permissions: {}` sul job --------------------------
        senza_perm = watcher.replace(
            "    timeout-minutes: 5\n    permissions: {}\n", "    timeout-minutes: 5\n", 1
        )
        pretendi(senza_perm != watcher, "il banco non ha trovato i permessi del job")
        (wf / "healthchecks.yml").write_text(senza_perm, encoding="utf-8")
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "il watcher senza `permissions: {}` sul job e' una bocciatura",
            esito == 1 and "non ha piu' `permissions: {}`" in uscita,
        )
        (wf / "healthchecks.yml").write_text(watcher, encoding="utf-8")

        # --- il watcher sorveglia un nome senza check in tabella -----------------
        fantasma = watcher.replace("      - smoke\n", "      - smoke\n      - fantasma\n", 1)
        pretendi(fantasma != watcher, "il banco non ha trovato l'elenco workflow_run")
        (wf / "healthchecks.yml").write_text(fantasma, encoding="utf-8")
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "un nome sorvegliato senza check in tabella e' una bocciatura",
            esito == 1 and "che non ha nessun check nella tabella" in uscita,
        )
        (wf / "healthchecks.yml").write_text(watcher, encoding="utf-8")

        # --- il watcher sparisce del tutto ---------------------------------------
        (wf / "healthchecks.yml").unlink()
        esito, uscita = esegui(modulo, wf, copy.deepcopy(originali))
        caso(
            "il watcher rimosso e' una bocciatura, non un silenzio",
            esito == 1 and "e' stato rinominato o rimosso" in uscita,
        )

    rotti = [nome for nome, ok in ESITI if not ok]
    print()
    if rotti:
        for nome in rotti:
            print(f"::error::il banco non tiene: {nome}")
        print(f"\n{len(rotti)} casi su {len(ESITI)} non tengono")
        return 1
    print("TUTTO A POSTO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
