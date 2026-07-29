# The only ingress. Railway gives every service the option of a public domain; none
# of ours takes it — Grafana, the status API and the OTLP endpoint are reached
# through this tunnel, so the platform's own hostnames stay unused and there is no
# second door to forget about.
# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
FROM cloudflare/cloudflared:2026.7.3

# This one is not cosmetic: the published image sets no USER, so the connector runs
# as root. A tunnel that only makes outbound connections needs no privilege at all.
USER 65532:65532

# --no-autoupdate: the image tag is pinned on purpose; a connector that updates
# itself in place would quietly undo that.
CMD ["tunnel", "--no-autoupdate", "run"]
