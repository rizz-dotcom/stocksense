"""Supplier records, supplier-product quotes and deterministic supplier selection.

Selection policy (documented, deterministic, AI-independent)
------------------------------------------------------------
1. A quote is *eligible* only if: the supplier is active; price > 0; MOQ >= 1;
   lead time >= 0; the quote is not older than ``max_quote_age_days``;
   available quantity > 0 and covers the order quantity; and the supplier has
   no conflicting duplicate quote for the product.
2. Order quantity = max(required quantity, supplier MOQ).
3. A quote is *late* when its lead time >= the product's days of stock cover.
4. Urgent products (high risk): rank by (late?, lead time, total cost, code).
   Non-urgent products: rank by (late?, total cost, lead time, code).
   A late supplier is therefore never chosen over an on-time one, even if
   cheaper. If every eligible supplier is late, the fastest/cheapest is chosen
   and flagged so a human can decide.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from stocksense.exceptions import DomainValidationError, NotFoundError
from stocksense.models import Product, Supplier, SupplierQuote
from stocksense.schemas import QuoteInput, SupplierInput, validate_model
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.utils.dates import utcnow
from stocksense.utils.money import to_minor


@dataclass
class QuoteOption:
    supplier_id: int
    supplier_code: str
    supplier_name: str
    supplier_active: bool
    unit_price_minor: int | None
    min_order_qty: int | None
    available_qty: int | None
    lead_time_days: int | None
    quoted_at: datetime | None
    quote_id: int | None = None


@dataclass
class CandidateEvaluation:
    supplier_id: int
    supplier_code: str
    supplier_name: str
    unit_price_minor: int | None
    min_order_qty: int | None
    available_qty: int | None
    lead_time_days: int | None
    order_qty: int
    total_minor: int | None
    eligible: bool
    late: bool
    issues: list[str] = field(default_factory=list)
    rank: int | None = None


@dataclass
class SelectionResult:
    status: str  # NOT_NEEDED | SELECTED | SELECTED_LATE | NO_SUPPLIER | NO_ELIGIBLE
    selected: CandidateEvaluation | None
    candidates: list[CandidateEvaluation]
    explanation: str

    def to_dict(self) -> dict:
        return {"status": self.status, "explanation": self.explanation,
                "selected_supplier_id": self.selected.supplier_id if self.selected else None,
                "candidates": [asdict(c) for c in self.candidates]}


def select_supplier(required_qty: int, options: list[QuoteOption], *,
                    stockout_in_days: Decimal | None, urgent: bool,
                    max_quote_age_days: int, now: datetime | None = None) -> SelectionResult:
    now = now or utcnow()
    if required_qty <= 0:
        return SelectionResult("NOT_NEEDED", None, [], "No replenishment needed.")
    if not options:
        return SelectionResult("NO_SUPPLIER", None, [],
                               "No supplier carries this product. Add a supplier quote.")
    counts: dict[int, int] = {}
    for o in options:
        counts[o.supplier_id] = counts.get(o.supplier_id, 0) + 1

    evaluated: list[CandidateEvaluation] = []
    for o in options:
        issues: list[str] = []
        if counts[o.supplier_id] > 1:
            issues.append("Conflicting duplicate quotes from this supplier; resolve before use.")
        if not o.supplier_active:
            issues.append("Supplier is deactivated.")
        if o.unit_price_minor is None or o.unit_price_minor <= 0:
            issues.append("Missing or invalid unit price.")
        if o.min_order_qty is None or o.min_order_qty < 1:
            issues.append("Missing or invalid minimum order quantity.")
        if o.lead_time_days is None or o.lead_time_days < 0:
            issues.append("Missing or invalid lead time.")
        if o.quoted_at is None:
            issues.append("Quote date is missing.")
        elif now - o.quoted_at > timedelta(days=max_quote_age_days):
            age = (now - o.quoted_at).days
            issues.append(f"Quote is stale ({age} days old; limit {max_quote_age_days}).")
        moq = o.min_order_qty if (o.min_order_qty or 0) >= 1 else 1
        order_qty = max(required_qty, moq)
        if o.available_qty is None or o.available_qty < 0:
            issues.append("Missing or invalid available quantity.")
        elif o.available_qty == 0:
            issues.append("Supplier has zero stock available.")
        elif o.available_qty < order_qty:
            issues.append(f"Only {o.available_qty} available; {order_qty} needed.")
        total = order_qty * o.unit_price_minor if (o.unit_price_minor or 0) > 0 else None
        late = (stockout_in_days is not None and o.lead_time_days is not None
                and o.lead_time_days >= 0 and Decimal(o.lead_time_days) >= stockout_in_days)
        evaluated.append(CandidateEvaluation(
            o.supplier_id, o.supplier_code, o.supplier_name, o.unit_price_minor,
            o.min_order_qty, o.available_qty, o.lead_time_days, order_qty, total,
            not issues, late, issues))

    eligible = [c for c in evaluated if c.eligible]
    if urgent:
        key = lambda c: (c.late, c.lead_time_days, c.total_minor, c.supplier_code)  # noqa: E731
    else:
        key = lambda c: (c.late, c.total_minor, c.lead_time_days, c.supplier_code)  # noqa: E731
    eligible.sort(key=key)
    for i, c in enumerate(eligible, start=1):
        c.rank = i
    ordered = eligible + [c for c in evaluated if not c.eligible]

    if not eligible:
        insufficient = any("available" in " ".join(c.issues) for c in evaluated)
        why = ("No supplier has enough available stock." if insufficient
               else "No supplier quote passed validation.")
        return SelectionResult("NO_ELIGIBLE", None, ordered, why)

    best = eligible[0]
    policy = "fastest delivery first (urgent)" if urgent else "lowest total cost first"
    cheaper_late = [c for c in eligible if c.late and c.total_minor < best.total_minor]
    text = f"Selected {best.supplier_name} by policy: {policy}."
    if cheaper_late and not best.late:
        text += (f" {len(cheaper_late)} cheaper option(s) were not chosen because their lead "
                 "time would likely cause a stockout before delivery.")
    if best.late:
        text += (" WARNING: every eligible supplier delivers on or after the estimated "
                 "stockout day. Human review required.")
        return SelectionResult("SELECTED_LATE", best, ordered, text)
    if best.order_qty > required_qty:
        text += f" Order rounded up from {required_qty} to the supplier minimum of {best.order_qty}."
    return SelectionResult("SELECTED", best, ordered, text)


def quote_options_for_product(session, product_id: int) -> list[QuoteOption]:
    rows = session.execute(
        select(SupplierQuote, Supplier).join(Supplier, Supplier.id == SupplierQuote.supplier_id)
        .where(SupplierQuote.product_id == product_id)).all()
    return [QuoteOption(s.id, s.code, s.name, s.is_active, q.unit_price_minor, q.min_order_qty,
                        q.available_qty, q.lead_time_days, q.quoted_at, q.id) for q, s in rows]


# --- CRUD ---------------------------------------------------------------------------

def _supplier_dict(s: Supplier) -> dict:
    return {"id": s.id, "code": s.code, "name": s.name, "contact_name": s.contact_name,
            "phone": s.phone, "email": s.email, "active": s.is_active, "demo": s.is_demo}


def list_suppliers(db, include_inactive: bool = True) -> list[dict]:
    with db.session() as s:
        q = select(Supplier).order_by(Supplier.name)
        if not include_inactive:
            q = q.where(Supplier.is_active.is_(True))
        return [_supplier_dict(x) for x in s.scalars(q)]


def create_supplier(db, actor: UserContext, data: dict) -> int:
    require(actor, Permission.MANAGE_SUPPLIERS)
    payload = validate_model(SupplierInput, data)
    with db.session() as s:
        if s.scalar(select(Supplier.id).where(Supplier.code == payload.code)):
            raise DomainValidationError(f"Supplier code '{payload.code}' already exists.")
        sup = Supplier(**payload.model_dump())
        s.add(sup)
        s.flush()
        audit_service.record(s, actor=actor.username, action="SUPPLIER_CREATED",
                             entity_type="supplier", entity_id=str(sup.id),
                             message=f"Supplier {sup.code} '{sup.name}' created.")
        return sup.id


def update_supplier(db, actor: UserContext, supplier_id: int, data: dict) -> None:
    require(actor, Permission.MANAGE_SUPPLIERS)
    with db.session() as s:
        sup = s.get(Supplier, supplier_id)
        if sup is None:
            raise NotFoundError("Supplier not found.")
        merged = {**_supplier_dict(sup), **data, "code": sup.code}
        payload = validate_model(SupplierInput, {k: merged.get(k) for k in SupplierInput.model_fields})
        changes = {}
        for k, v in payload.model_dump().items():
            if getattr(sup, k) != v:
                changes[k] = {"from": getattr(sup, k), "to": v}
                setattr(sup, k, v)
        if changes:
            audit_service.record(s, actor=actor.username, action="SUPPLIER_UPDATED",
                                 entity_type="supplier", entity_id=str(sup.id),
                                 message=f"Supplier {sup.code} updated: {', '.join(changes)}.",
                                 details=changes)


def set_supplier_active(db, actor: UserContext, supplier_id: int, active: bool) -> None:
    require(actor, Permission.MANAGE_SUPPLIERS)
    with db.session() as s:
        sup = s.get(Supplier, supplier_id)
        if sup is None:
            raise NotFoundError("Supplier not found.")
        before = sup.is_active
        sup.is_active = active
        audit_service.record(s, actor=actor.username, action="SUPPLIER_STATUS_CHANGED",
                             entity_type="supplier", entity_id=str(sup.id),
                             previous_state="active" if before else "inactive",
                             new_state="active" if active else "inactive",
                             message=f"Supplier {sup.code} {'activated' if active else 'deactivated'}.")


def list_quotes(db, product_id: int | None = None, supplier_id: int | None = None,
                max_quote_age_days: int | None = None) -> list[dict]:
    now = utcnow()
    with db.session() as s:
        q = (select(SupplierQuote, Supplier, Product)
             .join(Supplier, Supplier.id == SupplierQuote.supplier_id)
             .join(Product, Product.id == SupplierQuote.product_id))
        if product_id:
            q = q.where(SupplierQuote.product_id == product_id)
        if supplier_id:
            q = q.where(SupplierQuote.supplier_id == supplier_id)
        out = []
        for quote, sup, prod in s.execute(q.order_by(Product.sku, Supplier.name)).all():
            age = (now - quote.quoted_at).days
            out.append({
                "quote_id": quote.id, "supplier_id": sup.id, "supplier_code": sup.code,
                "supplier": sup.name, "product_id": prod.id, "sku": prod.sku,
                "product": prod.name, "unit_price_minor": quote.unit_price_minor,
                "min_order_qty": quote.min_order_qty, "available_qty": quote.available_qty,
                "lead_time_days": quote.lead_time_days, "quoted_at": quote.quoted_at,
                "age_days": age,
                "stale": max_quote_age_days is not None and age > max_quote_age_days,
                "supplier_active": sup.is_active, "demo": quote.is_demo})
        return out


def upsert_quote(db, actor: UserContext, data: dict, quoted_at: datetime | None = None) -> int:
    require(actor, Permission.MANAGE_SUPPLIERS)
    payload = validate_model(QuoteInput, data)
    price_minor = to_minor(payload.unit_price)
    with db.session() as s:
        if s.get(Supplier, payload.supplier_id) is None:
            raise NotFoundError("Supplier not found.")
        if s.get(Product, payload.product_id) is None:
            raise NotFoundError("Product not found.")
        quote = s.scalar(select(SupplierQuote).where(
            SupplierQuote.supplier_id == payload.supplier_id,
            SupplierQuote.product_id == payload.product_id))
        values = dict(unit_price_minor=price_minor, min_order_qty=payload.min_order_qty,
                      available_qty=payload.available_qty, lead_time_days=payload.lead_time_days,
                      quoted_at=quoted_at or utcnow())
        if quote is None:
            quote = SupplierQuote(supplier_id=payload.supplier_id, product_id=payload.product_id,
                                  **values)
            s.add(quote)
            action, changes = "QUOTE_CREATED", values
        else:
            changes = {k: {"from": getattr(quote, k), "to": v} for k, v in values.items()
                       if getattr(quote, k) != v}
            for k, v in values.items():
                setattr(quote, k, v)
            action = "QUOTE_UPDATED"
        s.flush()
        audit_service.record(s, actor=actor.username, action=action, entity_type="quote",
                             entity_id=str(quote.id),
                             message=f"Quote for supplier {payload.supplier_id} / product "
                                     f"{payload.product_id} saved.", details=changes)
        return quote.id


def delete_quote(db, actor: UserContext, quote_id: int) -> None:
    require(actor, Permission.MANAGE_SUPPLIERS)
    with db.session() as s:
        quote = s.get(SupplierQuote, quote_id)
        if quote is None:
            raise NotFoundError("Quote not found.")
        s.delete(quote)
        audit_service.record(s, actor=actor.username, action="QUOTE_REMOVED", entity_type="quote",
                             entity_id=str(quote_id),
                             message=f"Quote {quote_id} removed (supplier {quote.supplier_id}, "
                                     f"product {quote.product_id}).")
