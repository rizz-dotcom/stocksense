from __future__ import annotations

import pandas as pd
import streamlit as st

from stocksense.security.authorization import Permission
from stocksense.services import inventory_service, supplier_service
from stocksense.services.settings_service import get_settings
from stocksense.ui.common import action, demo_banner, empty_state, money
from stocksense.ui.importer import render_import
from stocksense.utils.dates import fmt_dt
from stocksense.utils.money import from_minor


def render(db, user) -> None:
    st.title("Suppliers")
    settings = get_settings(db)
    can_edit = user.can(Permission.MANAGE_SUPPLIERS)
    t_quotes, t_sup, t_add, t_import = st.tabs(
        ["Product quotes", "Supplier directory", "Add or edit", "Import CSV"])

    with t_quotes:
        quotes = supplier_service.list_quotes(db, max_quote_age_days=settings.max_quote_age_days)
        if not quotes:
            empty_state("No supplier quotes yet. Add a supplier, then a quote for each product "
                        "it can deliver.")
        else:
            if any(q["demo"] for q in quotes):
                demo_banner("Demo quotes are FICTIONAL: the prices, minimum quantities, stock "
                            "and lead times are invented for demonstration.")
            frame = pd.DataFrame([{
                "Product": f"{q['sku']} {q['product']}", "Supplier": q["supplier"],
                "Unit price": money(q["unit_price_minor"], settings.currency),
                "Min order": q["min_order_qty"], "Available": q["available_qty"],
                "Lead time (days)": q["lead_time_days"], "Quoted": fmt_dt(q["quoted_at"]),
                "Quote status": ("STALE" if q["stale"] else "current")
                + ("" if q["supplier_active"] else ", supplier inactive")
                + (", no stock" if q["available_qty"] == 0 else ""),
                "Data": "demo" if q["demo"] else "real"} for q in quotes])
            st.dataframe(frame, hide_index=True, width="stretch")
            st.caption(f"Quotes older than {settings.max_quote_age_days} days are stale and "
                       "cannot be used in an approved proposal.")

    with t_sup:
        sups = supplier_service.list_suppliers(db)
        if not sups:
            empty_state("No suppliers yet.")
        else:
            st.dataframe(pd.DataFrame([{
                "Code": s["code"], "Name": s["name"], "Contact": s["contact_name"],
                "Phone": s["phone"], "Email": s["email"],
                "Status": "active" if s["active"] else "deactivated",
                "Data": "demo" if s["demo"] else "real"} for s in sups]),
                hide_index=True, width="stretch")

    with t_add:
        if not can_edit:
            st.info("Your role cannot change suppliers.")
        else:
            _supplier_forms(db, user, settings)

    with t_import:
        if can_edit:
            render_import(db, user, "suppliers")
        else:
            st.info("Your role cannot import suppliers.")


def _supplier_forms(db, user, settings) -> None:
    st.subheader("New supplier")
    with st.form("new_supplier"):
        c1, c2 = st.columns(2)
        code = c1.text_input("Supplier code *")
        name = c2.text_input("Supplier name *")
        c3, c4, c5 = st.columns(3)
        contact = c3.text_input("Contact person")
        phone = c4.text_input("Phone")
        email = c5.text_input("Email")
        if st.form_submit_button("Add supplier", type="primary"):
            with action(f"Supplier {code} added"):
                supplier_service.create_supplier(db, user, {
                    "code": code, "name": name, "contact_name": contact or None,
                    "phone": phone or None, "email": email or None})

    sups = supplier_service.list_suppliers(db)
    products = inventory_service.list_products(db)
    if not sups or not products:
        return
    st.subheader("Quote: what a supplier offers for a product")
    sup_labels = {f"{s['code']} - {s['name']}": s for s in sups}
    prod_labels = {f"{p['sku']} - {p['name']}": p for p in products}
    c1, c2 = st.columns(2)
    sup = sup_labels[c1.selectbox("Supplier", list(sup_labels), key="q_sup")]
    prod = prod_labels[c2.selectbox("Product", list(prod_labels), key="q_prod")]
    existing = next((q for q in supplier_service.list_quotes(db, prod["id"], sup["id"])), None)
    with st.form(f"quote_{sup['id']}_{prod['id']}"):
        c3, c4, c5, c6 = st.columns(4)
        price = c3.number_input(f"Unit price ({settings.currency})", min_value=0.0, step=0.5,
                                format="%.2f",
                                value=float(from_minor(existing["unit_price_minor"])) if existing else 0.0)
        moq = c4.number_input("Minimum order qty", min_value=1, step=1,
                              value=existing["min_order_qty"] if existing else 1)
        avail = c5.number_input("Available qty", min_value=0, step=1,
                                value=existing["available_qty"] if existing else 0)
        lead = c6.number_input("Lead time (days)", min_value=0, max_value=365, step=1,
                               value=existing["lead_time_days"] if existing else 1)
        st.caption("Saving stamps the quote with today's date, which makes it current.")
        if st.form_submit_button("Save quote", type="primary"):
            with action("Quote saved"):
                supplier_service.upsert_quote(db, user, {
                    "supplier_id": sup["id"], "product_id": prod["id"],
                    "unit_price": f"{price:.2f}", "min_order_qty": int(moq),
                    "available_qty": int(avail), "lead_time_days": int(lead)})
    if existing and st.button("Remove this quote", key=f"rmq_{existing['quote_id']}"):
        with action("Quote removed"):
            supplier_service.delete_quote(db, user, existing["quote_id"])
            st.rerun()

    st.subheader("Edit or deactivate a supplier")
    s = sup_labels[st.selectbox("Supplier to edit", list(sup_labels), key="edit_sup")]
    with st.form(f"edit_sup_{s['id']}"):
        name = st.text_input("Name", value=s["name"])
        c1, c2, c3 = st.columns(3)
        contact = c1.text_input("Contact", value=s["contact_name"] or "")
        phone = c2.text_input("Phone", value=s["phone"] or "")
        email = c3.text_input("Email", value=s["email"] or "")
        if st.form_submit_button("Save supplier"):
            with action("Supplier updated"):
                supplier_service.update_supplier(db, user, s["id"], {
                    "name": name, "contact_name": contact or None, "phone": phone or None,
                    "email": email or None})
    label = "Deactivate supplier" if s["active"] else "Reactivate supplier"
    if st.button(label, key=f"toggle_sup_{s['id']}"):
        with action("Supplier status changed"):
            supplier_service.set_supplier_active(db, user, s["id"], not s["active"])
            st.rerun()
