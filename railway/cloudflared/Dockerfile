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
CMD ["tunnel", "--no-autoupdate", "run"]
