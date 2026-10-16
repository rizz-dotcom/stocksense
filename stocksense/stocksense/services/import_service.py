"""CSV import with whole-file validation and all-or-nothing commit.

Flow: ``preview_import`` validates every row and reports accepted rows,
rejected rows, duplicates and updates to existing records - without writing
business data. ``commit_import`` re-validates the same bytes against the
current database and writes everything in ONE transaction, only if there are
zero rejected rows. Partial imports are deliberately not supported.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

from stocksense.exceptions import DomainValidationError, ImportValidationError
from stocksense.models import Product, SaleRecord, Supplier, SupplierQuote
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.utils.csv_utils import (check_text, parse_number_text, parse_whole_quantity,
                                        read_csv_bytes)
from stocksense.utils.dates import parse_date, parse_datetime, today, utcnow
from stocksense.utils.money import money_to_minor

COLUMNS = {
    "inventory": ["sku", "name", "category", "unit", "stock_qty", "safety_stock",
                  "reorder_threshold", "expiry_date"],
    "suppliers": ["supplier_code", "supplier_name", "contact_name", "phone", "email", "sku",
                  "unit_price", "min_order_qty", "available_qty", "lead_time_days", "quoted_at"],
    "sales": ["sale_date", "sku", "quantity"],
}
PERMISSION = {"inventory": Permission.MANAGE_INVENTORY,
              "suppliers": Permission.MANAGE_SUPPLIERS, "sales": Permission.MANAGE_SALES}
_CODE_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.")


@dataclass
class ImportPreview:
    kind: str
    file_hash: str
    total_rows: int
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    new_count: int = 0
    update_count: int = 0
    unchanged_count: int = 0

    @property
    def can_commit(self) -> bool:
        return not self.rejected and bool(self.accepted)


def _code(value: str, field_name: str) -> str:
    text = check_text(value, field=field_name, max_len=40)
    if not set(text) <= _CODE_OK:
        raise DomainValidationError(f"{field_name} may only contain letters, digits, '-', '_' or '.'.")
    return text


def _validate_inventory(row: dict) -> dict:
    expiry = (row["expiry_date"] or "").strip()
    return {
        "sku": _code(row["sku"], "sku"),
        "name": check_text(row["name"], field="name", max_len=120),
        "category": check_text(row["category"], field="category", required=False, max_len=60) or "General",
        "unit": check_text(row["unit"], field="unit", required=False, max_len=20) or "pcs",
        "stock_qty": parse_whole_quantity(row["stock_qty"], field="stock_qty"),
        "safety_stock": parse_whole_quantity(row["safety_stock"], field="safety_stock"),
        "reorder_threshold": parse_whole_quantity(row["reorder_threshold"], field="reorder_threshold"),
        "expiry_date": parse_date(expiry, field="expiry_date") if expiry else None,
    }


def _validate_supplier(row: dict) -> dict:
    price = parse_number_text(row["unit_price"], field="unit_price")
    email = (row["email"] or "").strip() or None
    if email and ("@" not in email or " " in email):
        raise DomainValidationError("email is not a valid address.")
    phone = (row["phone"] or "").strip() or None
    if phone and not set(phone) <= set("0123456789+()- "):
        raise DomainValidationError("phone may only contain digits, spaces and + ( ) -.")
    if phone and phone.startswith(("+", "-")) and not phone[1:2].isdigit():
        raise DomainValidationError("phone is not valid.")
    quoted_at = parse_datetime(row["quoted_at"], field="quoted_at")
    if quoted_at > utcnow() + timedelta(days=1):
        raise DomainValidationError("quoted_at is in the future.")
    lead = parse_whole_quantity(row["lead_time_days"], field="lead_time_days", maximum=365)
    return {
        "supplier_code": _code(row["supplier_code"], "supplier_code"),
        "supplier_name": check_text(row["supplier_name"], field="supplier_name", max_len=120),
        "contact_name": check_text(row["contact_name"], field="contact_name", required=False,
                                   max_len=120),
        "phone": phone, "email": email,
        "sku": _code(row["sku"], "sku"),
        "unit_price_minor": money_to_minor(price, field="unit_price", allow_zero=False),
        "min_order_qty": parse_whole_quantity(row["min_order_qty"], field="min_order_qty",
                                              allow_zero=False),
        "available_qty": parse_whole_quantity(row["available_qty"], field="available_qty"),
        "lead_time_days": lead, "quoted_at": quoted_at,
    }


def _validate_sale(row: dict) -> dict:
    d = parse_date(row["sale_date"], field="sale_date")
    if d > today():
        raise DomainValidationError("sale_date is in the future.")
    return {"sale_date": d, "sku": _code(row["sku"], "sku"),
            "quantity": parse_whole_quantity(row["quantity"], field="quantity")}


_VALIDATORS = {"inventory": _validate_inventory, "suppliers": _validate_supplier,
               "sales": _validate_sale}


def _build_preview(session, kind: str, data: bytes, max_bytes: int) -> ImportPreview:
    if kind not in COLUMNS:
        raise ImportValidationError(f"Unknown import type '{kind}'.")
    frame = read_csv_bytes(data, max_bytes=max_bytes, required=COLUMNS[kind])
    preview = ImportPreview(kind, hashlib.sha256(data).hexdigest(), len(frame))
    products = {p.sku: p for p in session.scalars(select(Product))}
    suppliers = {s.code: s for s in session.scalars(select(Supplier))}
    seen: dict[tuple, int] = {}
    supplier_names: dict[str, tuple] = {}
    for idx, raw in enumerate(frame.to_dict(orient="records")):
        line_no = idx + 2  # header is line 1
        try:
            row = _VALIDATORS[kind](raw)
            if kind == "inventory":
                key = (row["sku"],)
            elif kind == "sales":
                key = (row["sku"], row["sale_date"])
            else:
                key = (row["supplier_code"], row["sku"])
            if key in seen:
                raise DomainValidationError(f"Duplicate of line {seen[key]} in this file.")
            seen[key] = line_no
            if kind in ("sales", "suppliers"):
                prod = products.get(row["sku"])
                if prod is None:
                    raise DomainValidationError(f"Unknown product SKU '{row['sku']}'. "
                                                "Import inventory first.")
                if not prod.is_active:
                    raise DomainValidationError(f"Product '{row['sku']}' is deactivated.")
                row["product_id"] = prod.id
            status = "new"
            if kind == "inventory":
                existing = products.get(row["sku"])
                if existing is not None:
                    same = all(getattr(existing, k) == row[k] for k in row if k != "sku")
                    status = "unchanged" if same else "update"
            elif kind == "sales":
                rec = session.scalar(select(SaleRecord).where(
                    SaleRecord.product_id == row["product_id"],
                    SaleRecord.sale_date == row["sale_date"]))
                if rec is not None:
                    status = "unchanged" if rec.quantity == row["quantity"] else "update"
                    row["previous_quantity"] = rec.quantity
            else:
                identity = (row["supplier_name"], row["contact_name"], row["phone"], row["email"])
                prior = supplier_names.get(row["supplier_code"])
                if prior is not None and prior != identity:
                    raise DomainValidationError(
                        f"Conflicting details for supplier {row['supplier_code']} within the file.")
                supplier_names[row["supplier_code"]] = identity
                sup = suppliers.get(row["supplier_code"])
                if sup is not None:
                    q = session.scalar(select(SupplierQuote).where(
                        SupplierQuote.supplier_id == sup.id,
                        SupplierQuote.product_id == row["product_id"]))
                    status = "update" if q is not None else "new"
            row["_line"] = line_no
            row["_status"] = status
            preview.accepted.append(row)
            preview.new_count += status == "new"
            preview.update_count += status == "update"
            preview.unchanged_count += status == "unchanged"
        except DomainValidationError as exc:
            preview.rejected.append({"line": line_no, "error": exc.user_message,
                                     "values": {k: str(v)[:60] for k, v in raw.items()}})
    return preview


def preview_import(db, actor: UserContext, kind: str, data: bytes, *, max_bytes: int) -> ImportPreview:
    require(actor, PERMISSION.get(kind, Permission.MANAGE_INVENTORY))
    try:
        with db.session() as s:
            preview = _build_preview(s, kind, data, max_bytes)
    except ImportValidationError as exc:
        audit_service.record_standalone(
            db, actor=actor.username, action="CSV_IMPORT_VALIDATION_FAILED", entity_type="import",
            entity_id=kind, outcome="FAILURE", message=f"{kind} import rejected: {exc.user_message}")
        raise
    if preview.rejected:
        audit_service.record_standalone(
            db, actor=actor.username, action="CSV_IMPORT_VALIDATION_FAILED", entity_type="import",
            entity_id=kind, outcome="FAILURE",
            message=f"{kind} import: {len(preview.rejected)} of {preview.total_rows} row(s) "
                    "rejected; nothing was imported.",
            details={"first_errors": [f"line {r['line']}: {r['error']}" for r in preview.rejected[:10]]})
    return preview


def commit_import(db, actor: UserContext, kind: str, data: bytes, *, max_bytes: int) -> dict:
    require(actor, PERMISSION.get(kind, Permission.MANAGE_INVENTORY))
    with db.session() as s:
        preview = _build_preview(s, kind, data, max_bytes)
        if preview.rejected:
            raise ImportValidationError(
                f"{len(preview.rejected)} row(s) are invalid. Nothing was imported.")
        if not preview.accepted:
            raise ImportValidationError("There is nothing to import.")
        for row in preview.accepted:
            if row["_status"] == "unchanged":
                continue
            if kind == "inventory":
                fields = {k: row[k] for k in COLUMNS["inventory"]}
                p = s.scalar(select(Product).where(Product.sku == row["sku"]))
                if p is None:
                    s.add(Product(**fields))
                else:
                    for k, v in fields.items():
                        setattr(p, k, v)
            elif kind == "sales":
                rec = s.scalar(select(SaleRecord).where(
                    SaleRecord.product_id == row["product_id"],
                    SaleRecord.sale_date == row["sale_date"]))
                if rec is None:
                    s.add(SaleRecord(product_id=row["product_id"], sale_date=row["sale_date"],
                                     quantity=row["quantity"], source="import"))
                else:
                    rec.quantity = row["quantity"]
                    rec.source = "import-correction"
            else:
                sup = s.scalar(select(Supplier).where(Supplier.code == row["supplier_code"]))
                if sup is None:
                    sup = Supplier(code=row["supplier_code"], name=row["supplier_name"],
                                   contact_name=row["contact_name"], phone=row["phone"],
                                   email=row["email"])
                    s.add(sup)
                    s.flush()
                else:
                    sup.name, sup.contact_name = row["supplier_name"], row["contact_name"]
                    sup.phone, sup.email = row["phone"], row["email"]
                q = s.scalar(select(SupplierQuote).where(
                    SupplierQuote.supplier_id == sup.id,
                    SupplierQuote.product_id == row["product_id"]))
                values = {k: row[k] for k in ("unit_price_minor", "min_order_qty",
                                              "available_qty", "lead_time_days", "quoted_at")}
                if q is None:
                    s.add(SupplierQuote(supplier_id=sup.id, product_id=row["product_id"], **values))
                else:
                    for k, v in values.items():
                        setattr(q, k, v)
            s.flush()
        summary = {"new": preview.new_count, "updated": preview.update_count,
                   "unchanged": preview.unchanged_count}
        corrections = [f"{r['sku']} {r['sale_date']}: {r['previous_quantity']} -> {r['quantity']}"
                       for r in preview.accepted
                       if kind == "sales" and r["_status"] == "update"]
        audit_service.record(
            s, actor=actor.username, action="CSV_IMPORTED", entity_type="import", entity_id=kind,
            message=f"{kind} import committed: {summary['new']} new, {summary['updated']} updated, "
                    f"{summary['unchanged']} unchanged.",
            details={**summary, "file_sha256": preview.file_hash, "corrections": corrections[:50]})
        return summary
