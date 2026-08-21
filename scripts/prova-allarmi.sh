#!/usr/bin/env bash
# Prova ESEGUITA delle regole d'allarme. Fa girare Grafana e Prometheus VERI —
# le immagini che vengono spedite, costruite dai Dockerfile di railway/ — e
# pretende quattro cose che nessuna lettura del YAML puo' stabilire:
#
#   (a) Grafana carica il provisioning e le regole ci sono TUTTE. Un errore di
#       provisioning non impedisce a Grafana di partire: logga e prosegue, quindi
#       una regola scritta male semplicemente non esiste, senza che niente diventi
#       rosso.
#   (b) ogni regola nomina un datasource che ESISTE. E' il difetto che gli uid
#       fissi servono a prevenire, e da fuori una regola che non puo' interrogare
#       niente ha lo stesso aspetto di una regola che sta zitta perche' va tutto
#       bene.
#   (c) la NOTIFICA ARRIVA. Non "l'url e' quello giusto": l'API di Grafana
#       restituisce l'url di un contact point come `[REDACTED]`, perche' e' una
#       credenziale — misurato il 2026-08-21, ed e' il motivo per cui la prima
#       versione di questa prova falliva confrontando una stringa che nessuno le
#       avrebbe mai dato. L'unico controllo possibile e' l'unico che conta:
#       `$SLACK_WEBHOOK_URL` punta a un ricevitore locale, e la prova pretende che
#       una notifica ci finisca dentro. Se l'interpolazione non avvenisse, l'invio
#       fallirebbe solo al primo allarme vero — il giorno in cui serve.
#   (d) una regola scatta DAVVERO, rompendo la realta': Prometheus scrape un Loki
#       che non c'e', quindi `up{job="loki"}` vale 0 e la regola deve passare in
#       Alerting. E in parallelo una SECONDA regola deve restare Normal: senza
#       quel controllo, "tutte le regole scattano sempre" passerebbe per successo.
#
# IL LIMITE: non prova che Slack riceva. Quello si vede una volta sola, a mano,
# quando la variabile esiste — ed e' l'unico pezzo che resta a Marco.
set -euo pipefail
cd "$(dirname "$0")/.."

RETE=prova-allarmi
PASSWORD=password-di-prova-non-un-segreto
# Il ricevitore locale che sta al posto di Slack. Non e' un segreto e non esce
# da questa rete Docker.
WEBHOOK=http://allarmi-ricevitore:8080/hook
# Stessa base pinnata di services/public-status-api: nessuna immagine nuova da
# fidarsi, e il digest e' gia' sorvegliato da Dependabot.
IMG_PY=python:3.14.7-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

pulisci() {
  docker rm -f allarmi-grafana allarmi-prometheus allarmi-ricevitore >/dev/null 2>&1 || true
  docker network rm "$RETE" >/dev/null 2>&1 || true
  if [ -n "${TMP:-}" ]; then rm -rf "$TMP"; fi
}
trap pulisci EXIT
pulisci
TMP=$(mktemp -d)

# Le immagini SPEDITE, non le immagini base: e' il `COPY` dei Dockerfile a
# decidere se il provisioning finisce dove Grafana lo cerca, ed e' proprio la
# riga che questa prova deve esercitare. In CI sono gia' costruite dallo step
# delle immagini e questo `build` e' quasi istantaneo.
docker build -q -f railway/grafana/Dockerfile -t agentic-grafana:ci . >/dev/null
docker build -q -f railway/prometheus/Dockerfile -t agentic-prometheus:ci . >/dev/null

# La sola differenza dal provisioning spedito, e serve perche' la prova (d) deve
# vedere una TRANSIZIONE, non aspettarla: `for: 5m` piu' `interval: 1m` vorrebbe
# dire sei minuti di gate. Si accorcia la CADENZA, non la condizione — la soglia,
# la query e il datasource restano quelli spediti.
cp -R docker/grafana/provisioning "$TMP/provisioning"
python3 - "$TMP/provisioning/alerting/regole.yaml" <<'PY'
import sys, yaml
p = sys.argv[1]
d = yaml.safe_load(open(p))
attese = {"loki-giu", "loki-wal-disco-pieno", "loki-volume-pieno",
          "prometheus-volume-pieno", "collector-rifiuta"}
