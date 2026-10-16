from __future__ import annotations

import pandas as pd
import streamlit as st

from stocksense.security import authentication as auth
from stocksense.security.authorization import ROLES, ROLE_DESCRIPTIONS, Permission
from stocksense.services import demo_service, settings_service
from stocksense.ui.common import action, get_config, show_error
from stocksense.utils.dates import fmt_dt


def render(db, user) -> None:
    st.title("Settings")
    tabs = ["My account"]
    if user.can(Permission.MANAGE_SETTINGS):
        tabs = ["Business settings", "AI review", "Users", "Demo data and backup"] + tabs
    views = dict(zip(tabs, st.tabs(tabs)))
    if "Business settings" in views:
        with views["Business settings"]:
            _business(db, user)
        with views["AI review"]:
            _ai_status(db)
        with views["Users"]:
            _users(db, user)
        with views["Demo data and backup"]:
            _demo(db, user)
    with views["My account"]:
        _account(db, user)


def _business(db, user) -> None:
    s = settings_service.get_settings(db)
    with st.form("settings"):
        c1, c2 = st.columns([3, 1])
        name = c1.text_input("Business name", value=s.business_name)
        currency = c2.text_input("Currency (ISO code)", value=s.currency, max_chars=3)
        c3, c4, c5 = st.columns(3)
        horizon = c3.number_input("Default planning horizon (days)", 1, 90, s.default_horizon_days)
        budget = c4.number_input("Default budget", min_value=1.0, value=float(s.default_budget),
                                 step=500.0, format="%.2f")
        lookback = c5.number_input("Sales look-back (days)", 7, 365, s.sales_lookback_days)
        c6, c7, c8 = st.columns(3)
        high = c6.number_input("High risk at or below (days cover)", 0, 60, s.high_risk_cover_days)
        medium = c7.number_input("Medium risk at or below (days cover)", 0, 90,
                                 s.medium_risk_cover_days)
        expiry = c8.number_input("Expiry warning (days)", 0, 90, s.expiry_warning_days)
        c9, c10 = st.columns(2)
        quote_age = c9.number_input("Quote becomes stale after (days)", 1, 365, s.max_quote_age_days)
        timeout = c10.number_input("Sign-out after inactivity (minutes)", 5, 480,
                                   s.session_timeout_minutes)
        on_order = st.checkbox("Count open simulated orders as on-order stock",
                               value=s.count_simulated_po_as_on_order)
        ai = st.checkbox("Allow AI review (needs GEMINI_API_KEY)", value=s.ai_enabled)
        if st.form_submit_button("Save settings", type="primary"):
            with action("Settings saved"):
                settings_service.update_settings(db, user, {
                    "business_name": name, "currency": currency.upper(),
                    "default_horizon_days": int(horizon), "default_budget": f"{budget:.2f}",
                    "sales_lookback_days": int(lookback), "high_risk_cover_days": int(high),
                    "medium_risk_cover_days": int(medium), "expiry_warning_days": int(expiry),
                    "max_quote_age_days": int(quote_age), "session_timeout_minutes": int(timeout),
                    "count_simulated_po_as_on_order": on_order, "ai_enabled": ai})
    st.caption("Changing the currency does not convert amounts; existing proposals in the old "
               "currency will fail validation and need to be regenerated.")


def _ai_status(db) -> None:
    cfg = get_config()
    s = settings_service.get_settings(db)
    st.markdown(f"- AI review switch: **{'on' if s.ai_enabled else 'off'}**")
    st.markdown(f"- Gemini API key: **{'configured' if cfg.gemini_configured else 'not configured'}**")
    st.markdown(f"- Model: `{cfg.gemini_model}` (set GEMINI_MODEL in .env to change)")
    st.markdown(f"- Timeout: {cfg.gemini_timeout_seconds} s; up to 3 attempts for temporary errors")
    if not cfg.gemini_configured:
        st.info("To enable: copy .env.example to .env, set GEMINI_API_KEY, restart the app. "
                "Never paste the key into the app, chat, code or logs.")
        return
    st.caption("Sends one tiny request to Gemini to prove the key and model work.")
    if st.button("Test Gemini connection", key="ai_test"):
        from stocksense.services.ai_service import test_connection
        from stocksense.ui.proposal_view import _ai
        with st.spinner("Contacting Gemini..."):
            result = test_connection(_ai())
        (st.success if result["ok"] else st.error)(result["message"])


def _users(db, user) -> None:
    users = auth.list_users(db, user)
    st.dataframe(pd.DataFrame([{"Username": u["username"], "Role": u["role"],
                                "Active": "yes" if u["active"] else "no",
                                "Last sign-in": fmt_dt(u["last_login"])} for u in users]),
                 hide_index=True, width="stretch")
    st.subheader("Add a user")
    with st.form("new_user", clear_on_submit=True):
        c1, c2 = st.columns(2)
        username = c1.text_input("Username")
        role = c2.selectbox("Role", ROLES, index=2,
                            format_func=lambda r: f"{r} - {ROLE_DESCRIPTIONS[r]}")
        pw = st.text_input("Initial password", type="password")
        if st.form_submit_button("Create user", type="primary"):
            with action(f"User {username} created"):
                auth.create_user(db, user, username, pw, role)
    st.subheader("Manage a user")
    labels = {u["username"]: u for u in users}
    target = labels[st.selectbox("User", list(labels))]
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Deactivate" if target["active"] else "Activate", key=f"tu_{target['id']}"):
            with action("User updated"):
                auth.set_user_active(db, user, target["id"], not target["active"])
                st.rerun()
    with c2:
        with st.form(f"reset_{target['id']}", clear_on_submit=True):
            new = st.text_input("New password", type="password")
            if st.form_submit_button("Reset password"):
                with action("Password reset"):
                    auth.reset_password(db, user, target["id"], new)


def _demo(db, user) -> None:
    st.markdown("Load the **Demo Burger House** dataset: 5 products, 3 suppliers with "
                "FICTIONAL prices and quantities, and 7 days of sample sales. Existing records "
                "are never deleted or overwritten, and loading twice creates no duplicates.")
    if st.button("Load demo data", type="primary"):
        with action():
            created = demo_service.load_demo_data(db, user.username)
            st.success("Added: " + ", ".join(f"{v} {k}" for k, v in created.items()))
    cfg = get_config()
    st.subheader("Backup")
    st.markdown(f"Database file: `{cfg.db_path}`. To back up, stop the app and copy this file "
                "(and any `-wal` / `-shm` files next to it). Restore by copying it back while "
                "the app is stopped. See README for details.")


def _account(db, user) -> None:
    with st.form("own_pw", clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        again = st.text_input("Repeat new password", type="password")
        if st.form_submit_button("Change password"):
            if new != again:
                st.error("The new passwords do not match.")
            else:
                try:
                    auth.change_own_password(db, user, current, new)
                    st.success("Password changed.")
                except Exception as exc:  # noqa: BLE001 - shown via show_error, logged if unexpected
                    show_error(exc)
