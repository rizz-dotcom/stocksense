import pytest

from stocksense.security.authentication import authenticate
from stocksense.services import bootstrap, inventory_service


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("STOCKSENSE_ADMIN_USERNAME", "STOCKSENSE_ADMIN_PASSWORD",
                 "STOCKSENSE_LOAD_DEMO", "STOCKSENSE_ALLOW_WEB_SETUP"):
        monkeypatch.delenv(name, raising=False)


def test_nothing_happens_without_settings(db):
    out = bootstrap.bootstrap_from_env(db)
    assert out == {"admin_created": False, "demo_loaded": False, "problem": None}
    assert not db.has_users()


def test_admin_and_demo_created_once(db, monkeypatch):
    monkeypatch.setenv("STOCKSENSE_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("STOCKSENSE_ADMIN_PASSWORD", "Cloud-Demo-2026")
    monkeypatch.setenv("STOCKSENSE_LOAD_DEMO", "true")
    first = bootstrap.bootstrap_from_env(db)
    assert first["admin_created"] and first["demo_loaded"]
    assert authenticate(db, "owner", "Cloud-Demo-2026").role == "admin"
    again = bootstrap.bootstrap_from_env(db)  # every restart
    assert not again["admin_created"] and not again["demo_loaded"]
    assert len(inventory_service.list_products(db)) == 5


def test_existing_admin_is_never_overwritten(db, admin, monkeypatch):
    monkeypatch.setenv("STOCKSENSE_ADMIN_USERNAME", "intruder")
    monkeypatch.setenv("STOCKSENSE_ADMIN_PASSWORD", "Another-pass-99")
    assert not bootstrap.bootstrap_from_env(db)["admin_created"]
    from stocksense.exceptions import AuthenticationError
    with pytest.raises(AuthenticationError):
        authenticate(db, "intruder", "Another-pass-99")


def test_weak_password_reported_without_revealing_it(db, monkeypatch):
    monkeypatch.setenv("STOCKSENSE_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("STOCKSENSE_ADMIN_PASSWORD", "short")
    out = bootstrap.bootstrap_from_env(db)
    assert not out["admin_created"] and "rejected" in out["problem"]
    assert "short" not in out["problem"].replace("Password requirements", "")


def test_web_setup_flag(monkeypatch):
    assert bootstrap.web_setup_allowed()
    monkeypatch.setenv("STOCKSENSE_ALLOW_WEB_SETUP", "false")
    assert not bootstrap.web_setup_allowed()
