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
| curl senza `--max-time` | **26 invocazioni** — il primo conteggio diceva 31, ma era per riga fisica: 26 reali + 3 che il tetto ce l'avevano sulla continuazione (e che il fix ha reso doppi) + 2 righe di commento |

---

## Task 1: nessuna chiamata di rete senza tetto

**File:**
- Modifica: `scripts/verify-hub.sh` (i due `curl` verso Grafana e la status API)
- Modifica: `.github/workflows/smoke.yml`, `.github/workflows/sorveglianza.yml`,
  `.github/workflows/telemetry-baseline.yml`, `.github/workflows/security.yml`
  (i `curl` inline)
- Modifica: `scripts/prova-*.sh` (i `curl` verso i servizi locali)

- [x] **Passo 1: provare che oggi si appende.** Un `curl` verso un indirizzo che
  ingoia i SYN non torna. Atteso: il comando resta appeso finché non lo si uccide.

```bash
timeout 5 curl -s https://10.255.255.1/x; echo "exit $?"   # 124 = ucciso da timeout, non da curl
```

- [x] **Passo 2: aggiungere i due tetti a ogni curl.**
  `--connect-timeout 5` (la connessione, DNS e handshake compresi) e `--max-time 20`
  (l'intera operazione). Servono entrambi: il primo non limita il trasferimento, il
  secondo da solo aspetterebbe venti secondi anche su un host che non esiste.

- [x] **Passo 3: provare che ora esce 28.**

```bash
curl -s --connect-timeout 5 --max-time 20 https://10.255.255.1/x; echo "exit $?"   # 28
```

- [x] **Passo 4: contare che non ne resti nessuno senza tetto — unendo le
  continuazioni di riga.** Il comando scritto qui la prima volta era
  `grep -rn ... | grep -v '^\\s*#' | grep -c curl`, ed era **cieco da entrambi i
  lati**: `grep -rn` antepone `file:riga:`, quindi il filtro sui commenti non poteva
  mai matchare; e contando per riga fisica non vedeva ne' le invocazioni spezzate su
  piu' righe, ne' i tre `--max-time` finiti in doppio perche' il flag stava sulla
  continuazione. Il conteggio giusto unisce le continuazioni prima di guardare, ed e'
  esattamente cio' che fa il gate del Task 5 — che quindi e' anche l'oracolo di questo
  passo. Misurato dopo le correzioni: **32 invocazioni, tutte con esattamente un
  `--max-time` e un `--connect-timeout`**.

## Task 2: ogni job dichiara il proprio tetto

**File:** tutti gli undici workflow in `.github/workflows/`, 21 job.

- [x] **Passo 1: contare i job senza tetto.**

```bash
python3 - <<'PY'
import glob, yaml
senza = [(f, j) for f in glob.glob(".github/workflows/*.yml")
         for j, c in (yaml.safe_load(open(f)).get("jobs") or {}).items()
         if "timeout-minutes" not in c]
print(len(senza), "job senza timeout-minutes")
PY
```

- [x] **Passo 2: scegliere i numeri dalla durata osservata, non a occhio.** Le durate
  reali dei check della #158: `compose` 2m59s, `images` 2m10s, `mutation-score`
  2m49s, `sonar` 1m25s, tutto il resto sotto il minuto. Tetto = circa cinque volte
  la durata osservata, arrotondato: **15** per i job che costruiscono o installano,
  **10** per le sonde di rete, **5** per il notificatore.

- [x] **Passo 3: scrivere `timeout-minutes` su tutti e 21**, accanto a `runs-on`.

- [x] **Passo 4: rieseguire il conteggio del Passo 1.** Atteso: `0`.

- [ ] **Passo 5: provare che il tetto morde**, su un ramo usa-e-getta mai unito: un
  job con `timeout-minutes: 1` e uno `sleep 180`, lanciato con `gh workflow run
  --ref`. Atteso: `conclusion: cancelled`.

## Task 3: il notificatore smette di tacere sul silenzio più probabile

**File:** Modifica `.github/workflows/notifica.yml`

- [x] **Passo 1: rendere vero il commento.** Oggi dichiara che una sonda appesa
  arriva come `timed_out`: **è falso**, misurato — arriva come `cancelled`, e
  `cancelled` era escluso apposta. Il commento va riscritto con la misura.

- [x] **Passo 2: aggiungere `cancelled` alle conclusioni sorvegliate**, e dichiarare
  il prezzo: un push su `main` che ne supera un altro cancella il run del primo e
  produrrà una notifica. È rumore vero, e si accetta perché il CLAUDE.md di questo
  repository tratta già i merge ravvicinati come un pericolo da guardare
  (`SKIPPED`/`REMOVED` significano "non è andato in produzione").

- [x] **Passo 3: aggiornare il gate in `lint.yml`** — `ATTESA` contiene la
  condizione per intero, quindi cambiarla in `notifica.yml` senza toccare il gate lo
  fa diventare rosso. È il comportamento voluto: la costante è l'oracolo, e vive in
  un file diverso da quello sotto esame.

- [x] **Passo 4: provare rosso e verde sull'oracolo GIUSTO.** Questo passo diceva
  "il banco delle mutazioni deve restare 12/12", e quel banco non c'entra: le dodici
  funzioni bloccanti di `mutation.yml` sono tutte Python della status API, e nessun
  mutante tocca l'`if:` di un workflow. L'oracolo vero e' `ATTESA` dentro il gate
  `notifica.yml resta la forma sicura di workflow_run`, che confronta la condizione per
  intero e vive in un file diverso da quello sotto esame. Verde dopo la modifica;
  rosso se si cambia l'`if:` senza toccare `ATTESA`.

## Task 4: verifica end-to-end, sulla realtà rotta — SOLO DOPO IL MERGE

> **Vincolo d'ordine, che questo piano non dichiarava.** `workflow_run` valuta
> `notifica.yml` **dal ramo di default**: finché il fix vive su un ramo, la condizione
> viva è ancora quella a tre conclusioni, e questa prova misurerebbe il comportamento
> vecchio. L'unica prova che esiste oggi (run `32637543305`, `cancelled`, job `slack`
> uscito `skipped`) documenta proprio quello. Quindi finché queste caselle sono aperte,
> "il notificatore ora parla su `cancelled`" è **affermata e non misurata** — che è ciò
> che questo repository chiama difetto.

