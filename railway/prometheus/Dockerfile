# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
FROM prom/prometheus:v3.13.1

# nobody (65534), which is what the base image already uses — and what makes the
# volume-ownership question in railway/README.md worth checking on first deploy.
USER 65534:65534

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
