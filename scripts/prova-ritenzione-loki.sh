#!/usr/bin/env bash
# Prova ESEGUITA che `deletion_mode: disabled` faccia le due cose che ci si aspetta,
# e nessuna delle due si legge dalla config:
#   (a) l'API di cancellazione risponde 403 invece di accettare;
#   (b) la ritenzione automatica continua a girare, cioe' il compattatore applica
#       ancora la ritenzione dopo il cambiamento.
#
# Perche' (b) va provato e non letto: la doc del fornitore dice che l'API di
# cancellazione e' governata da `deletion_mode` e la ritenzione da
# `compactor.retention_enabled`, ma dice anche che le rotte di cancellazione
# ESISTONO solo se `retention_enabled` e' true — le due cose si toccano. Se
# `deletion_mode: disabled` spegnesse anche la ritenzione, i 720h diventerebbero
# "per sempre" e il conto R2 crescerebbe in silenzio, con Loki up=1 e tutti i gate
# verdi. E' la forma esatta del guasto che questo progetto esiste per non avere.
#
# IL LIMITE, e va letto PRIMA del risultato. Questa prova NON osserva un chunk
# scaduto sparire dallo storage. Misurato il 2026-08-21 su container pulito: i
# chunk arrivano nel bucket in ~10s, ma il tsdb shipper carica l'INDICE su un
# intervallo lungo e il compattatore non ha niente da compattare finche' non c'e'
# — dopo cinque minuti il bucket conteneva i chunk e nessun prefisso `index/`, e
# il compattatore ripeteva "no marks file found". Uno sweep vero chiede ~15 minuti:
# sarebbe un gate lento e a tempo, cioe' un futuro `continue-on-error`. Cio' che si
# misura qui e' che il ciclo di ritenzione GIRA e RIESCE dopo il cambiamento, che e'
# la proprieta' che `deletion_mode` potrebbe rompere. Che poi cancelli davvero e'
# provato dal fornitore, non da noi.
set -euo pipefail
cd "$(dirname "$0")/.."

RETE=prova-ritenzione-loki
# Stessi digest della produzione e della prova di privacy: una prova che gira su
# un'immagine diversa da quella spedita misura un'altra cosa.
IMG_LOKI=grafana/loki:3.7.6@sha256:efd47c67f9bac88ca29bcf8cb997d9ab29d1848bd0aff579282295542a745952
IMG_MINIO=minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e

pulisci() {
  docker rm -f ritenzione-loki ritenzione-minio >/dev/null 2>&1 || true
  docker network rm "$RETE" >/dev/null 2>&1 || true
  if [ -n "${TMP:-}" ]; then rm -rf "$TMP"; fi
}
trap pulisci EXIT
pulisci
TMP=$(mktemp -d)

# --- la config sotto esame, con l'oracolo QUI e non nel file sotto esame
python3 - "$TMP/loki.yaml" <<'PY'
import sys, yaml
c = yaml.safe_load(open("docker/loki.yaml"))

# L'ORACOLO. Scritto qui, a mano, perche' un confronto fra due letture dello stesso
# file non e' un oracolo — errore gia' pagato in scripts/prova-privacy-log.sh e
# dichiarato come garanzia in tre documenti prima di essere misurato.
atteso = {
    ("limits_config", "deletion_mode"): "disabled",
    ("compactor", "retention_enabled"): True,
    ("compactor", "delete_request_store"): "s3",
    ("limits_config", "retention_period"): "720h",
}
for (sezione, chiave), valore in atteso.items():
    reale = c.get(sezione, {}).get(chiave)
    if reale != valore:
        sys.exit(f"FALLITO: docker/loki.yaml ha {sezione}.{chiave} = {reale!r}, atteso {valore!r}")

# LA CHIAVE CHE QUESTA PROVA SOVRASCRIVE VA PINNATA PIU' DELLE ALTRE, non meno.
# `apply_retention_interval` vale 0 per default, e il binario lo documenta come "0
# means run at same interval as compaction": quindi in produzione la cadenza della
# RITENZIONE e' `compaction_interval`, cioe' esattamente cio' che qui sotto viene
# accorciato a 10s. Senza questa riga, un `compaction_interval: 24h` aggiunto al
# file SPEDITO lascerebbe questa prova verde su tutte e tre le asserzioni mentre in
# produzione la ritenzione girerebbe una volta al giorno — dimostrato eseguendo, ed
# e' la forma di guasto che l'intestazione di questo script dice di prevenire.
if "compaction_interval" in (c.get("compactor") or {}):
    sys.exit("FALLITO: docker/loki.yaml dichiara compactor.compaction_interval. "
             "Questa prova lo sovrascrive per non durare venti minuti, quindi non "
             "potrebbe piu' vedere una regressione su quella chiave — che, con "
             "apply_retention_interval a 0, E' la cadenza della ritenzione. "
             "Se il valore serve davvero, va deciso qui dentro e non solo la'.")

