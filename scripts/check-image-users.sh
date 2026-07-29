#!/bin/bash
# Ogni immagine di questo repo dichiara un utente non-root.
#
# Esiste perché il 29-07-2026 abbiamo dovuto concedere UNA deroga: il servizio
# prometheus gira come root su Railway (RAILWAY_RUN_UID=0), perché il volume
# montato è di proprietà di root e il processo come `nobody` non riesce nemmeno
# a creare /prometheus/queries.active. È documentata in docs/DECISIONS.md.
#
# Una deroga scritta resta una deroga. Una deroga copiata diventa la norma: questo
# script serve a impedire il secondo caso — un Dockerfile nuovo senza USER, o
# qualcuno che "risolve" un permesso mettendo USER 0 invece di capire perché.
set -euo pipefail

fallimenti=0

while IFS= read -r dockerfile; do
  utente=$(grep -E '^USER ' "$dockerfile" | tail -1 | awk '{print $2}' || true)

  if [ -z "$utente" ]; then
    echo "FAIL: ${dockerfile} non dichiara nessun USER" >&2
    fallimenti=1
    continue
  fi

  case "$utente" in
    0|0:*|root|root:*)
      echo "FAIL: ${dockerfile} gira come root (USER ${utente})" >&2
      fallimenti=1
      ;;
    *)
      echo "ok: ${dockerfile} -> USER ${utente}"
      ;;
  esac
done < <(find railway services -name Dockerfile -type f | sort)

if [ "$fallimenti" -ne 0 ]; then
  echo "" >&2
  echo "Se una deroga serve davvero, non va messa qui: si applica al singolo" >&2
  echo "servizio sulla piattaforma e si scrive in docs/DECISIONS.md col suo costo." >&2
  exit 1
fi

echo "tutte le immagini dichiarano un utente non-root"
