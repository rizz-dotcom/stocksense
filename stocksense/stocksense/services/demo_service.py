"""Idempotent, clearly labelled FICTIONAL demonstration data for
'Demo Burger House'. Existing records are never deleted or overwritten."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from stocksense.models import Product, SaleRecord, Supplier, SupplierQuote
from stocksense.services import audit_service
from stocksense.services.settings_service import set_raw
from stocksense.utils.dates import today, utcnow

DEMO_PRODUCTS = [
    # sku, name, category, unit, stock, safety, reorder, expiry offset (days) or None
    ("DBH-BUN", "Burger buns", "Bakery", "pcs", 60, 30, 80, 4),
    ("DBH-PATTY", "Chicken patties", "Frozen", "pcs", 35, 20, 50, 30),
    ("DBH-SAUCE", "Sauce packets", "Condiments", "pcs", 400, 100, 250, None),
    ("DBH-CHEESE", "Cheese slices", "Dairy", "pcs", 50, 25, 60, 10),
    ("DBH-FRIES", "Frozen fries", "Frozen", "kg", 18, 8, 12, 60),
]
# daily sales for the last 7 days (oldest first)
DEMO_SALES = {
    "DBH-BUN": [36, 41, 38, 44, 40, 47, 42],
    "DBH-PATTY": [27, 31, 29, 33, 30, 34, 31],
    "DBH-SAUCE": [55, 62, 58, 64, 60, 66, 61],
    "DBH-CHEESE": [22, 26, 24, 27, 25, 28, 26],
    "DBH-FRIES": [3, 4, 3, 4, 4, 5, 4],
}
DEMO_SUPPLIERS = [
    ("DEMO-SUP-A", "Fictional Fresh Foods (demo)", "Demo Contact A", "0300-0000001",
     "orders@fictional-fresh.example"),
    ("DEMO-SUP-B", "Fictional QuickServe Wholesale (demo)", "Demo Contact B", "0300-0000002",
     "sales@fictional-quickserve.example"),
    ("DEMO-SUP-C", "Fictional Budget Traders (demo)", "Demo Contact C", "0300-0000003",
     "hello@fictional-budget.example"),
]
# supplier, sku, price (PKR, FICTIONAL), MOQ, available, lead days
DEMO_QUOTES = [
    ("DEMO-SUP-A", "DBH-BUN", "12.00", 50, 1000, 1),
    ("DEMO-SUP-C", "DBH-BUN", "10.50", 200, 600, 3),
    ("DEMO-SUP-A", "DBH-PATTY", "38.00", 50, 400, 1),
    ("DEMO-SUP-C", "DBH-PATTY", "32.00", 100, 500, 4),   # cheaper but dangerously slow
    ("DEMO-SUP-B", "DBH-SAUCE", "2.50", 200, 5000, 2),
    ("DEMO-SUP-C", "DBH-SAUCE", "2.20", 500, 0, 3),       # zero availability
    ("DEMO-SUP-B", "DBH-CHEESE", "20.00", 100, 300, 2),
    ("DEMO-SUP-B", "DBH-FRIES", "320.00", 10, 200, 2),
]


def load_demo_data(db, actor_name: str = "demo-seed") -> dict:
    created = {"products": 0, "suppliers": 0, "quotes": 0, "sales": 0}
    now = utcnow()
    with db.session() as s:
        products = {p.sku: p for p in s.scalars(select(Product))}
        for sku, name, cat, unit, stock, safety, reorder, exp in DEMO_PRODUCTS:
            if sku in products:
                continue
            p = Product(sku=sku, name=name, category=cat, unit=unit, stock_qty=stock,
                        safety_stock=safety, reorder_threshold=reorder,
                        expiry_date=(today() + timedelta(days=exp)) if exp is not None else None,
                        is_demo=True)
            s.add(p)
            products[sku] = p
            created["products"] += 1
        s.flush()
        suppliers = {x.code: x for x in s.scalars(select(Supplier))}
        for code, name, contact, phone, email in DEMO_SUPPLIERS:
            if code in suppliers:
                continue
            sup = Supplier(code=code, name=name, contact_name=contact, phone=phone, email=email,
                           is_demo=True)
            s.add(sup)
            suppliers[code] = sup
            created["suppliers"] += 1
        s.flush()
        from stocksense.utils.money import money_to_minor
        for code, sku, price, moq, avail, lead in DEMO_QUOTES:
            sup, prod = suppliers[code], products[sku]
            exists = s.scalar(select(SupplierQuote.id).where(
                SupplierQuote.supplier_id == sup.id, SupplierQuote.product_id == prod.id))
            if exists:
                continue
            s.add(SupplierQuote(supplier_id=sup.id, product_id=prod.id,
                                unit_price_minor=money_to_minor(price), min_order_qty=moq,
                                available_qty=avail, lead_time_days=lead, quoted_at=now,
                                is_demo=True))
            created["quotes"] += 1
        start = today() - timedelta(days=7)
        for sku, values in DEMO_SALES.items():
            prod = products[sku]
            for offset, qty in enumerate(values):
                d = start + timedelta(days=offset)
                exists = s.scalar(select(SaleRecord.id).where(
                    SaleRecord.product_id == prod.id, SaleRecord.sale_date == d))
                if exists:
                    continue
                s.add(SaleRecord(product_id=prod.id, sale_date=d, quantity=qty, source="demo",
                                 is_demo=True))
                created["sales"] += 1
        from stocksense.models import AppSetting
        if s.get(AppSetting, "business_name") is None:
            set_raw(s, "business_name", "Demo Burger House")
        audit_service.record(s, actor=actor_name, action="DEMO_DATA_LOADED", entity_type="database",
                             entity_id="demo",
                             message="Fictional demo data loaded (existing records untouched): "
                                     + ", ".join(f"{v} {k}" for k, v in created.items()) + ".",
                             details=created)
    return created
