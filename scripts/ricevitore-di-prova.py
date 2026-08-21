"""Ricevitore HTTP usa-e-getta per scripts/prova-allarmi.sh: sta al posto di Slack.

Esiste perche' l'API di Grafana NON rilegge l'url di un contact point — lo
restituisce come `[REDACTED]`, e giustamente: e' una credenziale. Misurato il
2026-08-21, ed e' il motivo per cui la prova non puo' limitarsi a confrontare una
stringa. L'unico modo di sapere che `$SLACK_WEBHOOK_URL` e' stato interpolato e
che la catena regola -> politica -> contact point regge davvero e' **ricevere la
notifica**.

Scrive ogni corpo ricevuto su un file, una riga per notifica, e risponde 200:
qualunque altra risposta Grafana la conta come invio fallito.
"""

import http.server
import pathlib
import sys

DESTINAZIONE = pathlib.Path("/ricevute/notifiche.jsonl")


class Ricevitore(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — nome imposto da BaseHTTPRequestHandler
        lunghezza = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(lunghezza).decode("utf-8", "replace")
        DESTINAZIONE.parent.mkdir(parents=True, exist_ok=True)
        with DESTINAZIONE.open("a", encoding="utf-8") as f:
            # Su una riga sola: il corpo di Grafana e' gia' JSON, e una riga per
            # notifica rende banale contarle dallo script.
            f.write(corpo.replace("\n", " ") + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, formato: str, *args: object) -> None:
        # Su stderr, cosi' `docker logs` mostra gli arrivi durante la diagnosi
        # senza mescolarsi al contenuto.
        sys.stderr.write("ricevuto: " + formato % args + "\n")


if __name__ == "__main__":
    http.server.HTTPServer(("0.0.0.0", 8080), Ricevitore).serve_forever()
