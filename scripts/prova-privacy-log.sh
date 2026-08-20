#!/usr/bin/env bash
# Prova end-to-end che l'identita' e il contenuto di sessione NON arrivano nel log
# store. Fa passare un payload OTLP dal Collector VERO a Loki VERO, cioe' attraverso
# ENTRAMBE le allow-list in serie, e poi interroga Loki come lo interrogherebbe una
# persona.
#
# Perche' esiste: i gate in images.yml controllano la FORMA delle due allow-list
# (keep_keys, ignore_defaults, il drop catch-all in fondo). Nessuno di loro esegue
# niente. E su questo progetto una config valida ha gia' mentito piu' di una volta:
# `-verify-config` di Loki accetta un endpoint che a runtime uccide il processo, e
# `validate` del Collector esce 0 su nomi deprecati che compaiono solo all'avvio.
#
# I LIMITI, e vanno letti prima del risultato:
#  - il payload e' SINTETICO. Prova che le allow-list scartano cio' che gli si mette
#    davanti, NON che il client mandi solo quello. La seconda meta' e' il dry run col
#    client vero, in docs/LOCAL_DRY_RUN.md.
#  - lo storage e' MinIO, non R2, e per questo l'unica chiave di docker/loki.yaml che
#    viene modificata e' `insecure` (MinIO qui parla HTTP). Lo script VERIFICA che
#    `limits_config` — dove vive l'allow-list sotto esame — sia identico al file
#    spedito: se qualcuno "aggiustasse" la config per far passare la prova, questa
#    riga se ne accorge.
set -euo pipefail
cd "$(dirname "$0")/.."

RETE=prova-privacy-log
TOKEN=token-di-prova-non-un-segreto
IMG_LOKI=grafana/loki:3.7.6@sha256:efd47c67f9bac88ca29bcf8cb997d9ab29d1848bd0aff579282295542a745952
IMG_COLL=otel/opentelemetry-collector-contrib:0.158.0-386@sha256:93e4793719dd55d0d9499328e7b45af219116cc9d1bcb95df5b3a0f8cade831d
# Pinnata per digest come ogni altra immagine del repository: un tag e' un'etichetta
# e si puo' ripuntare sotto lo stesso nome. Era l'unica immagine del ramo senza, e
# gira in CI su un checkout — con `contents: read` e nessun segreto, ma pinnarla costa
# una riga.
IMG_MINIO=minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e

pulisci() {
  docker rm -f privacy-loki privacy-coll privacy-minio >/dev/null 2>&1 || true
  docker network rm "$RETE" >/dev/null 2>&1 || true
  if [ -n "${TMP:-}" ]; then rm -rf "$TMP"; fi
}
trap pulisci EXIT
# La prima pulizia gira PRIMA di creare TMP: se cancellasse la cartella appena
# creata, lo script morirebbe sul primo file che ci scrive.
pulisci
TMP=$(mktemp -d)

# --- la config sotto esame, con UNA sola chiave diversa e la prova che sia una sola
python3 - "$TMP/loki.yaml" <<'PY'
import sys, yaml
prova = yaml.safe_load(open("docker/loki.yaml"))
prova["storage_config"]["object_store"]["s3"]["insecure"] = True

# L'ORACOLO STA QUI DENTRO, non nel file sotto esame. La prima versione faceva
#     spedita = yaml.safe_load(open("docker/loki.yaml"))
#     prova   = yaml.safe_load(open("docker/loki.yaml"))
#     assert prova["limits_config"] == spedita["limits_config"]
# cioe' confrontava due parse dello STESSO file, con in mezzo una modifica che non
# tocca `limits_config`: un oggetto contro se' stesso. Passava sempre, anche dopo
# aver aggiunto `user.email` all'allow-list — misurato. E tre documenti dichiaravano
# che quella riga impediva di "aggiustare la config per far passare la prova".
# Un oracolo che vive nel file sotto esame non e' un oracolo.
#
# Adesso l'atteso e' scritto qui e va cambiato a mano, deliberatamente, da chi tocca
# l'allow-list — che e' il gesto che si vuole rendere visibile.
CHIAVI_ATTESE = {
    "event.name", "event.sequence", "session.id", "prompt.id", "tool_name",
    "tool_use_id", "success", "error_type", "duration_ms", "decision", "source",
    "model", "query_source", "status", "status_code", "attempt", "error_code",
    "error_name", "transport_type", "server_scope", "prompt_length", "response_length",
}
otlp = prova["limits_config"]["otlp_config"]
tenute = set()
for voce in otlp["log_attributes"]:
    if voce.get("action") == "structured_metadata":
        tenute |= set(voce.get("attributes") or [])
