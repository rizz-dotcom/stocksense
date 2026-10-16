"""STOCKSENSE - Streamlit entry point.  Run with:  streamlit run app.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="STOCKSENSE", page_icon="📦", layout="wide")

try:
    from stocksense.config import load_config  # noqa: E402
    from stocksense.logging_setup import configure_logging  # noqa: E402
    from stocksense.ui import common  # noqa: E402
except ImportError as exc:  # missing dependency
    st.error("A required package is missing. Run: pip install -r requirements.txt "
             f"(missing module: {exc.name}).")
    st.stop()

_cfg = load_config()
configure_logging(_cfg.log_path, _cfg.log_level)
st.markdown(common.CSS, unsafe_allow_html=True)

try:
    db = common.get_db()
except Exception as exc:  # noqa: BLE001 - shown to the user, details in log
    common.show_error(exc)
    st.stop()

from stocksense.ui.auth_ui import require_login  # noqa: E402
from stocksense.ui import (approvals, audit_log, dashboard, forecasts, inventory,  # noqa: E402
                           proposals, purchase_orders, sales, settings, suppliers)
from stocksense.security.authorization import Permission  # noqa: E402

user = require_login(db)
common.show_flash()

PAGES = [
    ("Dashboard", "📊", Permission.VIEW, dashboard.render),
    ("Inventory", "📦", Permission.VIEW, inventory.render),
    ("Suppliers", "🚚", Permission.VIEW, suppliers.render),
    ("Sales & demand", "🧾", Permission.VIEW, sales.render),
    ("Forecasts", "📈", Permission.VIEW, forecasts.render),
    ("Purchase proposals", "📝", Permission.VIEW, proposals.render),
    ("Approval center", "✅", Permission.VIEW, approvals.render),
    ("Simulated orders", "📄", Permission.VIEW, purchase_orders.render),
    ("Audit log", "🔎", Permission.VIEW_AUDIT, audit_log.render),
    ("Settings", "⚙️", Permission.VIEW, settings.render),
]
available = [p for p in PAGES if user.can(p[2])]
labels = [f"{icon}  {name}" for name, icon, _, _ in available]
choice = st.sidebar.radio("Navigate", labels, key="nav", label_visibility="collapsed")
name, _, _, render = available[labels.index(choice)]
try:
    render(db, user)
except Exception as exc:  # noqa: BLE001 - last-resort boundary; logged, no traceback shown
    common.show_error(exc)
