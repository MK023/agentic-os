#!/bin/bash
set -euo pipefail

REPO_URL="https://github.com/MK023/agentic-os.git"
INSTALL_DIR="/opt/agentic-os"

if [ ! -d "$INSTALL_DIR" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR/docker"

if [ ! -f .env ]; then
  echo "Missing docker/.env on the VPS — copy .env.example, fill in real secrets, then re-run:" >&2
  echo "  cd $INSTALL_DIR/docker && docker compose up -d" >&2
  exit 1
fi

docker compose up -d
