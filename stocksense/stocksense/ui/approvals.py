from __future__ import annotations

import streamlit as st

from stocksense.services import proposal_service
from stocksense.ui.common import empty_state, money
from stocksense.ui.proposal_view import render_detail


def render(db, user) -> None:
    st.title("Approval center")
    st.caption("A human decides every purchase. Approval re-runs the full deterministic audit; "
               "AI review is advisory and can never approve.")
    queue = proposal_service.list_proposals(db, ["PENDING_APPROVAL", "BLOCKED", "NEEDS_REVISION"])
    if not queue:
        empty_state("Nothing is waiting for a decision.")
        return
    labels = {f"{p['reference']} v{p['version']} - {p['status'].replace('_', ' ').lower()} - "
              f"{money(p['total_minor'], p['currency'])}": p["id"] for p in queue}
    pid = labels[st.selectbox("Proposal", list(labels))]
    with st.container(border=True):
        render_detail(db, user, pid, mode="approval")
