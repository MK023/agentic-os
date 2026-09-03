# The only ingress. Railway gives every service the option of a public domain; none
# of ours takes it — Grafana, the status API and the OTLP endpoint are reached
# through this tunnel, so the platform's own hostnames stay unused and there is no
# second door to forget about.
# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
# Pinnata per DIGEST oltre che per tag: un tag e' un'etichetta e si puo' ripuntare
# sotto lo stesso nome. Il ragionamento per esteso sta in docs/DECISIONS.md.
FROM cloudflare/cloudflared:2026.8.3@sha256:51c9cefcb4569df44e1ad403ab1d3d8065aa8e84339bcfc6aee75502e1140339

# Questa riga non corregge niente, e la ragione scritta prima era falsa: l'immagine
# pubblicata dichiara gia' `USER 65532:65532` — misurato con `docker image inspect`
# su questo digest e sul precedente 2026.7.3, identici. Resta per due motivi veri.
# Primo: fissa l'identita' QUI invece di ereditarla, cosi' un bump del base image non
# puo' cambiarla in silenzio. Secondo: `scripts/check-image-users.sh` legge i
# Dockerfile, non i metadati dell'immagine — senza questa riga quel gate fallisce su
# questo file, e non perche' abbia trovato qualcosa.
USER 65532:65532

# --no-autoupdate: the image tag is pinned on purpose; a connector that updates
# itself in place would quietly undo that.
#
# --metrics: il connettore e' l'UNICO ingresso del sistema, ed era l'unico servizio a
# non dire niente di se'. Ogni altro qui ha uno scrape dedicato, aggiunto dopo essere
# stato bruciato: il Collector su 8888, Prometheus su localhost:9090. Se il tunnel
# muore, l'unica cosa che se ne accorge e' una sonda esterna che vive in un ALTRO
# repository, e per sua stessa ammissione vede solo i guasti che durano.
#
# Su questa porta c'e' anche /ready, che la doc Cloudflare descrive cosi': "returns
# 200 if and only if it has an active connection to Cloudflare's network". E' la
# differenza fra "il processo e' partito" e "il tunnel e' connesso" — cioe' fra cio'
# che Railway sa oggi e cio' che conta.
#
# `healthcheckPath: /ready` ORA e' dichiarato in railway.json. Era stato omesso
# deliberatamente perche' Railway sonda la porta indicata dalla variabile `PORT` e su
# questo servizio non esisteva: e' un connettore in USCITA, non espone niente di suo.
# Il rischio era che ogni deploy restasse in attesa di un 200 che non arriva — e non
# era teorico, e' esattamente come e' fallito status-api lo stesso giorno.
#
# Cosa e' cambiato: `PORT=2000` e' stata impostata sul servizio il 20-08-2026, e
# 2000 e' la porta che il `--metrics` qui sotto apre davvero. Il valore vive su
# Railway, fuori da git: se qualcuno lo cambiasse senza toccare questo CMD, i deploy
# ricomincerebbero a fallire e nessun gate di questo repository potrebbe accorgersene.
# I due numeri vanno insieme.
#
# `/ready` e non `/metrics`: "Cloudflared has a /ready endpoint which returns 200 if
# and only if it has an active connection to Cloudflare's network" (guida Kubernetes
# di Cloudflare, letta il 20-08-2026). E' la differenza fra "il processo e' partito" e
# "il tunnel e' connesso" — cioe' l'intero motivo per cui questo controllo esiste.
# Il timeout resta il default di Railway, 300 secondi: piu' che sufficiente per aprire
# la connessione all'edge, quindi non c'e' niente da dichiarare.
#
# Railway NON risonda dopo la promozione ("does not monitor the healthcheck endpoint
# after the deployment has gone live"): questo e' un cancello sul deploy, non
# monitoraggio. Il tunnel che muore a regime lo REGISTRA lo scrape su :2000 qui sotto
# — registra, non segnala: nessuna regola valuta quella serie, e il perche' sta
# accanto al job in docker/prometheus.yml — mentre a NOTARLO e' lo smoke test da
# fuori. Non questa riga, in nessuno dei due casi.
#
# COSA ALTRO APRE QUESTA PORTA — e' il costo, ed e' dichiarato perche' non si puo'
# togliere. Il server delle metriche non serve solo /metrics e /ready: monta
# l'INTERO `http.DefaultServeMux` sotto /debug/, dove `net/http/pprof` si registra
# da solo. Misurato sul binario estratto da QUESTO digest (docker cp, poi
# disassembly di `metrics.newMetricsHandler`), non dedotto: le rotte sono /metrics,
# /ready, /healthcheck, /quicktunnel, /config, /diag/{configuration,tunnel,system}
# e /debug/*. L'unica bloccata e' /debug/pprof/cmdline, che risponde 403
# "forbidden" — e il fornitore dice perche', nel commento accanto: "prevent leaking
# secret command-line arguments (e.g. tunnel tokens) that are exposed via os.Args".
#
# Il guasto che questo commento previene e' credere che la porta sia innocua.
# /debug/pprof/heap resta aperto: chi ottiene esecuzione in un ALTRO container del
# progetto arriva a cloudflared.railway.internal:2000 senza credenziali e scarica la
# memoria del processo che TIENE il token del tunnel. Cloudflare ha chiuso la porta
# di argv e lasciato quella dell'heap; qui il token entra da TUNNEL_TOKEN, quindi e'
# esattamente quella rimasta aperta. Cosa vale quel token sta in docs/DECISIONS.md:
# uno solo, e ne rende altri tre.
#
# Perche' la porta resta com'e': non esiste un flag che spenga /debug/ — i soli
# parametri in tema sono --metrics, --metrics-update-freq e --management-diagnostics,
# e nessuno tocca il mux (letta la pagina run-parameters del fornitore ed estratti i
# flag dal binario). L'ascoltatore e' uno solo: spegnerlo perde insieme lo scrape di
# Prometheus e `healthcheckPath: /ready`, cioe' riporta l'unico ingresso del sistema
# a non dire piu' niente di se'. Il contenimento e' che questa porta non ha nessun
# hostname del Tunnel e non e' pubblicata: ci arriva solo chi e' gia' dentro la rete
# privata del progetto. E' una superficie dichiarata, non una che crediamo assente.
CMD ["tunnel", "--no-autoupdate", "--metrics", "0.0.0.0:2000", "run"]
