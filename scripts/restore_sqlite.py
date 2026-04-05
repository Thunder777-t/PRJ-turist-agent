from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse


def parse_sqlite_path(database_url_or_path: str) -> Path:
    if database_url_or_path.startswith("sqlite:///"):
        parsed = urlparse(database_url_or_path)
        raw_path = parsed.path
        if raw_path.startswith("/") and os.name == "nt" and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path.lstrip("/")
        return Path(raw_path)
    return Path(database_url_or_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore SQLite database from backup file.")
    parser.add_argument("--backup", required=True, help="Backup db file path.")
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_URL", "tourist_agent.db"),
        help="Target db path or DATABASE_URL-style value.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite target DB without confirmation.",
    )
    args = parser.parse_args()

    backup_path = Path(args.backup).expanduser().resolve()
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    db_path = parse_sqlite_path(args.db).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists() and not args.force:
        raise RuntimeError(
            f"Target DB already exists: {db_path}. Rerun with --force to overwrite."
        )

    shutil.copy2(backup_path, db_path)
    print(f"Database restored to: {db_path}")


if __name__ == "__main__":
    main()
