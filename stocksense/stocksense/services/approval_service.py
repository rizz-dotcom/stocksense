"""Approval state machine. Human approval is mandatory; AI can never approve.

States: DRAFT -> PENDING_APPROVAL -> APPROVED, plus REJECTED, NEEDS_REVISION
and BLOCKED. Transitions are enforced here, in the service layer.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from stocksense.exceptions import (AlreadyDecidedError, ApprovalBlockedError,
                                   DomainValidationError, InvalidTransitionError, NotFoundError,
                                   PermissionDeniedError, StaleVersionError)
from stocksense.models import ApprovalDecision, Proposal, ProposalVersion
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_APPROVAL", "BLOCKED"},
    "PENDING_APPROVAL": {"APPROVED", "REJECTED", "NEEDS_REVISION", "BLOCKED",
                         "PENDING_APPROVAL"},
    "NEEDS_REVISION": {"PENDING_APPROVAL", "BLOCKED", "REJECTED"},
    "BLOCKED": {"PENDING_APPROVAL", "BLOCKED", "REJECTED"},
    "APPROVED": set(),
    "REJECTED": set(),
}
DECISIONS = ("APPROVED", "REJECTED", "NEEDS_REVISION")


def assert_transition(current: str, new: str) -> None:
    if new not in TRANSITIONS.get(current, set()):
        raise InvalidTransitionError(
            f"A proposal in status {current} cannot move to {new}.")


def decide(db, actor: UserContext, proposal_id: int, expected_version: int, decision: str,
           reason: str) -> dict:
    """Record a human decision atomically.

    Approval re-runs the full deterministic validation. On failure the proposal
    becomes BLOCKED and ApprovalBlockedError is raised. Stale, duplicate and
    unauthorized attempts are refused and audited.
    """
    from stocksense.services.proposal_service import revalidate
    from stocksense.services.purchase_order_service import create_orders_for_approval
    from stocksense.services.settings_service import get_settings

    decision = (decision or "").upper()
    reason = (reason or "").strip()
    actor_name = actor.username if actor else None
    try:
        require(actor, Permission.APPROVE_PROPOSALS)
        if decision not in DECISIONS:
            raise DomainValidationError(f"Unknown decision '{decision}'.")
        if decision != "APPROVED" and len(reason) < 3:
            raise DomainValidationError("A reason is required to reject or request revision.")
        if len(reason) > 2000:
            raise DomainValidationError("The reason is too long (max 2000 characters).")
    except PermissionDeniedError:
        audit_service.record_standalone(
            db, actor=actor_name, action="APPROVAL_DENIED", entity_type="proposal",
            entity_id=str(proposal_id), outcome="DENIED",
            message=f"{decision or 'Decision'} attempt refused: insufficient permission.")
        raise

    settings = get_settings(db)
    blocked: list[str] | None = None
    result: dict = {}
    try:
        with db.session() as s:
            p = s.get(Proposal, proposal_id)
            if p is None:
                raise NotFoundError("Proposal not found.")
            already = s.scalar(select(ApprovalDecision).where(
                ApprovalDecision.proposal_id == p.id,
                ApprovalDecision.version == expected_version))
            if already is not None:
                raise AlreadyDecidedError(
                    f"Version {expected_version} was already {already.decision} by "
                    f"{already.decided_by}.")
            if p.current_version != expected_version:
                raise StaleVersionError(
                    f"You reviewed version {expected_version}, but the latest is "
                    f"v{p.current_version}. Reload and review the latest version.")
            if decision == "APPROVED" and p.status != "PENDING_APPROVAL":
                raise InvalidTransitionError(
                    f"Only proposals pending approval can be approved (status is {p.status}).")
            assert_transition(p.status, decision)
            before = p.status

            if decision == "APPROVED":
                report = revalidate(s, p, settings)
                if not report.passed:
                    p.status = "BLOCKED"
                    audit_service.record(
                        s, actor=actor_name, action="APPROVAL_BLOCKED", entity_type="proposal",
                        entity_id=str(p.id), previous_state=before, new_state="BLOCKED",
                        outcome="BLOCKED",
                        message=f"Approval of {p.reference} v{p.current_version} blocked by "
                                f"validation: {'; '.join(report.errors[:5])}",
                        details={"errors": report.errors})
                    blocked = report.errors
                    s.flush()
                    return_value = None
                else:
                    return_value = report
            else:
                return_value = None

            if blocked is None:
                s.add(ApprovalDecision(proposal_id=p.id, version=expected_version,
                                       decision=decision, decided_by=actor_name,
                                       reason=reason or "Approved"))
                p.status = decision
                try:
                    s.flush()
                except IntegrityError:
                    raise AlreadyDecidedError() from None
                except StaleDataError:
                    raise StaleVersionError() from None
                audit_service.record(
                    s, actor=actor_name, action=f"PROPOSAL_{decision}", entity_type="proposal",
                    entity_id=str(p.id), previous_state=before, new_state=decision,
                    message=f"{p.reference} v{expected_version} {decision.replace('_', ' ').lower()}"
                            f" by {actor_name}. Reason: {reason or 'Approved'}")
                order_ids: list[int] = []
                if decision == "APPROVED":
                    version = s.scalar(select(ProposalVersion).where(
                        ProposalVersion.proposal_id == p.id,
                        ProposalVersion.version == expected_version))
                    order_ids = create_orders_for_approval(s, p, version, actor)
                result = {"status": decision, "purchase_order_ids": order_ids,
                          "total_minor": return_value.total_minor if return_value else None}
    except (StaleVersionError, InvalidTransitionError, NotFoundError) as exc:
        audit_service.record_standalone(
            db, actor=actor_name, action="APPROVAL_REFUSED", entity_type="proposal",
            entity_id=str(proposal_id),
            outcome="STALE" if isinstance(exc, StaleVersionError) else "REFUSED",
            message=f"{decision} attempt on v{expected_version} refused: {exc.user_message}")
        raise
    if blocked is not None:
        raise ApprovalBlockedError(
            "Approval blocked: the proposal failed the deterministic audit. "
            "It is now BLOCKED and must be revised.", errors=blocked)
    return result
