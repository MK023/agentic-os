# CLAUDE.md — agentic-os

> Project memory, loaded every turn. Short and dense. General rules — PRs, baseline
> security, the two MUST models, what counts as proof — live in the global `CLAUDE.md`
> and in `~/.claude/rules/`; here only what is specific to this repo. A section that
> grows becomes a file with a path pointer.

<!--
CRITERIO DI QUESTO FILE, 25/08/2026 — e' il modello per gli altri repo di Marco.

Tre caselle, e ogni riga sta in una sola:
  su Marco .................... ~/.claude/CLAUDE.md      ("non decidere per lui")
  su come si fa un progetto ... ~/.claude/rules/*.md     ("branch e PR", OWASP per strato)
  su QUESTO repo .............. questo file              ("sei servizi su Railway")

Due criteri di taglio, entrambi verificati alla fonte il 25/08:
  - il fornitore dichiara "target under 200 lines per CLAUDE.md file. Longer files
    consume more context and reduce adherence" (code.claude.com/docs/en/memory). E' un
    TARGET, non un taglio: il limite duro e' 4 MiB, sopra il quale il file viene ignorato.
  - `/doctor` propone i tagli con il criterio giusto: via cio' che si deduce dal codice
    (layout, dipendenze, architettura), restano trabocchetti, motivazioni e convenzioni
    che divergono dai default. Applicato qui a mano.

E questi commenti HTML sono GRATIS *qui*, con un limite che va copiato insieme al resto:
la doc garantisce lo stripping SOLO per i file CLAUDE.md ("Block-level HTML comments in
CLAUDE.md files are stripped before the content is injected"). Nei `rules/` e nei
`SKILL.md` vanno contati come testo, e otto blocchi esistono gia' la' dentro. Restano
comunque per chi apre il file o lo legge con Read, quindi il ragionamento non paga
l'affitto a ogni sessione. Dei 16 CLAUDE.md di PROGETTO sotto ~/GitHub nessuno li usava
prima di oggi; il `~/.claude/CLAUDE.md` di Marco si', dallo stesso giorno.

Righe, non parole: il file va a capo a ~88 colonne, quindi il conteggio righe e il costo
in token non sono la stessa misura. Non allargare il wrap per far tornare il numero.

Come si sposta una riga, in quest'ordine e non in un altro:
  1. prima si SCRIVE nella casella di destinazione, poi si cancella dall'origine — mai il
     contrario: fra i due passi il fatto non esiste da nessuna parte, e una sessione che
     si interrompe li' lo perde;
  2. si rilegge la destinazione e si verifica che si carichi QUANDO SERVE — un file con
     `paths:` si innesca sulla LETTURA, quindi una regola che vale mentre SCRIVI non ci va;
  3. un puntatore vale solo se il documento puntato contiene l'ARGOMENTO, non la parola:
     si apre e si legge il paragrafo, non si fa grep;
  4. un'affermazione di misura porta numero, data e dove sta la prova, o non e' tale.

Collaudo prima della PR: `git show HEAD:CLAUDE.md` accanto al nuovo, e per OGNI
affermazione del vecchio si dichiara dove e' finita — stessa casella, altra casella,
documento puntato, o tagliata di proposito perche' deducibile dal codice. Una riga che non
entra in nessuna delle quattro e' una perdita, non un taglio. Il conteggio va nella PR.

Cosa non lascia MAI il CLAUDE.md del repo: i numeri misurati con la loro data, i nomi
propri (servizi, host, workflow, variabili) e i trabocchetti che nominano il guasto che
prevengono. Salgono in `rules/` solo le frasi vere in OGNI progetto, e quando salgono i
fatti che le hanno pagate restano giu'.

Dove e' arrivato questo file, misurato il 25/08: da 241 righe / 2648 parole a 228 righe /
2345 parole iniettate (i commenti non contano). Sopra il target di 200, e
consapevolmente: quello che resta sono misure con data e trabocchetti. Non si taglia una
misura per far scendere un numero — se un repo arriva sotto 200, tanto meglio; se ci
arriva togliendo motivazioni, ha applicato il criterio al rovescio.

Collaudo eseguito: 132 identificatori e fatti del vecchio file confrontati uno a uno,
zero perdite. Il conto sta nella PR.

NOTA 25/08: il terzo strato (`~/.claude/rules/`) e' rimandato per decisione di Marco, e
finche' non esiste queste lezioni restano qui sotto, dove si vedono solo aprendo questo
repo. E' un limite dichiarato, non una svista.
-->

## What this is

**Personal observability hub for AI-assisted work**, six services on **Railway**: OTel
Collector → Prometheus → Grafana, a tiny FastAPI status API behind a Cloudflare Tunnel,
cloudflared, and — since Phase 1.5 — `loki`. The
public widget lives in the `marcobellingeri.dev` repo. **Loki's chunks and index live on
Cloudflare R2**, so a full Railway volume cannot take them with it; the local disk holds
only the active index and the `tsdb_shipper` cache.

**Live since 2026-08-21**, and what that deploy *proved* rather than assumed: Loki wrote
an object to R2 on startup, so the credentials, the schemeless endpoint and
`use_thanos_objstore: true` all work in production; and the 5 GB volume mounted without
the `permission denied` that `RAILWAY_RUN_UID=0` exists to prevent. The log path proved
itself later the same day, once sessions were relaunched with `OTEL_LOGS_EXPORTER=otlp`
— that switch is on the **client**, so on a machine without it the store stays empty,
and an empty store looks exactly like a broken one.

The priority here is **the public/private boundary**: three aggregate numbers are
public, everything else stays inside the project.

## Layout — the part `ls` does not explain

<!--
Il criterio e' quello di `/doctor`: si taglia cio' che si deduce dal codice (elenchi di
cartelle, di dipendenze, panoramiche di architettura) e si tiene cio' che nessun `ls`
dice — trabocchetti, motivazioni, convenzioni che divergono dai default. Le quattro
cartelle si vedono; il fatto che `docker/` sia l'UNICA copia no.
-->

- `railway/` = production (one Dockerfile + one `railway.json` per service);
  `railway/README.md` is the deployment guide — **start there** for infra.
- **`docker/` is the single source of every configuration file** the Railway images copy
  in, not just the local environment. `docker/.env` is fake secrets, never in git.
- `services/public-status-api/` is the only application code, and the only place
  coverage/mutation thresholds apply.
- `scripts/verify-hub.sh` is the fuller post-deploy check (Grafana behind Access + the
  status API's bearer). Run it by hand after a deploy — and know `sorveglianza.yml` also
  runs it daily at 05:23 UTC, its three secrets existing since 2026-08-20, so "manual"
  stopped meaning "nothing else runs it".

## What watches production

- `.github/workflows/smoke.yml` — the automatic half beside `sorveglianza.yml`, and no
  longer public-endpoint only: seven assertions against the OTLP ingest (three requiring `401` from the
  Collector on a bad bearer, four requiring `403` from the WAF rule in front of it) plus
  the public endpoint, which fails on `null` *and* on `stale`, never on the HTTP status
  alone.
- **Railway ignores the Dockerfile `HEALTHCHECK`**; it uses its own `healthcheckPath`,
  declared since 2026-08-20 on status-api (`/healthz`) and cloudflared (`/ready`) and
  **requiring a `PORT` service variable to work at all**. On the other four (`loki`
  included since Phase 1.5, same reason written in `railway/loki/Dockerfile`) `SUCCESS`
  still only means "the container started" — **a closed decision, not a gap**: their
  health routes would be unauthenticated, and a Prometheus `up` scrape watches each of
  them every 15s for as long as they live, which is more than a healthcheck does
  (Railway stops probing once the deploy is live).
- **Six scrape jobs since 2026-08-20. The `up`-scrape claim just above was false for
  months before that**: `docker/prometheus.yml` scraped the Collector, cloudflared and
  itself, never Grafana, and the Loki job did not exist while this file already claimed
  it did. The Collector has two jobs (`:8889` exported, `:8888` internal); `status-api`
  is deliberately not scraped, being the one service with both a `healthcheckPath` and
  an outside witness in `smoke.yml`. **An asserted control that is absent is worse than
  a declared gap: the reader stops looking for it** — and an absence still declared
  after it has been filled is the same mistake wearing the opposite face.

## Commands

```bash
docker build -f railway/prometheus/Dockerfile -t p .   # same for the other five
docker compose -f docker/docker-compose.yml config --quiet   # the 9 env vars only silence warnings; it exits 0 without them
bash scripts/prova-privacy-log.sh                              # gate: runs the log privacy proof, ~90s, Docker + python3 con PyYAML
bash scripts/prova-ritenzione-loki.sh                          # gate: delete route closed AND retention still running, ~60s, Docker + python3 con PyYAML
bash scripts/prova-allarmi.sh                                  # gate: rules load, fire on a broken reality, and notify, ~2m, Docker + python3 con PyYAML
python3 scripts/prova-gate-workflow.py                         # gate: the two lint.yml workflow gates, 7 cases + 3 mutants + both gates' declared-inspection check, ~4s (measured), python3 con PyYAML
cd services/public-status-api && pytest test_main.py -q --cov=. --cov-report=term
pip-audit -r services/public-status-api/requirements.txt       # gate: any advisory fails
zizmor --min-severity=high .github/workflows/                  # gate: blocks on HIGH
checkov --config-file .checkov.yml -d .
cd services/public-status-api && mutmut run && mutmut results  # 0 survivors in the 12 blocking functions (mutation.yml names them)
```

Dependencies are hash-locked: edit `requirements.in`, then
`pip-compile --generate-hashes --output-file requirements.txt requirements.in`.
Never hand-edit `requirements.txt`.

To validate the Collector config without a Docker daemon, download the real
`otelcol-contrib` binary and run `otelcol-contrib validate --config=file:...` —
`docker compose config` does **not** check it.

## How we work here

- **Autonomy stops before real infrastructure**: creating Railway services, setting
  their variables, `cloudflared tunnel create` and the Cloudflare Access policies are
  Marco's, always. Writing and validating the code for them is not.
- **The house lesson**: twelve bugs in the original plan survived an execute-everything
  pass and died to a read-the-docs pass. (The rule it proves is global rule 7.)
- **MUST: deploys on this project queue and take time** — and since 2026-08-19 the plan
  is Hobby, billed on usage, not the free credits this line used to name. Never merge a
  run of PRs back to back and call it done. A push arriving while a build is in flight
  **cancels** it, and the replacement deploy is `SKIPPED` if its own commit misses that
  service's `watchPatterns` — so the change lands on `main` and never reaches
  production, green everywhere, silently. That happened on 2026-08-13: #39 bumped
  Prometheus, #40 landed 72 seconds later, production stayed on the old image. **After
  merging more than one PR, check the running commit of each affected service**
  (`list-deployments` → newest `SUCCESS` and its `commitHash`). `SKIPPED` and `REMOVED`
  are the two statuses that mean "did not ship".
- **What to look for in the Collector's log while a real payload flows: `failed to
  execute statement`.** On 2026-08-21 `set(log.trace_id.string, "")` failed on **every
  record**, at execution time, once per record (`trace ids must be 32 hex characters` —
  the setter goes through `ParseTraceID`). The config parses, so `validate` exits 0 and
  the Collector starts: the twelve blocking gates (`CONTRIBUTING.md`) were green, a hand
  review missed it, and so did a proof that *executes*. The log line was the only
  witness. **A config that parses is not a config that executes.**
- **Log volume, two numbers from 2026-08-21 and two different measurements** — do not
  read one as a subset of the other: Grafana emitted **1823 lines in 35 seconds**,
  measured locally on the shipped image; and separately, in production, Railway reported
  **676 messages dropped** in a two-second window, because it discards above **500
  lines/second per replica**. **Measure on the image that ships, and remember the
  platform has limits the laptop does not** — the local run could not have produced the
  second number.

## Conventions

- Metric names live in three places — the Collector's `translation_strategy`,
  `docker/grafana/dashboards/claude-code.json`, and `QUERIES` in `main.py`. Change one,
  change all three.
- Every image and action is pinned to a version or SHA. Never `:latest`.
- Comments say *why*, and name the failure they prevent.
- **There is exactly one copy of every configuration file**, in `docker/`. The Railway
  images copy them at build time, the compose file mounts them, and compose gives each
  container a network alias equal to its Railway internal DNS name
  (`<service>.railway.internal`) so hostnames inside those files are correct in both
  places. Never fork a config to "fix it for local".

## Security (non-negotiable)

- Public surface is exactly three numbers. No free-form PromQL, no session content.
- OTLP ingest authenticates **inside the Collector** (`bearertokenauth`): a public
  Tunnel hostname is not an access control (CVE-2026-28798 pattern).
- Prometheus never gets a Tunnel hostname. **Not** because it cannot authenticate — it
  supports TLS and basic auth via `--web.config.file`, verified against the vendor's
  security page — but because it is not configured to, and no route means nothing to
  authenticate. Do not restate the old, false reason.
- Secrets are Railway service variables in production, `docker/.env` locally (fake
  values, gitignored), and GitHub/Worker secrets elsewhere. Never in git.

## What NOT to do (closed decisions — don't reopen without new data)

- **No Kubernetes/K3s, no Docker Swarm, no self-hosted Langfuse, no LangChain, and no
  going back to a VPS** (that last decided 2026-07-29). Six small services for one user:
  an orchestrator buys nothing, and the point of this platform is that there is no
  machine to maintain. The full argument is in `docs/DECISIONS.md`.
- **Never put the `prometheus` exporter in a `logs` pipeline** — metrics-only, the
  Collector refuses to start, so the *metrics* stop with it and the three public numbers
  freeze. Since Phase 1.5 `.github/workflows/images.yml` reads the pipeline and fails on
  that exporter; until then it was a sentence in a file no gate reads. **A rule that is
  watched and a rule that is merely written look identical on the page and behave
  nothing alike.**
- **Never turn the label allow-list into a delete-list**, and never assume
  `resource_to_telemetry_conversion: false` does that job: Claude Code sends identity as
  *data point* attributes, so a real email address reaches Prometheus without the
  processor (measured, not theorised). A delete-list fails open on every attribute a
  future release invents; the allow-list drops it by default. Adding a producer means
  adding its labels deliberately.
- **Never delete `session.id`** along with the identity attributes: the counters are
  cumulative per process, so without it concurrent sessions collapse into one series and
  the last export wins — two sessions read as one.
- **Never reduce the log path to one barrier, and never move the catch-all.** Two
  allow-lists on purpose: `transform/log-allowlist` in the Collector — **three**
  statements, `resource`, `scope` *and* `log`, all `keep_keys` — and `otlp_config` in
  `docker/loki.yaml` with `ignore_defaults: true` plus a `drop` catch-all **last** in
  all three sections. **Both govern the OTLP path only**: measured 2026-08-21, Loki's
  native `POST /loki/api/v1/push` walks past both with arbitrary labels, so the claim is
  "the Collector cannot leak identity", not "nothing can" (`SECURITY.md` has the
  measurement). The `scope` statement looks droppable and is not: without it the scope
  attributes crossed the Collector untouched and only Loki's list stopped them, so the
  two "independent" barriers were not independent — measured 2026-08-20 by running
  `scripts/prova-privacy-log.sh`, invisible to any gate that reads the config's shape.
  Identity rides on the **log records**, not the resource; the vendor sends it on every
  event with no environment variable to turn it off, so the allow-list is the only
  control on it, not a backup for one. Redaction of `prompt` and `response` is a
  *default*, and four `OTEL_LOG_*` variables switch it back off. Order is not cosmetic:
  a catch-all atop `resource_attributes` makes Loki answer `400`, loudly; atop
  `log_attributes` the push returns `204`, `-verify-config` calls the config valid, and
  every useful piece of structured metadata disappears in silence. Two CI checks see
  that second case: the allow-list gate reads the shape,
  `scripts/prova-privacy-log.sh` measures it.
- **Never guess Claude Code metric names.** The exporter's suffix behaviour is pinned
  precisely so they stop depending on unit metadata.
- **Never let a gate skip because its credential is missing** — the `sonar` job stays
  red instead: that is `continue-on-error` with extra steps. The one exception is argued
  rather than implied: `sonar.yml` skips on fork and Dependabot PRs, which cannot read
  the token at all, and the README says what compensates. **An exception that is argued
  is not the thing this rule forbids; an exception that is silent is.**
- **No app-level rate limiter in the status API.** The old claim "Cloudflare does it at
  the edge" was false, and the proof is the zone's own configuration: **it carries no
  rate limiting rule at all** (checked 2026-08-16 — the 60-request burst that day is
  consistent with it, but a burst alone never settles the question). The throttle lives
  in the site's Worker instead: `marcobellingeri.dev` PR #216, 2026-08-16, 60 requests /
  60s per IP, `Retry-After: 60`. Measured against production 2026-08-20: **75 requests
  in ~11s → zero `429`; 200 in ~26s → 13, the first at request 167**
  (`docs/DECISIONS.md`). Per-datacentre and eventually consistent, so **a ceiling
  against a sustained flood, not a guillotine at the 61st request** — and the burst is
  written down beside the result precisely because a short one reads as "broken".
- **`deploy.limitOverride` is a runaway guard, not a cost lever** — and mind which half
  is measured: `status-api` peaks at 0.0094 vCPU and 87 MB and Railway bills consumption
  rather than provisioned capacity, so the cap is *reasoned from measured consumption*,
  while **whether Hobby enforces it at all is still unmeasured** (the experiment is
  described, and unrun, in `docs/DECISIONS.md`). Numbers, dates and reasoning for both
  live there — read it before reopening either.
- **Two backstops that are not rate limiters**: the workspace usage limit ($15 soft /
  $30 hard, set by the operator — no tool here can read it back) and a WAF custom rule
  on `otel.` blocking everything except `POST /v1/metrics` and `POST /v1/logs` carrying
  an `Authorization` header (presence only, never the value) — two paths since Phase
  1.5, and closing `/v1/logs` again is manual (`docs/BLOCKERS.md` §4). That rule is
  defence in depth, never the auth: auth stays `bearertokenauth` inside the Collector,
  and `smoke.yml` proves it with a *wrong* token, the only shape the edge lets through.

## References (read on demand)

`docs/DECISIONS.md` (every closed decision and what measuring changed about it — read
this before reopening anything) · `railway/README.md` (how production is deployed) ·
`README.md` (pipeline level, test contract, gate policy) · `docs/BLOCKERS.md` (what is
left) · `docs/CLOUDFLARE_TUNNEL_SETUP.md` · `docs/CLAUDE_CODE_TELEMETRY.md` ·
`docs/LOCAL_DRY_RUN.md` (how to verify behaviour instead of assuming it) ·
`docs/superpowers/specs/` (the design, realigned to Railway) · `SECURITY.md`
(what is exposed and what is not) · `CONTRIBUTING.md` (the loop and the gates).
