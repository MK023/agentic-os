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
# Lo STESSO digest della produzione (docker-compose.yml e railway/otel-collector/
# Dockerfile), non il tag `-386`. Quel suffisso non e' un numero di build: e' la
# piattaforma, ed e' il difetto che #126 ha tolto dalla produzione mentre questi due
# script hanno continuato a pinnarlo — la prova eseguiva un binario a 32 bit per
# convalidare una config che ne spedisce uno a 64. E' la stessa famiglia del dry run
# senza alias di rete: misura una cosa diversa da quella che gira. Il digest e' un
# manifest list (386, amd64, arm64, ...), quindi lo stesso valore vale in CI e qui.
IMG_COLL=otel/opentelemetry-collector-contrib:0.158.0@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5
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
    # L'errore del pull VA MOSTRATO all'ultimo tentativo: rate limit di Docker Hub,
    # digest che non corrisponde piu', DNS sono tre cause con tre rimedi diversi, e
    # senza stderr avevano lo stesso testo. Questo meccanismo e' nato da un
    # `TLS handshake timeout` verso auth.docker.io: l'errore E' l'informazione.
    if [ "$tentativo" = "3" ]; then docker pull "$img" || true; fi
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
coll_pronto=""
for _ in $(seq 1 30); do
  docker logs privacy-coll 2>&1 | grep -q "Everything is ready" && { coll_pronto=si; echo "pronto"; break; }
  sleep 1
done
# Senza questa asserzione lo script proseguiva e moriva sul `curl` successivo con
# `curl: (7)`, mentre il trap aveva gia' rimosso il container: rosso giusto, diagnosi
# perduta. Ed e' il caso che questo script esiste per catturare — il Collector che
# muore all'avvio su un nome deprecato, cioe' proprio cio' che `validate` non vede.
[ -n "$coll_pronto" ] || {
  echo "FALLITO: il Collector non e' mai arrivato a servire. I suoi ultimi log:"
  docker logs privacy-coll 2>&1 | tail -20
  exit 1
}

# Un payload che contiene TUTTO cio' che non deve arrivare, in OGNI posto in cui puo'
# stare — e l'elenco dei posti e' cresciuto il 2026-08-20, quando un audit ha notato
# che la prima versione metteva `prova` nel body: quella dimensione non era mai
# esercitata, quindi il test passava per costruzione e non per proprieta'.
# I sei posti: resource attribute, il VALORE di service.name (unica label di indice, e
# keep_keys filtra le chiavi non i valori), scope attribute, NOME e VERSIONE dello
# scope (campi, non attributi: Loki li mette in structured metadata per costruzione),
# log record attribute, e il BODY. Piu' una chiave che nessuna versione ha mai
# mandato — il caso su cui una delete-list fallirebbe.
# I CAMPI DEL RECORD nel payload qui sotto — traceId, spanId, severityText,
# severityNumber, flags — non stanno in nessuna mappa di attributi, quindi non li
# tocca ne' `keep_keys` (governa attributi) ne' `otlp_config` di Loki (tre sezioni,
# tutte di attributi): la sola barriera sono i `set(...)` del Collector. Non erano
# in questo payload, e il 2026-08-21 e' costato una difesa che falliva a OGNI
# record senza che niente diventasse rosso — `set(log.trace_id.string, "")`, e il
# parser di OTTL rifiuta la stringa vuota. I due id non possono portare la stringa
# NON-DEVE-ARRIVARE (devono essere esadecimali), quindi per loro il marcatore e' il
# VALORE: l'asserzione (b-bis) pretende che `deadbeef...` non torni.
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
      "traceId":"deadbeefdeadbeefdeadbeefdeadbeef",
      "spanId":"deadbeefdeadbeef",
      "severityText":"NON-DEVE-ARRIVARE-severity",
      "severityNumber":17,
      "flags":255,
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
        {"key":"chiave.inventata.domani","value":{"stringValue":"NON-DEVE-ARRIVARE-futuro"}}]},
      {
      "timeUnixNano":"'"$ORA"'",
      "body":{"stringValue":"NON-DEVE-ARRIVARE-nel-body-2"},
      "attributes":[
        {"key":"event.name","value":{"stringValue":"api_error"}},
        {"key":"session.id","value":{"stringValue":"DEVE-ARRIVARE-sessione"}},
        {"key":"status_code","value":{"stringValue":"500"}},
        {"key":"attempt","value":{"stringValue":"1"}}]},
      {
      "timeUnixNano":"'"$ORA"'",
      "body":{"stringValue":"NON-DEVE-ARRIVARE-nel-body-3"},
      "attributes":[
        {"key":"event.name","value":{"stringValue":"mcp_server_connection"}},
        {"key":"session.id","value":{"stringValue":"DEVE-ARRIVARE-sessione"}},
        {"key":"status","value":{"stringValue":"failed"}},
        {"key":"transport_type","value":{"stringValue":"stdio"}},
        {"key":"server_scope","value":{"stringValue":"user"}}]},
      {
      "timeUnixNano":"'"$ORA"'",
      "body":{"stringValue":"NON-DEVE-ARRIVARE-nel-body-4"},
      "attributes":[
        {"key":"event.name","value":{"stringValue":"mcp_server_connection"}},
        {"key":"session.id","value":{"stringValue":"DEVE-ARRIVARE-sessione"}},
        {"key":"status","value":{"stringValue":"disconnected"}},
        {"key":"transport_type","value":{"stringValue":"sse"}},
        {"key":"server_scope","value":{"stringValue":"project"}},
        {"key":"error_code","value":{"stringValue":"DEVE-ARRIVARE-E42"}}]}]}]}]}')
