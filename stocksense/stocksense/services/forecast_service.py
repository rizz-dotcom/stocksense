"""Deterministic, explainable demand estimate and replenishment calculation.

This is a transparent *demand estimate* (an average), not machine learning.

Definitions
-----------
* Average daily demand: mean of the recorded daily sales quantities in the
  look-back window. Days without a record are treated as *unknown*, not zero.
  When at least 7 daily records exist, the mean of the most recent 7 records
  (the 7-day rolling baseline) is used because it reacts to recent change.
* Forecast demand = ceil(average daily demand x planning horizon days).
* Usable stock = on-hand stock, or 0 if the batch has expired.
* Effective available stock = usable stock + quantity on open SIMULATED
  purchase orders (only when the setting count_simulated_po_as_on_order is on).
  A purchase order stops counting the moment it is marked received, because
  its quantity is then added to on-hand stock - so nothing is counted twice.
* Target stock = forecast demand + safety stock.
* Suggested order quantity = max(0, target stock - effective available stock).
* Days of cover = usable on-hand stock / average daily demand.
  (On-order stock is excluded because it has not arrived yet.)
* Stockout before delivery when days of cover <= fastest supplier lead time
  (equality counts as a risk: stock runs out on the delivery day).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select

from stocksense.models import (ForecastSnapshot, Product, PurchaseOrder, PurchaseOrderItem,
                               SaleRecord, Supplier, SupplierQuote)
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.utils.dates import today as _today

RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}


@dataclass(frozen=True)
class ForecastParams:
    horizon_days: int = 7
    high_risk_cover_days: int = 2
    medium_risk_cover_days: int = 5
    expiry_warning_days: int = 3


@dataclass
class ProductSnapshot:
    product_id: int
    sku: str
    name: str
    unit: str = "pcs"
    stock_qty: object = 0
    safety_stock: object = 0
    reorder_threshold: object = 0
    expiry_date: date | None = None
    on_order_qty: int = 0
    min_lead_time_days: int | None = None


@dataclass
class ForecastResult:
    product_id: int
    sku: str
    name: str
    unit: str
    valid: bool
    data_quality: str
    observations: int
    rejected_observations: int
    avg_daily_demand: Decimal | None
    baseline_method: str
    horizon_days: int
    forecast_demand: int
    stock_qty: int | None
    usable_stock: int
    safety_stock: int | None
    on_order_qty: int
    effective_stock: int
    target_stock: int
    suggested_qty: int
    days_cover: Decimal | None
    min_lead_time_days: int | None
    stockout_before_delivery: bool
    expiry_status: str
    risk_level: str
    reasons: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("avg_daily_demand", "days_cover"):
            d[k] = None if d[k] is None else str(d[k])
        return d


def _clean_whole(value) -> int | None:
    """Return a non-negative whole number or None when the value is unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            number = Decimal(repr(value))
        else:
            number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def forecast_product(snapshot: ProductSnapshot, sales: list[tuple], params: ForecastParams,
                     today: date | None = None) -> ForecastResult:
    """Pure calculation for one product. ``sales`` is a list of (date, quantity)."""
    today = today or _today()
    issues: list[str] = []
    reasons: list[str] = []

    stock = _clean_whole(snapshot.stock_qty)
    safety = _clean_whole(snapshot.safety_stock)
    reorder = _clean_whole(snapshot.reorder_threshold)
    on_order = _clean_whole(snapshot.on_order_qty) or 0
    valid = True
    if stock is None:
        valid = False
        issues.append("Stock quantity is missing or invalid (must be a whole number >= 0).")
    if safety is None:
        valid = False
        issues.append("Safety stock is missing or invalid (must be a whole number >= 0).")
    if reorder is None:
        reorder = 0
        issues.append("Reorder threshold is invalid; treated as 0.")
    if params.horizon_days < 1:
        valid = False
        issues.append("Planning horizon must be at least 1 day.")

    # --- clean sales observations ------------------------------------------
    daily: dict[date, int] = defaultdict(int)
    rejected = 0
    for raw_date, raw_qty in sales:
        qty = _clean_whole(raw_qty)
        if qty is None or not isinstance(raw_date, date):
            rejected += 1
            continue
        daily[raw_date] += qty
    if rejected:
        issues.append(f"{rejected} sales observation(s) were ignored as invalid "
                      "(negative, fractional, non-numeric, NaN or infinite).")
    observations = len(daily)

    avg: Decimal | None = None
    method = "none"
    if observations:
        ordered = [daily[d] for d in sorted(daily)]
        if observations >= 7:
            window = ordered[-7:]
            method = "7-day rolling average (most recent 7 recorded days)"
        else:
            window = ordered
            method = f"average of {observations} recorded day(s)"
        avg = (Decimal(sum(window)) / Decimal(len(window))).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP)

    if observations == 0:
        quality = "NO_HISTORY"
        issues.append("No sales history: demand cannot be estimated. Only safety stock is "
                      "used for the suggestion. Record sales to get a real estimate.")
    elif observations < 3:
        quality = "WEAK"
        issues.append(f"Only {observations} day(s) of sales history: the estimate is unreliable.")
    elif observations < 7:
        quality = "LIMITED"
        issues.append(f"{observations} days of history: treat the estimate with caution.")
    else:
        quality = "OK"
    if not valid:
        quality = "INVALID"

    # --- expiry ---------------------------------------------------------------
    expiry_status = "NOT_TRACKED"
    usable = stock or 0
    if snapshot.expiry_date is not None and stock is not None:
        days_left = (snapshot.expiry_date - today).days
        if days_left < 0:
            expiry_status = "EXPIRED"
            usable = 0
            if stock > 0:
                reasons.append(f"Batch expired on {snapshot.expiry_date.isoformat()}: "
                               f"{stock} {snapshot.unit} are not counted as usable stock.")
        elif days_left <= params.expiry_warning_days:
            expiry_status = "EXPIRING"
            if avg is not None and avg > 0:
                expected_use = avg * days_left
                waste = Decimal(stock) - expected_use
                if waste > 0:
                    reasons.append(
                        f"Expires in {days_left} day(s); about {int(waste.to_integral_value(ROUND_CEILING))} "
                        f"{snapshot.unit} may not be sold before expiry.")
            elif stock > 0:
                reasons.append(f"Expires in {days_left} day(s) and there is no demand "
                               "estimate to show it will be used.")
        else:
            expiry_status = "OK"

    if not valid:
        return ForecastResult(
            snapshot.product_id, snapshot.sku, snapshot.name, snapshot.unit, False, quality,
            observations, rejected, avg, method, params.horizon_days, 0, stock, usable, safety,
            on_order, usable + on_order, 0, 0, None, snapshot.min_lead_time_days, False,
            expiry_status, "UNKNOWN", reasons, issues)

    forecast_demand = 0
    if avg is not None:
        forecast_demand = int((avg * params.horizon_days).to_integral_value(rounding=ROUND_CEILING))
    effective = usable + on_order
    target = forecast_demand + safety
    suggested = max(0, target - effective)

    days_cover: Decimal | None = None
    if avg is not None and avg > 0:
        days_cover = (Decimal(usable) / avg).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    lead = snapshot.min_lead_time_days
    stockout = False
    if lead is not None and avg is not None and avg > 0 and days_cover is not None:
        stockout = days_cover <= lead
    elif lead is not None and usable == 0 and avg is not None and avg > 0:
        stockout = True

    # --- risk classification ----------------------------------------------------
    high, medium = [], []
    if usable == 0:
        high.append("No usable stock on hand.")
    if stockout:
        high.append(f"Stock covers about {days_cover} day(s) but the fastest supplier needs "
                    f"{lead} day(s): likely stockout before delivery.")
    if days_cover is not None and days_cover <= params.high_risk_cover_days and usable > 0:
        high.append(f"Only {days_cover} day(s) of cover (high-risk threshold "
                    f"{params.high_risk_cover_days}).")
    if expiry_status == "EXPIRED" and stock:
        high.append("Expired stock on hand.")
    if usable <= safety and usable > 0:
        medium.append(f"Usable stock ({usable}) is at or below safety stock ({safety}).")
    if reorder and usable <= reorder and usable > 0:
        medium.append(f"Usable stock ({usable}) is at or below the reorder threshold ({reorder}).")
    if days_cover is not None and params.high_risk_cover_days < days_cover <= params.medium_risk_cover_days:
        medium.append(f"{days_cover} day(s) of cover (medium-risk threshold "
                      f"{params.medium_risk_cover_days}).")
    if expiry_status == "EXPIRING" and any("expir" in r.lower() for r in reasons):
        medium.append("Stock is close to expiry.")
    if lead is None and suggested > 0:
        medium.append("No valid supplier quote: delivery time is unknown.")

    if high:
        risk = "HIGH"
        reasons = high + [r for r in reasons if r not in high]
    elif medium:
        risk = "MEDIUM"
        reasons = medium + [r for r in reasons if r not in medium]
    else:
        risk = "LOW"
        reasons.append("Stock is above thresholds for the planning horizon.")

    return ForecastResult(
        snapshot.product_id, snapshot.sku, snapshot.name, snapshot.unit, True, quality,
        observations, rejected, avg, method, params.horizon_days, forecast_demand, stock,
        usable, safety, on_order, effective, target, suggested, days_cover, lead, stockout,
        expiry_status, risk, reasons, issues)


