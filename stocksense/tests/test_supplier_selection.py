from datetime import datetime, timedelta
from decimal import Decimal

from stocksense.services.supplier_service import QuoteOption, select_supplier

NOW = datetime(2026, 10, 16, 12, 0)


def opt(sid, price=1000, moq=1, avail=1000, lead=1, age=0, active=True, code=None):
    return QuoteOption(sid, code or f"S{sid}", f"Supplier {sid}", active, price, moq, avail,
                       lead, NOW - timedelta(days=age))


def pick(qty, options, cover=None, urgent=False, max_age=14):
    return select_supplier(qty, options, stockout_in_days=Decimal(cover) if cover is not None else None,
                           urgent=urgent, max_quote_age_days=max_age, now=NOW)


def test_one_suitable_supplier():
    r = pick(10, [opt(1)])
    assert r.status == "SELECTED" and r.selected.supplier_id == 1 and r.selected.total_minor == 10000


def test_non_urgent_prefers_cheapest_on_time():
    r = pick(10, [opt(1, price=1200, lead=1), opt(2, price=900, lead=3)], cover="10")
    assert r.selected.supplier_id == 2


def test_urgent_prefers_fastest():
    r = pick(10, [opt(1, price=1200, lead=1), opt(2, price=900, lead=2)], cover="5", urgent=True)
    assert r.selected.supplier_id == 1


def test_cheapest_with_dangerous_lead_time_is_not_selected():
    r = pick(10, [opt(1, price=1200, lead=1), opt(2, price=500, lead=4)], cover="2")
    assert r.selected.supplier_id == 1 and r.status == "SELECTED"
    assert "cheaper option" in r.explanation
    late = next(c for c in r.candidates if c.supplier_id == 2)
    assert late.late


def test_all_suppliers_too_slow_is_flagged():
    r = pick(10, [opt(1, lead=5), opt(2, lead=6)], cover="2")
    assert r.status == "SELECTED_LATE" and "Human review" in r.explanation


def test_no_matching_supplier():
    assert pick(10, []).status == "NO_SUPPLIER"


def test_insufficient_availability():
    r = pick(100, [opt(1, avail=50)])
    assert r.status == "NO_ELIGIBLE" and "enough available" in r.explanation


def test_zero_availability():
    r = pick(10, [opt(1, avail=0)])
    assert r.status == "NO_ELIGIBLE"
    assert any("zero stock" in i for i in r.candidates[0].issues)


def test_quantity_rounded_up_to_moq():
    r = pick(10, [opt(1, moq=50)])
    assert r.selected.order_qty == 50 and "minimum" in r.explanation


def test_missing_or_invalid_price():
    r = pick(10, [opt(1, price=None), opt(2, price=0)])
    assert r.status == "NO_ELIGIBLE"
    assert all(any("price" in i for i in c.issues) for c in r.candidates)


def test_stale_quote_is_excluded():
    r = pick(10, [opt(1, age=30), opt(2, price=2000)])
    assert r.selected.supplier_id == 2
    assert any("stale" in i for i in r.candidates[1].issues)


def test_inactive_supplier_and_conflicting_records():
    r = pick(10, [opt(1, active=False), opt(2), opt(2, price=1)])
    assert r.status == "NO_ELIGIBLE"
    assert any("Conflicting" in i for c in r.candidates for i in c.issues)


def test_not_needed():
    assert pick(0, [opt(1)]).status == "NOT_NEEDED"


def test_quote_crud_validation(db, factory, staff, viewer):
    import pytest
    from stocksense.exceptions import DomainValidationError, PermissionDeniedError
    from stocksense.services import supplier_service
    pid = factory.product("A")
    sid = supplier_service.create_supplier(db, staff, {"code": "NEW", "name": "New Supplier"})
    qid = supplier_service.upsert_quote(db, staff, {"supplier_id": sid, "product_id": pid,
                                                   "unit_price": "12.345", "min_order_qty": 1,
                                                   "available_qty": 5, "lead_time_days": 1})
    assert supplier_service.list_quotes(db, pid)[0]["unit_price_minor"] == 1235  # half-up
    with pytest.raises(DomainValidationError):
        supplier_service.upsert_quote(db, staff, {"supplier_id": sid, "product_id": pid,
                                                 "unit_price": "-1", "min_order_qty": 1,
                                                 "available_qty": 5, "lead_time_days": 1})
    with pytest.raises(PermissionDeniedError):
        supplier_service.delete_quote(db, viewer, qid)