viste = {r["uid"] for g in d["groups"] for r in g["rules"]}
# L'oracolo sta QUI, non nel file sotto esame: l'elenco va cambiato a mano da chi
# aggiunge o toglie una regola, che e' il gesto che si vuole rendere visibile.
if viste != attese:
    sys.exit(f"FALLITO: le regole nel file sono {sorted(viste)}, attese {sorted(attese)}")
for g in d["groups"]:
    g["interval"] = "10s"
    for r in g["rules"]:
        r["for"] = "10s"
yaml.safe_dump(d, open(p, "w"))
PY

# --- (e) IL PROVISIONING SPEDITO, prima di qualunque mutazione.
# Il resto di questa prova monta una copia con `for` e `interval` accorciati, cioe'
# Grafana non parse MAI i byte che finiscono nell'immagine. Un `for: 5min` o
# qualunque refuso nei campi riscritti sarebbe invisibile qui e fatale la'. Venti
# secondi per chiuderlo, e senza la variabile del webhook: e' anche la guardia sul
# guasto peggiore trovato in revisione — con `SLACK_WEBHOOK_URL` assente Grafana
# NON PARTIVA AFFATTO, il modulo provisioning si portava dietro l'intero server.
echo -n "--- (e) il provisioning SPEDITO, senza SLACK_WEBHOOK_URL: "
docker rm -f allarmi-spedito >/dev/null 2>&1 || true
docker run -d --rm --name allarmi-spedito -p 3094:3000 \
  -e GF_SECURITY_ADMIN_PASSWORD="$PASSWORD" agentic-grafana:ci >/dev/null
spedito_pronto=""
for _ in $(seq 1 45); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3094/api/health)" = "200" ] && { spedito_pronto=si; break; }
  sleep 2
done
if [ -z "$spedito_pronto" ]; then
  echo "NON E' PARTITO"
  echo "FALLITO: (e) Grafana non parte con il provisioning spedito e senza SLACK_WEBHOOK_URL. In produzione sarebbe un crash loop, e questo servizio non ha healthcheckPath: nessuno lo direbbe. I suoi log:"
  docker logs allarmi-spedito 2>&1 | tail -20
  docker rm -f allarmi-spedito >/dev/null 2>&1 || true
  exit 1