# --- database-backed helpers -----------------------------------------------------

def on_order_quantities(session, enabled: bool = True) -> dict[int, int]:
    if not enabled:
        return {}
    rows = session.execute(
        select(PurchaseOrderItem.product_id, func.sum(PurchaseOrderItem.quantity))
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(PurchaseOrder.status == "SIMULATED_OPEN")
        .group_by(PurchaseOrderItem.product_id)).all()
    return {pid: int(q or 0) for pid, q in rows}


def fastest_valid_lead_times(session, max_quote_age_days: int, now=None) -> dict[int, int]:
    from stocksense.utils.dates import utcnow
    now = now or utcnow()
    cutoff = now - timedelta(days=max_quote_age_days)
    rows = session.execute(
        select(SupplierQuote.product_id, func.min(SupplierQuote.lead_time_days))
        .join(Supplier, Supplier.id == SupplierQuote.supplier_id)
        .where(Supplier.is_active.is_(True), SupplierQuote.available_qty > 0,
               SupplierQuote.unit_price_minor > 0, SupplierQuote.quoted_at >= cutoff)
        .group_by(SupplierQuote.product_id)).all()
    return {pid: int(lt) for pid, lt in rows}


def compute_forecasts(session, settings, horizon_days: int | None = None,
                      product_ids: list[int] | None = None, today: date | None = None
                      ) -> list[ForecastResult]:
    today = today or _today()
    params = ForecastParams(
        horizon_days=horizon_days or settings.default_horizon_days,
        high_risk_cover_days=settings.high_risk_cover_days,
        medium_risk_cover_days=settings.medium_risk_cover_days,
        expiry_warning_days=settings.expiry_warning_days)
    q = select(Product).where(Product.is_active.is_(True))
    if product_ids is not None:
        q = q.where(Product.id.in_(product_ids))
    products = list(session.scalars(q.order_by(Product.sku)))
    start = today - timedelta(days=settings.sales_lookback_days)
    sales_rows = session.execute(
        select(SaleRecord.product_id, SaleRecord.sale_date, SaleRecord.quantity)
        .where(SaleRecord.sale_date >= start, SaleRecord.sale_date <= today)).all()
    sales_by_product: dict[int, list] = defaultdict(list)
    for pid, d, q in sales_rows:
        sales_by_product[pid].append((d, q))
    on_order = on_order_quantities(session, settings.count_simulated_po_as_on_order)
    leads = fastest_valid_lead_times(session, settings.max_quote_age_days)
    results = []
    for p in products:
        snap = ProductSnapshot(p.id, p.sku, p.name, p.unit, p.stock_qty, p.safety_stock,
                               p.reorder_threshold, p.expiry_date, on_order.get(p.id, 0),
                               leads.get(p.id))
        results.append(forecast_product(snap, sales_by_product.get(p.id, []), params, today))
    results.sort(key=lambda r: (RISK_ORDER[r.risk_level],
                                r.days_cover if r.days_cover is not None else Decimal("1e9"),
                                r.sku))
    return results


