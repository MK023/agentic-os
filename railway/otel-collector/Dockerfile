# Ingest OTLP. Build context is the repository root (no Root Directory set for this
# service), so the config comes from docker/ — the same file the local compose run
# mounts, not a copy of it.
# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
# Pinnata per DIGEST oltre che per tag: un tag e' un'etichetta e si puo' ripuntare
# sotto lo stesso nome. Il ragionamento per esteso sta in docs/DECISIONS.md.
# ATTENZIONE AL TAG. Fino al 2026-08-20 qui c'era `0.158.0-386`, e il `-386` si
# legge come un numero di build: e' un suffisso di ARCHITETTURA. Misurato con
# `docker image inspect`: quell'immagine e' linux/386, x86 a 32 bit, mentre le altre
# quattro del progetto sono amd64. Il digest pinnato era il PRIMO elemento della
# manifest list, non quello della piattaforma di destinazione.
#
# Perche' conta proprio qui: e' l'unico servizio esposto a internet in scrittura, e
# il suo mestiere e' parsare protobuf e JSON ostili fino a 4 MiB. linux/386 e' un
# porting secondario di Go — `int` a 32 bit nell'aritmetica delle dimensioni,
# allineamento manuale degli atomici a 64 bit, spazio d'indirizzamento di 4 GiB,
# e meno fuzzing a monte. Non e' un exploit dimostrato: e' un runtime involontario
# su cui nessuno aveva deciso niente, e Dependabot avrebbe continuato a seguire il
# tag `-386` per sempre.
#
# Adesso e' pinnato il digest della MANIFEST LIST, non quello di una piattaforma:
# in locale su Mac risolve arm64, su Railway amd64, e resta un solo digest scritto
# in un solo posto.
FROM otel/opentelemetry-collector-contrib:0.160.0@sha256:799dc6cf12c96192af37b5bdba804da8c10b3bc563b43cb90c3f3c58d9572ad6

# The base image already runs as 10001; stated explicitly so it is visible here and
# survives a base-image change that silently drops it.
USER 10001:10001

COPY docker/otel-collector-config.yaml /etc/otelcol-contrib/config.yaml

# Explicit, rather than relying on Railway's startCommand overriding the image CMD:
# one less platform behaviour to be right about.
CMD ["--config=/etc/otelcol-contrib/config.yaml"]