- [x] **Passo 1:** ramo usa-e-getta, `mutation` con `timeout-minutes: 1` e uno
  `sleep 180`, `gh workflow run mutation.yml --ref <ramo>`.
- [x] **Passo 2:** attendere la conclusione. Atteso: `cancelled`.
- [x] **Passo 3:** cercare il run di `notifica` corrispondente. Atteso: il job
  `slack` **eseguito** (non `skipped`) e `Slack ha risposto 200`.
- [x] **Passo 4:** cancellare il ramo. `main` non deve mai diventare rosso.

**MISURATO il 24/08/2026, dopo il merge della #159.** Ramo `prova/notifica-cancelled`,
cancellato subito dopo; `main` non e' mai diventato rosso.

| cosa | atteso | osservato |
|---|---|---|
| conclusione di `mutation` (run `32681358781`) | `cancelled` | `cancelled` |
| job `slack` di `notifica` (run `32681432069`) | eseguito, non `skipped` | `success` |
| la conclusione che il notificatore ha letto | `cancelled` | `CONCLUSIONE: cancelled` |
| risposta del webhook | `200` | `Slack ha risposto 200: ok` |

La frase "il notificatore ora parla su `cancelled`" smette qui di essere affermata.

Prima di questa misura l'unica prova esistente documentava il comportamento **vecchio**,
e sta in **due** run come quella nuova — attribuirla a uno solo era un errore di questo
documento, corretto il 24/08: `mutation` run `32637543305` uscito `cancelled`, e
`notifica` run `32637604133` con il job `slack` **`skipped`**. Le due coppie insieme
sono il prima e il dopo dello stesso guasto.

