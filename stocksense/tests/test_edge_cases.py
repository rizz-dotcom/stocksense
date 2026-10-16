from datetime import date, timedelta

import pytest
from sqlalchemy.orm.exc import StaleDataError

from stocksense.exceptions import (DomainValidationError, NotFoundError, StaleVersionError)
from stocksense.models import AppSetting, Product, Proposal
from stocksense.services import (approval_service, demo_service, proposal_service,
                                 sales_service, settings_service)
from stocksense.utils.dates import parse_date, today


def test_demo_seed_is_idempotent_and_non_destructive(db, staff):
    from stocksense.services import inventory_service
    inventory_service.create_product(db, staff, {"sku": "MINE", "name": "Mine", "stock_qty": 3,
                                                 "safety_stock": 0, "reorder_threshold": 0})
    first = demo_service.load_demo_data(db)
    second = demo_service.load_demo_data(db)
    assert first["products"] == 5 and first["sales"] == 35
    assert all(v == 0 for v in second.values())
    skus = {p["sku"] for p in inventory_service.list_products(db)}
    assert "MINE" in skus and len(skus) == 6


def test_demo_proposal_within_fictional_budget(db, admin):
    demo_service.load_demo_data(db)
    pid = proposal_service.generate_proposal(db, admin, budget="12000", horizon_days=7)
    d = proposal_service.get_proposal_detail(db, pid)
    assert d["current"]["total_minor"] <= 1_200_000
    patty = next(i for i in d["current"]["items"] if i["sku"] == "DBH-PATTY")
    assert "Fresh Foods" in patty["supplier"]  # the cheap 4-day supplier is not chosen


def test_future_sales_and_duplicates(db, staff, factory):
    pid = factory.product("A")
    with pytest.raises(DomainValidationError):
        sales_service.record_sale(db, staff, pid, today() + timedelta(days=1), 5)
    sales_service.record_sale(db, staff, pid, today(), 5)
    with pytest.raises(DomainValidationError):
        sales_service.record_sale(db, staff, pid, today(), 6)
    with pytest.raises(DomainValidationError):
        sales_service.record_sale(db, staff, pid, today() - timedelta(days=1), -2)


def test_sale_correction_requires_reason(db, staff, factory):
    pid = factory.product("A")
    sid = sales_service.record_sale(db, staff, pid, today(), 5)
    with pytest.raises(DomainValidationError):
        sales_service.correct_sale(db, staff, sid, 4, "")
    sales_service.correct_sale(db, staff, sid, 4, "recount")
    assert sales_service.list_sales(db)[0]["quantity"] == 4
    with pytest.raises(NotFoundError):
        sales_service.correct_sale(db, staff, 999, 4, "recount")


def test_settings_validation(db, admin):
    with pytest.raises(DomainValidationError):
        settings_service.update_settings(db, admin, {"currency": "rupees"})
    with pytest.raises(DomainValidationError):
        settings_service.update_settings(db, admin, {"high_risk_cover_days": 9,
                                                     "medium_risk_cover_days": 3})
    s = settings_service.update_settings(db, admin, {"business_name": "Test Café"})
    assert s.business_name == "Test Café"


def test_corrupt_stored_settings_fall_back_to_defaults(db):
    with db.session() as s:
        s.add(AppSetting(key="default_horizon_days", value="not-a-number"))
    assert settings_service.get_settings(db).default_horizon_days == 7


def test_currency_change_invalidates_old_proposal(db, ready_proposal, admin, manager):
    proposal_service.submit_proposal(db, admin, ready_proposal["proposal_id"], 1)
    settings_service.update_settings(db, admin, {"currency": "USD"})
    from stocksense.exceptions import ApprovalBlockedError
    with pytest.raises(ApprovalBlockedError) as exc:
        approval_service.decide(db, manager, ready_proposal["proposal_id"], 1, "APPROVED", "")
    assert any("currency" in e.lower() for e in exc.value.errors)


def test_concurrent_edit_detected_by_optimistic_lock(db, ready_proposal):
    s1 = db._factory()
    s2 = db._factory()
    try:
        p1 = s1.get(Proposal, ready_proposal["proposal_id"])
        p2 = s2.get(Proposal, ready_proposal["proposal_id"])
        p1.status = "PENDING_APPROVAL"
        s1.commit()
        p2.status = "BLOCKED"
        with pytest.raises(StaleDataError):
            s2.commit()
    finally:
        s1.close()
        s2.close()


def test_concurrent_revisions_second_is_stale(db, ready_proposal, staff):
    args = [{"product_id": ready_proposal["product_id"],
             "supplier_id": ready_proposal["supplier_id"], "quantity": 60}]
    proposal_service.revise_proposal(db, staff, ready_proposal["proposal_id"], 1, args, "first")
    with pytest.raises(StaleVersionError):
        proposal_service.revise_proposal(db, staff, ready_proposal["proposal_id"], 1, args, "second")


def test_expired_product_warning_in_proposal(db, factory, admin):
    pid = factory.product("OLD", stock=50, safety=5, expiry=today() - timedelta(days=2))
    sid = factory.supplier()
    factory.quote(sid, pid, price_minor=100, lead=0)
    factory.sales(pid, [5] * 7)
    prop = proposal_service.generate_proposal(db, admin, budget="1000", horizon_days=7)
    d = proposal_service.get_proposal_detail(db, prop)
    item = d["current"]["items"][0]
    assert item["quantity"] == 40  # expired stock counts as zero: 35 demand + 5 safety
    assert any("expired" in w for w in d["current"]["validation"]["warnings"])


