#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKUPS="$ROOT/deploy/nas/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUPS"

docker compose -p fridge -f "$ROOT/compose.nas.yaml" exec -T server python - "$STAMP" <<'PY'
import hashlib
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

stamp = sys.argv[1]
data_dir = Path("/data")
backup_dir = Path("/backups")
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
        for path in sorted(data_dir.iterdir()):
            if path.name != "fridge.db":
                bundle.add(path, arcname=path.name)
    os.replace(pending, archive)

digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix(archive.suffix + ".sha256").write_text(
    f"{digest}  {archive.name}\n", encoding="utf-8"
)

archives = sorted(backup_dir.glob("fridge-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
for old in archives[14:]:
    old.unlink()
    checksum = old.with_suffix(old.suffix + ".sha256")
    if checksum.exists():
        checksum.unlink()

print(archive)
PY
