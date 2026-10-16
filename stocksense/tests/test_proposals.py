import pytest

from stocksense.exceptions import DomainValidationError, PermissionDeniedError, StaleVersionError
from stocksense.services import proposal_service


def test_generated_proposal_is_versioned_draft(db, ready_proposal):
    d = proposal_service.get_proposal_detail(db, ready_proposal["proposal_id"])
    assert d["status"] == "DRAFT" and d["current_version"] == 1
    item = d["current"]["items"][0]
    assert item["quantity"] == 70 and item["line_total_minor"] == 70_000
    assert d["current"]["total_minor"] == 70_000
    assert d["current"]["validation_passed"]
    assert "forecasts" in d["current"]["inputs"] and "selections" in d["current"]["inputs"]


def test_nothing_needed_creates_no_proposal(db, factory, admin):
    pid = factory.product("A", stock=1000, safety=0)
    factory.sales(pid, [1] * 7)
    with pytest.raises(DomainValidationError):
        proposal_service.generate_proposal(db, admin, budget="100", horizon_days=7)


def test_invalid_budget_and_horizon(db, ready_proposal, admin):
    for budget in ("0", "-5", "abc", "NaN"):
        with pytest.raises(DomainValidationError):
            proposal_service.generate_proposal(db, admin, budget=budget, horizon_days=7)
    with pytest.raises(DomainValidationError):
        proposal_service.generate_proposal(db, admin, budget="100", horizon_days=0)


def test_total_exactly_equal_to_budget_is_allowed(db, factory, admin):
    pid = factory.product("A", stock=0, safety=0)
    sid = factory.supplier()
    factory.quote(sid, pid, price_minor=1000, lead=0)
    factory.sales(pid, [10] * 7)  # needs 70 units x 10.00 = 700.00
    prop = proposal_service.generate_proposal(db, admin, budget="700.00", horizon_days=7)
    d = proposal_service.get_proposal_detail(db, prop)
    assert d["current"]["total_minor"] == 70_000 and d["current"]["validation_passed"]
    assert d["current"]["validation"]["remaining_minor"] == 0


def test_over_budget_line_is_disclosed_not_dropped(db, factory, admin):
    pid = factory.product("A", stock=0, safety=0)
    sid = factory.supplier()
    factory.quote(sid, pid, price_minor=1000, lead=0)
    factory.sales(pid, [10] * 7)
    prop = proposal_service.generate_proposal(db, admin, budget="699.99", horizon_days=7)
    d = proposal_service.get_proposal_detail(db, prop)
    item = d["current"]["items"][0]
    assert not item["funded"] and "Unfunded" in item["note"]
    assert not d["current"]["validation_passed"]  # nothing funded
    assert any("Not funded" in w for w in d["current"]["validation"]["warnings"])


def test_supplier_minimum_exceeding_budget(db, factory, admin):
    pid = factory.product("A", stock=0, safety=0)
    sid = factory.supplier()
    factory.quote(sid, pid, price_minor=1000, moq=500, lead=0)
    factory.sales(pid, [1] * 7)
    prop = proposal_service.generate_proposal(db, admin, budget="100", horizon_days=7)
    item = proposal_service.get_proposal_detail(db, prop)["current"]["items"][0]
    assert not item["funded"]


def test_multiple_lines_fit_limited_budget_by_priority(db, factory, admin):
    urgent = factory.product("URG", stock=5, safety=0)   # 1 day cover -> HIGH
    calm = factory.product("CALM", stock=30, safety=0)   # 6 days cover -> LOW
    sid = factory.supplier()
    factory.quote(sid, urgent, price_minor=1000, lead=0)
    factory.quote(sid, calm, price_minor=1000, lead=0)
    factory.sales(urgent, [5] * 7)
    factory.sales(calm, [5] * 7)
    # URG needs 35-5=30 units (300.00); CALM needs 35-30=5 units (50.00)
    prop = proposal_service.generate_proposal(db, admin, budget="320", horizon_days=7)
    items = {i["sku"]: i for i in proposal_service.get_proposal_detail(db, prop)["current"]["items"]}
    assert items["URG"]["funded"] and not items["CALM"]["funded"]


def test_no_supplier_line_is_unsourced(db, factory, admin):
    a = factory.product("A", stock=0, safety=0)
    b = factory.product("B", stock=0, safety=0)
    sid = factory.supplier()
    factory.quote(sid, a, price_minor=100, lead=0)
    factory.sales(a, [1] * 7)
    factory.sales(b, [1] * 7)
    prop = proposal_service.generate_proposal(db, admin, budget="100", horizon_days=7)
    items = {i["sku"]: i for i in proposal_service.get_proposal_detail(db, prop)["current"]["items"]}
    assert items["B"]["supplier_id"] is None and "Unsourced" in items["B"]["note"]


def test_submit_and_stale_submit(db, ready_proposal, admin):
    pid = ready_proposal["proposal_id"]
    with pytest.raises(StaleVersionError):
        proposal_service.submit_proposal(db, admin, pid, 2)
    assert proposal_service.submit_proposal(db, admin, pid, 1) == "PENDING_APPROVAL"


def test_viewer_cannot_generate(db, ready_proposal, viewer):
    with pytest.raises(PermissionDeniedError):
        proposal_service.generate_proposal(db, viewer, budget="100", horizon_days=7)


def test_revision_preserves_history(db, ready_proposal, admin):
    pid, prod, sup = (ready_proposal[k] for k in ("proposal_id", "product_id", "supplier_id"))
    v = proposal_service.revise_proposal(db, admin, pid, 1,
                                         [{"product_id": prod, "supplier_id": sup, "quantity": 50}],
                                         "fewer buns")
    d = proposal_service.get_proposal_detail(db, pid)
    assert v == 2 and d["current_version"] == 2 and len(d["versions"]) == 2
    assert d["versions"][1]["items"][0]["quantity"] == 70  # v1 untouched
    assert d["current"]["total_minor"] == 50_000 and d["status"] == "PENDING_APPROVAL"
