# Ingest OTLP. Build context is the repository root (no Root Directory set for this
# service), so the config comes from docker/ — the same file the local compose run
# mounts, not a copy of it.
FROM otel/opentelemetry-collector-contrib:0.157.0

COPY docker/otel-collector-config.yaml /etc/otelcol-contrib/config.yaml

# Explicit, rather than relying on Railway's startCommand overriding the image CMD:
# one less platform behaviour to be right about.
CMD ["--config=/etc/otelcol-contrib/config.yaml"]
