#!/bin/sh
# Guardia sulla password di amministratore, poi via all'entrypoint vero di Grafana.
#
# Perche' esiste. `GF_SECURITY_ADMIN_PASSWORD` e' l'unico pezzo dello strato interno
# di autenticazione, e vive soltanto in un campo del dashboard Railway: non in git,
# non in railway.json, asserito da niente. Se manca o viene rinominata, Grafana parte
# con l'utente `admin` e la password documentata come default (`admin`) — e nulla nel
# repo, nella CI o in scripts/verify-hub.sh puo' accorgersene, perche' quegli
# strumenti vedono solo lo strato ESTERNO (Cloudflare Access), che risponde 401
# identico sia che dietro ci sia una password forte sia che non ci sia.
#
# E il servizio non ha volume, di proposito: ogni deploy ricrea grafana.db, quindi
# una variabile mancante non e' un errore che capita una volta — e' un default che
# si riapplica a ogni ridispiegamento, per sempre, in silenzio.
#
# Stessa scelta gia' presa per STATUS_API_TOKEN vuoto: meglio un servizio che non
# parte, visibile con restartPolicyType ON_FAILURE, di un controllo che qualcuno
# CREDE attivo. Il guasto rumoroso e' quello che si nota.
set -eu

if [ -z "${GF_SECURITY_ADMIN_PASSWORD:-}" ]; then
	echo "FATAL: GF_SECURITY_ADMIN_PASSWORD non e' impostata." >&2
	echo "Grafana partirebbe con la password di default 'admin', e lo strato" >&2
	echo "esterno (Cloudflare Access) risponde 401 identico in entrambi i casi:" >&2
	echo "nessun controllo esistente potrebbe accorgersene." >&2
	echo "Impostala come variabile del servizio Railway, poi ridispiega." >&2
	exit 1
fi

exec /run.sh "$@"