echo "push OTLP verso il Collector: HTTP $CODICE"
[ "$CODICE" = "200" ] || { echo "FALLITO: il Collector ha rifiutato il payload"; cat "$TMP/push.txt"; exit 1; }

# Polling invece di un'attesa fissa. Con `sleep 12` su un runner carico l'asserzione
# (c) falliva dicendo "l'allow-list scarta anche cio' che serve" — cioe' accusava la
# config di un problema di tempistica. E questo e' un gate bloccante il cui preambolo
# dice che un gate instabile smette di essere un segnale: il ragionamento era gia'
# applicato ai `docker pull` e non all'ingestione.
echo -n "attendo che il record sia interrogabile: "
trovato=""
for tentativo in $(seq 1 20); do
  n=$(curl -sG "http://localhost:3198/loki/api/v1/query_range" \
        --data-urlencode 'query={service_name="claude-code"}' \
        --data-urlencode "start=$((ORA - 300000000000))" --data-urlencode "end=$((ORA + 300000000000))" \
      | python3 -c "import json,sys; print(sum(len(s['values']) for s in json.load(sys.stdin).get('data',{}).get('result',[])))" 2>/dev/null || echo 0)
  [ "${n:-0}" -gt 0 ] && { trovato=si; echo "dopo ${tentativo}s"; break; }
  sleep 1
done
[ -n "$trovato" ] || echo "MAI COMPARSO dopo 20s — cio' che segue distingue 'scartato' da 'in ritardo'"

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
    # I nomi si ricavano da DOVE sta il marcatore, non da un elenco confrontato con
    # il testo intero. La prima versione faceva la seconda cosa e nominava
    # `scope_name` a ogni fallimento: quella chiave c'e' sempre, con un valore
    # innocuo messo da `set(scope.name, ...)`, quindi la diagnosi mandava a cercare
    # in un posto sano. E' lo stesso difetto corretto in smoke.yml — la spiegazione
    # appartiene all'esito, non alla lista di cio' che si temeva.
    def porta(k, v): return "NON-DEVE-ARRIVARE" in str(k) or "NON-DEVE-ARRIVARE" in str(v)
    posti = set()
    for stream in risultato:
        for k, v in (stream.get("stream") or {}).items():
            if porta(k, v): posti.add(f"label:{k}")
        for riga in (stream.get("values") or []):
            if len(riga) > 1 and "NON-DEVE-ARRIVARE" in str(riga[1]): posti.add("body")
            for k, v in ((riga[2] if len(riga) > 2 else None) or {}).items():
                if porta(k, v): posti.add(k)
    # Se il marcatore e' nella risposta ma non in nessuno di questi posti, il
    # fallimento resta — con scritto che non si sa dire dove, invece di indovinare.
    fallimenti.append(f"(b) il payload proibito e' interrogabile — chiavi coinvolte: {sorted(posti) or 'in una forma che questo controllo non sa localizzare'}")