**Cosa questa misura NON copre, perche' "misurato end-to-end" senza il residuo dichiarato
sarebbe la stessa mezza verita' che sta correggendo.** La condizione di `notifica.yml` ha
due `contains()`: la conclusione e l'**evento**. Qui e' stato esercitato
`cancelled` x `workflow_dispatch`. Il caso per cui il file esiste — un cron notturno che
si appende — e' `schedule`, e non e' mai stato osservato: su 106 run di `notifica` il job
`slack` ha girato due volte in tutto, entrambe con `EVENTO: workflow_dispatch`. Il rischio
residuo e' basso (i due valori stanno in liste letterali nella stessa espressione, e il
ramo non-default e' invece coperto: il run di `notifica` e' partito da `main`), ma non e'
zero e non e' misurato.

## Task 5: le due invarianti nuove diventano sorvegliate

Non era in questo piano: e' arrivato da una revisione avversaria. `21/21` job hanno il
tetto **oggi**; il 22° no, e niente diventa rosso. Una convenzione che vive solo in un
file di piano e' un desiderio — il README di questo repository lo dice con parole sue.

> **RIMOSSA META' DI QUESTO TASK IL 24/08/2026, e le caselle qui sotto vanno lette con
> questa riga davanti.** Il controllo sui curl non esiste piu': in quattro giri il suo
> regex ha sbagliato in entrambe le direzioni — lasciava passare `curl ... && git commit
> -m "x"` e bocciava `curl -sS "https://x?a=1&b=2"` — perche' non si puo' decidere se
> una `&` separa o e' un dato senza interpretare il quoting. La motivazione per esteso
> sta in `.github/workflows/lint.yml`, sopra lo step `ogni job dichiara il proprio
> tetto`. **Resta solo il controllo sui `timeout-minutes`.** Lasciare queste caselle
> spuntate senza dirlo faceva credere a chi legge che un gate fermera' il prossimo curl
> senza tetti: un controllo affermato che non esiste, cioe' la cosa che questo
> repository considera peggiore di un buco dichiarato.

- [x] **Passo 1:** in `lint.yml`, dentro il job `workflow-lint` che gia' carica ogni
  workflow con `yaml.safe_load`, un controllo che pretende `timeout-minutes` su ogni
  job ~~e, su ogni invocazione di `curl` (continuazioni unite), **esattamente un**
  `--max-time` piu' `--connect-timeout`~~ (seconda meta' rimossa il 24/08).
- [x] **Passo 2: il gate e' nato rotto, e va detto** — matchava la parola `curl` dentro
  il proprio codice sorgente (il nome dello step, le stringhe dei messaggi, il regex
  stesso), quindi era rosso sulla realta'. Ancorato alla posizione di comando, che e'
  la stessa classe imparata lo stesso giorno su un altro hook.
- [x] **Passo 3: verde sulla realta', rosso su sei rotture** — job nuovo senza tetto,
  ~~curl nuovo senza tetto, `--max-time` doppio, `--connect-timeout` mancante~~, e i due
  pavimenti ~~(meno di 15 job, meno di 20 curl)~~. Dal 24/08 le rotture sorvegliate sono
  tre e il pavimento uno solo: `meno di 15 job`. Il banco versionato che le esercita —
  `scripts/prova-gate-workflow.py`, 7 casi e 3 mutanti, in CI — e' nato lo stesso
  giorno, perche' "provato rosso a mano" e' un ricordo, non un oracolo.

---

## Cosa questo piano NON chiude, dichiarato

- **Un cron che non parte affatto.** `workflow_run` scatta quando un run finisce.
  Serve un dead man's switch, ed è una decisione di Marco, non di questo piano.
- **Chi notifica il notificatore.** Nessuno: i suoi due modi di morire sono quelli
  in cui un secondo tentativo morirebbe uguale.
- **La finestra cieca sul percorso pubblico** (15–92 minuti misurati su `smoke`).
  Si chiude spostando la sonda sul Worker del sito, che è un altro repository.
