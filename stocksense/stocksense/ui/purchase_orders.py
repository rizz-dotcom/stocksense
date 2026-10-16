from __future__ import annotations

import pandas as pd
import streamlit as st

from stocksense.security.authorization import Permission
from stocksense.services import audit_service
from stocksense.services import purchase_order_service as po_service
from stocksense.ui.common import action, empty_state, money, simulated_banner, status_badge
from stocksense.utils.csv_utils import dataframe_to_safe_csv
from stocksense.utils.dates import fmt_dt


def render(db, user) -> None:
    st.title("Simulated purchase orders")
    simulated_banner()
    orders = po_service.list_orders(db)
    if not orders:
        empty_state("No simulated orders yet. They appear after a proposal is approved.")
        return
    frame = pd.DataFrame([{
        "PO": o["po_number"], "Supplier": o["supplier"], "Status": o["status"],
        "Total": money(o["total_minor"], o["currency"]), "Proposal": o["proposal_id"],
        "Version": o["proposal_version"], "Created": fmt_dt(o["created_at"]),
        "Simulated": "yes" if o["demo_only"] else "?",
        "Real order placed": "no" if not o["real_supplier_order_placed"] else "YES"}
        for o in orders])
    st.dataframe(frame, hide_index=True, width="stretch")
    st.download_button("Export orders (CSV)",
                       dataframe_to_safe_csv(frame, banner="SIMULATED purchase orders - no real "
                                             "supplier order was placed."),
                       file_name="simulated_purchase_orders.csv", mime="text/csv")
    labels = {f"{o['po_number']} ({o['status']})": o["id"] for o in orders}
    oid = labels[st.selectbox("Open order", list(labels))]
    order = po_service.get_order(db, oid)
    with st.container(border=True):
        h, b = st.columns([4, 1])
        h.subheader(f"{order['po_number']} - {order['supplier']}")
        with b:
            status_badge(order["status"])
        st.caption(f"From proposal {order['proposal_id']} v{order['proposal_version']}, approved "
                   f"by {order['created_by']} on {fmt_dt(order['created_at'])}. "
                   "SIMULATED: demo_only = true, real_supplier_order_placed = false.")
        st.dataframe(pd.DataFrame([{
            "Product": f"{i['sku']} {i['product']}", "Quantity": i["quantity"], "Unit": i["unit"],
            "Unit price": money(i["unit_price_minor"], order["currency"]),
            "Line total": money(i["line_total_minor"], order["currency"])}
            for i in order["items"]]), hide_index=True, width="stretch")
        st.metric("Order total", money(order["total_minor"], order["currency"]))
        if order["status"] == "SIMULATED_OPEN" and user.can(Permission.RECEIVE_ORDERS):
            st.caption("When goods physically arrive, record it here. This adds the quantities "
                       "to STOCKSENSE stock only; no external system is changed.")
            if st.button("Record delivery received", key=f"recv_{oid}"):
                with action("Delivery recorded; stock updated"):
                    po_service.mark_received(db, user, oid)
                    st.rerun()
        events = audit_service.list_events(db, entity_type="purchase_order", entity_id=str(oid))
        if events:
            st.markdown("**Event history**")
            st.dataframe(pd.DataFrame([{"When": fmt_dt(e["timestamp"]), "Who": e["actor"],
                                        "Event": e["action"], "Details": e["message"]}
                                       for e in events]), hide_index=True, width="stretch")
