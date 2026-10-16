"""Load the fictional 'Demo Burger House' dataset. Safe to run repeatedly.

Usage:  python scripts/seed_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stocksense.config import load_config  # noqa: E402
from stocksense.database import Database  # noqa: E402
from stocksense.exceptions import StockSenseError  # noqa: E402
from stocksense.services.demo_service import load_demo_data  # noqa: E402


def main() -> int:
    cfg = load_config()
    try:
        db = Database(cfg.database_url)
        db.init_schema()
        created = load_demo_data(db, "seed-script")
    except StockSenseError as exc:
        print(f"ERROR: {exc.user_message}")
        return 1
    print(f"Database: {cfg.db_path}")
    print("Demo data (FICTIONAL) - added: " + ", ".join(f"{v} {k}" for k, v in created.items()))
    if not any(created.values()):
        print("Nothing new to add; the demo data was already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
