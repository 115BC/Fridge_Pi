#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${1:?用法: container-backup.sh <容器名> [NAS备份目录]}"
HOST_DIR="${2:-/volume2/docker/fridge-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$HOST_DIR"

ARCHIVE_NAME="$(docker exec -i "$CONTAINER" python3 - "$STAMP" <<'PY'
import hashlib
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

stamp = sys.argv[1]
data_dir = Path("/app/data")
backup_dir = Path("/app/backups")
backup_dir.mkdir(parents=True, exist_ok=True)
archive = backup_dir / f"fridge-{stamp}.tar.gz"

with tempfile.TemporaryDirectory(prefix=".backup-", dir=backup_dir) as tmp:
    db_copy = Path(tmp) / "fridge.db"
    with sqlite3.connect(data_dir / "fridge.db") as source:
        with sqlite3.connect(db_copy) as target:
            source.backup(target)
    with sqlite3.connect(db_copy) as check:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity_check failed: {result}")

    pending = archive.with_suffix(archive.suffix + ".tmp")
    with tarfile.open(pending, "w:gz") as bundle:
        bundle.add(db_copy, arcname="fridge.db")
        bundle.add("/app/server/app.env", arcname="app.env")
        for path in sorted(data_dir.iterdir()):
            if path.name != "fridge.db":
                bundle.add(path, arcname=Path("data") / path.name)
    os.replace(pending, archive)

digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix(archive.suffix + ".sha256").write_text(
    f"{digest}  {archive.name}\n", encoding="utf-8"
)
print(archive.name)
PY
)"

docker cp "$CONTAINER:/app/backups/$ARCHIVE_NAME" "$HOST_DIR/"
docker cp "$CONTAINER:/app/backups/$ARCHIVE_NAME.sha256" "$HOST_DIR/"
echo "$HOST_DIR/$ARCHIVE_NAME"
