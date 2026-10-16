from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from stocksense.security.authorization import Permission
from stocksense.services import inventory_service
from stocksense.services.forecast_service import compute_forecasts
from stocksense.services.settings_service import get_settings
from stocksense.ui.common import action, demo_banner, empty_state, risk_label
from stocksense.ui.importer import render_import
from stocksense.utils.csv_utils import dataframe_to_safe_csv


def _status_map(db):
    settings = get_settings(db)
    with db.session() as s:
        return {f.product_id: f for f in compute_forecasts(s, settings)}


def render(db, user) -> None:
    st.title("Inventory")
    can_edit = user.can(Permission.MANAGE_INVENTORY)
    tab_list, tab_add, tab_edit, tab_import = st.tabs(
        ["Stock list", "Add product", "Edit or deactivate", "Import CSV"])

    with tab_list:
        c1, c2 = st.columns([3, 1])
        search = c1.text_input("Search by SKU, name or category", key="inv_search")
        show_inactive = c2.checkbox("Show deactivated", key="inv_inactive")
        products = inventory_service.list_products(db, show_inactive, search)
        if not products:
            empty_state("No products match. Add one in the 'Add product' tab or import a CSV.")
        else:
            if any(p["demo"] for p in products):
                demo_banner()
            status = _status_map(db)
            rows = []
            for p in products:
                f = status.get(p["id"])
                rows.append({
                    "SKU": p["sku"], "Product": p["name"], "Category": p["category"],
                    "Stock": p["stock_qty"], "Unit": p["unit"], "Safety stock": p["safety_stock"],
                    "Reorder at": p["reorder_threshold"],
                    "Expiry": p["expiry_date"].isoformat() if p["expiry_date"] else "not tracked",
                    "Risk": risk_label(f.risk_level) if f else "Deactivated",
                    "Days cover": float(f.days_cover) if f and f.days_cover is not None else None,
                    "Status": "active" if p["active"] else "deactivated",
                    "Data": "demo" if p["demo"] else "real"})
            frame = pd.DataFrame(rows)
            st.dataframe(frame, hide_index=True, width="stretch")
            st.download_button("Export inventory summary (CSV)",
                               dataframe_to_safe_csv(frame, banner="STOCKSENSE inventory export. "
                                                     "Rows marked demo are fictional."),
                               file_name="inventory_summary.csv", mime="text/csv")

    with tab_add:
        if not can_edit:
            st.info("Your role cannot add products.")
        else:
            with st.form("add_product", clear_on_submit=False):
                c1, c2, c3 = st.columns(3)
                sku = c1.text_input("SKU *", help="Letters, digits, - _ . (max 40)")
                name = c2.text_input("Product name *")
                category = c3.text_input("Category", value="General")
                c4, c5, c6, c7 = st.columns(4)
                unit = c4.text_input("Unit", value="pcs")
                stock = c5.number_input("Stock on hand", min_value=0, step=1, value=0)
                safety = c6.number_input("Safety stock", min_value=0, step=1, value=0)
                reorder = c7.number_input("Reorder threshold", min_value=0, step=1, value=0)
                track = st.checkbox("Track expiry date")
                expiry = st.date_input("Expiry date", value=date.today(), disabled=False)
                if st.form_submit_button("Add product", type="primary"):
                    with action(f"Product {sku} added"):
                        inventory_service.create_product(db, user, {
                            "sku": sku, "name": name, "category": category, "unit": unit,
                            "stock_qty": int(stock), "safety_stock": int(safety),
                            "reorder_threshold": int(reorder),
                            "expiry_date": expiry if track else None})

    with tab_edit:
        products = inventory_service.list_products(db, include_inactive=True)
        if not can_edit:
            st.info("Your role cannot edit products.")
        elif not products:
            empty_state("No products yet.")
        else:
            labels = {f"{p['sku']} - {p['name']}" + ("" if p["active"] else " (deactivated)"): p
                      for p in products}
            p = labels[st.selectbox("Product", list(labels), key="inv_edit_sel")]
            with st.form(f"edit_{p['id']}"):
                c1, c2, c3 = st.columns(3)
                name = c1.text_input("Name", value=p["name"])
                category = c2.text_input("Category", value=p["category"])
                unit = c3.text_input("Unit", value=p["unit"])
                c4, c5, c6 = st.columns(3)
                stock = c4.number_input("Stock on hand", min_value=0, step=1, value=p["stock_qty"])
                safety = c5.number_input("Safety stock", min_value=0, step=1, value=p["safety_stock"])
                reorder = c6.number_input("Reorder threshold", min_value=0, step=1,
                                          value=p["reorder_threshold"])
                track = st.checkbox("Track expiry date", value=p["expiry_date"] is not None)
                expiry = st.date_input("Expiry date", value=p["expiry_date"] or date.today())
                reason = st.text_input("Reason for change (recorded in audit log)",
                                       placeholder="e.g. weekly stock count")
                if st.form_submit_button("Save changes", type="primary"):
                    with action("Product updated"):
                        changes = inventory_service.update_product(db, user, p["id"], {
                            "name": name, "category": category, "unit": unit,
                            "stock_qty": int(stock), "safety_stock": int(safety),
                            "reorder_threshold": int(reorder),
                            "expiry_date": expiry if track else None}, reason)
                        if not changes:
                            st.info("Nothing changed.")
            if p["active"]:
                if st.button("Deactivate product", key=f"deact_{p['id']}",
                             help="Hidden from forecasts and proposals; history is kept."):
                    with action("Product deactivated"):
                        inventory_service.set_product_active(db, user, p["id"], False)
                        st.rerun()
            elif st.button("Reactivate product", key=f"react_{p['id']}"):
                with action("Product reactivated"):
                    inventory_service.set_product_active(db, user, p["id"], True)
                    st.rerun()

    with tab_import:
        if can_edit:
            render_import(db, user, "inventory")
        else:
            st.info("Your role cannot import inventory.")
