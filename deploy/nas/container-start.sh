#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${1:?用法: container-start.sh <容器名>}"
docker inspect "$CONTAINER" >/dev/null
docker exec "$CONTAINER" mkdir -p /app/data /app/logs
if ! docker exec "$CONTAINER" pgrep -x supervisord >/dev/null; then
    docker exec -d "$CONTAINER" supervisord -c /etc/supervisor/supervisord.conf
fi
docker exec "$CONTAINER" supervisorctl -c /etc/supervisor/supervisord.conf status
