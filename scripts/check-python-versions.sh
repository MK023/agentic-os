#!/bin/bash
# La CI deve girare sullo stesso Python della produzione.
#
# Esiste perché il 13-08-2026 Dependabot ha proposto il salto del runtime del
# status-api da 3.12 a 3.14 (PR #46) e la CI era verde: i test giravano su 3.12
# pinnato a mano in quattro punti dei workflow, mentre l'immagine ne dichiarava
# un altro. Verde significava soltanto che le wheel hash-locked si installavano
# su 3.14 — nessun test ha mai eseguito su quell'interprete.
#
# Il numero resta scritto in cinque posti perché setup-python non legge un
# Dockerfile e il Dockerfile non legge un file di versione: la duplicazione non
# si elimina, si sorveglia. Stessa scelta di scripts/check-image-users.sh.
set -euo pipefail

dockerfile="services/public-status-api/Dockerfile"

# `FROM python:3.14.0-slim` -> `3.14`. L'immagine pinna la patch, la CI il minor:
# confrontiamo il minor, l'unico livello che entrambi dichiarano.
riga_from=$(grep -m1 -E '^FROM python:' "$dockerfile" || true)
if [ -z "$riga_from" ]; then
  echo "FAIL: ${dockerfile} non dichiara nessun 'FROM python:'" >&2
  exit 1
fi
atteso=$(sed -E 's/^FROM python:([0-9]+\.[0-9]+).*/\1/' <<<"$riga_from")

if ! grep -qE '^[0-9]+\.[0-9]+$' <<<"$atteso"; then
  echo "FAIL: versione non riconosciuta in ${dockerfile}: ${riga_from}" >&2
  exit 1
fi

fallimenti=0
trovati=0

while IFS= read -r riga; do
  trovati=$((trovati + 1))
  file=${riga%%:*}
  resto=${riga#*:}
  numero_riga=${resto%%:*}
  # `          python-version: "3.14"` -> `3.14`
  versione=$(sed -E 's/.*python-version:[[:space:]]*"?([^"[:space:]]+)"?.*/\1/' <<<"$riga")

  if [ "$versione" = "$atteso" ]; then
    echo "ok: ${file}:${numero_riga} -> ${versione}"
  else
    echo "FAIL: ${file}:${numero_riga} usa Python ${versione}, l'immagine ${atteso}" >&2
    fallimenti=1
  fi
done < <(grep -rn 'python-version:' .github/workflows/ | sort)

# Zero occorrenze non è un successo: vorrebbe dire che i workflow hanno smesso
# di pinnare la versione e girano su quella di default del runner, che cambia
# sotto di noi senza che nessun diff lo mostri.
if [ "$trovati" -eq 0 ]; then
  echo "FAIL: nessun 'python-version:' nei workflow — la CI gira sul default del runner" >&2
  exit 1
fi

if [ "$fallimenti" -ne 0 ]; then
  echo "" >&2
  echo "La CI proverebbe un interprete che la produzione non ha. Allinea i" >&2
  echo "workflow al Dockerfile (o viceversa) nello stesso commit." >&2
  exit 1
fi

echo "tutti i ${trovati} pin dei workflow sono su Python ${atteso}, come l'immagine"