# (b-bis) I CAMPI DI RECORD, che il marcatore NON-DEVE-ARRIVARE non puo' coprire:
# un trace id deve essere esadecimale, quindi il segnale e' il VALORE. Se
# `deadbeef...` torna interrogabile, gli statement che lo azzerano non si sono
# eseguiti — che e' esattamente cio' che succedeva con la stringa vuota, in
# silenzio, mentre il Collector scriveva un warning che nessuno leggeva.
for campo in ("deadbeefdeadbeefdeadbeefdeadbeef", "deadbeefdeadbeef"):
    if campo in query:
        fallimenti.append(f"(b-bis) {campo} e' interrogabile: il campo di record non e' stato azzerato dal Collector")

# (c-bis) IL LIVELLO DERIVATO. Il fornitore non manda severity (verificato sulla sua
# doc), quindi senza gli statement di derivazione Grafana mostra `unk` su ogni riga.
# Qui si pretende che i due rami esistano davvero: il `tool_result` con
# success="false" deve arrivare WARN, l'`api_error` deve arrivare ERROR. Senza
# questa asserzione la derivazione sarebbe una difesa che nessuna prova esercita,
# che e' il difetto gia' pagato il 2026-08-21 con set(log.trace_id.string, "").
# Dove guardare: `query_range` restituisce la structured metadata dentro `stream`,
# non nel terzo elemento di `values`. La prima versione di questo controllo cercava
# solo li' e falliva su una derivazione che funzionava — rumorosamente, per
# fortuna. Si leggono entrambi i posti, perche' quale dei due usi Loki dipende da
# come la metadata si distribuisce fra le righe.
livelli = set()
for stream in risultato:
    testo = (stream.get("stream") or {}).get("severity_text")
    if testo:
        livelli.add(testo)
    for riga in (stream.get("values") or []):
        meta = (riga[2] if len(riga) > 2 else None) or {}
        if meta.get("severity_text"):
            livelli.add(meta["severity_text"])
for atteso in ("WARN", "ERROR"):
    if atteso not in livelli:
        fallimenti.append(f"(c-bis) livello {atteso} assente: trovati {sorted(livelli) or 'nessuno'} — la derivazione non si e' eseguita")

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

# --- (d) OGNI PANNELLO Loki DELLA DASHBOARD RESTITUISCE QUALCOSA
# Il difetto che chiude: una query di pannello che non trova niente e un pannello
# rotto hanno lo stesso aspetto — vuoto. E il modo di sbagliarla non e' esotico:
# in Loki `session.id` diventa `session_id` e `event.name` diventa `event_name`,
# quindi scrivere il nome col punto produce una query VALIDA che non torna mai
# niente. Nessun controllo di forma puo' vederlo.
#
# L'oracolo non e' un elenco di query copiate qui dentro: sono le query CHE
# VENGONO SPEDITE, lette dalla dashboard. Copiarle significherebbe provare la copia.
echo "--- (d) i pannelli Loki della dashboard trovano davvero qualcosa"
python3 - "$ORA" <<'PYD'
import json, re, sys, urllib.error, urllib.parse, urllib.request

ora = int(sys.argv[1])
d = json.load(open("docker/grafana/dashboards/claude-code.json"))

# I default delle variabili di dashboard. `$sessione` non lo risolve nessuno via
# API, e senza sostituirlo la query tornerebbe vuota per un motivo che non
# riguarda i dati — cioe' un rosso con la diagnosi sbagliata.
variabili = {v["name"]: (v.get("current") or {}).get("value", "")
             for v in (d.get("templating", {}) or {}).get("list", [])}

# Lo uid ATTESO si legge dal file di provisioning, non si scrive qui: e'
# l'accoppiamento fra due file, ed e' esattamente il guasto che lo uid fisso
# esiste per chiudere. Senza questo controllo la prova interroga Loki DIRETTAMENTE
# e scavalca la risoluzione del datasource, quindi uno uid sbagliato nei pannelli
# la lascerebbe verde mentre Grafana mostra "Datasource ... was not found".
uid_atteso = None
for riga in open("docker/grafana/provisioning/datasources/loki.yml"):
    spoglia = riga.strip()
    if spoglia.startswith("uid:"):
        uid_atteso = spoglia.split(":", 1)[1].strip()
        break
if not uid_atteso:
    print("FALLITO: (d) il datasource Loki non dichiara nessun uid: Grafana ne genererebbe uno a caso e i pannelli punterebbero a una fonte inesistente")
    sys.exit(1)

