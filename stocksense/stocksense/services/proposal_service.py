"""Purchase proposal generation, versioned revision and submission."""
from __future__ import annotations

import json
import secrets

from sqlalchemy import select

from stocksense.exceptions import (DomainValidationError, NotFoundError, StaleVersionError)
from stocksense.models import (AIReview, ApprovalDecision, Product, Proposal, ProposalItem,
                               ProposalVersion, Supplier)
from stocksense.schemas import RevisionLine, validate_model
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.services.approval_service import assert_transition
from stocksense.services.forecast_service import RISK_ORDER, compute_forecasts
from stocksense.services.settings_service import get_settings
from stocksense.services.supplier_service import quote_options_for_product, select_supplier
from stocksense.services.validation_service import LineDraft, ValidationReport, validate_lines
from stocksense.utils.dates import utcnow
from stocksense.utils.money import format_money, money_to_minor


def _store_version(session, proposal: Proposal, version_no: int, report: ValidationReport,
                   inputs: dict, actor: str, note: str | None) -> ProposalVersion:
    version = ProposalVersion(
        proposal_id=proposal.id, version=version_no, created_by=actor,
        total_minor=report.total_minor, validation_passed=report.passed,
        validation_json=json.dumps(report.to_dict(), default=str),
        inputs_json=json.dumps(inputs, default=str), change_note=note)
    session.add(version)
    session.flush()
    for line in report.lines:
        session.add(ProposalItem(
            version_id=version.id, product_id=line.product_id, supplier_id=line.supplier_id,
            quantity=line.quantity, suggested_qty=line.suggested_qty,
            unit_price_minor=line.unit_price_minor, line_total_minor=line.line_total_minor,
            funded=line.funded, risk_level=line.risk_level, note=line.note))
    session.flush()
    return version


def generate_proposal(db, actor: UserContext, *, budget, horizon_days: int,
                      title: str | None = None) -> int:
    """Build a DRAFT proposal from deterministic forecasts and supplier rules."""
    require(actor, Permission.MANAGE_PROPOSALS)
    budget_minor = money_to_minor(budget, field="Budget", allow_zero=False)
    if not isinstance(horizon_days, int) or not 1 <= horizon_days <= 90:
        raise DomainValidationError("Planning horizon must be between 1 and 90 days.")
    settings = get_settings(db)
    with db.session() as s:
        forecasts = compute_forecasts(s, settings, horizon_days)
        needed = [f for f in forecasts if f.valid and f.suggested_qty > 0]
        invalid = [f for f in forecasts if not f.valid]
        if not needed:
            raise DomainValidationError(
                "No product currently needs replenishment for this horizon, so no proposal "
                "was created." + (f" {len(invalid)} product(s) have invalid data." if invalid else ""))

        selections = {}
        for f in needed:
            sel = select_supplier(f.suggested_qty, quote_options_for_product(s, f.product_id),
                                  stockout_in_days=f.days_cover, urgent=f.risk_level == "HIGH",
                                  max_quote_age_days=settings.max_quote_age_days)
            selections[f.product_id] = sel

        # Fund the most urgent lines first; never silently drop the rest.
        ordered = sorted(needed, key=lambda f: (RISK_ORDER[f.risk_level],
                                                float(f.days_cover) if f.days_cover is not None else 1e9,
                                                f.sku))
        remaining = budget_minor
        drafts: list[LineDraft] = []
        for f in ordered:
            sel = selections[f.product_id]
            if sel.selected is None:
                drafts.append(LineDraft(f.product_id, None, 0, False, f.suggested_qty,
                                        note=f"Unsourced: {sel.explanation}"))
                continue
            cost = sel.selected.total_minor
            if cost <= remaining:
                drafts.append(LineDraft(f.product_id, sel.selected.supplier_id,
                                        sel.selected.order_qty, True, f.suggested_qty,
                                        note=sel.explanation))
                remaining -= cost
            else:
                drafts.append(LineDraft(
                    f.product_id, sel.selected.supplier_id, 0, False, f.suggested_qty,
                    note=(f"Unfunded: {sel.selected.order_qty} units would cost "
                          f"{format_money(cost, settings.currency)} but only "
                          f"{format_money(remaining, settings.currency)} of budget remained.")))

        report = validate_lines(s, drafts, budget_minor=budget_minor, currency=settings.currency,
                                settings=settings, horizon_days=horizon_days)
        reference = f"PR-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"
        proposal = Proposal(reference=reference,
                            title=(title or f"Replenishment for {horizon_days} days").strip()[:150],
                            status="DRAFT", current_version=1, budget_minor=budget_minor,
                            currency=settings.currency, horizon_days=horizon_days,
                            created_by=actor.username)
        s.add(proposal)
        s.flush()
        inputs = {
            "generated_at": utcnow().isoformat(), "horizon_days": horizon_days,
            "budget_minor": budget_minor, "currency": settings.currency,
            "settings": settings.__dict__,
            "forecasts": [f.to_dict() for f in forecasts],
            "selections": {str(pid): sel.to_dict() for pid, sel in selections.items()},
        }
        _store_version(s, proposal, 1, report, inputs, actor.username, "Generated")
        audit_service.record(
            s, actor=actor.username, action="PROPOSAL_CREATED", entity_type="proposal",
            entity_id=str(proposal.id), new_state="DRAFT",
            message=f"{reference} v1 created: {sum(1 for d in drafts if d.funded)} funded line(s), "
                    f"total {format_money(report.total_minor, settings.currency)} of budget "
                    f"{format_money(budget_minor, settings.currency)}; validation "
                    f"{'passed' if report.passed else 'failed'}.")
        return proposal.id


