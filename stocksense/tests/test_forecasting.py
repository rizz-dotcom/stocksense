from datetime import date, timedelta
from decimal import Decimal

from stocksense.services.forecast_service import (ForecastParams, ProductSnapshot,
                                                  forecast_product, run_forecast)

TODAY = date(2026, 10, 16)
P = ForecastParams(horizon_days=7, high_risk_cover_days=2, medium_risk_cover_days=5,
                   expiry_warning_days=3)


def snap(**kw):
    base = dict(product_id=1, sku="X", name="X", stock_qty=50, safety_stock=10,
                reorder_threshold=0, min_lead_time_days=2)
    base.update(kw)
    return ProductSnapshot(**base)


def days(values):
    return [(TODAY - timedelta(days=len(values) - i), v) for i, v in enumerate(values)]


def test_empty_sales_history_is_flagged_not_fabricated():
    r = forecast_product(snap(), [], P, TODAY)
    assert r.data_quality == "NO_HISTORY"
    assert r.avg_daily_demand is None and r.forecast_demand == 0
    assert r.suggested_qty == 0  # stock 50 already exceeds safety stock 10
    assert any("No sales history" in i for i in r.issues)


def test_no_history_still_tops_up_to_safety_stock():
    r = forecast_product(snap(stock_qty=3, safety_stock=10), [], P, TODAY)
    assert r.suggested_qty == 7


def test_exactly_one_observation_is_weak():
    r = forecast_product(snap(), days([12]), P, TODAY)
    assert r.observations == 1 and r.data_quality == "WEAK"
    assert r.avg_daily_demand == Decimal("12.000")


def test_all_zero_sales():
    r = forecast_product(snap(), days([0] * 7), P, TODAY)
    assert r.avg_daily_demand == 0 and r.forecast_demand == 0
    assert r.days_cover is None and not r.stockout_before_delivery
    assert r.suggested_qty == 0


def test_missing_stock_quantity_is_invalid():
    r = forecast_product(snap(stock_qty=None), days([5] * 7), P, TODAY)
    assert not r.valid and r.risk_level == "UNKNOWN" and r.suggested_qty == 0


def test_negative_stock_or_safety_is_invalid():
    assert not forecast_product(snap(stock_qty=-1), days([5] * 7), P, TODAY).valid
    assert not forecast_product(snap(safety_stock=-3), days([5] * 7), P, TODAY).valid


def test_bad_sales_values_are_ignored_and_reported():
    sales = days([10, 10, 10]) + [(TODAY, -5), (TODAY, float("nan")), (TODAY, float("inf")),
                                   (TODAY, "abc"), (TODAY, 2.5), ("not-a-date", 4)]
    r = forecast_product(snap(), sales, P, TODAY)
    assert r.rejected_observations == 6
    assert r.avg_daily_demand == Decimal("10.000")
    assert any("ignored" in i for i in r.issues)


def test_zero_stock_is_high_risk():
    r = forecast_product(snap(stock_qty=0), days([5] * 7), P, TODAY)
    assert r.risk_level == "HIGH" and r.stockout_before_delivery


def test_below_safety_stock_is_at_least_medium():
    r = forecast_product(snap(stock_qty=8, safety_stock=10, min_lead_time_days=0),
                         days([1] * 7), P, TODAY)
    assert r.risk_level == "MEDIUM"
    assert any("safety stock" in x for x in r.reasons)


def test_demand_rounding_is_up_to_whole_units():
    r = forecast_product(snap(stock_qty=0, safety_stock=0), days([3, 3, 4]), P, TODAY)
    # average 3.333 x 7 = 23.33 -> 24 units
    assert r.forecast_demand == 24 and r.suggested_qty == 24


def test_replenishment_formula():
    r = forecast_product(snap(stock_qty=20, safety_stock=10, on_order_qty=15),
                         days([10] * 7), P, TODAY)
    assert r.target_stock == 80          # 70 demand + 10 safety
    assert r.effective_stock == 35       # 20 on hand + 15 on order
    assert r.suggested_qty == 45


def test_lead_time_equal_to_cover_counts_as_stockout():
    r = forecast_product(snap(stock_qty=20, min_lead_time_days=2), days([10] * 7), P, TODAY)
    assert r.days_cover == Decimal("2.0") and r.stockout_before_delivery


def test_seven_day_rolling_baseline():
    r = forecast_product(snap(), days([100] * 5 + [10] * 7), P, TODAY)
    assert r.avg_daily_demand == Decimal("10.000")
    assert "7-day" in r.baseline_method


def test_valid_expiry_date():
    r = forecast_product(snap(expiry_date=TODAY + timedelta(days=30)), days([5] * 7), P, TODAY)
    assert r.expiry_status == "OK"


def test_expired_product_is_not_usable():
    r = forecast_product(snap(stock_qty=40, expiry_date=TODAY - timedelta(days=1)),
                         days([5] * 7), P, TODAY)
    assert r.expiry_status == "EXPIRED" and r.usable_stock == 0 and r.risk_level == "HIGH"


def test_expiring_soon_with_unsellable_stock():
    r = forecast_product(snap(stock_qty=100, expiry_date=TODAY + timedelta(days=2),
                              min_lead_time_days=1), days([5] * 7), P, TODAY)
    assert r.expiry_status == "EXPIRING"
    assert any("may not be sold" in x for x in r.reasons)


def test_missing_expiry_data():
    assert forecast_product(snap(expiry_date=None), days([5] * 7), P, TODAY).expiry_status == "NOT_TRACKED"


def test_very_large_inventory():
    r = forecast_product(snap(stock_qty=9_000_000_000), days([5] * 7), P, TODAY)
    assert r.valid and r.suggested_qty == 0 and r.risk_level == "LOW"


def test_run_forecast_saves_snapshot_and_audits(db, factory, staff):
    from stocksense.services import audit_service
    pid = factory.product("A", stock=5, safety=5)
    factory.sales(pid, [5] * 7)
    results = run_forecast(db, staff, 7, save_snapshot=True)
    assert results[0].sku == "A"
    assert audit_service.list_events(db, actions=["FORECAST_GENERATED"])
