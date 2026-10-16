"""Pydantic schemas used to validate input crossing a trust boundary
(forms, CSV rows, settings and AI output)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stocksense.utils.csv_utils import FORMULA_PREFIXES

_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,39}$"


def _safe_text(value: str | None, field_name: str, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    if text.startswith(FORMULA_PREFIXES):
        raise ValueError(f"{field_name} must not start with = + - @ (spreadsheet formula risk)")
    if any(ord(c) < 32 for c in text):
        raise ValueError(f"{field_name} contains control characters")
    return text


class ProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    sku: str = Field(pattern=_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="General", min_length=1, max_length=60)
    unit: str = Field(default="pcs", min_length=1, max_length=20)
    stock_qty: int = Field(ge=0, le=10_000_000)
    safety_stock: int = Field(ge=0, le=10_000_000)
    reorder_threshold: int = Field(ge=0, le=10_000_000)
    expiry_date: date | None = None

    @field_validator("name", "category", "unit")
    @classmethod
    def _text(cls, v, info):
        return _safe_text(v, info.field_name)


class SupplierInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    code: str = Field(pattern=_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    contact_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40, pattern=r"^[0-9+()\- ]*$")
    email: str | None = Field(default=None, max_length=120,
                              pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return _safe_text(v, "name")

    @field_validator("contact_name")
    @classmethod
    def _contact(cls, v):
        return _safe_text(v, "contact_name", required=False)

    @field_validator("phone", "email")
    @classmethod
    def _blank_to_none(cls, v):
        return v or None


class QuoteInput(BaseModel):
    supplier_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    unit_price: Decimal = Field(gt=0)  # rounded half-up to 2 dp by utils.money
    min_order_qty: int = Field(ge=1, le=10_000_000)
    available_qty: int = Field(ge=0, le=10_000_000)
    lead_time_days: int = Field(ge=0, le=365)

    @field_validator("unit_price")
    @classmethod
    def _finite(cls, v: Decimal):
        if not v.is_finite():
            raise ValueError("unit_price must be finite")
        return v


class SaleInput(BaseModel):
    product_id: int = Field(gt=0)
    sale_date: date
    quantity: int = Field(ge=0, le=10_000_000)


class SettingsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    business_name: str = Field(min_length=1, max_length=120)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    default_horizon_days: int = Field(ge=1, le=90)
    default_budget: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    sales_lookback_days: int = Field(ge=7, le=365)
    high_risk_cover_days: int = Field(ge=0, le=60)
    medium_risk_cover_days: int = Field(ge=0, le=90)
    max_quote_age_days: int = Field(ge=1, le=365)
    expiry_warning_days: int = Field(ge=0, le=90)
    ai_enabled: bool
    session_timeout_minutes: int = Field(ge=5, le=480)
    count_simulated_po_as_on_order: bool

    @field_validator("business_name")
    @classmethod
    def _bn(cls, v):
        return _safe_text(v, "business_name")

    @field_validator("medium_risk_cover_days")
    @classmethod
    def _order(cls, v, info):
        high = info.data.get("high_risk_cover_days")
        if high is not None and v < high:
            raise ValueError("medium risk threshold must be >= high risk threshold")
        return v


class RevisionLine(BaseModel):
    """One edited proposal line, as submitted by a human reviewer."""
    product_id: int
    supplier_id: int | None
    quantity: int

    @field_validator("quantity", mode="before")
    @classmethod
    def _whole(cls, v):
        if isinstance(v, bool):
            raise ValueError("quantity must be a whole number")
        if isinstance(v, float):
            if v != v or v in (float("inf"), float("-inf")) or not v.is_integer():
                raise ValueError("quantity must be a whole number of units")
            v = int(v)
        if isinstance(v, Decimal):
            if not v.is_finite() or v != v.to_integral_value():
                raise ValueError("quantity must be a whole number of units")
            v = int(v)
        if isinstance(v, str):
            if not v.strip().lstrip("-").isdigit():
                raise ValueError("quantity must be a whole number of units")
            v = int(v)
        if v < 0:
            raise ValueError("quantity cannot be negative")
        return v


class AIReviewOutput(BaseModel):
    """Schema the Gemini response must satisfy. Extra keys are ignored."""
    model_config = ConfigDict(extra="ignore")
    summary: str = Field(min_length=1, max_length=3000)
    overall_risk: Literal["low", "medium", "high"]
    warnings: list[str] = Field(default_factory=list, max_length=20)
    questions_for_owner: list[str] = Field(default_factory=list, max_length=15)
    questionable_assumptions: list[str] = Field(default_factory=list, max_length=15)
    missing_evidence: list[str] = Field(default_factory=list, max_length=15)
    stated_total_cost: float | None = None

    @field_validator("warnings", "questions_for_owner", "questionable_assumptions",
                     "missing_evidence")
    @classmethod
    def _trim(cls, items: list[str]):
        return [str(i).strip()[:500] for i in items if str(i).strip()]


def validate_model(model_cls, data: dict):
    """Validate with Pydantic and convert errors into a DomainValidationError."""
    from pydantic import ValidationError

    from stocksense.exceptions import DomainValidationError
    try:
        return model_cls(**data)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "input"
            msg = err["msg"].replace("Value error, ", "")
            errors.append(f"{loc}: {msg}")
        raise DomainValidationError("Please correct: " + "; ".join(errors), errors=errors) from None