fi
regole_spedite=$(curl -s -u "admin:$PASSWORD" http://localhost:3094/api/v1/provisioning/alert-rules \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
docker rm -f allarmi-spedito >/dev/null 2>&1 || true
if [ "${regole_spedite:-0}" != "5" ]; then
  echo "solo ${regole_spedite} regole"
  echo "FALLITO: (e) il provisioning SPEDITO carica ${regole_spedite} regole invece di 5: il resto di questa prova gira su una copia mutata e non lo vedrebbe"
  exit 1
fi
echo "parte, e carica 5 regole"

docker network create "$RETE" >/dev/null

# Alias di rete uguale al DNS interno di Railway: gli hostname dentro
# docker/prometheus.yml e dentro il datasource sono gli stessi in locale e in
# produzione, ed e' la regola di casa. Senza l'alias questa prova misurerebbe una
# topologia diversa da quella spedita — errore gia' pagato il 2026-08-20.
docker run -d --rm --name allarmi-prometheus --network "$RETE" \
  --network-alias prometheus.railway.internal \
  agentic-prometheus:ci \
  --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus >/dev/null

mkdir -p "$TMP/ricevute"
# `--user` con l'utente dell'HOST, e non e' un dettaglio: su Linux il container
# girerebbe come root e il file di notifiche nascerebbe root:root dentro una
# cartella dell'utente. La riscrittura dallo script fallirebbe con `Permission
# denied` — misurato in CI, dove e' andata rossa mentre in locale su macOS passava,
# perche' Docker Desktop rimappa la proprieta' e Linux no.
docker run -d --rm --name allarmi-ricevitore --network "$RETE" \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/scripts/ricevitore-di-prova.py:/ricevitore.py:ro" \
  -v "$TMP/ricevute:/ricevute" \
  "$IMG_PY" python /ricevitore.py >/dev/null

# Il ricevitore deve funzionare PRIMA di aspettarsi qualcosa da lui. Senza questa
# riga, un bind mount che non arriva nel container — su macOS $TMPDIR sta sotto
# /var/folders, che Docker non condivide sempre — si leggerebbe come "la notifica
# non e' mai arrivata": rosso giusto, diagnosi che manda a cercare in Grafana.
docker run --rm --network "$RETE" "$IMG_PY" python -c "
import urllib.request
urllib.request.urlopen(urllib.request.Request('$WEBHOOK', data=b'{\"prova\":\"avvio\"}', method='POST'), timeout=10).read()
" >/dev/null 2>&1 || { echo "FALLITO: il ricevitore di prova non risponde"; docker logs allarmi-ricevitore 2>&1 | tail; exit 1; }
sleep 1
[ -s "$TMP/ricevute/notifiche.jsonl" ] || { echo "FALLITO: il ricevitore ha risposto ma il file non arriva fuori dal container: il bind mount di $TMP non funziona su questa macchina"; exit 1; }
# La riga di avvio non deve contare come notifica.
: > "$TMP/ricevute/notifiche.jsonl"

# NESSUN container `loki` su questa rete: e' la realta' rotta della prova (d).
docker run -d --rm --name allarmi-grafana --network "$RETE" -p 3093:3000 \
  -v "$TMP/provisioning:/etc/grafana/provisioning:ro" \
  -e GF_SECURITY_ADMIN_PASSWORD="$PASSWORD" \
  -e SLACK_WEBHOOK_URL="$WEBHOOK" \
  agentic-grafana:ci >/dev/null

echo -n "attendo Grafana: "
for _ in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3093/api/health)" = "200" ] && { echo "pronto"; break; }
  sleep 2
done
[ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3093/api/health)" = "200" ] || {
  echo "FALLITO: Grafana non e' arrivato a servire"; docker logs allarmi-grafana 2>&1 | tail -30; exit 1; }

AUTH="admin:$PASSWORD"
curl -s -u "$AUTH" http://localhost:3093/api/v1/provisioning/alert-rules > "$TMP/regole.json"
curl -s -u "$AUTH" http://localhost:3093/api/v1/provisioning/contact-points > "$TMP/contatti.json"
curl -s -u "$AUTH" http://localhost:3093/api/datasources > "$TMP/datasource.json"

python3 - "$TMP/regole.json" "$TMP/contatti.json" "$TMP/datasource.json" <<'PY'
import json, sys
regole = json.load(open(sys.argv[1]))
contatti = json.load(open(sys.argv[2]))
datasource = json.load(open(sys.argv[3]))
fallimenti = []

ATTESE = {"loki-giu", "loki-wal-disco-pieno", "loki-volume-pieno",
          "prometheus-volume-pieno", "collector-rifiuta"}

# (a) Grafana logga gli errori di provisioning e PROSEGUE: una regola scritta male
#     non esiste e basta, senza nessun segnale.
caricate = {r["uid"] for r in regole} if isinstance(regole, list) else set()
mancanti = ATTESE - caricate
if mancanti:
    fallimenti.append(f"(a) Grafana NON ha caricato {sorted(mancanti)}: il provisioning fallisce in silenzio, la regola semplicemente non esiste")

# (b) ogni datasource nominato da una regola deve esistere davvero.
uid_esistenti = {d["uid"] for d in datasource} | {"__expr__"}
for r in (regole if isinstance(regole, list) else []):
    for q in r.get("data", []):
        if q.get("datasourceUid") not in uid_esistenti:
            fallimenti.append(f"(b) la regola {r['uid']} interroga il datasource {q.get('datasourceUid')!r}, che non esiste: non scattera' mai")

