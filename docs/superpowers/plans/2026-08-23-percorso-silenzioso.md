# Chiudere il percorso silenzioso — piano

> **Per chi esegue:** i passi hanno la casella (`- [ ]`). Ogni passo si chiude
> misurando, non guardando il diff.

**Obiettivo:** togliere l'unico modo rimasto in cui un guasto di questa produzione
non lo dice a nessuno — una sonda che si appende, un job che muore per timeout, e un
notificatore che su quella conclusione tace.

**Architettura:** tre strati, dal più profondo al più superficiale. (1) Nessuna
chiamata di rete senza un tetto, così un guasto di rete diventa un rosso rumoroso in
secondi invece di un'attesa di ore. (2) Ogni job con un `timeout-minutes` esplicito,
così il tetto è dichiarato e non è il default di sei ore. (3) Il notificatore
sorveglia anche `cancelled`, che è la conclusione che GitHub dà a un job ucciso da
`timeout-minutes` — misurato, non dedotto.

**Perché in quest'ordine:** se si chiudesse solo (3) resterebbe il rumore di ogni
`cancel-in-progress`; se si chiudesse solo (1) e (2), il tetto raggiunto sarebbe
comunque silenzioso. Il primo strato rende il terzo raro, il terzo rende il primo
non necessario per essere avvisati.

---

## Cosa è stato misurato prima di scrivere questo piano

| fatto | misura |
|---|---|
| un job ucciso da `timeout-minutes` esce `cancelled` | ramo usa-e-getta, `mutation` con `timeout-minutes: 1` + `sleep 180` → `conclusion: cancelled`, e `notifica` è partita col job **skipped** |
| il default di `timeout-minutes` è 360 | doc del fornitore, verbatim: *"The maximum number of minutes to let a job run before GitHub automatically cancels it. Default: 360"* |
| il tetto della piattaforma è 6h e "il job fallisce" | pagina dei limiti — due frasi del fornitore che non concordano sulla conclusione, ed è il motivo per cui è stato misurato |
| `--connect-timeout` limita solo la connessione | manpage di curl: *"This only limits the connection phase"* |
| curl esce **28** su entrambi i timeout | eseguito contro un indirizzo che non risponde |
| job senza `timeout-minutes` | **21 su 21** |
| curl senza `--max-time` | **31** |

---

## Task 1: nessuna chiamata di rete senza tetto

**File:**
- Modifica: `scripts/verify-hub.sh` (i due `curl` verso Grafana e la status API)
- Modifica: `.github/workflows/smoke.yml`, `.github/workflows/sorveglianza.yml`,
  `.github/workflows/telemetry-baseline.yml`, `.github/workflows/security.yml`
  (i `curl` inline)
- Modifica: `scripts/prova-*.sh` (i `curl` verso i servizi locali)

- [ ] **Passo 1: provare che oggi si appende.** Un `curl` verso un indirizzo che
  ingoia i SYN non torna. Atteso: il comando resta appeso finché non lo si uccide.

```bash
timeout 5 curl -s https://10.255.255.1/x; echo "exit $?"   # 124 = ucciso da timeout, non da curl
```

