"""Start-up setup for cloud hosting, driven by environment variables (secrets).

A cloud container has no keyboard, so the first administrator cannot be typed in
with scripts/create_admin.py. Instead:

  STOCKSENSE_ADMIN_USERNAME / STOCKSENSE_ADMIN_PASSWORD
      Create this administrator at start-up if NO user exists yet. Ignored once
      any account exists (it never resets a password or adds a second admin).
  STOCKSENSE_LOAD_DEMO=true
      Load the fictional demo data at start-up if there are no products yet.
  STOCKSENSE_ALLOW_WEB_SETUP=false
      Hide the "create administrator" web screen. Set this on any public host so a
      stranger can never create the first admin. (Default: true, for laptops.)
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import func, select

from stocksense.exceptions import StockSenseError
from stocksense.models import Product

log = logging.getLogger("stocksense.bootstrap")
_TRUE = {"1", "true", "yes", "on"}


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return default if not raw else raw in _TRUE


def web_setup_allowed() -> bool:
    return env_flag("STOCKSENSE_ALLOW_WEB_SETUP", True)


def bootstrap_from_env(db) -> dict:
    """Idempotent: safe to run on every start-up."""
    from stocksense.security.authentication import create_first_admin
    from stocksense.services.demo_service import load_demo_data
    result = {"admin_created": False, "demo_loaded": False, "problem": None}
    username = os.getenv("STOCKSENSE_ADMIN_USERNAME", "").strip()
    password = os.getenv("STOCKSENSE_ADMIN_PASSWORD", "")
    if username and password and not db.has_users():
        try:
            create_first_admin(db, username, password)
            result["admin_created"] = True
            log.info("Administrator created from environment settings.")
        except StockSenseError as exc:
            # Never log the password; the message only names the rule that failed.
            result["problem"] = f"STOCKSENSE_ADMIN_* settings rejected: {exc.user_message}"
            log.error(result["problem"])
    if env_flag("STOCKSENSE_LOAD_DEMO", False):
        with db.session() as s:
            has_products = (s.scalar(select(func.count(Product.id))) or 0) > 0
        if not has_products:
            load_demo_data(db, "startup")
            result["demo_loaded"] = True
    return result