bersagli = []
fuori_posto = []
for p in d["panels"]:
    ds = p.get("datasource") or {}
    tipo = ds.get("type") if isinstance(ds, dict) else ds
    for t in (p.get("targets") or []):
        espressione = t.get("expr", "")
        # UNA QUERY LogQL SI RICONOSCE DA SOLA: comincia con un selettore di
        # stream. E' cosi' che si smette di dipendere da come il pannello e'
        # etichettato — togliere il blocco `datasource` da UN pannello lo faceva
        # sparire dai bersagli e la prova restava verde, mentre in Grafana quella
        # query finiva su Prometheus, che e' il datasource di default.
        sembra_logql = espressione.lstrip().startswith("{") or "count_over_time({" in espressione
        if tipo != "loki":
            if sembra_logql:
                fuori_posto.append((p.get("title"), tipo))
            continue
        if (ds.get("uid") if isinstance(ds, dict) else None) != uid_atteso:
            fuori_posto.append((p.get("title"), f"uid={ds.get('uid')!r}, atteso {uid_atteso!r}"))
            continue
        for nome, valore in variabili.items():
            espressione = espressione.replace("$" + nome, str(valore))
        bersagli.append((p.get("title"), espressione))

if fuori_posto:
    for titolo, perche in fuori_posto:
        print(f"FALLITO: (d) il pannello \"{titolo}\" porta una query LogQL ma non e' agganciato al datasource Loki ({perche}): in Grafana finisce sulla fonte di default, che e' Prometheus")
    sys.exit(1)

# Un'asserzione che non guarda niente passa sempre: e' il modo in cui questo gate
# morirebbe in silenzio se qualcuno togliesse i pannelli o il riferimento al
# datasource.
if not bersagli:
    print("FALLITO: (d) nessun pannello con datasource loki nella dashboard — questa asserzione non sta guardando niente")
    sys.exit(1)

fallimenti = []
for titolo, espressione in bersagli:
    parametri = urllib.parse.urlencode({
        "query": espressione,
        "start": ora - 300_000_000_000,
        "end": ora + 300_000_000_000,
        "step": "60",
    })
    # urlopen SOLLEVA su 4xx invece di restituire il corpo, e Loki risponde 400
    # proprio nel caso piu' probabile: un nome di chiave scritto col punto
    # (`event.name` invece di `event_name`). Senza questo `except` il gate diventava
    # rosso con un traceback di urllib al posto della diagnosi — misurato provando
    # i denti, che e' l'unico modo in cui si vede.
    try:
        with urllib.request.urlopen("http://localhost:3198/loki/api/v1/query_range?" + parametri) as r:
            risposta = json.load(r)
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "replace").strip()
        fallimenti.append('(d) "%s": Loki ha RIFIUTATO la query (HTTP %s): %s | query: %s'
                          % (titolo, e.code, corpo[:300], espressione))
        continue
    if risposta.get("status") != "success":
        fallimenti.append('(d) "%s": Loki non ha risposto success -> %s' % (titolo, json.dumps(risposta)[:200]))
        continue
    risultato = risposta["data"]["result"]
    if not risultato:
        fallimenti.append('(d) "%s": zero serie. La query e\' valida e non trova niente — su un pannello si vede come "non e\' ancora successo nulla". Query: %s' % (titolo, espressione))
        continue

    # "ALMENO UNA SERIE" E' PIU' DEBOLE DI CIO' CHE IL PANNELLO PROMETTE.
    # `sum by (transport_type, server_scope) (...)` su uno stream in cui quelle
    # chiavi fossero cadute NON restituisce zero serie: ne restituisce UNA, con il
    # label set vuoto. Il gate sarebbe verde e il pannello disegnerebbe una riga
    # anonima. Quindi si pretende che ogni label del `by (...)` esista davvero, con
    # un valore non vuoto, in almeno una serie: e' la differenza fra una query che
    # si esegue e un campo che viene esercitato.
    raggruppate = re.findall(r"by\s*\(([^)]*)\)", espressione)
    attese = [l.strip() for gruppo in raggruppate for l in gruppo.split(",") if l.strip()]
    for label in attese:
        if not any((s.get("metric") or {}).get(label) for s in risultato):
            fallimenti.append('(d) "%s": la label `%s` del raggruppamento non compare con un valore in nessuna serie: la query si esegue ma quel campo non e\' esercitato, e sul pannello sarebbe una riga anonima' % (titolo, label))
    print("  ok: %s -> %d serie, label %s" % (titolo, len(risultato), attese or "nessuna"))

if fallimenti:
    for f in fallimenti:
        print("FALLITO:", f)
    sys.exit(1)
print("ok: (d) tutti i %d pannelli Loki della dashboard trovano dati nella pipeline vera" % len(bersagli))
PYD