- [ ] **Passo 2: aggiungere i due tetti a ogni curl.**
  `--connect-timeout 5` (la connessione, DNS e handshake compresi) e `--max-time 20`
  (l'intera operazione). Servono entrambi: il primo non limita il trasferimento, il
  secondo da solo aspetterebbe venti secondi anche su un host che non esiste.

- [ ] **Passo 3: provare che ora esce 28.**

```bash
curl -s --connect-timeout 5 --max-time 20 https://10.255.255.1/x; echo "exit $?"   # 28
```

- [ ] **Passo 4: contare che non ne resti nessuno senza tetto.**

```bash
grep -rn "curl " scripts/*.sh .github/workflows/*.yml | grep -v '^\s*#' | grep -v "max-time" | grep -c curl   # 0
```

## Task 2: ogni job dichiara il proprio tetto

**File:** tutti gli undici workflow in `.github/workflows/`, 21 job.

- [ ] **Passo 1: contare i job senza tetto.**

```bash
python3 - <<'PY'
import glob, yaml
senza = [(f, j) for f in glob.glob(".github/workflows/*.yml")
         for j, c in (yaml.safe_load(open(f)).get("jobs") or {}).items()
         if "timeout-minutes" not in c]
print(len(senza), "job senza timeout-minutes")
PY
```

- [ ] **Passo 2: scegliere i numeri dalla durata osservata, non a occhio.** Le durate
  reali dei check della #158: `compose` 2m59s, `images` 2m10s, `mutation-score`
  2m49s, `sonar` 1m25s, tutto il resto sotto il minuto. Tetto = circa cinque volte
  la durata osservata, arrotondato: **15** per i job che costruiscono o installano,
  **10** per le sonde di rete, **5** per il notificatore.

- [ ] **Passo 3: scrivere `timeout-minutes` su tutti e 21**, accanto a `runs-on`.

- [ ] **Passo 4: rieseguire il conteggio del Passo 1.** Atteso: `0`.

- [ ] **Passo 5: provare che il tetto morde**, su un ramo usa-e-getta mai unito: un
  job con `timeout-minutes: 1` e uno `sleep 180`, lanciato con `gh workflow run
  --ref`. Atteso: `conclusion: cancelled`.

## Task 3: il notificatore smette di tacere sul silenzio più probabile

**File:** Modifica `.github/workflows/notifica.yml`

- [ ] **Passo 1: rendere vero il commento.** Oggi dichiara che una sonda appesa
  arriva come `timed_out`: **è falso**, misurato — arriva come `cancelled`, e
  `cancelled` era escluso apposta. Il commento va riscritto con la misura.

- [ ] **Passo 2: aggiungere `cancelled` alle conclusioni sorvegliate**, e dichiarare
  il prezzo: un push su `main` che ne supera un altro cancella il run del primo e
  produrrà una notifica. È rumore vero, e si accetta perché il CLAUDE.md di questo
  repository tratta già i merge ravvicinati come un pericolo da guardare
  (`SKIPPED`/`REMOVED` significano "non è andato in produzione").

- [ ] **Passo 3: aggiornare il gate in `lint.yml`** — `ATTESA` contiene la
  condizione per intero, quindi cambiarla in `notifica.yml` senza toccare il gate lo
  fa diventare rosso. È il comportamento voluto: la costante è l'oracolo, e vive in
  un file diverso da quello sotto esame.

- [ ] **Passo 4: provare rosso e verde.** Il banco delle mutazioni deve restare
  12/12: la mutazione "una conclusione tolta dalla lista" ora si riferisce a quattro
  conclusioni invece di tre.

## Task 4: verifica end-to-end, sulla realtà rotta

- [ ] **Passo 1:** ramo usa-e-getta, `mutation` con `timeout-minutes: 1` e uno
  `sleep 180`, `gh workflow run mutation.yml --ref <ramo>`.
- [ ] **Passo 2:** attendere la conclusione. Atteso: `cancelled`.
- [ ] **Passo 3:** cercare il run di `notifica` corrispondente. Atteso: il job
  `slack` **eseguito** (non `skipped`) e `Slack ha risposto 200`.
- [ ] **Passo 4:** cancellare il ramo. `main` non deve mai diventare rosso.

---

## Cosa questo piano NON chiude, dichiarato

- **Un cron che non parte affatto.** `workflow_run` scatta quando un run finisce.
  Serve un dead man's switch, ed è una decisione di Marco, non di questo piano.
- **Chi notifica il notificatore.** Nessuno: i suoi due modi di morire sono quelli
  in cui un secondo tentativo morirebbe uguale.
- **La finestra cieca sul percorso pubblico** (15–92 minuti misurati su `smoke`).
  Si chiude spostando la sonda sul Worker del sito, che è un altro repository.
