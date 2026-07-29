FROM prom/prometheus:v3.13.1

COPY docker/prometheus.yml /etc/prometheus/prometheus.yml

# Flags baked in rather than passed as a Railway startCommand: the image has an
# ENTRYPOINT, and how a platform composes its own command with an existing
# ENTRYPOINT is exactly the kind of detail that fails on the first deploy.
# --storage.tsdb.path must match the volume mount path configured on the service.
CMD [ \
  "--config.file=/etc/prometheus/prometheus.yml", \
  "--storage.tsdb.path=/prometheus", \
  "--storage.tsdb.retention.time=30d" \
]
