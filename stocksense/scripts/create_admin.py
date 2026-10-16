"""Create the first administrator from the command line (no default password).

Usage:  python scripts/create_admin.py
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stocksense.config import load_config  # noqa: E402
from stocksense.database import Database  # noqa: E402
from stocksense.exceptions import StockSenseError  # noqa: E402
from stocksense.security.authentication import create_first_admin  # noqa: E402


def main() -> int:
    cfg = load_config()
    db = Database(cfg.database_url)
    db.init_schema()
    if db.has_users():
        print("An account already exists. Sign in as an administrator and add users under "
              "Settings > Users.")
        return 1
    try:
        username = input("Administrator username [owner]: ").strip() or "owner"
        pw1 = getpass.getpass("Password (min 10 chars, letters and digits): ")
        pw2 = getpass.getpass("Repeat password: ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled: run this command in an interactive terminal.")
        return 1
    if pw1 != pw2:
        print("Passwords do not match.")
        return 1
    try:
        create_first_admin(db, username, pw1)
    except StockSenseError as exc:
        print(f"ERROR: {exc.user_message}")
        return 1
    print(f"Administrator '{username.lower()}' created. Start the app with: streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