if tenute != CHIAVI_ATTESE:
    print(f"FALLITO: l'allow-list di loki.yaml e' cambiata. In piu': {sorted(tenute - CHIAVI_ATTESE)}. "
          f"In meno: {sorted(CHIAVI_ATTESE - tenute)}. Se il cambio e' voluto, aggiorna CHIAVI_ATTESE "
          f"in questo script nello stesso commit.", file=sys.stderr)
    sys.exit(1)
for nome, voci in (("resource_attributes", otlp["resource_attributes"]["attributes_config"]),
                   ("scope_attributes", otlp["scope_attributes"]),
                   ("log_attributes", otlp["log_attributes"])):
    if voci[-1] != {"action": "drop", "regex": ".*"}:
        print(f"FALLITO: {nome} non finisce con il drop catch-all", file=sys.stderr)
        sys.exit(1)
if prova["limits_config"].get("allow_structured_metadata") is not True:
    print("FALLITO: allow_structured_metadata non e' true", file=sys.stderr); sys.exit(1)

yaml.safe_dump(prova, open(sys.argv[1], "w"))
print(f"config di prova: {len(tenute)} chiavi in allow-list come atteso, tre sezioni chiuse, "
      "e la sola differenza dalla config spedita e' storage_config.object_store.s3.insecure")
PY

# Le tre immagini si scaricano PRIMA, con la ritentata. Questo script e' un gate
# bloccante in CI, e cinquecento righe piu' su nello stesso workflow c'e' gia' la
# lezione: il primo fallimento non deterministico di questa pipeline fu un
# `TLS handshake timeout` verso auth.docker.io. Un gate che fallisce a caso smette di
# essere un segnale.
for img in "$IMG_MINIO" "$IMG_LOKI" "$IMG_COLL"; do
  scaricata=""
  for tentativo in 1 2 3; do
    if docker pull -q "$img" >/dev/null 2>&1; then scaricata=si; break; fi
    echo "pull di ${img%%@*} fallito (tentativo ${tentativo}), riprovo fra $((tentativo * 10))s" >&2
    sleep $((tentativo * 10))
  done
  [ -n "$scaricata" ] || { echo "FALLITO: impossibile scaricare $img dopo tre tentativi"; exit 1; }
done

docker network create "$RETE" >/dev/null

docker run -d --rm --name privacy-minio --network "$RETE" \
  -e MINIO_ROOT_USER=provaprova -e MINIO_ROOT_PASSWORD=provaprova \
  "$IMG_MINIO" server /data >/dev/null
# `mc` nella stessa immagine: crea il bucket che Loki si aspetta.
bucket_creato=""
for _ in $(seq 1 30); do
  if docker run --rm --network "$RETE" --entrypoint sh "$IMG_MINIO" -c \
    "mc alias set m http://privacy-minio:9000 provaprova provaprova >/dev/null 2>&1 && mc mb --ignore-existing m/loki-prova >/dev/null 2>&1"; then
    bucket_creato=si; break
  fi
  sleep 1
done
# Senza questa riga l'errore veniva ingoiato: `/ready` di Loki non guarda l'object
# storage e le letture recenti arrivano dall'ingester, quindi la prova sarebbe passata
# verde senza aver mai toccato un bucket.
[ -n "$bucket_creato" ] || { echo "FALLITO: bucket MinIO non creato dopo 30 tentativi"; exit 1; }