# LE DUE DIFFERENZE DI VALORE rispetto al file spedito:
#  - `insecure`: qui lo storage e' MinIO in HTTP, non R2 in HTTPS.
#  - `compaction_interval`: il default e' 10m e la prova (c) deve vedere DUE giri del
#    ciclo. Con il default durerebbe venti minuti. NON e' fuori dalla parte sotto
#    esame — e' la cadenza della ritenzione — e proprio per questo la guardia qui
#    sopra pretende che il file spedito non la dichiari.
#
# E c'e' una TERZA differenza, di forma, che vale per ogni prova di questo repository
# e va nominata invece di lasciare la parola "uniche" a coprirla: il file che Loki
# esegue qui e' una RI-SERIALIZZAZIONE di PyYAML, non i byte spediti. Commenti persi,
# chiavi riordinate. Un difetto che PyYAML tollera e il parser Go rifiuta — chiavi
# duplicate, per dire — passerebbe verde qui e ucciderebbe la produzione all'avvio.
# Cio' che copre quel caso e' il `-verify-config` sull'immagine vera, altrove.
c["storage_config"]["object_store"]["s3"]["insecure"] = True
c["compactor"]["compaction_interval"] = "10s"
yaml.safe_dump(c, open(sys.argv[1], "w"))
PY

for img in "$IMG_MINIO" "$IMG_LOKI"; do
  scaricata=""
  for tentativo in 1 2 3; do
    if docker pull -q "$img" >/dev/null 2>&1; then scaricata=si; break; fi
    # L'errore del pull va mostrato all'ultimo tentativo: rate limit, digest sparito
    # e DNS hanno tre rimedi diversi e senza stderr avrebbero lo stesso testo.
    if [ "$tentativo" = "3" ]; then docker pull "$img" || true; fi
    sleep $((tentativo * 10))
  done
  [ -n "$scaricata" ] || { echo "FALLITO: impossibile scaricare $img dopo tre tentativi"; exit 1; }
done

docker network create "$RETE" >/dev/null
docker run -d --rm --name ritenzione-minio --network "$RETE" \
  -e MINIO_ROOT_USER=provaprova -e MINIO_ROOT_PASSWORD=provaprova \
  "$IMG_MINIO" server /data >/dev/null

bucket_creato=""
for _ in $(seq 1 30); do
  if docker run --rm --network "$RETE" --entrypoint sh "$IMG_MINIO" -c \
    "mc alias set m http://ritenzione-minio:9000 provaprova provaprova >/dev/null 2>&1 && mc mb --ignore-existing m/loki-prova >/dev/null 2>&1"; then
    bucket_creato=si; break
  fi
  sleep 1
done
# Senza questa riga l'errore verrebbe ingoiato: `/ready` non guarda l'object storage.
[ -n "$bucket_creato" ] || { echo "FALLITO: bucket MinIO non creato dopo 30 tentativi"; exit 1; }

docker run -d --rm --name ritenzione-loki --network "$RETE" -p 3197:3100 \
  -v "$TMP/loki.yaml:/etc/loki/loki.yaml:ro" \
  -e LOKI_R2_BUCKET=loki-prova -e LOKI_R2_ENDPOINT=ritenzione-minio:9000 \
  -e LOKI_R2_ACCESS_KEY_ID=provaprova -e LOKI_R2_SECRET_ACCESS_KEY=provaprova \
  "$IMG_LOKI" -config.file=/etc/loki/loki.yaml -config.expand-env=true >/dev/null

echo -n "attendo Loki: "
for _ in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3197/ready)" = "200" ] && { echo "pronto"; break; }
  sleep 2
done
[ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3197/ready)" = "200" ] || {
  echo "FALLITO: Loki non e' arrivato a servire"; docker logs ritenzione-loki 2>&1 | tail -20; exit 1; }

ORA=$(python3 -c "import time;print(int(time.time()))")
QUERY='%7Bservice_name%3D%22claude-code%22%7D'
fallimenti=0

echo "--- (a) l'API di cancellazione e' chiusa"
# I parametri VANNO nella query string anche in POST: con `--data-urlencode` Loki
# risponde 400 "query not set", che e' un rosso per il motivo sbagliato e
# nasconderebbe un 204. Misurato il 2026-08-21.
POST=$(curl -s -o "$TMP/post.txt" -w '%{http_code}' -X POST \
  "http://localhost:3197/loki/api/v1/delete?query=$QUERY&start=$((ORA-7200))&end=$((ORA-3600))")
