#!/usr/bin/env python3
"""Stampa il corpo JSON della query GraphQL sul consumo Railway.

Esiste come file e non come heredoc dentro il workflow per una ragione pratica:
un heredoc annidato dentro un blocco `run: |` rompe il parsing YAML, e la logica
in un file si prova in locale senza doverla estrarre dal workflow.
"""
import json
import sys

QUERY = (
    "query($w: String!, $m: [MetricMeasurement!]!) { "
    "estimatedUsage(workspaceId: $w, measurements: $m) { measurement estimatedValue } }"
)

# Solo CPU e memoria: sono le due che un processo scappato fa esplodere. Disco e
# rete crescono per ragioni legittime e farebbero rumore.
MISURE = ["CPU_USAGE", "MEMORY_USAGE_GB"]

if __name__ == "__main__":
    print(json.dumps({"query": QUERY, "variables": {"w": sys.argv[1], "m": MISURE}}))
