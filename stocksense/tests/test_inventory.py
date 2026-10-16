import pytest

from stocksense.exceptions import DomainValidationError, NotFoundError, PermissionDeniedError
from stocksense.services import audit_service, inventory_service
from stocksense.utils.money import format_money, money_to_minor, parse_money


def data(**kw):
    base = {"sku": "BUN-1", "name": "Buns", "category": "Bakery", "unit": "pcs",
            "stock_qty": 10, "safety_stock": 5, "reorder_threshold": 8, "expiry_date": None}
    base.update(kw)
    return base


def test_create_update_deactivate_with_audit(db, staff):
    pid = inventory_service.create_product(db, staff, data())
    changes = inventory_service.update_product(db, staff, pid, {"stock_qty": 4}, "count")
    assert changes["stock_qty"] == {"from": 10, "to": 4}
    inventory_service.set_product_active(db, staff, pid, False)
    assert inventory_service.list_products(db) == []
    assert len(inventory_service.list_products(db, include_inactive=True)) == 1
    actions = [e["action"] for e in audit_service.list_events(db, entity_type="product")]
    assert {"PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_STATUS_CHANGED"} <= set(actions)


def test_duplicate_sku_rejected(db, staff):
    inventory_service.create_product(db, staff, data())
    with pytest.raises(DomainValidationError):
        inventory_service.create_product(db, staff, data())


@pytest.mark.parametrize("field,value", [("stock_qty", -1), ("safety_stock", -5),
                                         ("name", "=HYPERLINK(1)"), ("sku", "bad sku!")])
def test_invalid_product_input(db, staff, field, value):
    with pytest.raises(DomainValidationError):
        inventory_service.create_product(db, staff, data(**{field: value}))


def test_unicode_names_are_supported(db, staff):
    pid = inventory_service.create_product(db, staff, data(name="بن برگر – Bün"))
    assert inventory_service.get_product(db, pid)["name"] == "بن برگر – Bün"


def test_viewer_cannot_edit_and_missing_record(db, staff, viewer):
    with pytest.raises(PermissionDeniedError):
        inventory_service.create_product(db, viewer, data())
    with pytest.raises(NotFoundError):
        inventory_service.update_product(db, staff, 999, {"stock_qty": 1})


def test_search(db, staff):
    inventory_service.create_product(db, staff, data(sku="A1", name="Chicken patties"))
    inventory_service.create_product(db, staff, data(sku="B1", name="Sauce"))
    assert [p["sku"] for p in inventory_service.list_products(db, search="chick")] == ["A1"]


def test_money_is_exact_decimal():
    assert money_to_minor("0.1") + money_to_minor("0.2") == money_to_minor("0.3")
    assert money_to_minor(0.1) == 10
    assert money_to_minor("2.005") == 201  # ROUND_HALF_UP
    assert format_money(123456789, "PKR") == "PKR 1,234,567.89"
    for bad in ("NaN", "Infinity", "-1", "abc", ""):
        with pytest.raises(DomainValidationError):
            parse_money(bad)