docker run -d --rm --name privacy-loki --network "$RETE" \
  --network-alias loki.railway.internal -p 3198:3100 \
  -v "$TMP/loki.yaml:/etc/loki/loki.yaml:ro" \
  -e LOKI_R2_BUCKET=loki-prova -e LOKI_R2_ENDPOINT=privacy-minio:9000 \
  -e LOKI_R2_ACCESS_KEY_ID=provaprova -e LOKI_R2_SECRET_ACCESS_KEY=provaprova \
  "$IMG_LOKI" -config.file=/etc/loki/loki.yaml -config.expand-env=true >/dev/null

echo -n "attendo Loki: "
for _ in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3198/ready)" = "200" ] && { echo "pronto"; break; }
  sleep 2
done
[ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3198/ready)" = "200" ] || {
  echo "FALLITO: Loki non e' arrivato a servire"; docker logs privacy-loki 2>&1 | tail -20; exit 1; }

# Il Collector con la config SPEDITA, non una copia: e' l'altra meta' sotto esame.
docker run -d --rm --name privacy-coll --network "$RETE" -p 4398:4318 \
  -v "$PWD/docker/otel-collector-config.yaml:/c.yaml:ro" \
  -e OTLP_INGEST_TOKEN="$TOKEN" "$IMG_COLL" --config=file:/c.yaml >/dev/null
echo -n "attendo il Collector: "
for _ in $(seq 1 30); do
  docker logs privacy-coll 2>&1 | grep -q "Everything is ready" && { echo "pronto"; break; }
  sleep 1
done

# Un payload che contiene TUTTO cio' che non deve arrivare, in OGNI posto in cui puo'
# stare — e l'elenco dei posti e' cresciuto il 2026-08-20, quando un audit ha notato
# che la prima versione metteva `prova` nel body: quella dimensione non era mai
# esercitata, quindi il test passava per costruzione e non per proprieta'.
# I sei posti: resource attribute, il VALORE di service.name (unica label di indice, e
# keep_keys filtra le chiavi non i valori), scope attribute, NOME e VERSIONE dello
# scope (campi, non attributi: Loki li mette in structured metadata per costruzione),
# log record attribute, e il BODY. Piu' una chiave che nessuna versione ha mai
# mandato — il caso su cui una delete-list fallirebbe.
ORA=$(python3 -c "import time;print(int(time.time()*1e9))")
CODICE=$(curl -sS -o "$TMP/push.txt" -w '%{http_code}' -X POST http://localhost:4398/v1/logs \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"resourceLogs":[{
  "resource":{"attributes":[
    {"key":"service.name","value":{"stringValue":"claude-code-NON-DEVE-ARRIVARE-nel-valore"}},
    {"key":"user.email","value":{"stringValue":"NON-DEVE-ARRIVARE@example.com"}},
    {"key":"host.arch","value":{"stringValue":"NON-DEVE-ARRIVARE-arm64"}}]},
  "scopeLogs":[{"scope":{
      "name":"NON-DEVE-ARRIVARE-nome-scope",
      "version":"NON-DEVE-ARRIVARE-versione-scope",
      "attributes":[
      {"key":"scope.secret","value":{"stringValue":"NON-DEVE-ARRIVARE-scope"}}]},
    "logRecords":[{
      "timeUnixNano":"'"$ORA"'",
      "body":{"stringValue":"NON-DEVE-ARRIVARE-nel-body prompt=segreto user.email=vittima@example.com"},
      "attributes":[
        {"key":"event.name","value":{"stringValue":"tool_result"}},
        {"key":"session.id","value":{"stringValue":"DEVE-ARRIVARE-sessione"}},
        {"key":"tool_name","value":{"stringValue":"DEVE-ARRIVARE-Bash"}},
        {"key":"success","value":{"stringValue":"false"}},
        {"key":"error_type","value":{"stringValue":"DEVE-ARRIVARE-timeout"}},
        {"key":"user.id","value":{"stringValue":"NON-DEVE-ARRIVARE-userid"}},
        {"key":"organization.id","value":{"stringValue":"NON-DEVE-ARRIVARE-org"}},
        {"key":"prompt","value":{"stringValue":"NON-DEVE-ARRIVARE-il-prompt"}},
        {"key":"chiave.inventata.domani","value":{"stringValue":"NON-DEVE-ARRIVARE-futuro"}}]}]}]}]}')
