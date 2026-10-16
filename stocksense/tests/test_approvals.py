import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from stocksense.exceptions import (AlreadyDecidedError, ApprovalBlockedError, AuditWriteError,
                                   DatabaseError, DomainValidationError, InvalidTransitionError,
                                   PermissionDeniedError, StaleVersionError)
from stocksense.models import ApprovalDecision, Product, PurchaseOrder
from stocksense.services import (approval_service, audit_service, proposal_service,
                                 purchase_order_service)


@pytest.fixture
def pending(db, ready_proposal, admin):
    proposal_service.submit_proposal(db, admin, ready_proposal["proposal_id"], 1)
    return ready_proposal


def status(db, pid):
    return proposal_service.get_proposal_detail(db, pid)["status"]


def orders(db, pid):
    return purchase_order_service.list_orders(db, pid)


def test_valid_approval_creates_simulated_order(db, pending, manager):
    pid = pending["proposal_id"]
    result = approval_service.decide(db, manager, pid, 1, "APPROVED", "")
    assert result["status"] == "APPROVED" and status(db, pid) == "APPROVED"
    po = orders(db, pid)
    assert len(po) == 1 and po[0]["demo_only"] and not po[0]["real_supplier_order_placed"]
    assert po[0]["total_minor"] == 70_000
    d = proposal_service.get_proposal_detail(db, pid)["decisions"][0]
    assert d["decided_by"] == "manager" and d["version"] == 1


def test_valid_rejection_creates_no_order(db, pending, manager):
    pid = pending["proposal_id"]
    approval_service.decide(db, manager, pid, 1, "REJECTED", "too expensive")
    assert status(db, pid) == "REJECTED" and orders(db, pid) == []


def test_reject_requires_reason(db, pending, manager):
    with pytest.raises(DomainValidationError):
        approval_service.decide(db, manager, pending["proposal_id"], 1, "REJECTED", "")


def test_revision_then_new_approval(db, pending, manager, staff):
    pid = pending["proposal_id"]
    approval_service.decide(db, manager, pid, 1, "NEEDS_REVISION", "cut quantity")
    assert status(db, pid) == "NEEDS_REVISION"
    with pytest.raises(InvalidTransitionError):
        approval_service.decide(db, manager, pid, 1, "APPROVED", "")
    v2 = proposal_service.revise_proposal(
        db, staff, pid, 1, [{"product_id": pending["product_id"],
                             "supplier_id": pending["supplier_id"], "quantity": 60}], "cut to 60")
    assert status(db, pid) == "PENDING_APPROVAL"
    approval_service.decide(db, manager, pid, v2, "APPROVED", "ok")
    assert orders(db, pid)[0]["total_minor"] == 60_000


def test_stale_version_approval_is_refused_and_audited(db, pending, manager, staff):
    pid = pending["proposal_id"]
    proposal_service.revise_proposal(
        db, staff, pid, 1, [{"product_id": pending["product_id"],
                             "supplier_id": pending["supplier_id"], "quantity": 60}], "edit")
    with pytest.raises(StaleVersionError):
        approval_service.decide(db, manager, pid, 1, "APPROVED", "")
    assert orders(db, pid) == []
    assert audit_service.list_events(db, actions=["APPROVAL_REFUSED"], outcome="STALE")


def test_approving_rejected_proposal_fails(db, pending, manager):
    pid = pending["proposal_id"]
    approval_service.decide(db, manager, pid, 1, "REJECTED", "not needed")
    with pytest.raises(InvalidTransitionError):
        approval_service.decide(db, manager, pid, 1, "APPROVED", "")
    assert orders(db, pid) == []


def test_duplicate_approval_submission(db, pending, manager):
    pid = pending["proposal_id"]
    approval_service.decide(db, manager, pid, 1, "APPROVED", "")
    with pytest.raises(AlreadyDecidedError):
        approval_service.decide(db, manager, pid, 1, "APPROVED", "")
    assert len(orders(db, pid)) == 1


def test_database_constraint_blocks_second_decision(db, pending, manager):
    approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    with pytest.raises(Exception):
        with db.session() as s:
            s.add(ApprovalDecision(proposal_id=pending["proposal_id"], version=1,
                                   decision="REJECTED", decided_by="x", reason="x"))


@pytest.mark.parametrize("who", ["staff", "viewer"])
def test_user_without_permission_cannot_approve(db, pending, who, request):
    user = request.getfixturevalue(who)
    with pytest.raises(PermissionDeniedError):
        approval_service.decide(db, user, pending["proposal_id"], 1, "APPROVED", "")
    assert status(db, pending["proposal_id"]) == "PENDING_APPROVAL"
    assert audit_service.list_events(db, actions=["APPROVAL_DENIED"])


def test_unauthenticated_cannot_approve(db, pending):
    with pytest.raises(PermissionDeniedError):
        approval_service.decide(db, None, pending["proposal_id"], 1, "APPROVED", "")


def test_approval_blocked_when_price_changed(db, pending, manager, factory):
    factory.set_quote(pending["quote_id"], unit_price_minor=1100)
    with pytest.raises(ApprovalBlockedError) as exc:
        approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    assert any("price changed" in e for e in exc.value.errors)
    assert status(db, pending["proposal_id"]) == "BLOCKED" and orders(db, pending["proposal_id"]) == []
    assert audit_service.list_events(db, actions=["APPROVAL_BLOCKED"])


