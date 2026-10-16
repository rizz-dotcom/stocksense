import pytest
from sqlalchemy import text

from stocksense.exceptions import AuditWriteError
from stocksense.services import audit_service, inventory_service


def test_database_initialization_is_audited(db):
    assert audit_service.list_events(db, actions=["DATABASE_INITIALIZED"])


def test_init_is_repeatable_and_non_destructive(db, staff):
    inventory_service.create_product(db, staff, {
        "sku": "KEEP", "name": "Keep me", "stock_qty": 1, "safety_stock": 0,
        "reorder_threshold": 0})
    assert db.init_schema() is False
    assert inventory_service.list_products(db)[0]["sku"] == "KEEP"
    assert len(audit_service.list_events(db, actions=["DATABASE_INITIALIZED"])) == 1


def test_event_fields(db, staff):
    pid = inventory_service.create_product(db, staff, {
        "sku": "A", "name": "A", "stock_qty": 1, "safety_stock": 0, "reorder_threshold": 0})
    e = audit_service.list_events(db, entity_type="product", entity_id=str(pid))[0]
    assert e["event_id"] and e["timestamp"] and e["actor"] == "staff"
    assert e["action"] == "PRODUCT_CREATED" and e["outcome"] == "SUCCESS"


@pytest.mark.parametrize("sql", ["UPDATE audit_events SET message='tampered'",
                                 "DELETE FROM audit_events"])
def test_audit_events_are_append_only_in_database(db, sql):
    with pytest.raises(Exception):
        with db.engine.begin() as conn:
            conn.execute(text(sql))
    assert audit_service.list_events(db)[0]["message"] != "tampered"


def test_secrets_are_redacted(db):
    with db.session() as s:
        audit_service.record(s, actor="x", action="TEST", entity_type="t", entity_id="1",
                             message="api_key=AIzaSyA1234567890abcdefghijklmnop password=hunter2",
                             details={"password": "hunter2", "note": "ok"})
    e = audit_service.list_events(db, actions=["TEST"])[0]
    assert "AIza" not in e["message"] and "hunter2" not in e["message"]
    assert "hunter2" not in e["details"]


def test_failed_audit_write_raises(db):
    class Broken:
        def add(self, *_):
            from sqlalchemy.exc import OperationalError
            raise OperationalError("x", {}, Exception("locked"))
    with pytest.raises(AuditWriteError):
        audit_service.record(Broken(), actor=None, action="X", entity_type="t",
                             entity_id=None, message="m")


def test_filters(db, staff):
    inventory_service.create_product(db, staff, {
        "sku": "Z", "name": "Zed", "stock_qty": 1, "safety_stock": 0, "reorder_threshold": 0})
    assert audit_service.list_events(db, actor="staff")
    assert audit_service.list_events(db, text="Zed")
    assert not audit_service.list_events(db, text="nothing-matches-this")
