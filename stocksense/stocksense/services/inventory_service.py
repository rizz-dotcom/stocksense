"""Product and inventory-balance management."""
from __future__ import annotations

from sqlalchemy import or_, select

from stocksense.exceptions import DomainValidationError, NotFoundError
from stocksense.models import Product
from stocksense.schemas import ProductInput, validate_model
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service


def product_dict(p: Product) -> dict:
    return {"id": p.id, "sku": p.sku, "name": p.name, "category": p.category, "unit": p.unit,
            "stock_qty": p.stock_qty, "safety_stock": p.safety_stock,
            "reorder_threshold": p.reorder_threshold, "expiry_date": p.expiry_date,
            "active": p.is_active, "demo": p.is_demo, "updated_at": p.updated_at}


def list_products(db, include_inactive: bool = False, search: str | None = None) -> list[dict]:
    with db.session() as s:
        q = select(Product).order_by(Product.sku)
        if not include_inactive:
            q = q.where(Product.is_active.is_(True))
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(Product.sku.ilike(like), Product.name.ilike(like),
                            Product.category.ilike(like)))
        return [product_dict(p) for p in s.scalars(q)]


def get_product(db, product_id: int) -> dict:
    with db.session() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        return product_dict(p)


def create_product(db, actor: UserContext, data: dict) -> int:
    require(actor, Permission.MANAGE_INVENTORY)
    payload = validate_model(ProductInput, data)
    with db.session() as s:
        if s.scalar(select(Product.id).where(Product.sku == payload.sku)):
            raise DomainValidationError(f"SKU '{payload.sku}' already exists.")
        p = Product(**payload.model_dump())
        s.add(p)
        s.flush()
        audit_service.record(s, actor=actor.username, action="PRODUCT_CREATED",
                             entity_type="product", entity_id=str(p.id),
                             message=f"Product {p.sku} '{p.name}' created with stock {p.stock_qty}.")
        return p.id


def update_product(db, actor: UserContext, product_id: int, data: dict, reason: str = "") -> dict:
    require(actor, Permission.MANAGE_INVENTORY)
    with db.session() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        current = product_dict(p)
        merged = {k: data.get(k, current[k]) for k in ProductInput.model_fields}
        merged["sku"] = p.sku  # SKU is the stable identifier and is not editable
        payload = validate_model(ProductInput, merged)
        changes = {}
        for k, v in payload.model_dump().items():
            if getattr(p, k) != v:
                changes[k] = {"from": getattr(p, k), "to": v}
                setattr(p, k, v)
        if changes:
            audit_service.record(
                s, actor=actor.username, action="PRODUCT_UPDATED", entity_type="product",
                entity_id=str(p.id),
                message=f"Product {p.sku} updated: {', '.join(changes)}."
                        + (f" Reason: {reason.strip()}" if reason.strip() else ""),
                details=changes)
        return changes


def set_product_active(db, actor: UserContext, product_id: int, active: bool) -> None:
    require(actor, Permission.MANAGE_INVENTORY)
    with db.session() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        before = p.is_active
        p.is_active = active
        audit_service.record(s, actor=actor.username, action="PRODUCT_STATUS_CHANGED",
                             entity_type="product", entity_id=str(p.id),
                             previous_state="active" if before else "inactive",
                             new_state="active" if active else "inactive",
                             message=f"Product {p.sku} {'reactivated' if active else 'deactivated'}.")