# Il contact point deve ESISTERE. Il suo url no: Grafana lo restituisce
# `[REDACTED]` perche' e' una credenziale, quindi confrontarlo qui sarebbe un
# controllo che non puo' passare mai. Che sia quello giusto lo dimostra
# l'asserzione (c), piu' sotto, ricevendo la notifica.
if not any(c.get("type") == "slack" for c in (contatti if isinstance(contatti, list) else [])):
    fallimenti.append("nessun contact point slack: gli allarmi non hanno dove andare")

if fallimenti:
    for f in fallimenti: print("FALLITO:", f)
    sys.exit(1)
print(f"ok: (a) {len(caricate)} regole caricate, (b) datasource tutti esistenti, e il contact point c'e'")
PY

echo -n "--- (d) attendo che 'Loki e' giu'' passi ad Alerting (Loki non esiste su questa rete): "
stato_loki=""
for tentativo in $(seq 1 40); do
  sleep 3
  curl -s -u "$AUTH" http://localhost:3093/api/prometheus/grafana/api/v1/rules > "$TMP/stato.json" || true
  stato_loki=$(python3 -c "
import json
d = json.load(open('$TMP/stato.json')).get('data', {}).get('groups', [])
for g in d:
    for r in g.get('rules', []):
        if r.get('labels', {}).get('__alert_rule_uid__') == 'loki-giu' or r.get('name') == \"Loki e' giu'\":
            print(r.get('state', '')); break
" 2>/dev/null || echo "")
  [ "$stato_loki" = "firing" ] && { echo "dopo $((tentativo * 3))s"; break; }
done
if [ "$stato_loki" != "firing" ]; then
  echo "MAI SCATTATA (stato: ${stato_loki:-sconosciuto})"
  echo "FALLITO: (d) 'up{job=\"loki\"} == 0' non ha fatto scattare la regola con Loki assente: la regola c'e' e non funziona, che e' peggio di non averla"
  docker logs allarmi-grafana 2>&1 | grep -iE 'alert|provision' | tail -20
  exit 1
fi

# Il CONTROLLO NEGATIVO. Senza, "tutte le regole scattano sempre" passerebbe per
# successo: e' la stessa forma dell'allow-list rotta nel verso "scarta tutto".
# NESSUNA REGOLA DEVE ESSERE IN ERRORE. Una query che Grafana non riesce a valutare
# non e' rumorosa: e' muta, e da fuori e' identica a una regola che sta zitta perche'
# va tutto bene. Grafana lo dice nel campo `health`, e nessuno lo leggeva.
malate=$(python3 -c "
import json
d = json.load(open('$TMP/stato.json')).get('data', {}).get('groups', [])
rotte = [(r.get('name'), r.get('health'), (r.get('lastError') or '')[:120])
         for g in d for r in g.get('rules', []) if r.get('health') == 'error']
for nome, salute, errore in rotte:
    print(f'{nome}: {salute} — {errore}')
")
if [ -n "$malate" ]; then
  echo "FALLITO: (d) regole che Grafana NON riesce a valutare:"
  echo "$malate"
  exit 1
fi

stato_prom=$(python3 -c "
import json
d = json.load(open('$TMP/stato.json')).get('data', {}).get('groups', [])
for g in d:
    for r in g.get('rules', []):
        if r.get('name', '').startswith('Prometheus cancella blocchi'):
            print(r.get('state', '')); break
")
echo "--- controllo negativo: 'Prometheus cancella blocchi...' e' in stato ${stato_prom:-sconosciuto}"
[ "$stato_prom" = "inactive" ] || {
  echo "FALLITO: (d) anche la regola sul TSDB e' in stato '${stato_prom}' su un Prometheus appena nato e vuoto: se scattano tutte, non sta discriminando niente"
  exit 1
}

echo "ok: (d) la regola giusta e' scattata su una realta' rotta, e quella sbagliata no"

echo -n "--- (c) attendo la notifica sul ricevitore locale: "
# `group_wait: 30s` sta nel file spedito e NON viene accorciato: la prova aspetta
# la cadenza vera invece di misurarne una diversa da quella che gira.
ricevute=0
for tentativo in $(seq 1 40); do
  sleep 3
  # `wc -l < file` con il file assente fa fallire il REDIRECT, che e' della shell:
  # `2>/dev/null` sta su wc e non lo copre, e sotto `set -e` lo script muore qui
  # invece di riprovare al giro dopo. Misurato: la prima versione moriva sul primo
  # tentativo, cioe' prima che la notifica potesse arrivare.
  ricevute=$(cat "$TMP/ricevute/notifiche.jsonl" 2>/dev/null | wc -l | tr -d ' ')
  # `if`, NON `[ ... ] && { ... }`: sotto `set -e` un `&&` che fallisce come ULTIMO
  # comando del corpo di un ciclo fa uscire lo script. Misurato: la prima versione
  # moriva alla PRIMA iterazione, con exit 1 e senza stampare niente — cioe' (c)
  # non veniva provata affatto e il rosso sembrava "la notifica non arriva".
  if [ "${ricevute:-0}" -gt 0 ]; then
    echo "arrivata dopo $((tentativo * 3))s"
    break
  fi
done
if [ "${ricevute:-0}" -eq 0 ]; then
  echo "MAI ARRIVATA"
  echo "FALLITO: (c) la regola e' scattata e la notifica non e' uscita. Le cause possibili sono tre e i log di Grafana qui sotto le distinguono: \$SLACK_WEBHOOK_URL non interpolato, politica che non instrada, contact point rotto."
  docker logs allarmi-grafana 2>&1 | grep -iE 'notif|contact|slack|alertmanager' | tail -20
  exit 1
fi
# LA NOTIFICA DEVE ESSERE QUELLA GIUSTA, E DEVE AVERE UN CORPO. Cercare "Loki"
# non bastava: TRE regole su cinque hanno "Loki" nel titolo, e nella topologia di
# questa prova anche `loki-volume-pieno` consegna — in NoData — un messaggio che
# contiene "Il volume di Loki", nella stessa finestra di group_wait. L'asserzione
# poteva quindi passare senza che la regola scattata avesse mai notificato.
# E il corpo va preteso: con due righe di template sbagliate (nomi Alertmanager
# dentro Grafana) le notifiche arrivavano con `text` VUOTO, cioe' senza nessuna
# delle descrizioni scritte in regole.yaml — e nessuno se ne accorgeva.
python3 - "$TMP/ricevute/notifiche.jsonl" <<'PYN'
import json, sys
righe = [r for r in open(sys.argv[1]) if r.strip()]
giusta = None
for r in righe:
    if "loki-giu" in r or "Loki e' giu'" in r:
        giusta = json.loads(r)
        break
if giusta is None:
    print("FALLITO: (c) sono arrivate %d notifiche e nessuna e' della regola scattata (`loki-giu`): l'asserzione stava per accettare quella sbagliata" % len(righe))
    sys.exit(1)
corpo = " ".join(a.get("text", "") for a in (giusta.get("attachments") or [])) + giusta.get("text", "")
if not corpo.strip():
    print("FALLITO: (c) la notifica giusta e' arrivata con il CORPO VUOTO: nessuna delle descrizioni scritte in regole.yaml raggiunge il destinatario (e' cosi' che si comportano i template di Alertmanager dentro Grafana)")
    sys.exit(1)
if "healthcheckPath" not in corpo:
    print("FALLITO: (c) il corpo non contiene la description della regola. Corpo: %s" % corpo[:300])
    sys.exit(1)
print("  ok: la notifica e' di `loki-giu` e porta la sua description")
PYN
echo "ok: (c) la catena regola -> politica -> contact point -> consegna regge, e \$SLACK_WEBHOOK_URL era interpolato"
