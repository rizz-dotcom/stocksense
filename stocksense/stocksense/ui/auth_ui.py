"""Sign-in, first-run administrator setup, session expiry and logout.

Streamlit keeps session state in server memory per browser tab. This is a
local-use login, not a hardened internet-facing authentication system.
"""
from __future__ import annotations

import time

import streamlit as st

from stocksense.exceptions import AuthenticationError, StockSenseError
from stocksense.security import authentication as auth
from stocksense.security.authorization import ROLE_DESCRIPTIONS, UserContext
from stocksense.services import audit_service
from stocksense.services.settings_service import get_settings
from stocksense.ui.common import show_error

_KEY = "auth"


def _logout(db, reason: str | None = None) -> None:
    session = st.session_state.pop(_KEY, None)
    if session:
        audit_service.record_standalone(
            db, actor=session["username"], action="LOGOUT", entity_type="user",
            entity_id=str(session["user_id"]), message=reason or "Signed out.")
    for key in list(st.session_state.keys()):
        if key != "nav":
            del st.session_state[key]


def _first_run(db) -> None:
    from stocksense.services.bootstrap import web_setup_allowed
    if not web_setup_allowed():
        st.title("STOCKSENSE is not set up yet")
        st.error("No administrator exists, and creating one from the web page is switched off "
                 "on this server. The person hosting it must set the secrets "
                 "STOCKSENSE_ADMIN_USERNAME and STOCKSENSE_ADMIN_PASSWORD and restart the app.")
        problem = st.session_state.get("_bootstrap_problem")
        if problem:
            st.warning(problem)
        st.stop()
    st.title("Welcome to STOCKSENSE")
    st.write("No accounts exist yet. Create the administrator account for this installation. "
             "There is no default password.")
    with st.form("first_admin"):
        username = st.text_input("Administrator username", value="owner")
        pw1 = st.text_input("Password", type="password",
                            help="At least 10 characters with letters and digits.")
        pw2 = st.text_input("Repeat password", type="password")
        ok = st.form_submit_button("Create administrator", type="primary")
    if ok:
        if pw1 != pw2:
            st.error("The passwords do not match.")
        else:
            try:
                user = auth.create_first_admin(db, username, pw1)
                _start_session(user)
                st.rerun()
            except StockSenseError as exc:
                show_error(exc)
    st.stop()


def _start_session(user: UserContext) -> None:
    now = time.time()
    st.session_state[_KEY] = {"user_id": user.user_id, "username": user.username,
                              "role": user.role, "login_at": now, "last_seen": now}


def _login_form(db) -> None:
    left, mid, right = st.columns([1, 1.3, 1])
    with mid:
        st.title("STOCKSENSE")
        st.caption("Inventory, purchasing and risk - with a human in charge of every purchase.")
        if st.session_state.pop("_expired", False):
            st.warning("Your session expired. Please sign in again.")
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in", type="primary", width="stretch")
        if ok:
            try:
                user = auth.authenticate(db, username, password)
                _start_session(user)
                st.rerun()
            except AuthenticationError as exc:
                st.error(exc.user_message)
            except StockSenseError as exc:
                show_error(exc)
    st.stop()


def require_login(db) -> UserContext:
    if not db.has_users():
        _first_run(db)
    session = st.session_state.get(_KEY)
    if not session:
        _login_form(db)
    timeout = get_settings(db).session_timeout_minutes * 60
    now = time.time()
    if now - session["last_seen"] > timeout:
        _logout(db, "Session expired after inactivity.")
        st.session_state["_expired"] = True
        st.rerun()
    try:
        user = auth.refresh_user(db, session["user_id"])
    except AuthenticationError:
        _logout(db, "Account no longer active.")
        st.rerun()
    session["last_seen"] = now
    session["role"] = user.role
    with st.sidebar:
        st.markdown(f"**{get_settings(db).business_name}**")
        st.caption(f"Signed in as **{user.username}** ({user.role})")
        st.caption(ROLE_DESCRIPTIONS.get(user.role, ""))
        if st.button("Sign out", width="stretch"):
            _logout(db)
            st.rerun()
        st.divider()
    return user
