# No volume on this service, deliberately: the datasource and the dashboard both
# arrive by provisioning, the admin password is an environment variable, and nothing
# else in this Grafana is worth persisting. A volume would add a redeploy downtime
# window (Railway serialises access) to protect state we do not have.
FROM grafana/grafana:13.1.1

COPY docker/grafana/provisioning /etc/grafana/provisioning
COPY docker/grafana/dashboards /etc/grafana/dashboards
