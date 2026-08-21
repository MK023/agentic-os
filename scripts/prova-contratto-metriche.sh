#!/usr/bin/env bash
# CONTRACT TEST fra il Collector e i due consumatori delle sue metriche: la status API
# e il pannello Grafana. Verifica ESEGUENDO cio' che finora era sorvegliato leggendo
# stringhe.
#
# Perche' esiste. CLAUDE.md dice: "Metric names live in three places — the Collector's
# translation_strategy, docker/grafana/dashboards/claude-code.json, and QUERIES in
# main.py. Change one, change all three." Quell'accoppiamento aveva un gate che
# CONFRONTA TESTO fra i tre file. Il testo puo' essere identico in tutti e tre e
# sbagliato in tutti e tre insieme: `translation_strategy` decide i nomi veri, e
# nessuno dei tre file lo esegue. Questo script chiede al Collector cosa espone
# DAVVERO e confronta quello con cio' che i consumatori cercano.
#
# Verifica anche le due allow-list delle metriche sullo stesso giro, perche' sono
# nella stessa risposta: un nome di metrica inventato deve sparire (filter), e
# l'identita' non deve diventare una label (transform).
#
# LIMITE DICHIARATO: il payload e' sintetico. Prova il contratto e le allow-list, non
# che il client mandi solo quello — quella meta' e' il dry run di docs/LOCAL_DRY_RUN.md.
set -euo pipefail
cd "$(dirname "$0")/.."

# Porte alte e improbabili: questo script deve poter girare mentre altro occupa
# le porte consuete dello stack locale.
PORTA=38889
NOME=contratto-metriche
TOKEN=token-di-prova-non-un-segreto
# Lo STESSO digest della produzione (docker-compose.yml e railway/otel-collector/
# Dockerfile), non il tag `-386`. Quel suffisso non e' un numero di build: e' la
# piattaforma, ed e' il difetto che #126 ha tolto dalla produzione mentre questi due
# script hanno continuato a pinnarlo — la prova eseguiva un binario a 32 bit per
# convalidare una config che ne spedisce uno a 64. E' la stessa famiglia del dry run
# senza alias di rete: misura una cosa diversa da quella che gira. Il digest e' un
# manifest list (386, amd64, arm64, ...), quindi lo stesso valore vale in CI e qui.
IMG_COLL=otel/opentelemetry-collector-contrib:0.158.0@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5

pulisci() { docker rm -f "$NOME" >/dev/null 2>&1 || true; }
trap pulisci EXIT
pulisci

scaricata=""
for tentativo in 1 2 3; do
  if docker pull -q "$IMG_COLL" >/dev/null 2>&1; then scaricata=si; break; fi
  echo "pull fallito (tentativo ${tentativo}), riprovo fra $((tentativo * 10))s" >&2
  sleep $((tentativo * 10))
done
[ -n "$scaricata" ] || { echo "FALLITO: immagine del Collector non scaricabile"; exit 1; }

# La config SPEDITA, non una copia.
docker run -d --rm --name "$NOME" -p "34318:4318" -p "${PORTA}:8889" \
  -v "$PWD/docker/otel-collector-config.yaml:/c.yaml:ro" \
  -e OTLP_INGEST_TOKEN="$TOKEN" "$IMG_COLL" --config=file:/c.yaml >/dev/null

echo -n "attendo il Collector: "
pronto=""
for _ in $(seq 1 30); do
  docker logs "$NOME" 2>&1 | grep -q "Everything is ready" && { pronto=si; echo "pronto"; break; }
  sleep 1
done
[ -n "$pronto" ] || { echo "FALLITO: non e' partito"; docker logs "$NOME" 2>&1 | tail -20; exit 1; }

# Tre metriche attese, una inventata (deve sparire), identita' fra gli attributi
# (deve sparire), e una label ammessa (deve restare).
ORA=$(python3 -c "import time;print(int(time.time()*1e9))")
punto() { # $1 nome, $2 valore, $3 attributi json
  printf '{"name":"%s","sum":{"aggregationTemporality":2,"isMonotonic":true,"dataPoints":[{"asDouble":%s,"timeUnixNano":"%s","attributes":[%s]}]}}' "$1" "$2" "$ORA" "$3"
}
IDENT='{"key":"user.email","value":{"stringValue":"NON-DEVE-ARRIVARE@example.com"}},{"key":"organization.id","value":{"stringValue":"NON-DEVE-ARRIVARE-org"}}'
CODICE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "http://localhost:34318/v1/metrics" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"resourceMetrics":[{
   "resource":{"attributes":[{"key":"service.name","value":{"stringValue":"claude-code"}}]},
   "scopeMetrics":[{"metrics":['"$(punto claude_code.session.count 1 '{"key":"session.id","value":{"stringValue":"contratto"}},'"$IDENT")"',
     '"$(punto claude_code.token.usage 4242 '{"key":"type","value":{"stringValue":"input"}},{"key":"model","value":{"stringValue":"claude-haiku-4-5"}},'"$IDENT")"',
     '"$(punto claude_code.cost.usage 0.5 '{"key":"model","value":{"stringValue":"claude-haiku-4-5"}},'"$IDENT")"',
     '"$(punto claude_code.inventata.domani 99 '')"'
   ]}]}]}')
