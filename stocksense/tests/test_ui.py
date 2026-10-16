"""Drives the real Streamlit app headlessly (streamlit.testing AppTest)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from stocksense.config import load_config
from stocksense.database import Database
from stocksense.security.authentication import create_user
from stocksense.services import inventory_service, purchase_order_service
from stocksense.ui.proposal_view import editor_rows_to_lines

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PW = "Kitchen-Lead-2026"


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSENSE_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("STOCKSENSE_LOG_PATH", str(tmp_path / "ui.log"))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    st.cache_resource.clear()
    db = Database(load_config().database_url)
    db.init_schema()
    yield db
    st.cache_resource.clear()
    db.engine.dispose()


def new_app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def go(at, page):
    at.sidebar.radio[0].set_value(next(o for o in at.sidebar.radio[0].options if page in o))
    at.run()
    assert not at.exception, at.exception


def first_admin(at):
    at.text_input[0].set_value("owner")
    at.text_input[1].set_value(PW)
    at.text_input[2].set_value(PW)
    button(at, "Create administrator").click()
    at.run()


def login(at, user, pw=PW):
    at.text_input[0].set_value(user)
    at.text_input[1].set_value(pw)
    button(at, "Sign in").click()
    at.run()


def test_first_run_then_sign_in(app_env):
    at = new_app()
    assert at.title[0].value == "Welcome to STOCKSENSE"
    first_admin(at)
    assert not at.exception and at.title[0].value == "My Business"
    at2 = new_app()
    login(at2, "owner", "wrong-password-1")
    assert "Invalid username or password." in [e.value for e in at2.error]
    login(at2, "owner")
    assert at2.title[0].value == "My Business"


def test_every_page_renders_and_full_workflow(app_env):
    at = new_app()
    first_admin(at)
    go(at, "Settings")
    button(at, "Load demo data").click()
    at.run()
    for page in ("Dashboard", "Inventory", "Suppliers", "Sales", "Forecasts", "Purchase",
                 "Approval", "Simulated", "Audit", "Settings"):
        go(at, page)
    go(at, "Purchase proposals")
    button(at, "Generate proposal").click()
    at.run()
    button(at, "Submit for approval").click()
    at.run()
    go(at, "Approval center")
    next(c for c in at.checkbox if c.label.startswith("I reviewed")).check()
    button(at, "Record decision").click()
    at.run()
    assert not at.exception and not at.error
    orders = purchase_order_service.list_orders(app_env)
    assert orders and all(o["demo_only"] and not o["real_supplier_order_placed"] for o in orders)
    go(at, "Simulated orders")
    button(at, "Record delivery received").click()
    at.run()
    statuses = [o["status"] for o in purchase_order_service.list_orders(app_env)]
    assert statuses.count("SIMULATED_RECEIVED") == 1


def test_csv_upload_preview_and_import(app_env):
    at = new_app()
    first_admin(at)
    go(at, "Inventory")
    csv = (b"sku,name,category,unit,stock_qty,safety_stock,reorder_threshold,expiry_date\n"
           b"NAN-1,Naan,Bakery,pcs,40,10,20,\n")
    at.file_uploader[0].set_value(("inventory.csv", csv, "text/csv"))
    at.run()
    assert not at.exception
    button(at, "Import 1 row(s)").click()
    at.run()
    assert any("Imported: 1 new" in s.value for s in at.success)
    assert inventory_service.list_products(app_env)[0]["sku"] == "NAN-1"


def test_csv_upload_with_bad_row_imports_nothing(app_env):
    at = new_app()
    first_admin(at)
    go(at, "Inventory")
    csv = (b"sku,name,category,unit,stock_qty,safety_stock,reorder_threshold,expiry_date\n"
           b"OK-1,Fine,Bakery,pcs,5,1,1,\nBAD-1,Bad,Bakery,pcs,-3,1,1,\n")
    at.file_uploader[0].set_value(("inventory.csv", csv, "text/csv"))
    at.run()
    assert any("nothing will be imported" in e.value for e in at.error)
    assert not [b for b in at.button if b.label.startswith("Import ")]
    assert inventory_service.list_products(app_env) == []


def test_staff_cannot_approve_and_viewer_has_no_audit_page(app_env):
    at = new_app()
    first_admin(at)
    from stocksense.security.authentication import authenticate
    owner = authenticate(app_env, "owner", PW)
    create_user(app_env, owner, "cook", PW + "x", "staff")
    create_user(app_env, owner, "guest", PW + "y", "viewer")
    at2 = new_app()
    login(at2, "cook", PW + "x")
    go(at2, "Approval center")
    at3 = new_app()
    login(at3, "guest", PW + "y")
    assert not any("Audit" in o for o in at3.sidebar.radio[0].options)


def test_editor_rows_to_lines():
    rows = [{"product_id": 1, "Supplier": "S1", "Quantity": 5.0},
            {"product_id": 2, "Supplier": None, "Quantity": float("nan")},
            {"product_id": 3, "Supplier": "S9", "Quantity": 2.5}]
    lines = editor_rows_to_lines(rows, {"S1": 11})
    assert lines[0] == {"product_id": 1, "supplier_id": 11, "quantity": 5}
    assert lines[1] == {"product_id": 2, "supplier_id": None, "quantity": 0}
    assert lines[2]["quantity"] == 2.5  # left for the service layer to reject


def test_public_host_hides_admin_setup_screen(app_env, monkeypatch):
    monkeypatch.setenv("STOCKSENSE_ALLOW_WEB_SETUP", "false")
    at = new_app()
    assert at.title[0].value == "STOCKSENSE is not set up yet"
    assert not [t for t in at.text_input]  # no way to create an account


def test_cloud_bootstrap_signs_in_with_demo(app_env, monkeypatch):
    monkeypatch.setenv("STOCKSENSE_ALLOW_WEB_SETUP", "false")
    monkeypatch.setenv("STOCKSENSE_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("STOCKSENSE_ADMIN_PASSWORD", PW)
    monkeypatch.setenv("STOCKSENSE_LOAD_DEMO", "true")
    at = new_app()
    login(at, "owner")
    assert at.title[0].value == "Demo Burger House" and not at.exception
