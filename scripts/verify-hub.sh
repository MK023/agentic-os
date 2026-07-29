#!/bin/bash
# Smoke test dopo un deploy. È il vero controllo che l'hub funziona: Railway ignora
# l'HEALTHCHECK del Dockerfile, e un deploy "riuscito" dice solo che il container è
# partito, non che sta servendo.
#
# Entrambi gli hostname stanno dietro Cloudflare Access, quindi:
#  - la status API si interroga con il token di servizio Access (CF_ACCESS_CLIENT_ID /
#    CF_ACCESS_CLIENT_SECRET) PIÙ il suo bearer token: due strati, ed è esattamente
#    come la chiama il Worker del sito;
#  - Grafana ha una policy a email, quindi da riga di comando non si entra: lì la
#    verifica onesta è che Access risponda, il che prova tunnel e policy insieme.
#    Un 200 senza autenticazione sarebbe il vero fallimento, e infatti lo trattiamo
#    come tale.
set -uo pipefail

GRAFANA_URL="${1:?Uso: verify-hub.sh <grafana-url> <status-url> <status-token>}"
STATUS_URL="${2:?}"
STATUS_TOKEN="${3:?}"

fallimenti=0

intestazioni_access=()
if [ -n "${CF_ACCESS_CLIENT_ID:-}" ] && [ -n "${CF_ACCESS_CLIENT_SECRET:-}" ]; then
  intestazioni_access=(-H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}")
else
  echo "nota: CF_ACCESS_CLIENT_ID/SECRET non impostati — la status API risponderà con la sfida di Access" >&2
fi

# --- Grafana: raggiungibile E protetta -------------------------------------
# Niente "|| echo 000": curl scrive gia' 000 in %{http_code} quando non si connette,
# e il fallback finiva per concatenare due codici ("000000").
codice=$(curl -s -o /dev/null -w '%{http_code}' "${GRAFANA_URL}/api/health")
codice=${codice:-000}
case "$codice" in
  200)
    echo "ATTENZIONE: Grafana risponde 200 senza autenticazione — Access non protegge ${GRAFANA_URL}" >&2
    fallimenti=1
    ;;
  302|303|401|403)
    echo "ok: Grafana raggiungibile e protetta da Access (HTTP ${codice})"
    ;;
  000)
    echo "FAIL: Grafana irraggiungibile — tunnel giu' o rotta assente" >&2
    fallimenti=1
    ;;
  *)
    echo "FAIL: Grafana ha risposto HTTP ${codice}" >&2
    fallimenti=1
    ;;
esac

# --- status API: i tre numeri ----------------------------------------------
# ${arr[@]+"${arr[@]}"}: espandere un array VUOTO sotto `set -u` e' un errore sul
# bash 3.2 che macOS spedisce ancora. Questa forma lo rende un no-op.
risposta=$(curl -s -w '\n%{http_code}' ${intestazioni_access[@]+"${intestazioni_access[@]}"} \
  -H "Authorization: Bearer ${STATUS_TOKEN}" "${STATUS_URL}/status")
codice=$(echo "$risposta" | tail -1)
corpo=$(echo "$risposta" | sed '$d')

if [ "$codice" = "200" ] && echo "$corpo" | grep -q 'sessions_today'; then
  echo "ok: status API risponde con i campi attesi — ${corpo}"
elif [ "$codice" = "502" ]; then
  echo "FAIL: status API viva ma Prometheus non risponde (502 controllato)" >&2
  fallimenti=1
else
  echo "FAIL: status API ha risposto HTTP ${codice}" >&2
  if [ -z "${CF_ACCESS_CLIENT_ID:-}" ]; then
    echo "      (probabile: mancano le credenziali del service token Access)" >&2
  fi
  fallimenti=1
fi

if [ "$fallimenti" -ne 0 ]; then
  exit 1
fi
echo "OK: l'hub e' raggiungibile e sano"