def test_inactive_product_in_revision_blocks(db, ready_proposal, staff):
    from stocksense.services import inventory_service
    inventory_service.set_product_active(db, staff, ready_proposal["product_id"], False)
    proposal_service.revise_proposal(db, staff, ready_proposal["proposal_id"], 1,
                                     [{"product_id": ready_proposal["product_id"],
                                       "supplier_id": ready_proposal["supplier_id"],
                                       "quantity": 70}], "retry")
    d = proposal_service.get_proposal_detail(db, ready_proposal["proposal_id"])
    assert d["status"] == "BLOCKED"


def test_duplicate_lines_in_revision(db, ready_proposal, staff):
    line = {"product_id": ready_proposal["product_id"],
            "supplier_id": ready_proposal["supplier_id"], "quantity": 70}
    with pytest.raises(DomainValidationError):
        proposal_service.revise_proposal(db, staff, ready_proposal["proposal_id"], 1,
                                         [line, line], "dup")


def test_negative_stock_blocked_by_database(db, factory):
    pid = factory.product("A")
    with pytest.raises(Exception):
        with db.session() as s:
            s.get(Product, pid).stock_qty = -1


@pytest.mark.parametrize("text,expected", [("2026-10-05", date(2026, 10, 5)),
                                           ("2026/10/05", date(2026, 10, 5)),
                                           ("05-Oct-2026", date(2026, 10, 5))])
def test_supported_date_formats(text, expected):
    assert parse_date(text) == expected


def test_config_ignores_placeholder_key(monkeypatch):
    from stocksense.config import load_config
    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-api-key-here")
    monkeypatch.setenv("GEMINI_MODEL", "bad model name!")
    cfg = load_config()
    assert not cfg.gemini_configured and cfg.gemini_model == "gemini-3.5-flash"


def test_log_redaction():
    from stocksense.logging_setup import redact
    out = redact("key AIzaSyA1234567890abcdefghijk password=secret123 token: abc")
    assert "AIza" not in out and "secret123" not in out and "abc" not in out


def test_missing_records(db, admin, manager):
    from stocksense.services import purchase_order_service
    with pytest.raises(NotFoundError):
        proposal_service.get_proposal_detail(db, 12345)
    with pytest.raises(NotFoundError):
        approval_service.decide(db, manager, 12345, 1, "APPROVED", "")
    with pytest.raises(NotFoundError):
        purchase_order_service.get_order(db, 12345)


def test_supplier_deactivated_after_proposal_blocks_approval(db, ready_proposal, admin, manager):
    from stocksense.exceptions import ApprovalBlockedError
    from stocksense.services import supplier_service
    proposal_service.submit_proposal(db, admin, ready_proposal["proposal_id"], 1)
    supplier_service.set_supplier_active(db, admin, ready_proposal["supplier_id"], False)
    with pytest.raises(ApprovalBlockedError):
        approval_service.decide(db, manager, ready_proposal["proposal_id"], 1, "APPROVED", "")


def test_demo_guide_script_numbers_and_guardian_moment(db, admin, manager):
    """Replays DEMO_GUIDE.md Part 4 so the presentation never surprises the presenter."""
    from stocksense.exceptions import ApprovalBlockedError
    from stocksense.services import supplier_service
    demo_service.load_demo_data(db)
    pid = proposal_service.generate_proposal(db, admin, budget="12000", horizon_days=7)
    d = proposal_service.get_proposal_detail(db, pid)
    items = {i["sku"]: i for i in d["current"]["items"]}
    assert d["current"]["total_minor"] == 1_120_800                       # PKR 11,208
    assert (items["DBH-PATTY"]["quantity"], items["DBH-PATTY"]["line_total_minor"]) == (200, 760_000)
    assert (items["DBH-BUN"]["quantity"], items["DBH-BUN"]["line_total_minor"]) == (259, 310_800)
    assert (items["DBH-SAUCE"]["quantity"], items["DBH-SAUCE"]["line_total_minor"]) == (200, 50_000)
    assert not items["DBH-CHEESE"]["funded"] and not items["DBH-FRIES"]["funded"]
    assert "Fresh Foods" in items["DBH-PATTY"]["supplier"]
    proposal_service.submit_proposal(db, admin, pid, 1)
    # Step 7: change the sauce price, approval must be blocked
    sauce = items["DBH-SAUCE"]
    supplier_service.upsert_quote(db, admin, {"supplier_id": sauce["supplier_id"],
                                              "product_id": sauce["product_id"],
                                              "unit_price": "2.75", "min_order_qty": 200,
                                              "available_qty": 5000, "lead_time_days": 2})
    with pytest.raises(ApprovalBlockedError) as exc:
        approval_service.decide(db, manager, pid, 1, "APPROVED", "")
    assert any("price changed" in e for e in exc.value.errors)
    lines = [{"product_id": i["product_id"], "supplier_id": i["supplier_id"],
              "quantity": i["quantity"]} for i in d["current"]["items"]]
    v2 = proposal_service.revise_proposal(db, admin, pid, 1, lines, "accept new sauce price")
    assert proposal_service.get_proposal_detail(db, pid)["current"]["total_minor"] == 1_125_800
    assert approval_service.decide(db, manager, pid, v2, "APPROVED", "")["status"] == "APPROVED"
