"""Daily sales records and safe corrections."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from stocksense.exceptions import DomainValidationError, NotFoundError
from stocksense.models import Product, SaleRecord
from stocksense.schemas import SaleInput, validate_model
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.utils.dates import today as _today


def list_sales(db, product_id: int | None = None, start: date | None = None,
               end: date | None = None) -> list[dict]:
    with db.session() as s:
        q = select(SaleRecord, Product).join(Product, Product.id == SaleRecord.product_id)
        if product_id:
            q = q.where(SaleRecord.product_id == product_id)
        if start:
            q = q.where(SaleRecord.sale_date >= start)
        if end:
            q = q.where(SaleRecord.sale_date <= end)
        q = q.order_by(SaleRecord.sale_date.desc(), Product.sku)
        return [{"id": r.id, "sale_date": r.sale_date, "product_id": p.id, "sku": p.sku,
                 "product": p.name, "quantity": r.quantity, "source": r.source,
                 "demo": r.is_demo} for r, p in s.execute(q).all()]


def record_sale(db, actor: UserContext, product_id: int, sale_date, quantity) -> int:
    require(actor, Permission.MANAGE_SALES)
    payload = validate_model(SaleInput, {"product_id": product_id, "sale_date": sale_date,
                                         "quantity": quantity})
    if payload.sale_date > _today():
        raise DomainValidationError("Sales cannot be recorded for a future date.")
    with db.session() as s:
        product = s.get(Product, payload.product_id)
        if product is None or not product.is_active:
            raise NotFoundError("Product not found or deactivated.")
        existing = s.scalar(select(SaleRecord).where(
            SaleRecord.product_id == payload.product_id,
            SaleRecord.sale_date == payload.sale_date))
        if existing:
            raise DomainValidationError(
                f"Sales for {product.sku} on {payload.sale_date} already exist "
                f"({existing.quantity}). Use 'Correct a sales record' instead.")
        rec = SaleRecord(product_id=payload.product_id, sale_date=payload.sale_date,
                         quantity=payload.quantity, source="manual")
        s.add(rec)
        s.flush()
        audit_service.record(s, actor=actor.username, action="SALE_RECORDED", entity_type="sale",
                             entity_id=str(rec.id),
                             message=f"{payload.quantity} x {product.sku} on {payload.sale_date}.")
        return rec.id


def correct_sale(db, actor: UserContext, sale_id: int, new_quantity, reason: str) -> None:
    require(actor, Permission.MANAGE_SALES)
    if not reason or len(reason.strip()) < 3:
        raise DomainValidationError("A reason is required for corrections.")
    with db.session() as s:
        rec = s.get(SaleRecord, sale_id)
        if rec is None:
            raise NotFoundError("Sales record not found.")
        payload = validate_model(SaleInput, {"product_id": rec.product_id,
                                             "sale_date": rec.sale_date, "quantity": new_quantity})
        old = rec.quantity
        if old == payload.quantity:
            return
        rec.quantity = payload.quantity
        rec.source = "corrected"
        audit_service.record(s, actor=actor.username, action="SALE_CORRECTED", entity_type="sale",
                             entity_id=str(rec.id), previous_state=str(old),
                             new_state=str(payload.quantity),
                             message=f"Sales on {rec.sale_date} corrected {old} -> "
                                     f"{payload.quantity}. Reason: {reason.strip()}")
