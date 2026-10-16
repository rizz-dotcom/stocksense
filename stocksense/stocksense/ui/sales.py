from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from stocksense.security.authorization import Permission
from stocksense.services import inventory_service, sales_service
from stocksense.services.forecast_service import compute_forecasts
from stocksense.services.settings_service import get_settings
from stocksense.ui.common import action, demo_banner, empty_state
from stocksense.ui.importer import render_import


def render(db, user) -> None:
    st.title("Sales and demand")
    settings = get_settings(db)
    can_edit = user.can(Permission.MANAGE_SALES)
    products = inventory_service.list_products(db)
    t_hist, t_rec, t_fix, t_imp = st.tabs(["History and baseline", "Record sales",
                                           "Correct a record", "Import CSV"])
    with t_hist:
        days = st.slider("Show the last N days", 7, 180, max(14, settings.sales_lookback_days))
        sales = sales_service.list_sales(db, start=date.today() - timedelta(days=days))
        if not sales:
            empty_state("No sales recorded in this period. Record daily sales or import a CSV; "
                        "forecasts need history.")
        else:
            if any(s["demo"] for s in sales):
                demo_banner()
            frame = pd.DataFrame(sales)
            fig = px.line(frame.sort_values("sale_date"), x="sale_date", y="quantity",
                          color="product", markers=True,
                          labels={"sale_date": "Date", "quantity": "Units sold",
                                  "product": "Product"})
            fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch")
            st.subheader("Daily-demand baseline")
            with db.session() as s:
                fcs = compute_forecasts(s, settings)
            st.dataframe(pd.DataFrame([{
                "Product": f"{f.sku} {f.name}", "Days of history": f.observations,
                "Avg daily demand": float(f.avg_daily_demand) if f.avg_daily_demand is not None else None,
                "Method": f.baseline_method, "Data quality": f.data_quality} for f in fcs]),
                hide_index=True, width="stretch")
            st.caption(f"Baseline uses up to {settings.sales_lookback_days} days of records. "
                       "Days with no record are treated as unknown, not as zero sales.")
            with st.expander("All records in this period"):
                st.dataframe(frame[["sale_date", "sku", "product", "quantity", "source"]],
                             hide_index=True, width="stretch")

    with t_rec:
        if not can_edit:
            st.info("Your role cannot record sales.")
        elif not products:
            empty_state("Add products first.")
        else:
            labels = {f"{p['sku']} - {p['name']}": p for p in products}
            with st.form("record_sale"):
                c1, c2, c3 = st.columns([2, 1, 1])
                p = labels[c1.selectbox("Product", list(labels))]
                d = c2.date_input("Date", value=date.today(), max_value=date.today())
                q = c3.number_input("Units sold", min_value=0, step=1)
                if st.form_submit_button("Record sales", type="primary"):
                    with action("Sales recorded"):
                        sales_service.record_sale(db, user, p["id"], d, int(q))

    with t_fix:
        if not can_edit:
            st.info("Your role cannot correct sales.")
        else:
            recent = sales_service.list_sales(db, start=date.today() - timedelta(days=90))
            if not recent:
                empty_state("No recent sales records to correct.")
            else:
                labels = {f"{r['sale_date']} {r['sku']} ({r['quantity']})": r for r in recent}
                r = labels[st.selectbox("Record", list(labels))]
                with st.form(f"fix_{r['id']}"):
                    new_q = st.number_input("Correct quantity", min_value=0, step=1,
                                            value=r["quantity"])
                    reason = st.text_input("Reason *", placeholder="e.g. till report re-checked")
                    if st.form_submit_button("Save correction", type="primary"):
                        with action("Correction saved"):
                            sales_service.correct_sale(db, user, r["id"], int(new_q), reason)

    with t_imp:
        if can_edit:
            st.caption("Re-importing a day that already exists with a different quantity "
                       "is treated as a correction and audited.")
            render_import(db, user, "sales")
        else:
            st.info("Your role cannot import sales.")
