# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
FROM prom/prometheus:v3.13.2

# nobody (65534), which is what the base image already uses — and what makes the
# volume-ownership question in railway/README.md worth checking on first deploy.
USER 65534:65534

COPY docker/prometheus.yml /etc/prometheus/prometheus.yml

# Flags baked in rather than passed as a Railway startCommand: the image has an
# ENTRYPOINT, and how a platform composes its own command with an existing
# ENTRYPOINT is exactly the kind of detail that fails on the first deploy.
# --storage.tsdb.path must match the volume mount path configured on the service.
# Due limiti, non uno: vince quello che scatta per primo.
#
# Con la sola retention a tempo Prometheus riempie qualunque volume prima che i
# giorni scadano — non esiste contropressione dal disco. Il 13-08-2026 il volume
# da 500 MB si è riempito e la compaction ha iniziato a fallire una volta al
# minuto ("no space left on device"): l'ingestione reggeva ancora, perché il head
# sta in RAM, quindi da fuori non si vedeva nulla mentre la persistenza era già
# rotta. `retention.size` è il limite che quel guasto non poteva avere.
#
# 300MB su 500 disponibili. Il margine del 40% non è prudenza generica: la
# compaction scrive il blocco nuovo PRIMA di cancellare i vecchi, e il WAL sta
# sullo stesso volume ma NON è contato dentro retention.size. Alla crescita
# misurata (~50 MB/giorno) è il tetto di dimensione a scattare per primo, verso
# il sesto giorno: la finestra onesta è "circa una settimana", non sette giorni.
# Se il volume cresce, questo numero va rialzato a mano — non si ricava da solo.
CMD [ \
  "--config.file=/etc/prometheus/prometheus.yml", \
  "--storage.tsdb.path=/prometheus", \
  "--storage.tsdb.retention.time=7d", \
  "--storage.tsdb.retention.size=300MB" \
]