def test_approval_blocked_when_quote_stale_or_supplier_short(db, pending, manager, factory):
    from datetime import timedelta
    from stocksense.utils.dates import utcnow
    factory.set_quote(pending["quote_id"], quoted_at=utcnow() - timedelta(days=60),
                      available_qty=5)
    with pytest.raises(ApprovalBlockedError) as exc:
        approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    text = " ".join(exc.value.errors)
    assert "stale" in text and "exceeds the supplier's available" in text


def test_blocked_proposal_cannot_be_approved_until_revised(db, pending, manager, factory):
    factory.set_quote(pending["quote_id"], unit_price_minor=1100)
    with pytest.raises(ApprovalBlockedError):
        approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    with pytest.raises(InvalidTransitionError):
        approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    v = proposal_service.revise_proposal(
        db, manager, pending["proposal_id"], 1,
        [{"product_id": pending["product_id"], "supplier_id": pending["supplier_id"],
          "quantity": 70}], "accept new price")
    assert status(db, pending["proposal_id"]) == "PENDING_APPROVAL"
    assert approval_service.decide(db, manager, pending["proposal_id"], v, "APPROVED", "")["status"] == "APPROVED"


@pytest.mark.parametrize("qty", [-5, 2.5, "3.7", float("nan")])
def test_revision_rejects_negative_or_fractional(db, pending, staff, qty):
    with pytest.raises(DomainValidationError):
        proposal_service.revise_proposal(
            db, staff, pending["proposal_id"], 1,
            [{"product_id": pending["product_id"], "supplier_id": pending["supplier_id"],
              "quantity": qty}], "bad")
    assert proposal_service.get_proposal_detail(db, pending["proposal_id"])["current_version"] == 1


def test_revision_with_unknown_product(db, pending, staff):
    with pytest.raises(DomainValidationError):
        proposal_service.revise_proposal(db, staff, pending["proposal_id"], 1,
                                         [{"product_id": 9999, "supplier_id": None,
                                           "quantity": 5}], "unknown")


@pytest.mark.parametrize("qty,needle", [(5, "below the supplier minimum"),
                                        (900, "exceeds the supplier's available")])
def test_revision_below_moq_or_above_availability_is_blocked(db, pending, staff, qty, needle):
    proposal_service.revise_proposal(
        db, staff, pending["proposal_id"], 1,
        [{"product_id": pending["product_id"], "supplier_id": pending["supplier_id"],
          "quantity": qty}], "try")
    d = proposal_service.get_proposal_detail(db, pending["proposal_id"])
    assert d["status"] == "BLOCKED"
    assert any(needle in e for e in d["current"]["validation"]["errors"])


def test_revised_total_exceeding_budget_is_blocked(db, pending, staff, manager):
    proposal_service.revise_proposal(
        db, staff, pending["proposal_id"], 1,
        [{"product_id": pending["product_id"], "supplier_id": pending["supplier_id"],
          "quantity": 110}], "more")
    d = proposal_service.get_proposal_detail(db, pending["proposal_id"])
    assert d["status"] == "BLOCKED"
    assert any("exceeds the budget" in e for e in d["current"]["validation"]["errors"])
    with pytest.raises(InvalidTransitionError):
        approval_service.decide(db, manager, pending["proposal_id"], 2, "APPROVED", "")


def test_database_failure_during_transition_rolls_back(db, pending, manager, monkeypatch):
    def boom(*a, **k):
        raise OperationalError("INSERT", {}, Exception("disk I/O error"))
    monkeypatch.setattr(purchase_order_service, "create_orders_for_approval", boom)
    with pytest.raises(DatabaseError):
        approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    assert status(db, pending["proposal_id"]) == "PENDING_APPROVAL"
    with db.session() as s:
        assert s.scalars(select(ApprovalDecision)).all() == []
        assert s.scalars(select(PurchaseOrder)).all() == []


def test_audit_failure_cancels_approval(db, pending, manager, monkeypatch):
    original = audit_service.record

    def failing(session, **kw):
        if kw.get("action") == "PROPOSAL_APPROVED":
            raise AuditWriteError()
        return original(session, **kw)
    monkeypatch.setattr(audit_service, "record", failing)
    with pytest.raises(AuditWriteError):
        approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    monkeypatch.setattr(audit_service, "record", original)
    assert status(db, pending["proposal_id"]) == "PENDING_APPROVAL"
    assert orders(db, pending["proposal_id"]) == []


def test_receiving_order_updates_stock_once(db, pending, manager, staff):
    approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    po = orders(db, pending["proposal_id"])[0]
    purchase_order_service.mark_received(db, staff, po["id"])
    with db.session() as s:
        assert s.get(Product, pending["product_id"]).stock_qty == 80
    with pytest.raises(InvalidTransitionError):
        purchase_order_service.mark_received(db, staff, po["id"])


def test_simulated_flags_are_enforced_by_database(db, pending, manager):
    approval_service.decide(db, manager, pending["proposal_id"], 1, "APPROVED", "")
    with pytest.raises(Exception):
        with db.session() as s:
            po = s.scalars(select(PurchaseOrder)).first()
            po.real_supplier_order_placed = True


def test_transition_table():
    approval_service.assert_transition("DRAFT", "PENDING_APPROVAL")
    for bad in [("APPROVED", "PENDING_APPROVAL"), ("REJECTED", "APPROVED"), ("DRAFT", "APPROVED")]:
        with pytest.raises(InvalidTransitionError):
            approval_service.assert_transition(*bad)
