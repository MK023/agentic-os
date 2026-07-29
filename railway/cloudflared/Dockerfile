# The only ingress. Railway gives every service the option of a public domain; none
# of ours takes it — Grafana, the status API and the OTLP endpoint are reached
# through this tunnel, so the platform's own hostnames stay unused and there is no
# second door to forget about.
FROM cloudflare/cloudflared:2026.7.3

# --no-autoupdate: the image tag is pinned on purpose; a connector that updates
# itself in place would quietly undo that.
CMD ["tunnel", "--no-autoupdate", "run"]