GET=$(curl -s -o "$TMP/get.txt" -w '%{http_code}' "http://localhost:3197/loki/api/v1/delete")
echo "POST /loki/api/v1/delete -> $POST ; GET -> $GET"
# 204 e' il valore MISURATO prima del cambiamento, non un'ipotesi: e' cio' che
# rispondeva la config spedita fino al 2026-08-21.
[ "$POST" = "403" ] || { echo "FALLITO: (a) POST risponde $POST, atteso 403 — con 204 la rotta accetta cancellazioni non registrate"; cat "$TMP/post.txt"; fallimenti=1; }
[ "$GET"  = "403" ] || { echo "FALLITO: (a) GET risponde $GET, atteso 403";  cat "$TMP/get.txt";  fallimenti=1; }

echo "--- (b) l'ingestione non e' stata toccata dal cambiamento"
PUSH=$(curl -s -o "$TMP/push.txt" -w '%{http_code}' -X POST http://localhost:3197/loki/api/v1/push \
  -H 'Content-Type: application/json' \
  -d "{\"streams\":[{\"stream\":{\"service_name\":\"claude-code\"},\"values\":[[\"$((ORA * 1000000000))\",\"riga-di-prova\"]]}]}")
echo "push -> $PUSH"
# Perche' (b) esiste, corretto dopo averlo MISURATO: la motivazione scritta qui
# prima diceva "un deletion_mode scritto male che facesse rifiutare l'ingestione
# passerebbe (a)". E' uno scenario impossibile — `deletion_mode: bananas` fa
# rifiutare l'AVVIO ("unknown deletion mode: must be one of
# disabled|filter-only|filter-and-delete"), quindi lo script morirebbe prima, su
# "attendo Loki". Resta un sanity check che costa nulla e distingue "la rotta e'
# chiusa" da "e' chiuso tutto"; non resta la ragione inventata che ci stava sopra.
[ "$PUSH" = "204" ] || { echo "FALLITO: (b) il push risponde $PUSH, atteso 204"; cat "$TMP/push.txt"; fallimenti=1; }

echo "--- (c) il ciclo di ritenzione gira ancora, e riesce"
leggi() {
  curl -s http://localhost:3197/metrics \
    | awk -v m="loki_compactor_apply_retention_operation_total{status=\"$1\"}" '$1==m {print $2}'
}
primo=$(leggi success); primo=${primo:-0}
echo -n "attendo un giro di ritenzione: "
avanzato=""
for tentativo in $(seq 1 40); do
  sleep 2
  secondo=$(leggi success); secondo=${secondo:-0}
  # Il confronto e' fra DUE osservazioni. Un contatore letto una volta sola non
  # distingue "gira" da "e' girato una volta all'avvio e poi si e' fermato".
  if python3 -c "import sys; sys.exit(0 if float('$secondo') > float('$primo') else 1)"; then
    avanzato=si; echo "dopo $((tentativo * 2))s (${primo} -> ${secondo})"; break
  fi
done
[ -n "$avanzato" ] || {
  echo "FALLITO: (c) loki_compactor_apply_retention_operation_total{status=\"success\"} fermo a ${primo} dopo 80s: la ritenzione NON gira piu' e i 720h sono diventati per sempre"
  docker logs ritenzione-loki 2>&1 | grep -iE 'retention|compact' | tail -10
  fallimenti=1
}
fallite=$(leggi failure); fallite=${fallite:-0}
# META' ASSERZIONE, e va detto: in un run sano la serie con `status="failure"` NON
# ESISTE (misurato sul /metrics di un Loki vero), quindi `leggi` torna vuoto e il
# `:-0` lo rende 0. Questo controllo quindi FALLISCE APERTO — se un bump di Loki
# rinominasse la metrica o le aggiungesse una label, la meta' `success` diventerebbe
# rossa (fallisce chiuso, bene) e questa resterebbe muta. Vale come segnale in piu'
# quando la serie c'e', non come garanzia; il ramo rosso non e' provato perche' non
# si sa provocare un giro di ritenzione fallito senza rompere lo storage.
python3 -c "import sys; sys.exit(0 if float('$fallite') == 0 else 1)" || {
  echo "FALLITO: (c) ci sono ${fallite} giri di ritenzione FALLITI"; fallimenti=1; }

[ "$fallimenti" = "0" ] || exit 1
echo "ok: (a) l'API di cancellazione risponde 403, (b) l'ingestione regge, (c) la ritenzione gira e riesce"
