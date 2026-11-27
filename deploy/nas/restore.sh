#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARCHIVE_NAME="${1:-}"
if [[ -z "$ARCHIVE_NAME" ]]; then
    echo "用法: bash deploy/nas/restore.sh fridge-YYYYMMDD-HHMMSS.tar.gz" >&2
    exit 2
fi

ARCHIVE="$ROOT/deploy/nas/backups/$(basename "$ARCHIVE_NAME")"
if [[ ! -f "$ARCHIVE" ]]; then
    echo "备份不存在: $ARCHIVE" >&2
    exit 2
fi
EXPECTED="$(cut -d ' ' -f 1 "$ARCHIVE.sha256")"
ACTUAL="$(sha256sum "$ARCHIVE" | cut -d ' ' -f 1)"
if [[ "$EXPECTED" != "$ACTUAL" ]]; then
    echo "备份校验失败，已终止恢复" >&2
    exit 1
fi

printf "这将覆盖 NAS 当前数据。输入 RESTORE 确认: "
read -r CONFIRM
if [[ "$CONFIRM" != "RESTORE" ]]; then
    echo "已取消"
    exit 1
fi

COMPOSE=(docker compose -p fridge -f "$ROOT/compose.nas.yaml")
DATA="$ROOT/deploy/nas/data"
SAFETY="$ROOT/deploy/nas/data.before-restore-$(date +%Y%m%d-%H%M%S)"
"${COMPOSE[@]}" stop server
mv "$DATA" "$SAFETY"
mkdir -p "$DATA"

if ! "${COMPOSE[@]}" run --rm --no-deps -v "$DATA:/restore-data" server \
    python - "/backups/$(basename "$ARCHIVE")" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as bundle:
    bundle.extractall("/restore-data", filter="data")
PY
then
    rm -rf "$DATA"
    mv "$SAFETY" "$DATA"
    "${COMPOSE[@]}" start server
    echo "恢复失败，已回滚到恢复前数据" >&2
    exit 1
fi

"${COMPOSE[@]}" start server
echo "恢复完成；旧数据保留在: $SAFETY"
