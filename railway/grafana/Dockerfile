# No volume on this service, deliberately: the datasource and the dashboard both
# arrive by provisioning, the admin password is an environment variable, and nothing
# else in this Grafana is worth persisting. A volume would add a redeploy downtime
# window (Railway serialises access) to protect state we do not have.
# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath, and these images ship no shell or HTTP client to probe with
# anyway. What actually verifies the hub after a deploy is scripts/verify-hub.sh.
FROM grafana/grafana:13.1.3

# 472 is Grafana's own uid in this image.
USER 472:472

COPY docker/grafana/provisioning /etc/grafana/provisioning
COPY docker/grafana/dashboards /etc/grafana/dashboards
