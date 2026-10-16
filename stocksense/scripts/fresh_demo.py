"""Start a clean demo database with today's dates - ideal right before a presentation.

Your current database is NOT deleted: it is renamed to data/stocksense-backup-<time>.db.
Then a new database is created with the fictional demo data. You create the
administrator on the first screen of the app.

Usage:  python scripts/fresh_demo.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stocksense.config import load_config  # noqa: E402
from stocksense.database import Database  # noqa: E402
from stocksense.services.demo_service import load_demo_data  # noqa: E402


def main() -> int:
    cfg = load_config()
    db_path = cfg.db_path
    if db_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.stem}-backup-{stamp}{db_path.suffix}")
        try:
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(db_path) + suffix)
                if src.exists():
                    src.rename(Path(str(backup) + suffix))
        except PermissionError:
            print("ERROR: the database is in use. Stop STOCKSENSE (Ctrl+C) and try again.")
            return 1
        print(f"Previous database kept as: {backup}")
    db = Database(cfg.database_url)
    db.init_schema()
    created = load_demo_data(db, "fresh-demo")
    print("Fresh demo database ready (FICTIONAL data): "
          + ", ".join(f"{v} {k}" for k, v in created.items()))
    print("Next: streamlit run app.py  -> create the administrator on the first screen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