echo "push metriche: HTTP $CODICE"
[ "$CODICE" = "200" ] || { echo "FALLITO: il Collector ha rifiutato il payload"; exit 1; }
sleep 3

curl -sS "http://localhost:${PORTA}/metrics" > /tmp/contratto-metriche.txt

python3 - /tmp/contratto-metriche.txt <<'PY'
import json, pathlib, re, sys
esposto = open(sys.argv[1]).read()
fallimenti = []

# (1) i nomi che il Collector espone DAVVERO
famiglie = set(re.findall(r"^(claude_code_[a-z_]+)", esposto, re.M))
if not famiglie:
    fallimenti.append("il Collector non espone NESSUNA metrica claude_code_*: senza questa riga tutto il resto passerebbe su un file vuoto")

# (2) cio' che i due consumatori cercano
codice = pathlib.Path("services/public-status-api/main.py").read_text()
cerca_codice = set(re.findall(r"claude_code_[a-z_]+", codice))

# SOLO le espressioni dei target, non il testo del file. Cercare la sottostringa
# nel JSON intero prende anche le DESCRIZIONI dei pannelli, e una descrizione che
# nomina una metrica per dire "questa NON la usiamo" faceva fallire il gate con un
# messaggio che accusava una query inesistente. Misurato il 2026-08-21 su un
# pannello nuovo. E' la terza volta in questo repository che un gate legge prosa
# come se fosse codice: era gia' successo due volte in images.yml, con i commenti.
dashboard = json.loads(pathlib.Path("docker/grafana/dashboards/claude-code.json").read_text())

def espressioni_di(pannelli):
    # RICORSIVA, e non e' zelo: quando una `row` e' COLLASSATA, Grafana sposta i
    # pannelli figli dentro `row["panels"]`. Con la lettura piatta il gate trovava
    # zero metriche su una dashboard identica ma con le righe chiuse — e senza la
    # guardia qui sotto sarebbe passato verde. La versione precedente (regex sul
    # testo intero) i figli li trovava comunque: correggere il falso positivo
    # senza ricorsione avrebbe introdotto un falso NEGATIVO, che e' peggio.
    for pannello in pannelli or []:
        for bersaglio in (pannello.get("targets") or []):
            yield bersaglio.get("expr", "")
        yield from espressioni_di(pannello.get("panels"))

cerca_pannello = set(re.findall(r"claude_code_[a-z_]+", " ".join(espressioni_di(dashboard.get("panels")))))
# Un insieme vuoto rende `mancanti` vuoto e il gate passa: e' la stessa guardia
# che questo repository ha gia' scritto tre volte in images.yml.
if not cerca_pannello:
    fallimenti.append("nessuna metrica claude_code_* trovata nelle query della dashboard: questo gate non sta guardando niente")

for nome, cercati in (("services/public-status-api/main.py", cerca_codice),
                      ("docker/grafana/dashboards/claude-code.json", cerca_pannello)):
    mancanti = sorted(cercati - famiglie)
    if mancanti:
        fallimenti.append(
            f"{nome} interroga {mancanti}, che il Collector NON espone. "
            "I tre file possono essere d'accordo fra loro ed essere sbagliati insieme: "
            "il nome vero lo decide translation_strategy, e solo eseguendo si vede.")

# (3) allow-list dei NOMI: l'inventata non deve esserci
if "inventata" in esposto:
    fallimenti.append("`claude_code.inventata.domani` e' arrivata fino a /metrics: filter/metric-allowlist non tiene, e chi ha il token scrive nel TSDB i nomi che vuole")

# (4) allow-list delle LABEL: l'identita' non deve esserci
sporche = sorted({l for l in re.findall(r"[{,]([a-z_]+)=", esposto)
                  if l.startswith(("user_", "organization_"))})
if sporche or "NON-DEVE-ARRIVARE" in esposto:
    fallimenti.append(f"identita' fra le label esposte: {sporche or 'valore trovato nel testo'}")

# (5) cio' che DEVE restare: un allow-list che scarta tutto passerebbe (3) e (4)
for atteso in ('session_id="contratto"', 'model="claude-haiku-4-5"', 'type="input"'):
    if atteso not in esposto:
        fallimenti.append(f"manca {atteso}: l'allow-list scarta anche le label che il pannello usa per raggruppare")

if fallimenti:
    for f in fallimenti:
        print(f"::error::{f}")
    sys.exit(1)
print(f"ok: il Collector espone {sorted(famiglie)}; main.py e il pannello cercano esattamente quelli; "
      "nome inventato filtrato, identita' assente, label del pannello presenti")
PY
