from __future__ import annotations

import pandas as pd
import streamlit as st

from stocksense.security.authorization import Permission
from stocksense.services import forecast_service
from stocksense.services.settings_service import get_settings
from stocksense.ui.common import action, empty_state, risk_label
from stocksense.utils.dates import fmt_dt

METHOD = """
**How the numbers are calculated** (a transparent demand *estimate*, not machine learning)

* **Average daily demand** - mean of recorded daily sales in the look-back window; with 7+
  recorded days, the most recent 7 days are used. Days with no record are unknown, not zero.
* **Forecast demand** - average x horizon, rounded up to whole units.
* **Target stock** - forecast demand + safety stock.
* **Effective available stock** - usable on-hand stock (expired batches count as 0) plus units
  on open *simulated* purchase orders (if enabled in Settings). A received order stops counting
  as on-order when its units are added to stock, so nothing is counted twice.
* **Suggested order** - max(0, target stock - effective available stock).
* **Stockout before delivery** - days of cover (on-hand only) <= fastest valid supplier lead
  time. Equal counts as a risk.
"""


def render(db, user) -> None:
    st.title("Forecasts")
    settings = get_settings(db)
    horizon = st.slider("Planning horizon (days)", 1, 30, settings.default_horizon_days)
    can_save = user.can(Permission.RUN_FORECAST)
    if can_save and st.button("Save forecast snapshot",
                              help="Stores the current results and records an audit event."):
        with action("Forecast snapshot saved"):
            forecast_service.run_forecast(db, user, horizon, save_snapshot=True)
    results = forecast_service.run_forecast(db, user, horizon, save_snapshot=False)
    if not results:
        empty_state("No active products to forecast.")
        return
    st.dataframe(pd.DataFrame([{
        "Risk": risk_label(r.risk_level), "Product": f"{r.sku} {r.name}",
        "Avg/day": float(r.avg_daily_demand) if r.avg_daily_demand is not None else None,
        "Data": r.data_quality, "Stock": r.usable_stock, "On order (sim.)": r.on_order_qty,
        "Days cover": float(r.days_cover) if r.days_cover is not None else None,
        "Fastest lead": r.min_lead_time_days, f"Demand {horizon}d": r.forecast_demand,
        "Safety": r.safety_stock, "Target": r.target_stock, "Suggested": r.suggested_qty,
        "Expiry": r.expiry_status.replace("_", " ").lower()} for r in results]),
        hide_index=True, width="stretch")
    st.subheader("Why each product got its rating")
    for r in results:
        with st.expander(f"{risk_label(r.risk_level)}  {r.sku} {r.name}"):
            for reason in r.reasons:
                st.markdown(f"- {reason}")
            for issue in r.issues:
                st.warning(issue)
            st.caption(f"Baseline: {r.baseline_method}. Target {r.target_stock} = demand "
                       f"{r.forecast_demand} + safety {r.safety_stock}. Effective stock "
                       f"{r.effective_stock} = usable {r.usable_stock} + on order {r.on_order_qty}.")
    with st.expander("Calculation method and assumptions"):
        st.markdown(METHOD)
    snaps = forecast_service.recent_snapshots(db)
    if snaps:
        st.caption("Recent saved snapshots: " + "; ".join(
            f"#{s['id']} {fmt_dt(s['created_at'])} by {s['created_by']} ({s['horizon_days']}d)"
            for s in snaps[:5]))