def run_forecast(db, actor: UserContext, horizon_days: int, save_snapshot: bool = True
                 ) -> list[ForecastResult]:
    require(actor, Permission.VIEW)
    from stocksense.services.settings_service import get_settings
    settings = get_settings(db)
    with db.session() as s:
        results = compute_forecasts(s, settings, horizon_days)
        if save_snapshot:
            require(actor, Permission.RUN_FORECAST)
            snap = ForecastSnapshot(created_by=actor.username, horizon_days=horizon_days,
                                    payload_json=json.dumps([r.to_dict() for r in results],
                                                            default=str))
            s.add(snap)
            s.flush()
            high = sum(1 for r in results if r.risk_level == "HIGH")
            audit_service.record(s, actor=actor.username, action="FORECAST_GENERATED",
                                 entity_type="forecast", entity_id=str(snap.id),
                                 message=f"Forecast for {len(results)} product(s), horizon "
                                         f"{horizon_days} day(s); {high} high-risk.")
    return results


def recent_snapshots(db, limit: int = 10) -> list[dict]:
    with db.session() as s:
        return [{"id": f.id, "created_at": f.created_at, "created_by": f.created_by,
                 "horizon_days": f.horizon_days}
                for f in s.scalars(select(ForecastSnapshot)
                                   .order_by(ForecastSnapshot.id.desc()).limit(limit))]
