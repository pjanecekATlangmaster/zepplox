#!/bin/sh
# Pull ZeppLox from GHCR and start it. Secrets stay in .env.
cd "$(dirname "$0")"
if command -v sudo >/dev/null 2>&1; then
  SUDO=sudo
else
  SUDO=
fi
$SUDO docker compose pull
$SUDO docker compose up -d
$SUDO docker image prune -f
$SUDO docker compose ps
curl -sI http://127.0.0.1:8456/healthz || true
