"""Aggregated figures for the dashboard (read-only)."""
from __future__ import annotations

from sqlalchemy import func, select

from stocksense.models import Proposal, ProposalVersion, PurchaseOrder
from stocksense.services import audit_service
from stocksense.services.forecast_service import compute_forecasts
from stocksense.services.settings_service import get_settings


def summary(db) -> dict:
    settings = get_settings(db)
    with db.session() as s:
        forecasts = compute_forecasts(s, settings)
        pending = s.scalar(select(func.count(Proposal.id))
                           .where(Proposal.status == "PENDING_APPROVAL")) or 0
        blocked = s.scalar(select(func.count(Proposal.id))
                           .where(Proposal.status == "BLOCKED")) or 0
        po_open = s.scalar(select(func.coalesce(func.sum(PurchaseOrder.total_minor), 0))
                           .where(PurchaseOrder.status == "SIMULATED_OPEN")) or 0
        po_all = s.scalar(select(func.coalesce(func.sum(PurchaseOrder.total_minor), 0))) or 0
        latest = s.execute(
            select(Proposal, ProposalVersion.total_minor)
            .join(ProposalVersion, (ProposalVersion.proposal_id == Proposal.id)
                  & (ProposalVersion.version == Proposal.current_version))
            .order_by(Proposal.id.desc()).limit(1)).first()
    budget = None
    if latest:
        p, total = latest
        budget = {"reference": p.reference, "status": p.status, "total_minor": total,
                  "budget_minor": p.budget_minor,
                  "utilization": (total / p.budget_minor) if p.budget_minor else 0}
    return {
        "settings": settings, "forecasts": forecasts,
        "tracked": len(forecasts),
        "low_stock": sum(1 for f in forecasts if f.risk_level in ("HIGH", "MEDIUM")),
        "critical": sum(1 for f in forecasts if f.risk_level == "HIGH"),
        "stockout_before_delivery": sum(1 for f in forecasts if f.stockout_before_delivery),
        "pending": pending, "blocked": blocked,
        "po_open_minor": po_open, "po_all_minor": po_all, "latest_budget": budget,
        "recent_events": audit_service.list_events(db, limit=8),
    }