def _current_drafts(session, proposal: Proposal, *, keep_prices: bool) -> list[LineDraft]:
    version = session.scalar(select(ProposalVersion).where(
        ProposalVersion.proposal_id == proposal.id,
        ProposalVersion.version == proposal.current_version))
    return [LineDraft(i.product_id, i.supplier_id, i.quantity, i.funded, i.suggested_qty,
                      i.unit_price_minor if (keep_prices and i.funded) else None, i.note)
            for i in version.items]


def revalidate(session, proposal: Proposal, settings) -> ValidationReport:
    """Re-run the deterministic audit on the stored current version.
    Stored prices must still match current quotes."""
    return validate_lines(session, _current_drafts(session, proposal, keep_prices=True),
                          budget_minor=proposal.budget_minor, currency=proposal.currency,
                          settings=settings, horizon_days=proposal.horizon_days)


def submit_proposal(db, actor: UserContext, proposal_id: int, expected_version: int) -> str:
    require(actor, Permission.MANAGE_PROPOSALS)
    settings = get_settings(db)
    with db.session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise NotFoundError("Proposal not found.")
        if p.current_version != expected_version:
            raise StaleVersionError()
        report = revalidate(s, p, settings)
        new_status = "PENDING_APPROVAL" if report.passed else "BLOCKED"
        assert_transition(p.status, new_status)
        before = p.status
        p.status = new_status
        audit_service.record(
            s, actor=actor.username, action="PROPOSAL_SUBMITTED", entity_type="proposal",
            entity_id=str(p.id), previous_state=before, new_state=new_status,
            outcome="SUCCESS" if report.passed else "BLOCKED",
            message=f"{p.reference} v{p.current_version} submitted for approval."
                    if report.passed else
                    f"{p.reference} v{p.current_version} blocked: {'; '.join(report.errors[:5])}",
            details={"errors": report.errors})
        return new_status


def revise_proposal(db, actor: UserContext, proposal_id: int, expected_version: int,
                    lines: list[dict], note: str) -> int:
    """Save a new version with human-edited quantities/suppliers.

    The whole proposal is recalculated from current data. A valid revision
    returns to PENDING_APPROVAL and needs a fresh approval; an invalid one is
    saved as BLOCKED so the reason is preserved in history."""
    require(actor, Permission.MANAGE_PROPOSALS)
    if not note or len(note.strip()) < 3:
        raise DomainValidationError("Describe the change (at least 3 characters).")
    parsed = [validate_model(RevisionLine, line) for line in lines]
    ids = [l.product_id for l in parsed]
    if len(ids) != len(set(ids)):
        raise DomainValidationError("Each product may appear only once in a revision.")
    if not parsed:
        raise DomainValidationError("A revision must contain at least one line.")
    settings = get_settings(db)
    with db.session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise NotFoundError("Proposal not found.")
        if p.current_version != expected_version:
            raise StaleVersionError()
        assert_transition(p.status, "PENDING_APPROVAL")  # raises for APPROVED/REJECTED
        for pid in ids:
            if s.get(Product, pid) is None:
                raise DomainValidationError(f"Revision references unknown product id {pid}.")
        for l in parsed:
            if l.supplier_id is not None and s.get(Supplier, l.supplier_id) is None:
                raise DomainValidationError(f"Revision references unknown supplier id {l.supplier_id}.")
        previous = {d.product_id: d for d in _current_drafts(s, p, keep_prices=False)}
        drafts = [LineDraft(l.product_id, l.supplier_id, l.quantity, l.quantity > 0,
                            previous[l.product_id].suggested_qty if l.product_id in previous else 0,
                            None, "Edited by reviewer") for l in parsed]
        old_total = s.scalar(select(ProposalVersion.total_minor).where(
            ProposalVersion.proposal_id == p.id, ProposalVersion.version == p.current_version))
        report = validate_lines(s, drafts, budget_minor=p.budget_minor, currency=p.currency,
                                settings=settings, horizon_days=p.horizon_days)
        new_version = p.current_version + 1
        changes = []
        for l in parsed:
            prev = previous.get(l.product_id)
            if prev is None or prev.quantity != l.quantity or prev.supplier_id != l.supplier_id:
                changes.append({"product_id": l.product_id,
                                "from_qty": prev.quantity if prev else None, "to_qty": l.quantity,
                                "from_supplier": prev.supplier_id if prev else None,
                                "to_supplier": l.supplier_id})
        inputs = {"revised_at": utcnow().isoformat(), "based_on_version": p.current_version,
                  "lines": [l.model_dump() for l in parsed], "changes": changes}
        _store_version(s, p, new_version, report, inputs, actor.username, note.strip())
        new_status = "PENDING_APPROVAL" if report.passed else "BLOCKED"
        before = p.status
        p.current_version = new_version
        p.status = new_status
        audit_service.record(
            s, actor=actor.username, action="PROPOSAL_REVISED", entity_type="proposal",
            entity_id=str(p.id), previous_state=before, new_state=new_status,
            outcome="SUCCESS" if report.passed else "BLOCKED",
            message=f"{p.reference} revised to v{new_version}: total "
                    f"{format_money(old_total, p.currency)} -> "
                    f"{format_money(report.total_minor, p.currency)}. Note: {note.strip()}"
                    + ("" if report.passed else f" Blocked: {'; '.join(report.errors[:5])}"),
            details={"changes": changes, "errors": report.errors})
        s.flush()
        return new_version


