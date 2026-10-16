from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stocksense.services import dashboard_service
from stocksense.ui.common import money, risk_label, table
from stocksense.utils.dates import fmt_dt


def render(db, user) -> None:
    data = dashboard_service.summary(db)
    cfg = data["settings"]
    cur = cfg.currency
    st.title(cfg.business_name)
    st.caption(f"Today's risks and decisions. Planning horizon {cfg.default_horizon_days} days. "
               "Demand figures are averages of recorded sales, not machine-learning predictions.")

    if not data["tracked"]:
        st.info("No products yet. Add products under Inventory, or an administrator can load "
                "the fictional demo data under Settings.")
        return

    c = st.columns(6)
    c[0].metric("Products tracked", data["tracked"])
    c[1].metric("Need attention", data["low_stock"], help="High or medium risk")
    c[2].metric("Critical", data["critical"], help="High risk")
    c[3].metric("Stockout before delivery", data["stockout_before_delivery"])
    c[4].metric("Pending approvals", data["pending"],
                help=f"{data['blocked']} blocked proposal(s)" if data["blocked"] else None)
    c[5].metric("Open simulated orders", money(data["po_open_minor"], cur))

    if data["latest_budget"]:
        b = data["latest_budget"]
        st.progress(min(1.0, b["utilization"]),
                    text=f"Latest proposal {b['reference']} ({b['status'].replace('_', ' ').lower()}): "
                         f"{money(b['total_minor'], cur)} of {money(b['budget_minor'], cur)} budget "
                         f"({b['utilization']:.0%})")

    attention = [f for f in data["forecasts"] if f.risk_level in ("HIGH", "MEDIUM", "UNKNOWN")]
    left, right = st.columns([1.35, 1])
    with left:
        st.subheader("What needs a decision")
        if not attention:
            st.success("No product is below its thresholds for the planning horizon.")
        for f in attention:
            with st.container(border=True):
                st.markdown(f"**{f.name}** ({f.sku}) {risk_label(f.risk_level)}")
                cover = f"{f.days_cover} days of cover" if f.days_cover is not None else "cover unknown"
                lead = f"fastest delivery {f.min_lead_time_days} day(s)" \
                    if f.min_lead_time_days is not None else "no valid supplier quote"
                st.caption(f"Stock {f.usable_stock} {f.unit}, {cover}, {lead}. "
                           f"Suggested order: {f.suggested_qty} {f.unit}.")
                for r in f.reasons[:3]:
                    st.markdown(f"- {r}")
                for i in f.issues[:2]:
                    st.markdown(f"- _{i}_")
    with right:
        st.subheader("Stock cover vs delivery time")
        rows = [f for f in data["forecasts"] if f.days_cover is not None]
        if rows:
            frame = pd.DataFrame({"product": [f.name for f in rows],
                                  "cover": [float(f.days_cover) for f in rows],
                                  "lead": [f.min_lead_time_days for f in rows],
                                  "risk": [f.risk_level for f in rows]})
            colors = frame["risk"].map({"HIGH": "#B42318", "MEDIUM": "#B54708",
                                        "LOW": "#2E7D4F"}).fillna("#7A7A7A")
            fig = go.Figure()
            fig.add_bar(y=frame["product"], x=frame["cover"], orientation="h",
                        marker_color=colors, name="Days of stock cover")
            fig.add_scatter(y=frame["product"], x=frame["lead"], mode="markers",
                            marker=dict(symbol="line-ns", size=22, line=dict(width=3, color="#1C2421")),
                            name="Fastest delivery (days)")
            fig.update_layout(height=60 + 48 * len(frame), margin=dict(l=0, r=10, t=10, b=0),
                              xaxis_title="Days", legend=dict(orientation="h", y=-0.25))
            st.plotly_chart(fig, width="stretch")
            st.caption("A bar shorter than its delivery marker means stock likely runs out "
                       "before a new delivery can arrive.")
        else:
            st.caption("No demand estimates yet: record sales to see stock cover.")
        st.subheader("Recent activity")
        table([{"When": fmt_dt(e["timestamp"]), "Who": e["actor"], "What": e["message"]}
               for e in data["recent_events"]])
