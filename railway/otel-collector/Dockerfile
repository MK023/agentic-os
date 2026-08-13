# Ingest OTLP. Build context is the repository root (no Root Directory set for this
# service), so the config comes from docker/ — the same file the local compose run
# mounts, not a copy of it.
# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
FROM otel/opentelemetry-collector-contrib:0.158.0

# The base image already runs as 10001; stated explicitly so it is visible here and
# survives a base-image change that silently drops it.
USER 10001:10001

COPY docker/otel-collector-config.yaml /etc/otelcol-contrib/config.yaml

# Explicit, rather than relying on Railway's startCommand overriding the image CMD:
# one less platform behaviour to be right about.
CMD ["--config=/etc/otelcol-contrib/config.yaml"]
