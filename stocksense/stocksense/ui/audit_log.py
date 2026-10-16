from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from stocksense.security.authorization import Permission, require
from stocksense.services import audit_service
from stocksense.ui.common import empty_state
from stocksense.utils.csv_utils import dataframe_to_safe_csv


def render(db, user) -> None:
    require(user, Permission.VIEW_AUDIT)
    st.title("Audit log")
    st.caption("Read-only, append-only history. The database refuses edits and deletions of "
               "audit and decision records.")
    values = audit_service.distinct_values(db)
    c1, c2, c3, c4 = st.columns(4)
    actions = c1.multiselect("Action", values["actions"])
    entity = c2.selectbox("Record type", [""] + values["entity_types"])
    actor = c3.selectbox("Actor", [""] + values["actors"])
    outcome = c4.selectbox("Outcome", ["", "SUCCESS", "FAILURE", "BLOCKED", "STALE", "REFUSED",
                                       "DENIED"])
    c5, c6, c7 = st.columns([1, 1, 2])
    start = c5.date_input("From", value=date.today() - timedelta(days=30))
    end = c6.date_input("To", value=date.today())
    text = c7.text_input("Search text")
    events = audit_service.list_events(db, actions=actions or None, entity_type=entity or None,
                                       actor=actor or None, outcome=outcome or None, start=start,
                                       end=end, text=text or None, limit=2000)
    if not events:
        empty_state("No events match these filters.")
        return
    frame = pd.DataFrame([{
        "Time (UTC)": e["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), "Actor": e["actor"],
        "Action": e["action"], "Record": f"{e['entity_type']} {e['entity_id'] or ''}".strip(),
        "Outcome": e["outcome"], "From": e["previous_state"] or "", "To": e["new_state"] or "",
        "Explanation": e["message"], "Event ID": e["event_id"]} for e in events])
    st.dataframe(frame, hide_index=True, width="stretch", height=520)
    st.caption(f"{len(events)} event(s) shown (max 2000).")
    st.download_button("Export filtered events (CSV)",
                       dataframe_to_safe_csv(frame, banner="STOCKSENSE audit export"),
                       file_name="audit_log.csv", mime="text/csv")