echo "push OTLP verso il Collector: HTTP $CODICE"
[ "$CODICE" = "200" ] || { echo "FALLITO: il Collector ha rifiutato il payload"; cat "$TMP/push.txt"; exit 1; }

sleep 12

echo "--- (a) le label di indice, e i loro VALORI"
curl -s "http://localhost:3198/loki/api/v1/labels" | tee "$TMP/labels.json"; echo
# I valori, non solo i nomi: `keep_keys` filtra le CHIAVI, quindi un mittente puo'
# scrivere quello che vuole DENTRO service.name, che e' l'unica label di indice.
curl -s "http://localhost:3198/loki/api/v1/label/service_name/values" | tee "$TMP/valori.json"; echo
echo "--- (b) tutto cio' che Loki ha memorizzato per quello stream"
curl -sG "http://localhost:3198/loki/api/v1/query_range" \
  --data-urlencode 'query={service_name="claude-code"}' \
  --data-urlencode "start=$((ORA - 300000000000))" --data-urlencode "end=$((ORA + 300000000000))" > "$TMP/query.json"

python3 - "$TMP/labels.json" "$TMP/query.json" "$TMP/valori.json" <<'PY'
import json, sys
labels = json.load(open(sys.argv[1])).get("data") or []
query  = open(sys.argv[2]).read()
risultato = json.loads(query).get("data", {}).get("result", [])
fallimenti = []

# (a) nessuna label d'identita' nell'indice
sporche = [l for l in labels if any(x in l for x in ("user", "organization", "prompt", "scope_secret", "host_arch", "inventata"))]
if sporche: fallimenti.append(f"(a) l'indice contiene {sporche}")
valori = json.load(open(sys.argv[3])).get("data") or []
# Il VALORE, non solo il nome. Senza questa riga, un mittente che scrive
# `service.name: claude-code-<qualunque cosa>` produce uno stream nuovo, la query
# di (c) non lo trova, e il test fallisce lamentandosi che manca il record utile —
# rosso giusto, diagnosi sbagliata, e la cardinalita' dell'indice non la nomina
# nessuno.
if valori != ["claude-code"]:
    fallimenti.append(f"(a) service_name vale {valori}, non esattamente ['claude-code']: il mittente controlla il valore dell'unica label di indice, e ogni valore nuovo e' uno stream nuovo")

# (b) niente di proibito, in NESSUNA forma: ne' label, ne' structured metadata, ne' body
if "NON-DEVE-ARRIVARE" in query:
    posti = sorted({p for p in ("user.email","user_email","user_id","organization_id","prompt","scope_secret","host_arch","chiave_inventata_domani","scope_name","scope_version","nel-body","nome-scope","versione-scope") if p in query})
    fallimenti.append(f"(b) il payload proibito e' interrogabile — chiavi coinvolte: {posti}")

# (c) cio' che serve C'E'. Un allow-list che scarta tutto passerebbe (a) e (b) e
#     sembrerebbe un successo: questo e' il controllo che lo smaschera.
if not risultato: fallimenti.append("(c) nessuno stream: il record utile non e' arrivato affatto")
# `tool_result` e' il BODY: ancorarlo a event.name lo rende sicuro, svuotarlo lo
# renderebbe inutile, e le due cose si distinguono solo cercandolo.
for atteso in ("DEVE-ARRIVARE-sessione", "DEVE-ARRIVARE-Bash", "DEVE-ARRIVARE-timeout", "tool_result"):
    if atteso not in query: fallimenti.append(f"(c) manca {atteso}: l'allow-list scarta anche cio' che serve")

print("label di indice:", labels)
if fallimenti:
    for f in fallimenti: print("FALLITO:", f)
    sys.exit(1)
print("ok: (a) indice pulito, (b) niente identita' ne' contenuto in nessuna forma, (c) il record utile c'e'")
PY
