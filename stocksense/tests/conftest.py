"""Isolated fixtures: every test gets its own temporary SQLite database."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stocksense.database import Database  # noqa: E402
from stocksense.models import Product, SaleRecord, Supplier, SupplierQuote, User  # noqa: E402
from stocksense.security.authentication import hash_password  # noqa: E402
from stocksense.security.authorization import UserContext  # noqa: E402
from stocksense.utils.dates import today, utcnow  # noqa: E402

PASSWORD = "Corr3ct-Horse-Battery"


@pytest.fixture(scope="session")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.init_schema()
    yield database
    database.engine.dispose()


def _user(db, password_hash, name, role):
    with db.session() as s:
        u = User(username=name, password_hash=password_hash, role=role)
        s.add(u)
        s.flush()
        return UserContext(u.id, u.username, u.role)


@pytest.fixture
def admin(db, password_hash):
    return _user(db, password_hash, "admin", "admin")


@pytest.fixture
def manager(db, password_hash):
    return _user(db, password_hash, "manager", "manager")


@pytest.fixture
def staff(db, password_hash):
    return _user(db, password_hash, "staff", "staff")


@pytest.fixture
def viewer(db, password_hash):
    return _user(db, password_hash, "viewer", "viewer")


class Factory:
    def __init__(self, db):
        self.db = db

    def product(self, sku="P1", stock=10, safety=5, reorder=0, expiry=None, unit="pcs",
                active=True):
        with self.db.session() as s:
            p = Product(sku=sku, name=f"Product {sku}", category="Test", unit=unit,
                        stock_qty=stock, safety_stock=safety, reorder_threshold=reorder,
                        expiry_date=expiry, is_active=active)
            s.add(p)
            s.flush()
            return p.id

    def supplier(self, code="S1", active=True):
        with self.db.session() as s:
            sup = Supplier(code=code, name=f"Supplier {code}", is_active=active)
            s.add(sup)
            s.flush()
            return sup.id

    def quote(self, supplier_id, product_id, price_minor=1000, moq=1, available=1000, lead=1,
              age_days=0):
        with self.db.session() as s:
            q = SupplierQuote(supplier_id=supplier_id, product_id=product_id,
                              unit_price_minor=price_minor, min_order_qty=moq,
                              available_qty=available, lead_time_days=lead,
                              quoted_at=utcnow() - timedelta(days=age_days))
            s.add(q)
            s.flush()
            return q.id

    def sales(self, product_id, quantities):
        """quantities oldest-first, ending yesterday."""
        with self.db.session() as s:
            n = len(quantities)
            for i, qty in enumerate(quantities):
                s.add(SaleRecord(product_id=product_id,
                                 sale_date=today() - timedelta(days=n - i), quantity=qty))

    def set_quote(self, quote_id, **values):
        with self.db.session() as s:
            q = s.get(SupplierQuote, quote_id)
            for k, v in values.items():
                setattr(q, k, v)


@pytest.fixture
def factory(db):
    return Factory(db)


@pytest.fixture
def ready_proposal(db, factory, admin):
    """A product that needs 70 units: demand 10/day x 7 days + safety 10 - stock 10."""
    from stocksense.services import proposal_service
    pid = factory.product("BUN", stock=10, safety=10)
    sid = factory.supplier("FAST")
    qid = factory.quote(sid, pid, price_minor=1000, moq=10, available=500, lead=0)
    factory.sales(pid, [10] * 7)
    proposal_id = proposal_service.generate_proposal(db, admin, budget="1000", horizon_days=7)
    return {"proposal_id": proposal_id, "product_id": pid, "supplier_id": sid, "quote_id": qid}
