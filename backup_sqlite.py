#!/usr/bin/env python3
"""Create a consistent SQLite backup and prune expired local copies."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import time
from pathlib import Path


def backup_database(database: Path, destination: Path, keep_days: int = 30) -> Path:
    if keep_days < 1:
        raise ValueError("keep_days must be at least 1")
    if not database.is_file():
        raise FileNotFoundError(database)

    destination.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"market_brief.{stamp}.sqlite3"
    temporary = destination / f".{final.name}.{os.getpid()}.tmp"

    try:
        with sqlite3.connect(database) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
            # A source in WAL mode can copy that persistent pragma to the
            # target. Convert the standalone backup to DELETE mode so it never
            # depends on sidecar files when copied off-host or restored.
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target.execute("PRAGMA journal_mode=DELETE")
            row = target.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise RuntimeError(f"backup integrity check failed: {row}")
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)
        Path(f"{temporary}-wal").unlink(missing_ok=True)
        Path(f"{temporary}-shm").unlink(missing_ok=True)

    cutoff = time.time() - keep_days * 86_400
    for old in destination.glob("market_brief.*.sqlite3"):
        if old != final and old.stat().st_mtime < cutoff:
            old.unlink()
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--keep-days", type=int, default=30)
    args = parser.parse_args()
    output = backup_database(args.db, args.destination, args.keep_days)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
