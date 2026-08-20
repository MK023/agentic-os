# checkov:skip=CKV_DOCKER_2: Railway ignora l'HEALTHCHECK del Dockerfile — usa il suo
# healthcheckPath, che qui NON e' dichiarato: e' una decisione chiusa per i servizi
# senza rotta pubblica (docs/DECISIONS.md). Cio' che sorveglia Loki e' lo scrape
# Prometheus, che continua per tutta la vita del container invece di fermarsi quando
# il deploy diventa live.
# Pinnata per DIGEST oltre che per tag: un tag e' un'etichetta e si puo' ripuntare
# sotto lo stesso nome. Verificato il 2026-08-20 che 3.7.6 e' l'ultima 3.x pubblicata
# (v3.7.6, 2026-08-06) — un digest vecchio pinna un'immagine vecchia in silenzio.
FROM grafana/loki:3.7.6@sha256:efd47c67f9bac88ca29bcf8cb997d9ab29d1848bd0aff579282295542a745952

# 10001 e' l'utente che l'immagine di Loki usa gia' (letto con `docker inspect`,
# non assunto).
USER 10001:10001

COPY docker/loki.yaml /etc/loki/loki.yaml

# -config.expand-env: le credenziali R2 arrivano da variabili di servizio Railway,
# mai dal file. Senza questo flag Loki leggerebbe la stringa `${LOKI_R2_BUCKET}`
# alla lettera e fallirebbe la connessione con un errore che non nomina la causa.
CMD ["-config.file=/etc/loki/loki.yaml", "-config.expand-env=true"]
