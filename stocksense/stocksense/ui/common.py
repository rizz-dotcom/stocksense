"""Shared UI helpers. Presentation only - no business rules here."""
from __future__ import annotations

import logging
from contextlib import contextmanager

import pandas as pd
import streamlit as st

from stocksense.config import load_config
from stocksense.database import Database
from stocksense.exceptions import StockSenseError
from stocksense.utils.money import format_money

log = logging.getLogger("stocksense.ui")

RISK_COLORS = {"HIGH": "red", "MEDIUM": "orange", "LOW": "green", "UNKNOWN": "gray"}
STATUS_COLORS = {"DRAFT": "gray", "PENDING_APPROVAL": "blue", "APPROVED": "green",
                 "REJECTED": "red", "NEEDS_REVISION": "orange", "BLOCKED": "red",
                 "SIMULATED_OPEN": "blue", "SIMULATED_RECEIVED": "green"}
STATUS_LABELS = {"PENDING_APPROVAL": "Pending approval", "NEEDS_REVISION": "Needs revision",
                 "SIMULATED_OPEN": "Simulated - open", "SIMULATED_RECEIVED": "Simulated - received"}

CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1280px;}
h1, h2, h3 {letter-spacing: -0.01em;}
.ss-banner {border-left: 4px solid #C8890B; background: rgba(200,137,11,0.09);
            padding: .55rem .9rem; border-radius: 4px; margin: .2rem 0 1rem 0; font-size: .92rem;}
.ss-sim {border-left: 4px solid #3A6EA5; background: rgba(58,110,165,0.08);
         padding: .55rem .9rem; border-radius: 4px; margin: .2rem 0 1rem 0; font-size: .92rem;}
.ss-muted {opacity: .72; font-size: .88rem;}
div[data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.22); border-radius: 6px;
                             padding: .6rem .8rem;}
</style>
"""


@st.cache_resource(show_spinner=False)
def get_db() -> Database:
    from stocksense.services.bootstrap import bootstrap_from_env
    config = load_config()
    db = Database(config.database_url)
    db.init_schema()
    outcome = bootstrap_from_env(db)
    if outcome["problem"]:
        st.session_state["_bootstrap_problem"] = outcome["problem"]
    return db


@st.cache_resource(show_spinner=False)
def get_config():
    return load_config()


def money(minor, currency: str) -> str:
    return format_money(minor, currency)


def status_badge(status: str) -> None:
    st.badge(STATUS_LABELS.get(status, status.replace("_", " ").title()),
             color=STATUS_COLORS.get(status, "gray"))


def risk_label(level: str) -> str:
    return {"HIGH": "🔴 High", "MEDIUM": "🟠 Medium", "LOW": "🟢 Low",
            "UNKNOWN": "⚪ Unknown"}.get(level, level)


def demo_banner(text: str = "Records marked 'demo' are FICTIONAL demonstration data, "
                            "including all prices and supplier quantities.") -> None:
    st.markdown(f'<div class="ss-banner">{text}</div>', unsafe_allow_html=True)


def simulated_banner() -> None:
    st.markdown('<div class="ss-sim"><b>Simulated orders only.</b> STOCKSENSE never contacts '
                'suppliers, places real orders or spends money. Every purchase order here is a '
                'record for planning and demonstration.</div>', unsafe_allow_html=True)


def show_error(exc: Exception) -> None:
    if isinstance(exc, StockSenseError):
        st.error(exc.user_message)
        for err in getattr(exc, "errors", []) or []:
            st.markdown(f"- {err}")
    else:
        log.exception("Unexpected UI error")
        st.error("Something unexpected went wrong. The details were written to the "
                 "application log (data/stocksense.log). Nothing was partially saved.")


@contextmanager
def action(success: str | None = None):
    """Run a user action and show a friendly result. Errors are never swallowed:
    they are displayed, and unexpected ones are logged with a traceback."""
    try:
        yield
        if success:
            st.toast(success, icon="✅")
    except Exception as exc:  # noqa: BLE001 - top-level UI boundary, shown + logged
        show_error(exc)
    except BaseException:
        # st.rerun()/st.stop() control flow: keep the success message for the next run.
        if success:
            st.session_state["_flash"] = success
        raise


def show_flash() -> None:
    message = st.session_state.pop("_flash", None)
    if message:
        st.toast(message, icon="✅")


def table(rows, columns: dict | None = None, **kwargs) -> None:
    if not rows:
        return
    frame = pd.DataFrame(rows)
    if columns:
        frame = frame[[c for c in columns if c in frame.columns]].rename(columns=columns)
    st.dataframe(frame, hide_index=True, width="stretch", **kwargs)


def empty_state(message: str) -> None:
    st.info(message, icon="ℹ️")
