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
# Il token si puo' passare come terzo argomento (uso a mano) oppure in
# STATUS_API_TOKEN (uso in CI). In CI la seconda forma e' quella giusta: un
# segreto in argv finisce nella riga di comando del processo, ed e' visibile a
# chiunque legga `ps` sullo stesso host.
STATUS_TOKEN="${3:-${STATUS_API_TOKEN:-}}"
if [ -z "$STATUS_TOKEN" ]; then
  echo "Uso: verify-hub.sh <grafana-url> <status-url> <status-token>" >&2
  echo "     oppure STATUS_API_TOKEN nell'ambiente, che e' la forma per la CI." >&2
  exit 2
fi

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
codice=$(curl --connect-timeout 5 --max-time 30 -s -o /dev/null -w '%{http_code}' "${GRAFANA_URL}/api/health")
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
risposta=$(curl --connect-timeout 5 --max-time 30 -s -w '\n%{http_code}' ${intestazioni_access[@]+"${intestazioni_access[@]}"} \
  -H "Authorization: Bearer ${STATUS_TOKEN}" "${STATUS_URL}/status")
codice=$(echo "$risposta" | tail -1)
corpo=$(echo "$risposta" | sed '$d')

# La FORMA dei tre campi, non la presenza di una sottostringa. Fino al 21/08/2026
# qui c'era `grep -q 'sessions_today'`: passava un corpo con `null` al posto del
# numero, passava se due campi su tre sparivano in un refactor, e passava anche un
# messaggio d'errore che per caso nominasse il campo. E' la stessa famiglia di
# guasto che smoke.yml prende sul serio dall'altro lato del Worker.
# Si asserisce la forma e NON "diverso da zero": il cron e' alle 05:23 UTC e tre
# zeri a quell'ora sono spesso la risposta giusta — un gate che diventa rosso su un
# hub sano e' il modo piu' rapido di insegnare a ignorarlo.
# NIENTE apostrofi dentro il blocco qui sotto: e' racchiuso in virgolette singole.
problema=$(printf '%s' "$corpo" | python3 -c '
import json, sys

try:
    d = json.load(sys.stdin)
except Exception as e:  # noqa: BLE001 — qualunque corpo non-JSON e un guasto
    print("il corpo non e JSON: " + str(e))
    sys.exit(0)
attesi = ("sessions_today", "tokens_today", "cost_usd_today")
# bool e sottoclasse di int: un true passerebbe per numero.
rotti = [k for k in attesi if not isinstance(d.get(k), (int, float)) or isinstance(d.get(k), bool)]
if rotti:
    print("campi assenti o non numerici: " + ", ".join(rotti))
' 2>&1)

if [ "$codice" = "200" ] && [ -z "$problema" ]; then
  echo "ok: status API risponde con i tre numeri — ${corpo}"
elif [ "$codice" = "200" ]; then
  echo "FAIL: status API risponde 200 ma il corpo non regge — ${problema}" >&2
  echo "      corpo: ${corpo}" >&2
  fallimenti=1
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