# --- read models for the UI ------------------------------------------------------

def list_proposals(db, statuses: list[str] | None = None) -> list[dict]:
    with db.session() as s:
        q = select(Proposal).order_by(Proposal.id.desc())
        if statuses:
            q = q.where(Proposal.status.in_(statuses))
        out = []
        for p in s.scalars(q):
            total = s.scalar(select(ProposalVersion.total_minor).where(
                ProposalVersion.proposal_id == p.id, ProposalVersion.version == p.current_version))
            out.append({"id": p.id, "reference": p.reference, "title": p.title,
                        "status": p.status, "version": p.current_version,
                        "total_minor": total, "budget_minor": p.budget_minor,
                        "currency": p.currency, "horizon_days": p.horizon_days,
                        "created_by": p.created_by, "created_at": p.created_at,
                        "updated_at": p.updated_at})
        return out


def _version_dict(session, v: ProposalVersion) -> dict:
    items = []
    for i, prod in session.execute(select(ProposalItem, Product)
                                   .join(Product, Product.id == ProposalItem.product_id)
                                   .where(ProposalItem.version_id == v.id)
                                   .order_by(Product.sku)).all():
        sup = session.get(Supplier, i.supplier_id) if i.supplier_id else None
        items.append({"product_id": prod.id, "sku": prod.sku, "product": prod.name,
                      "unit": prod.unit, "supplier_id": i.supplier_id,
                      "supplier": sup.name if sup else None, "quantity": i.quantity,
                      "suggested_qty": i.suggested_qty, "unit_price_minor": i.unit_price_minor,
                      "line_total_minor": i.line_total_minor, "funded": i.funded,
                      "risk_level": i.risk_level, "note": i.note})
    return {"version": v.version, "created_at": v.created_at, "created_by": v.created_by,
            "total_minor": v.total_minor, "validation_passed": v.validation_passed,
            "validation": json.loads(v.validation_json), "inputs": json.loads(v.inputs_json),
            "change_note": v.change_note, "items": items}


def get_proposal_detail(db, proposal_id: int) -> dict:
    with db.session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise NotFoundError("Proposal not found.")
        versions = [_version_dict(s, v) for v in s.scalars(
            select(ProposalVersion).where(ProposalVersion.proposal_id == p.id)
            .order_by(ProposalVersion.version.desc()))]
        decisions = [{"version": d.version, "decision": d.decision, "decided_by": d.decided_by,
                      "decided_at": d.decided_at, "reason": d.reason}
                     for d in s.scalars(select(ApprovalDecision)
                                        .where(ApprovalDecision.proposal_id == p.id)
                                        .order_by(ApprovalDecision.id))]
        reviews = [{"id": r.id, "version": r.proposal_version, "created_at": r.created_at,
                    "model": r.model, "status": r.status, "requested_by": r.requested_by,
                    "result": json.loads(r.result_json) if r.result_json else None,
                    "error": r.error_message}
                   for r in s.scalars(select(AIReview).where(AIReview.proposal_id == p.id)
                                      .order_by(AIReview.id.desc()))]
        return {"id": p.id, "reference": p.reference, "title": p.title, "status": p.status,
                "current_version": p.current_version, "budget_minor": p.budget_minor,
                "currency": p.currency, "horizon_days": p.horizon_days,
                "created_by": p.created_by, "created_at": p.created_at,
                "versions": versions, "current": versions[0] if versions else None,
                "decisions": decisions, "ai_reviews": reviews}


def live_validation(db, proposal_id: int) -> ValidationReport:
    settings = get_settings(db)
    with db.session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise NotFoundError("Proposal not found.")
        return revalidate(s, p, settings)
