# No volume on this service, deliberately: the datasource and the dashboard both
# arrive by provisioning, the admin password is an environment variable, and nothing
# else in this Grafana is worth persisting. A volume would add a redeploy downtime
# window (Railway serialises access) to protect state we do not have.
# checkov:skip=CKV_DOCKER_2: Railway ignores the Dockerfile HEALTHCHECK — it uses its
# own healthcheckPath. La riga diceva anche "these images ship no shell": per Grafana
# e' FALSO, verificato il 20-08-2026 (`sh` c'e', l'immagine e' Alpine), ed e' cio' che
# rende possibile la guardia sull'entrypoint qui sotto. Restava vero per le altre
# immagini, non per questa. What actually verifies the hub after a deploy is
# scripts/verify-hub.sh.
# Pinnata per DIGEST oltre che per tag: un tag e' un'etichetta e si puo' ripuntare
# sotto lo stesso nome. Il ragionamento per esteso sta in docs/DECISIONS.md.
FROM grafana/grafana:13.2.1@sha256:f772d434e8fab0049deb2b1b30abd43342bcfca1537614aa8d36080232cf4283

# 472 is Grafana's own uid in this image.
USER 472:472

COPY docker/grafana/provisioning /etc/grafana/provisioning
COPY docker/grafana/dashboards /etc/grafana/dashboards

# Le IMPOSTAZIONI, non solo il provisioning. Prima di questo file Grafana era l'unico
# servizio la cui configurazione non stava in docker/: cookie, gravatar, telemetria in
# uscita, snapshot e plugin erano tutti default del fornitore, quindi la sua postura
# di sicurezza non era in git e un bump minore poteva cambiarla con un diff vuoto.
COPY docker/grafana/grafana.ini /etc/grafana/grafana.ini

# Guardia sulla password di amministratore: senza, Grafana parte con quella di default
# e nessuno strumento esistente potrebbe accorgersene. Il perche' per esteso sta nello
# script. `exec` in coda, quindi il processo resta il PID 1 di Grafana.
COPY docker/grafana/entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
