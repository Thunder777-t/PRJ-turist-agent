from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
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


def cleanup_old_backups(out_dir: Path, keep: int) -> None:
    backups = sorted(out_dir.glob("tourist_agent_backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup SQLite database file.")
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_URL", "tourist_agent.db"),
        help="SQLite DB path or DATABASE_URL-style value (default: env DATABASE_URL or tourist_agent.db).",
    )
    parser.add_argument(
        "--out-dir",
        default="backups",
        help="Output directory for backups.",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=20,
        help="How many recent backups to keep (default: 20).",
    )
    args = parser.parse_args()

    db_path = parse_sqlite_path(args.db).expanduser().resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = out_dir / f"tourist_agent_backup_{timestamp}.db"

    shutil.copy2(db_path, backup_path)
    cleanup_old_backups(out_dir, max(1, args.keep))

    print(f"Backup created: {backup_path}")


if __name__ == "__main__":
    main()
