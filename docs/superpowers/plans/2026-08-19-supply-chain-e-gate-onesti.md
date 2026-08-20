# Supply chain, e i gate che dicono la verità — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chiudere i buchi di supply chain trovati dall'audit del 19/08/2026 e allineare la documentazione ai controlli che esistono davvero, smettendo di affermarne di inesistenti.

**Architecture:** Nessun codice applicativo cambia. Si tocca la pipeline (`.github/workflows/`), la configurazione dei gate (`.checkov.yml`), un nuovo lockfile di sviluppo, e i documenti che oggi dichiarano più di quanto la pipeline faccia. Ogni gate nuovo o modificato si verifica **rosso e verde in locale** prima del commit, eseguendo lo script estratto dal YAML — non leggendolo.

**Tech Stack:** GitHub Actions, pip-tools (`pip-compile --generate-hashes`), Checkov, mutmut, Python 3.14.

**Branch:** impilato su `fix/prometheus-5gb-e-watchdog-che-richiama` (PR #86), perché #86 ha già modificato `images.yml` e `mutation.yml`. La PR di questo piano ha `--base` su quel ramo, non su `main`.

**Fuori scope, e perché:** il digest pinning delle cinque immagini base e la rigenerazione del lockfile su Python 3.14 sono reali e confermati, ma **ridispiegano servizi in produzione** (tutti e cinque il primo, `status-api` il secondo) su una coda a crediti. Sono decisione di Marco e vanno in una PR separata, dopo la sua conferma.

---

### Task 1: Gli strumenti della CI si installano con gli hash

**Perché:** sei righe in cinque workflow installano gli strumenti che sorvegliano la supply chain **senza** hash e **senza** `--only-binary=:all:`. Il primo livello è pinnato, la chiusura transitiva no: una sdist di `pluggy`/`packaging`/`stevedore` esegue il proprio `setup.py` sul runner. Nel job `sonar` quel runner ha `SONAR_TOKEN` nello step successivo. Il commento sopra quelle righe afferma il contrario di ciò che fanno.

**Files:**
- Create: `services/public-status-api/requirements-dev.in`
- Create: `services/public-status-api/requirements-dev.txt` (generato, mai a mano)
- Modify: `.github/workflows/tests.yml:73`, `.github/workflows/sonar.yml:83`, `.github/workflows/mutation.yml:42`, `.github/workflows/lint.yml:64`, `.github/workflows/lint.yml:87`, `.github/workflows/security.yml:135`
- Modify: `.github/dependabot.yml` (verificare che la entry `pip` su quella directory copra il nuovo file; se sì, nessuna modifica)

- [ ] **Step 1: scrivere `requirements-dev.in` con i pin già in uso**

```
# Gli strumenti che verificano la pipeline. Stessa regola delle dipendenze di
# produzione: hash-locked e solo wheel. Un pin di primo livello lascia la
# chiusura transitiva al caso, ed e' li' che sono atterrati gli incidenti PyPI.
# Rigenerare con:
#   pip-compile --generate-hashes --output-file requirements-dev.txt requirements-dev.in
bandit==1.9.2
httpx2==2.10.0
mutmut==3.6.0
pip-audit==2.10.1
pytest==9.1.1
pytest-cov==7.1.0
respx==0.23.1
ruff==0.15.4
zizmor==1.28.0
```

- [ ] **Step 2: generare il lockfile**

Run: `cd services/public-status-api && pip-compile --generate-hashes --output-file requirements-dev.txt requirements-dev.in`
Expected: file generato, ogni riga con `--hash=sha256:...`

- [ ] **Step 3: verificare che l'installazione con hash funzioni davvero**

Run: `python3 -m pip install --dry-run --require-hashes --only-binary=:all: -r services/public-status-api/requirements-dev.txt`
Expected: nessun errore. Se un pacchetto non ha wheel per la piattaforma, il piano cambia: annotarlo qui e usare `--only-binary` mirato invece che `:all:`, spiegando quale pacchetto e perché.

- [ ] **Step 4: sostituire le sei righe di install**

Ogni riga diventa, con il path relativo corretto per il `working-directory` di quel job:

```yaml
      - run: "pip install --require-hashes --only-binary=:all: -r services/public-status-api/requirements-dev.txt"
```

In `lint.yml:87` l'install di `zizmor` è dentro uno script multi-riga: sostituire solo quel comando, lasciando il resto dello step.

- [ ] **Step 5: correggere i commenti che affermavano il falso**

In `sonar.yml` e `tests.yml`, la frase *"Pinnate come tutto il resto della pipeline: erano l'unico install non deterministico rimasto"* va sostituita con la verità e la sua storia:

```yaml
      # Hash-locked come le dipendenze di produzione. La versione precedente
      # pinnava solo il primo livello e il commento qui sopra affermava che
      # bastasse: non bastava. Senza hash e senza --only-binary la chiusura
      # transitiva si risolve a runtime e una sdist esegue il proprio setup.py
      # sul runner — cioe' esattamente dove sono atterrati gli incidenti PyPI.
```

- [ ] **Step 6: verificare che i workflow siano ancora YAML validi**

Run: `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('ok')"`
Expected: `ok`

- [ ] **Step 7: zizmor**

Run: `zizmor --min-severity=high .github/workflows/`
Expected: `No findings to report.`

- [ ] **Step 8: commit**

```bash
git add services/public-status-api/requirements-dev.in services/public-status-api/requirements-dev.txt .github/workflows/
git commit -m "fix(catena): gli strumenti che sorvegliano la supply chain erano l'unico anello non bloccato"
```

---

### Task 2: Il gate di mutation non può più passare a vuoto

**Perché:** `set -uo pipefail` senza `-e`, più `mutmut run || true`: se mutmut non parte, `mutation-results.txt` resta vuoto, ogni `grep -c` restituisce 0 e lo step esce 0. Il gate conta solo gli esiti cattivi e non asserisce mai quello buono, quindi "nessun sopravvissuto" e "nessun mutante eseguito" sono lo stesso output. Il 19/08 questo gate è stato usato per dichiarare zero sopravvissuti: la dichiarazione valeva solo perché un umano ha visto scorrere 257 mutanti.

**Files:**
- Modify: `.github/workflows/mutation.yml` (lo step `mutmut sulle funzioni bloccanti`)

- [ ] **Step 1: aggiungere il pavimento, prima del ciclo sui sopravvissuti**

```bash
          # Un gate che conta solo i fallimenti non distingue "nessun sopravvissuto"
          # da "nessun mutante". Se mutmut non parte — cache sporca, dipendenze
          # sfasate, un errore di import — il file dei risultati e' vuoto, ogni
          # grep -c da' 0, e lo step esce verde avendo verificato NIENTE. Il
          # pavimento asserisce l'esito buono invece di limitarsi a non trovare
          # quello cattivo.
          generati=$(python3 -m mutmut results --all 2>/dev/null | grep -c "^    main\." || true)
          echo "mutanti generati: ${generati}"
          if [ "${generati}" -lt 100 ]; then
            echo "FAIL: mutmut ha prodotto ${generati} mutanti (attesi >=100)." >&2
            echo "Non e' un punteggio basso: e' un run che non ha girato." >&2
            exit 1
          fi
```

Nota: il numero 100 è un pavimento grossolano, non una soglia di qualità — al 19/08 i mutanti sono 257. Serve a distinguere "ha girato" da "non ha girato", e va scritto così nel commento.

- [ ] **Step 2: `set -euo pipefail`**

Sostituire `set -uo pipefail` con `set -euo pipefail`. Attenzione: `mutmut run || true` resta necessario (mutmut esce non-zero quando ci sono sopravvissuti, che è il caso normale che vogliamo *misurare*, non subire), e i `grep -c ... || true` restano perché `grep` esce 1 quando non trova nulla, che è l'esito **buono**.

- [ ] **Step 3: verificare il verde in locale**

Run:
```bash
cd services/public-status-api && rm -rf .mutmut-cache mutants && python3 -m mutmut run >/dev/null 2>&1; python3 -m mutmut results --all | grep -c "^    main\."
```
Expected: un numero >= 100 (al 19/08: 257).

- [ ] **Step 4: verificare il rosso**

Run: simulare un run morto — `printf '' > /tmp/vuoto.txt` e applicare la logica del pavimento a quel file.
Expected: `FAIL: mutmut ha prodotto 0 mutanti`.

- [ ] **Step 5: commit**

```bash
git add .github/workflows/mutation.yml
git commit -m "fix(gate): il gate di mutation non distingueva zero sopravvissuti da zero mutanti"
```

---

### Task 3: Il loop di build fallisce quando fallisce

**Perché:** `images.yml` costruisce cinque immagini in un ciclo con `set -uo pipefail` (senza `-e`). L'esito dello step è quello dell'**ultimo** comando: il fallimento della build di otel-collector, prometheus, grafana o cloudflared lascia lo step verde. Oggi il guasto è ancora intercettato, ma per caso e nel posto sbagliato (gli step Trivy successivi riferiscono tag che non esisterebbero). Il giorno che si aggiunge un'immagine senza uno step a valle che la nomini, il fallimento diventa muto.

**Files:**
- Modify: `.github/workflows/images.yml:64`

- [ ] **Step 1: `set -euo pipefail`**

La funzione di ritentata è `if docker build …; then`, che `-e` non altera: nessun altro comportamento cambia.

- [ ] **Step 2: commit**

```bash
git add .github/workflows/images.yml
git commit -m "fix(gate): la build di quattro immagini su cinque poteva fallire in silenzio"
```

---

### Task 4: Checkov guarda anche il compose, e i tag del compose sono pinnati

**Perché:** `framework: [dockerfile, github_actions]` — `docker/docker-compose.yml` non è scansionato da niente, e `scripts/check-image-users.sh` legge solo `USER`, mai `FROM`. La regola "ogni immagine pinnata, mai `:latest`" è quindi applicata ai Dockerfile e **a nulla** per il compose: un `:latest` lì passerebbe tutti e dodici i gate.

**Files:**
- Modify: `.checkov.yml`
- Modify: `.github/workflows/images.yml` (job `compose`, nuovo step)

- [ ] **Step 1: aggiungere il framework**

```yaml
framework:
  - dockerfile
  - github_actions
  - docker_compose
```

- [ ] **Step 2: eseguire Checkov e leggere cosa diventa rosso**

Run: `checkov --config-file .checkov.yml -d .`
Expected: da decidere sul risultato. Se compaiono check nuovi falliti, NON aggiungere `skip-check` globali: o si sistema il compose, o si mette lo skip inline con la ragione, come già fanno i Dockerfile. Annotare qui l'esito reale.

- [ ] **Step 3: gate esplicito sui tag del compose**

```yaml
      - name: le immagini del compose sono pinnate e non divergono dalla produzione
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import re, sys, pathlib

          compose = pathlib.Path("docker/docker-compose.yml").read_text()
          immagini = re.findall(r"^\s*image:\s*(\S+)", compose, re.MULTILINE)
          if not immagini:
              sys.exit("::error::nessuna `image:` trovata nel compose: il gate sta guardando il file sbagliato")
          for riferimento in immagini:
              if ":" not in riferimento or riferimento.endswith(":latest"):
                  sys.exit(f"::error::{riferimento} non e' pinnata a una versione")
          print(f"immagini del compose pinnate: {immagini}")
          PY
```

Il pavimento (`if not immagini`) c'è per la stessa ragione del Task 2: un gate che itera su una lista vuota passa sempre.

- [ ] **Step 4: verificare rosso e verde**

Verde: eseguire lo script estratto dal YAML sul repo così com'è.
Rosso: cambiare temporaneamente una `image:` in `grafana/grafana:latest`, rieseguire, attendersi exit 1, ripristinare.

- [ ] **Step 5: commit**

```bash
git add .checkov.yml .github/workflows/images.yml
git commit -m "fix(gate): il compose non era scansionato da niente, e un :latest ci sarebbe passato"
```

---

### Task 5: Un gate lega i `COPY` di un Dockerfile ai `watchPatterns` del suo servizio

**Perché:** è il guasto silenzioso del 13/08 messo per iscritto in `CLAUDE.md` e affidato a una **procedura manuale**. Se un Dockerfile copia un file che non compare nei `watchPatterns` del `railway.json` accanto, la modifica entra in `main` verde e Railway non ricostruisce quel servizio: produzione e `main` divergono in silenzio. Questo repo ha già un gate per ogni altro accoppiamento che l'ha morso; questo era rimasto scoperto, ed è quello che è già costato una giornata.

**Files:**
- Modify: `.github/workflows/images.yml` (job `compose`, nuovo step)

- [ ] **Step 1: lo step**

```yaml
      - name: ogni COPY di un Dockerfile e' coperto dai watchPatterns del suo servizio
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import fnmatch, json, pathlib, re, sys

          servizi = sorted(pathlib.Path("railway").glob("*/railway.json"))
          if not servizi:
              sys.exit("::error::nessun railway.json trovato: il gate sta guardando il posto sbagliato")

          problemi = []
          for config in servizi:
              dockerfile = config.parent / "Dockerfile"
              if not dockerfile.exists():
                  continue
              patterns = json.loads(config.read_text()).get("build", {}).get("watchPatterns", [])
              sorgenti = re.findall(r"^COPY\s+(?!--)(\S+)", dockerfile.read_text(), re.MULTILINE)
              for sorgente in sorgenti:
                  percorso = f"{config.parent}/{sorgente}" if not sorgente.startswith(("docker/", "services/", "railway/")) else sorgente
                  if not any(fnmatch.fnmatch(percorso, p) or fnmatch.fnmatch(percorso, p.rstrip("*") + "*") for p in patterns):
                      problemi.append(f"{config.parent.name}: COPY {sorgente} non e' coperto da {patterns}")

          if problemi:
              print("::error::un COPY non sorvegliato significa che Railway NON ricostruisce il servizio "
                    "quando quel file cambia: la modifica entra in main verde e la produzione resta indietro.")
              for p in problemi:
                  print(f"::error::{p}")
              sys.exit(1)
          print(f"watchPatterns coerenti con i COPY per {len(servizi)} servizi")
          PY
```

- [ ] **Step 2: verificare il verde sui quattro servizi attuali**

Run: script estratto dal YAML.
Expected: `watchPatterns coerenti con i COPY per 5 servizi` (o il numero reale). **Se esce rosso, non ammorbidire il gate**: significa che una divergenza esiste già, e va capita prima.

- [ ] **Step 3: verificare il rosso**

Aggiungere temporaneamente `COPY docker/inesistente.yaml /tmp/x` a `railway/prometheus/Dockerfile`, rieseguire, attendersi exit 1 con il nome del servizio, ripristinare.

- [ ] **Step 4: commit**

```bash
git add .github/workflows/images.yml
git commit -m "feat(gate): i watchPatterns non erano legati ai COPY, ed e' il guasto del 13/08"
```

---

### Task 6: I documenti smettono di affermare controlli che non esistono

**Perché:** è il posizionamento pubblico di questo progetto. `docs/DECISIONS.md` contiene già la regola: *"un controllo affermato che non esiste è peggio di un'assenza dichiarata"*, e il repo l'ha già applicata due volte (rate limiting, tariffe stale). L'audit ne ha trovate quattro ancora aperte.

**Files:**
- Modify: `README.md:131-132` (il gate che salta), `README.md:95-96` (cosa verifica `check-image-users.sh`), `README.md:98-99` (l'hook gitleaks), `.github/workflows/security.yml:95-96` + `.checkov.yml:14-17` (la politica di soft-fail)
- Modify: `docs/DECISIONS.md` (sezione CI, la stessa affermazione)

- [ ] **Step 1: `README.md` — il gate che salta**

Sostituire *"a gate whose credential is missing stays red instead of skipping"* con:

```markdown
Every gate **blocks**. Nothing runs with `continue-on-error`, and a gate whose
credential is missing stays red instead of skipping — **with one measured
exception**: `sonar` skips on pull requests that cannot receive secrets (forks and
Dependabot), because a token-less scan cannot pass and a permanently red required
check would block every merge. A skipped required check reads as green, which this
repository has already measured once; what is lost on those PRs is Sonar's own rule
set, not coverage — `tests` still enforces `--cov-fail-under=100` unconditionally,
and lint, bandit, checkov and gitleaks all still run.
```

- [ ] **Step 2: `docs/DECISIONS.md`** — stessa correzione nella sezione CI, con la data e il fatto che è stata trovata da un audit, non da un guasto.

- [ ] **Step 3: `README.md` — cosa verifica davvero `check-image-users.sh`**

La frase attribuisce a una guardia un controllo che non fa (verifica `USER`, non `FROM`). Riscrivere separando i due fatti: il non-root è verificato dallo script, il pinning dalle regole Checkov sui Dockerfile e — dopo il Task 4 — da un gate sul compose.

- [ ] **Step 4: `README.md` — l'hook gitleaks è opt-in**

`.githooks/pre-commit` non fa nulla finché non si esegue `git config core.hooksPath .githooks`. `CONTRIBUTING.md` lo dice correttamente; il README no. Aggiungere "opt-in" e il comando.

- [ ] **Step 5: la politica di Checkov**

Run: `checkov --config-file .checkov.yml -d .` e leggere cosa blocca davvero. `soft-fail-on: [LOW, MEDIUM]` si appoggia a metadati di severità che Checkov open-source non ha senza chiave di piattaforma. Se la severità non c'è, il comportamento reale è "fallisce su qualunque check fallito": in quel caso sostituire `soft-fail-on` con ID di check espliciti **oppure** correggere il commento per dire cosa blocca davvero. Non lasciare una politica non verificabile.

- [ ] **Step 6: commit**

```bash
git add README.md docs/DECISIONS.md .checkov.yml .github/workflows/security.yml
git commit -m "docs(onesta): quattro controlli affermati che la pipeline non ha"
```

---

### Task 7: Verifica finale e revisione

- [ ] **Step 1:** `cd services/public-status-api && python3 -m pytest test_main.py -q --cov=. --cov-report=term` — 100%, tutti verdi.
- [ ] **Step 2:** `ruff check . && ruff format --check .` — pulito.
- [ ] **Step 3:** `zizmor --min-severity=high .github/workflows/` — nessun rilievo.
- [ ] **Step 4:** `docker compose -f docker/docker-compose.yml config --quiet` — nessun errore.
- [ ] **Step 5:** eseguire **ogni** gate nuovo estraendolo dal YAML, non leggendolo.
- [ ] **Step 6:** REQUIRED SUB-SKILL `superpowers:requesting-code-review` sul diff completo, con contratto di output esplicito.
- [ ] **Step 7:** REQUIRED SUB-SKILL `superpowers:verification-before-completion` prima di dichiarare finito.
- [ ] **Step 8:** aprire la PR con `--base fix/prometheus-5gb-e-watchdog-che-richiama`.

---

## Rimandato a decisione di Marco (non eseguire senza conferma)

1. **Digest pinning delle cinque immagini base.** Confermato come il controllo di provenance che ha senso qui: questo repo non pubblica artefatti — Railway costruisce dai Dockerfile e non esiste un digest pubblicato contro cui verificare una firma — quindi SBOM e attestation sarebbero teatro, mentre un tag è un puntatore mutabile e il digest no. Costo: ridispiega **tutti e cinque** i servizi.
2. **Rigenerare `requirements.txt` su Python 3.14.** L'header dice 3.12 mentre produzione e CI girano 3.14, quindi il lock è la chiusura di un interprete che non è quello che esegue. Aggiungere l'header come settimo posto sorvegliato da `scripts/check-python-versions.sh`. Costo: ridispiega `status-api`.
3. **`runs-on: ubuntu-latest`.** Lasciato apposta: GitHub non offre runner pinnati per digest, ed è la sua supply chain, non la nostra. `ubuntu-24.04` toglierebbe le migrazioni a sorpresa, ma non è una correzione di sicurezza e non va spacciata per tale.
