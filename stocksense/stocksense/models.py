"""SQLAlchemy ORM models.

Money columns end in ``_minor`` and hold integer minor units (paisa/cents).
Quantities are whole units (integers).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from stocksense.utils.dates import utcnow


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint("role IN ('admin','manager','staff','viewer')", name="ck_user_role"),
    )


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="General")
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="pcs")
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow,
                                                 nullable=False)
    __table_args__ = (
        CheckConstraint("stock_qty >= 0", name="ck_product_stock_nonneg"),
        CheckConstraint("safety_stock >= 0", name="ck_product_safety_nonneg"),
        CheckConstraint("reorder_threshold >= 0", name="ck_product_reorder_nonneg"),
    )


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    quotes: Mapped[list["SupplierQuote"]] = relationship(back_populates="supplier")


class SupplierQuote(Base):
    __tablename__ = "supplier_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    min_order_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    available_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    quoted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supplier: Mapped[Supplier] = relationship(back_populates="quotes")
    product: Mapped[Product] = relationship()
    __table_args__ = (
        UniqueConstraint("supplier_id", "product_id", name="uq_quote_supplier_product"),
        CheckConstraint("unit_price_minor > 0", name="ck_quote_price_pos"),
        CheckConstraint("min_order_qty >= 1", name="ck_quote_moq_pos"),
        CheckConstraint("available_qty >= 0", name="ck_quote_avail_nonneg"),
        CheckConstraint("lead_time_days >= 0", name="ck_quote_lead_nonneg"),
    )


class SaleRecord(Base):
    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    sale_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow,
                                                 nullable=False)
    __table_args__ = (
        UniqueConstraint("product_id", "sale_date", name="uq_sale_product_day"),
        CheckConstraint("quantity >= 0", name="ck_sale_qty_nonneg"),
        Index("ix_sales_date", "sale_date"),
    )


class ForecastSnapshot(Base):
    __tablename__ = "forecast_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(50))
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    budget_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow,
                                                 nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    versions: Mapped[list["ProposalVersion"]] = relationship(
        back_populates="proposal", order_by="ProposalVersion.version")
    __mapper_args__ = {"version_id_col": row_version}
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED',"
                        "'NEEDS_REVISION','BLOCKED')", name="ck_proposal_status"),
        CheckConstraint("budget_minor > 0", name="ck_proposal_budget_pos"),
        Index("ix_proposals_status", "status"),
    )


class ProposalVersion(Base):
    __tablename__ = "proposal_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    total_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_json: Mapped[str] = mapped_column(Text, nullable=False)
    inputs_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text)
    proposal: Mapped[Proposal] = relationship(back_populates="versions")
    items: Mapped[list["ProposalItem"]] = relationship(back_populates="version_obj")
    __table_args__ = (UniqueConstraint("proposal_id", "version", name="uq_proposal_version"),)


class ProposalItem(Base):
    __tablename__ = "proposal_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("proposal_versions.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price_minor: Mapped[int | None] = mapped_column(Integer)
    line_total_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    funded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False, default="LOW")
    note: Mapped[str | None] = mapped_column(Text)
    version_obj: Mapped[ProposalVersion] = relationship(back_populates="items")
    __table_args__ = (
        UniqueConstraint("version_id", "product_id", name="uq_item_version_product"),
        CheckConstraint("quantity >= 0", name="ck_item_qty_nonneg"),
        CheckConstraint("line_total_minor >= 0", name="ck_item_total_nonneg"),
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(50), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        # One decision per proposal version: the database-level duplicate guard.
        UniqueConstraint("proposal_id", "version", name="uq_decision_proposal_version"),
        CheckConstraint("decision IN ('APPROVED','REJECTED','NEEDS_REVISION')",
                        name="ck_decision_value"),
    )


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SIMULATED_OPEN")
    total_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    demo_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    real_supplier_order_placed: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                             default=False)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    items: Mapped[list["PurchaseOrderItem"]] = relationship(back_populates="order")
    __table_args__ = (
        UniqueConstraint("proposal_id", "supplier_id", name="uq_po_proposal_supplier"),
        CheckConstraint("demo_only = 1", name="ck_po_demo_only"),
        CheckConstraint("real_supplier_order_placed = 0", name="ck_po_never_real"),
        CheckConstraint("status IN ('SIMULATED_OPEN','SIMULATED_RECEIVED','CANCELLED')",
                        name="ck_po_status"),
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    order: Mapped[PurchaseOrder] = relationship(back_populates="items")
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_po_item_qty_pos"),)


class AIReview(Base):
    __tablename__ = "ai_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, default=_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="SUCCESS")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    previous_state: Mapped[str | None] = mapped_column(String(40))
    new_state: Mapped[str | None] = mapped_column(String(40))
    details_json: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_audit_timestamp", "timestamp"),
        Index("ix_audit_action", "action"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow,
                                                 nullable=False)
