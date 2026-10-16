"""Proposal detail page shared by 'Purchase proposals' and 'Approval center'."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from stocksense.exceptions import StockSenseError
from stocksense.security.authorization import Permission
from stocksense.services import ai_service, approval_service, proposal_service
from stocksense.services import purchase_order_service as po_service
from stocksense.services import supplier_service
from stocksense.services.settings_service import get_settings
from stocksense.ui.common import (action, get_config, money, risk_label, show_error,
                                  status_badge)
from stocksense.utils.csv_utils import dataframe_to_safe_csv
from stocksense.utils.dates import fmt_dt

REVISABLE = {"DRAFT", "PENDING_APPROVAL", "NEEDS_REVISION", "BLOCKED"}


@st.cache_resource(show_spinner=False)
def _ai() -> ai_service.AIReviewService:
    return ai_service.AIReviewService(get_config())


def editor_rows_to_lines(rows: list[dict], supplier_ids: dict[str, int]) -> list[dict]:
    """Convert edited table rows into revision lines. Blank quantity means 0.
    Values are passed through unchanged otherwise, so the service layer (not the
    UI) decides what is valid - negative or fractional numbers are rejected there."""
    lines = []
    for r in rows:
        qty = r.get("Quantity")
        if qty is None or (isinstance(qty, float) and pd.isna(qty)):
            qty = 0
        elif isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        code = r.get("Supplier")
        lines.append({"product_id": int(r["product_id"]),
                      "supplier_id": supplier_ids.get(code) if code else None,
                      "quantity": qty})
    return lines


def _viewed_version(detail: dict) -> int:
    key = f"viewed_v_{detail['id']}"
    if key not in st.session_state:
        st.session_state[key] = detail["current_version"]
    return st.session_state[key]


def _lines_frame(version: dict, cur: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "Risk": risk_label(i["risk_level"]), "Product": f"{i['sku']} {i['product']}",
        "Supplier": i["supplier"] or "-", "Suggested": i["suggested_qty"],
        "Order qty": i["quantity"], "Unit": i["unit"],
        "Unit price": money(i["unit_price_minor"], cur) if i["unit_price_minor"] else "-",
        "Line total": money(i["line_total_minor"], cur),
        "Funded": "yes" if i["funded"] else "NO", "Note": i["note"] or ""}
        for i in version["items"]])


def render_detail(db, user, proposal_id: int, mode: str) -> None:
    try:
        detail = proposal_service.get_proposal_detail(db, proposal_id)
    except StockSenseError as exc:
        show_error(exc)
        return
    cur = detail["currency"]
    viewed = _viewed_version(detail)
    version = next(v for v in detail["versions"] if v["version"] == viewed)

    head, badge = st.columns([4, 1])
    head.subheader(f"{detail['reference']}: {detail['title']}")
    with badge:
        status_badge(detail["status"])
    st.caption(f"Version {viewed} of {detail['current_version']}. Created by "
               f"{detail['created_by']} on {fmt_dt(detail['created_at'])}. "
               f"Horizon {detail['horizon_days']} days.")
    if viewed != detail["current_version"]:
        st.warning(f"You are viewing version {viewed}, but version "
                   f"{detail['current_version']} is now the latest. Decisions on an old "
                   "version are refused as stale.")
        if st.button("Load the latest version", key=f"reload_{proposal_id}"):
            st.session_state[f"viewed_v_{proposal_id}"] = detail["current_version"]
            st.rerun()

    funded = sum(1 for i in version["items"] if i["funded"])
    c = st.columns(4)
    c[0].metric("Budget", money(detail["budget_minor"], cur))
    c[1].metric("Recalculated total", money(version["total_minor"], cur))
    c[2].metric("Remaining", money(detail["budget_minor"] - version["total_minor"], cur))
    c[3].metric("Funded lines", f"{funded} of {len(version['items'])}")
    st.dataframe(_lines_frame(version, cur), hide_index=True, width="stretch")

    tabs = st.tabs(["Deterministic audit", "Supplier alternatives", "Inventory analysis",
                    "AI review", "History and orders"])
    with tabs[0]:
        try:
            live = proposal_service.live_validation(db, proposal_id)
        except StockSenseError as exc:
            show_error(exc)
            live = None
        if live is not None:
            st.caption(f"Re-checked now against current quotes and stock "
                       f"({fmt_dt(live.checked_at)}). Approval runs this same check again.")
            if live.passed:
                st.success(f"Passed. Total {money(live.total_minor, cur)} within budget "
                           f"{money(live.budget_minor, cur)}; remaining "
                           f"{money(live.remaining_minor, cur)}.")
            else:
                st.error("Failed - approval is blocked until this is fixed:")
                for e in live.errors:
                    st.markdown(f"- {e}")
            if live.warnings:
                st.warning("Warnings for the human reviewer (arithmetic can be correct while the "
                           "plan is still operationally risky):")
                for w in live.warnings:
                    st.markdown(f"- {w}")
    with tabs[1]:
        selections = detail["versions"][-1]["inputs"].get("selections", {})
        names = {str(i["product_id"]): f"{i['sku']} {i['product']}" for i in version["items"]}
        for pid, sel in selections.items():
            st.markdown(f"**{names.get(pid, 'Product ' + pid)}** - {sel['explanation']}")
            if sel["candidates"]:
                st.dataframe(pd.DataFrame([{
                    "Rank": c["rank"], "Supplier": c["supplier_name"],
                    "Unit price": money(c["unit_price_minor"], cur) if c["unit_price_minor"] else "-",
                    "MOQ": c["min_order_qty"], "Available": c["available_qty"],
                    "Lead (days)": c["lead_time_days"], "Order qty": c["order_qty"],
                    "Total": money(c["total_minor"], cur) if c["total_minor"] else "-",
                    "Late for stockout": "YES" if c["late"] else "no",
                    "Usable": "yes" if c["eligible"] else "no",
                    "Issues": "; ".join(c["issues"])} for c in sel["candidates"]]),
                    hide_index=True, width="stretch")
        st.caption("Alternatives were evaluated when version 1 was generated. A reviewer can "
                   "switch supplier in a revision; the switch is fully re-validated.")
    with tabs[2]:
        forecasts = detail["versions"][-1]["inputs"].get("forecasts", [])
        if forecasts:
            st.caption("Snapshot taken when the proposal was generated.")
            st.dataframe(pd.DataFrame([{
                "Risk": risk_label(f["risk_level"]), "Product": f"{f['sku']} {f['name']}",
                "Avg/day": f["avg_daily_demand"], "Data quality": f["data_quality"],
                "Stock": f["usable_stock"], "Days cover": f["days_cover"],
                "Fastest lead": f["min_lead_time_days"], "Suggested": f["suggested_qty"],
                "Why": " ".join(f["reasons"][:2])} for f in forecasts]),
                hide_index=True, width="stretch")
    with tabs[3]:
        _render_ai(db, user, detail)
    with tabs[4]:
        st.markdown("**Versions**")
        st.dataframe(pd.DataFrame([{
            "Version": v["version"], "Created": fmt_dt(v["created_at"]), "By": v["created_by"],
            "Total": money(v["total_minor"], cur),
            "Validation at save": "passed" if v["validation_passed"] else "failed",
            "Note": v["change_note"] or ""} for v in detail["versions"]]),
            hide_index=True, width="stretch")
        if detail["decisions"]:
            st.markdown("**Decisions**")
            st.dataframe(pd.DataFrame([{
                "Version": d["version"], "Decision": d["decision"], "By": d["decided_by"],
                "When": fmt_dt(d["decided_at"]), "Reason": d["reason"]}
                for d in detail["decisions"]]), hide_index=True, width="stretch")
        orders = po_service.list_orders(db, proposal_id)
        if orders:
            st.markdown("**Simulated purchase orders** (no real order was placed)")
            st.dataframe(pd.DataFrame([{
                "PO": o["po_number"], "Supplier": o["supplier"], "Status": o["status"],
                "Total": money(o["total_minor"], cur)} for o in orders]),
                hide_index=True, width="stretch")

    export = _lines_frame(version, cur)
    st.download_button("Export this version (CSV)",
                       dataframe_to_safe_csv(export, banner=f"{detail['reference']} v{viewed}. "
                                             "Figures derived from stored data; demo data is "
                                             "fictional. Not a real purchase order."),
                       file_name=f"{detail['reference']}_v{viewed}.csv", mime="text/csv",
                       key=f"exp_{proposal_id}_{viewed}")

    if mode == "proposal":
        _proposal_actions(db, user, detail, version)
    else:
        _decision_form(db, user, detail, viewed)


def _render_ai(db, user, detail: dict) -> None:
    settings = get_settings(db)
    service = _ai()
    review = ai_service.latest_review_for_version(db, detail["id"], detail["current_version"])
    older = [r for r in detail["ai_reviews"] if r["version"] != detail["current_version"]]
    st.caption("Advisory only. The AI reviews figures calculated by STOCKSENSE; it cannot "
               "approve, change quantities, prices or budgets, or place orders.")
    if review and review["status"] == "OK":
        res = review["result"]
        st.markdown(f"**Review of version {review['version']}** ({review['model']}, "
                    f"{fmt_dt(review['created_at'])}) - overall risk: **{res['overall_risk']}**")
        st.write(res["summary"])
        for note in res.get("system_notes", []):
            st.error(note)
        for title, key in (("Warnings", "warnings"), ("Questions for the owner", "questions_for_owner"),
                           ("Questionable assumptions", "questionable_assumptions"),
                           ("Missing evidence", "missing_evidence")):
            if res.get(key):
                st.markdown(f"**{title}**")
                for item in res[key]:
                    st.markdown(f"- {item}")
    elif review:
        st.warning(f"The last AI review attempt for this version failed ({review['status']}): "
                   f"{review['error']}")
    else:
        st.info("No AI review exists for the current version.")
    if older and not review:
        st.caption(f"{len(older)} older review(s) exist for previous versions; they do not apply "
                   "to this version.")
    if not settings.ai_enabled:
        st.info("AI review is switched off in Settings.")
    elif not service.configured:
        st.info("AI review is unavailable: GEMINI_API_KEY is not set. Everything else works. "
                "Add the key to your .env file and restart to enable it.")
    elif user.can(Permission.REQUEST_AI_REVIEW):
        if st.button("Request AI review of the current version", key=f"ai_{detail['id']}"):
            with st.spinner("Asking Gemini to review the proposal..."):
                with action():
                    out = ai_service.review_proposal(db, user, detail["id"], service)
                    if out["status"] != "OK":
                        st.warning(out["error"])
                    st.rerun()


def _proposal_actions(db, user, detail: dict, version: dict) -> None:
    if not user.can(Permission.MANAGE_PROPOSALS):
        return
    pid, status = detail["id"], detail["status"]
    if version["version"] != detail["current_version"]:
        return
    st.divider()
    if status == "DRAFT":
        if st.button("Submit for approval", type="primary", key=f"submit_{pid}"):
            with action():
                new = proposal_service.submit_proposal(db, user, pid, version["version"])
                st.session_state["_flash"] = ("Submitted for approval" if new == "PENDING_APPROVAL"
                                              else "Blocked by validation - see the audit tab")
                st.rerun()
    if status in REVISABLE:
        with st.expander("Revise quantities or suppliers", expanded=status in ("BLOCKED", "NEEDS_REVISION")):
            st.caption("Any change creates a new version, recalculates everything and needs a new "
                       "approval. Set quantity 0 to leave a line unfunded.")
            quotes = supplier_service.list_quotes(db)
            sup_by_label = {}
            for q in quotes:
                sup_by_label[f"{q['supplier_code']}"] = q["supplier_id"]
            labels = sorted(sup_by_label)
            id_to_label = {v: k for k, v in sup_by_label.items()}
            frame = pd.DataFrame([{
                "product_id": i["product_id"], "Product": f"{i['sku']} {i['product']}",
                "Suggested": i["suggested_qty"],
                "Supplier": id_to_label.get(i["supplier_id"]),
                "Quantity": i["quantity"]} for i in version["items"]])
            edited = st.data_editor(
                frame, hide_index=True, width="stretch", key=f"editor_{pid}_{version['version']}",
                disabled=["product_id", "Product", "Suggested"],
                column_config={
                    "product_id": None,
                    "Supplier": st.column_config.SelectboxColumn("Supplier code", options=labels),
                    "Quantity": st.column_config.NumberColumn("Order quantity", min_value=0,
                                                              step=1, format="%d")})
            note = st.text_input("What changed and why *", key=f"note_{pid}")
            if st.button("Save as new version", type="primary", key=f"revise_{pid}"):
                with action():
                    lines = editor_rows_to_lines(edited.to_dict(orient="records"), sup_by_label)
                    new_v = proposal_service.revise_proposal(db, user, pid, version["version"],
                                                             lines, note)
                    st.session_state[f"viewed_v_{pid}"] = new_v
                    st.session_state["_flash"] = f"Saved as version {new_v}"
                    st.rerun()


def _decision_form(db, user, detail: dict, viewed: int) -> None:
    st.divider()
    if not user.can(Permission.APPROVE_PROPOSALS):
        st.info("Your role can review but not decide on proposals. A manager or admin must approve.")
        return
    if detail["status"] not in ("PENDING_APPROVAL", "BLOCKED", "NEEDS_REVISION"):
        st.caption(f"No decision possible: status is {detail['status']}.")
        return
    st.subheader("Your decision")
    options = ["Approve", "Request revision", "Reject"] if detail["status"] == "PENDING_APPROVAL" \
        else ["Reject"]
    with st.form(f"decide_{detail['id']}_{viewed}"):
        choice = st.radio("Decision", options, horizontal=True)
        reason = st.text_area("Reason (required to reject or request revision)")
        confirm = st.checkbox("I reviewed the line items, warnings and supplier trade-offs. "
                              "I understand approval creates SIMULATED purchase orders only.")
        submitted = st.form_submit_button("Record decision", type="primary")
    if submitted:
        if choice == "Approve" and not confirm:
            st.error("Tick the confirmation box to approve.")
            return
        decision = {"Approve": "APPROVED", "Request revision": "NEEDS_REVISION",
                    "Reject": "REJECTED"}[choice]
        try:
            result = approval_service.decide(db, user, detail["id"], viewed, decision, reason)
            msg = f"{detail['reference']} {decision.replace('_', ' ').lower()}"
            if result.get("purchase_order_ids"):
                msg += f"; {len(result['purchase_order_ids'])} simulated purchase order(s) created"
            st.session_state["_flash"] = msg
            st.rerun()
        except StockSenseError as exc:
            show_error(exc)
