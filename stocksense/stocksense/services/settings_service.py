"""Persisted business settings (never secrets)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select

from stocksense.config import SETTING_DEFAULTS
from stocksense.exceptions import DomainValidationError
from stocksense.models import AppSetting
from stocksense.schemas import SettingsInput
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service


@dataclass(frozen=True)
class BusinessSettings:
    business_name: str
    currency: str
    default_horizon_days: int
    default_budget: Decimal
    sales_lookback_days: int
    high_risk_cover_days: int
    medium_risk_cover_days: int
    max_quote_age_days: int
    expiry_warning_days: int
    ai_enabled: bool
    session_timeout_minutes: int
    count_simulated_po_as_on_order: bool


def defaults() -> BusinessSettings:
    return _build(dict(SETTING_DEFAULTS))


def _build(raw: dict[str, str]) -> BusinessSettings:
    merged = {**SETTING_DEFAULTS, **raw}
    try:
        parsed = SettingsInput(**{k: merged[k] for k in SETTING_DEFAULTS})
    except ValidationError:
        # Corrupt stored values: fall back to safe defaults rather than crash.
        parsed = SettingsInput(**SETTING_DEFAULTS)
    return BusinessSettings(**parsed.model_dump())


def get_settings(db) -> BusinessSettings:
    with db.session() as s:
        raw = {row.key: row.value for row in s.scalars(
            select(AppSetting).where(AppSetting.key.in_(list(SETTING_DEFAULTS))))}
    return _build(raw)


def update_settings(db, actor: UserContext, values: dict) -> BusinessSettings:
    require(actor, Permission.MANAGE_SETTINGS)
    current = get_settings(db).__dict__
    merged = {**current, **values}
    try:
        parsed = SettingsInput(**merged)
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise DomainValidationError("Settings are not valid.", errors=errors) from None
    new = parsed.model_dump()
    changes = {k: {"from": str(current[k]), "to": str(new[k])}
               for k in new if str(current[k]) != str(new[k])}
    if not changes:
        return BusinessSettings(**new)
    with db.session() as s:
        for key, value in new.items():
            text = ("true" if value else "false") if isinstance(value, bool) else str(value)
            row = s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=text))
            else:
                row.value = text
        audit_service.record(s, actor=actor.username, action="SETTINGS_CHANGED",
                             entity_type="settings", entity_id="business",
                             message=f"Changed {', '.join(changes)}.", details=changes)
    return BusinessSettings(**new)


def set_raw(session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
