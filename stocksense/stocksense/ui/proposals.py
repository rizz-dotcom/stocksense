from __future__ import annotations

import pandas as pd
import streamlit as st

from stocksense.security.authorization import Permission
from stocksense.services import proposal_service
from stocksense.services.settings_service import get_settings
from stocksense.ui.common import action, empty_state, money
from stocksense.ui.proposal_view import render_detail
from stocksense.utils.dates import fmt_dt


def render(db, user) -> None:
    st.title("Purchase proposals")
    settings = get_settings(db)
    if user.can(Permission.MANAGE_PROPOSALS):
        with st.expander("Generate a new proposal", expanded=False):
            with st.form("gen"):
                title = st.text_input("Title", value="Weekly replenishment")
                c1, c2 = st.columns(2)
                budget = c1.number_input(f"Budget ({settings.currency})", min_value=1.0,
                                         value=float(settings.default_budget), step=500.0,
                                         format="%.2f")
                horizon = c2.number_input("Planning horizon (days)", 1, 90,
                                          settings.default_horizon_days)
                st.caption("Quantities, suppliers and costs are calculated deterministically "
                           "from stored data. Nothing is ordered.")
                if st.form_submit_button("Generate proposal", type="primary"):
                    with action():
                        pid = proposal_service.generate_proposal(
                            db, user, budget=f"{budget:.2f}", horizon_days=int(horizon),
                            title=title)
                        st.session_state["selected_proposal"] = pid
                        st.session_state["_flash"] = "Draft proposal created"
                        st.rerun()
    proposals = proposal_service.list_proposals(db)
    if not proposals:
        empty_state("No proposals yet. Generate one above.")
        return
    st.dataframe(pd.DataFrame([{
        "Reference": p["reference"], "Title": p["title"],
        "Status": p["status"].replace("_", " ").lower(), "Version": p["version"],
        "Total": money(p["total_minor"], p["currency"]),
        "Budget": money(p["budget_minor"], p["currency"]), "Created": fmt_dt(p["created_at"]),
        "By": p["created_by"]} for p in proposals]), hide_index=True, width="stretch")
    labels = {f"{p['reference']} - {p['title']} ({p['status'].replace('_', ' ').lower()})": p["id"]
              for p in proposals}
    ids = list(labels.values())
    default = st.session_state.get("selected_proposal")
    index = ids.index(default) if default in ids else 0
    pid = labels[st.selectbox("Open proposal", list(labels), index=index)]
    st.session_state["selected_proposal"] = pid
    with st.container(border=True):
        render_detail(db, user, pid, mode="proposal")
