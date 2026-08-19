# The only ingress. Railway gives every service the option of a public domain; none
# of ours takes it — Grafana, the status API and the OTLP endpoint are reached
# through this tunnel, so the platform's own hostnames stay unused and there is no
# second door to forget about.
# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
# Pinnata per DIGEST oltre che per tag: un tag e' un'etichetta e si puo' ripuntare
# sotto lo stesso nome. Il ragionamento per esteso sta in docs/DECISIONS.md.
FROM cloudflare/cloudflared:2026.8.2@sha256:0aa26e284f05e6c77ae375b8c9c11d9eb6a448fb7bcd8d40f31cb6176189eb38

# This one is not cosmetic: the published image sets no USER, so the connector runs
# as root. A tunnel that only makes outbound connections needs no privilege at all.
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
# `healthcheckPath: /ready` NON e' dichiarato in railway.json, e l'omissione e'
# deliberata: Railway sonda la porta di destinazione del servizio, e questo servizio
# non ne espone una — e' un connettore in USCITA. Se la sonda finisse sulla porta
# sbagliata, ogni deploy resterebbe bloccato in attesa di un 200 che non arriva, cioe'
# un guasto peggiore di quello che il controllo dovrebbe prevenire. Non e'
# verificabile in locale (senza un token valido cloudflared esce prima di aprire la
# porta, misurato), quindi va provato una volta sul servizio vero prima di scriverlo
# qui. Lo scrape su :2000 sotto, invece, non ha questo rischio: se la porta non
# risponde, `up` va a 0 e lo si vede, senza bloccare niente.
CMD ["tunnel", "--no-autoupdate", "--metrics", "0.0.0.0:2000", "run"]
