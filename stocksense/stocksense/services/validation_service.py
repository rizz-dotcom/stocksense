"""Deterministic proposal audit.

Every line cost and the total are recomputed from stored, validated data.
Errors block approval; warnings must be reviewed by a human but do not block.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from stocksense.models import Product, Supplier, SupplierQuote
from stocksense.services.forecast_service import compute_forecasts
from stocksense.utils.dates import utcnow
from stocksense.utils.money import format_money


@dataclass
class LineDraft:
    product_id: int
    supplier_id: int | None
    quantity: int
    funded: bool = True
    suggested_qty: int = 0
    expected_unit_price_minor: int | None = None
    note: str | None = None


@dataclass
class ValidatedLine:
    product_id: int
    sku: str
    product_name: str
    unit: str
    supplier_id: int | None
    supplier_name: str | None
    quantity: int
    suggested_qty: int
    unit_price_minor: int | None
    line_total_minor: int
    funded: bool
    risk_level: str
    days_cover: str | None
    lead_time_days: int | None
    late: bool
    note: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    passed: bool
    errors: list[str]
    warnings: list[str]
    total_minor: int
    budget_minor: int
    remaining_minor: int
    currency: str
    lines: list[ValidatedLine]
    checked_at: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checked_at"] = self.checked_at.isoformat()
        return d


def validate_lines(session, drafts: list[LineDraft], *, budget_minor: int, currency: str,
                   settings, horizon_days: int, now: datetime | None = None) -> ValidationReport:
    now = now or utcnow()
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(budget_minor, int) or budget_minor <= 0:
        errors.append("The budget must be a positive amount.")
    if not currency or not re.fullmatch(r"[A-Z]{3}", currency):
        errors.append("The currency is not defined.")
    elif currency != settings.currency:
        errors.append(f"Proposal currency {currency} differs from the business currency "
                      f"{settings.currency}. Create a new proposal.")

    seen: set[int] = set()
    for d in drafts:
        if d.product_id in seen:
            errors.append(f"Product id {d.product_id} appears more than once.")
        seen.add(d.product_id)

    forecasts = {f.product_id: f for f in compute_forecasts(
        session, settings, horizon_days, product_ids=list(seen))}

    lines: list[ValidatedLine] = []
    for d in drafts:
        line_errors: list[str] = []
        line_warnings: list[str] = []
        product = session.get(Product, d.product_id)
        if product is None:
            errors.append(f"Line references unknown product id {d.product_id}.")
            continue
        fc = forecasts.get(product.id)
        label = f"{product.sku}"
        qty = d.quantity
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
            line_errors.append("Quantity must be a whole number of units, 0 or more.")
            qty = 0
        funded = d.funded and qty > 0
        supplier = session.get(Supplier, d.supplier_id) if d.supplier_id else None
        unit_price = None
        line_total = 0
        lead = None
        late = False

        if not product.is_active:
            line_errors.append("Product is deactivated.")
        if funded:
            quote = None
            if d.supplier_id is None:
                line_errors.append("No supplier selected.")
            elif supplier is None:
                line_errors.append(f"Unknown supplier id {d.supplier_id}.")
            else:
                if not supplier.is_active:
                    line_errors.append(f"Supplier {supplier.code} is deactivated.")
                quote = session.scalar(select(SupplierQuote).where(
                    SupplierQuote.supplier_id == supplier.id,
                    SupplierQuote.product_id == product.id))
                if quote is None:
                    line_errors.append(f"Supplier {supplier.code} has no quote for this product.")
            if quote is not None:
                unit_price = quote.unit_price_minor
                lead = quote.lead_time_days
                if unit_price is None or unit_price <= 0:
                    line_errors.append("Supplier price is missing or invalid.")
                if now - quote.quoted_at > timedelta(days=settings.max_quote_age_days):
                    line_errors.append(
                        f"Supplier quote is stale ({(now - quote.quoted_at).days} days old; "
                        f"limit {settings.max_quote_age_days}). Refresh the quote.")
                if qty < quote.min_order_qty:
                    line_errors.append(f"Quantity {qty} is below the supplier minimum of "
                                       f"{quote.min_order_qty}.")
                if qty > quote.available_qty:
                    line_errors.append(f"Quantity {qty} exceeds the supplier's available "
                                       f"{quote.available_qty}.")
                if (d.expected_unit_price_minor is not None and unit_price is not None
                        and d.expected_unit_price_minor != unit_price):
                    line_errors.append(
                        f"Supplier price changed from {format_money(d.expected_unit_price_minor, currency)} "
                        f"to {format_money(unit_price, currency)} since the proposal was "
                        "calculated. Revise the proposal.")
                if unit_price and unit_price > 0:
                    line_total = qty * unit_price
                if fc and fc.days_cover is not None and lead is not None and lead >= fc.days_cover:
                    late = True
                    line_warnings.append(
                        f"Delivery in {lead} day(s) but stock covers about {fc.days_cover} "
                        "day(s): a stockout before delivery is likely.")
            if line_total < 0:
                line_errors.append("Line total is negative.")
        else:
            if d.suggested_qty > 0:
                line_warnings.append(
                    f"Not funded: {d.suggested_qty} {product.unit} suggested but not purchased."
                    + (f" {d.note}" if d.note else ""))

        if fc is not None and fc.data_quality in ("NO_HISTORY", "WEAK"):
            line_warnings.append(f"Demand estimate is {fc.data_quality.replace('_', ' ').lower()}.")
        if fc is not None and fc.expiry_status == "EXPIRED":
            line_warnings.append("Current batch is expired.")

        errors.extend(f"{label}: {e}" for e in line_errors)
        warnings.extend(f"{label}: {w}" for w in line_warnings)
        lines.append(ValidatedLine(
            product.id, product.sku, product.name, product.unit, d.supplier_id,
            supplier.name if supplier else None, qty, d.suggested_qty, unit_price,
            line_total if funded else 0, funded, fc.risk_level if fc else "UNKNOWN",
            str(fc.days_cover) if fc and fc.days_cover is not None else None, lead, late,
            d.note, line_errors, line_warnings))

    total = sum(l.line_total_minor for l in lines if l.funded)
    if not any(l.funded for l in lines):
        errors.append("The proposal contains no funded line items.")
    if isinstance(budget_minor, int) and total > budget_minor:
        errors.append(f"Total {format_money(total, currency)} exceeds the budget "
                      f"{format_money(budget_minor, currency)} by "
                      f"{format_money(total - budget_minor, currency)}.")
    remaining = (budget_minor - total) if isinstance(budget_minor, int) else 0
    return ValidationReport(not errors, errors, warnings, total, budget_minor, remaining,
                            currency, lines, now)
