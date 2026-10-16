"""Append-only audit trail.

``record`` writes inside the caller's transaction, so a business change and its
audit event commit (or roll back) together. If the audit row cannot be written
the whole operation fails with AuditWriteError - it never reports success
without an audit record.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from stocksense.exceptions import AuditWriteError
from stocksense.logging_setup import redact
from stocksense.models import AuditEvent

log = logging.getLogger("stocksense.audit")

_SENSITIVE_KEYS = {"password", "password_hash", "api_key", "gemini_api_key", "token", "secret"}


def _clean(details: dict | None) -> str | None:
    if not details:
        return None
    safe = {k: ("[REDACTED]" if k.lower() in _SENSITIVE_KEYS else v) for k, v in details.items()}
    return redact(json.dumps(safe, default=str, ensure_ascii=False))[:8000]


def record(session: Session, *, actor: str | None, action: str, entity_type: str,
           entity_id: str | None, message: str, outcome: str = "SUCCESS",
           previous_state: str | None = None, new_state: str | None = None,
           details: dict | None = None) -> AuditEvent:
    try:
        event = AuditEvent(actor=actor, action=action, entity_type=entity_type,
                           entity_id=None if entity_id is None else str(entity_id),
                           outcome=outcome, message=redact(message)[:2000],
                           previous_state=previous_state, new_state=new_state,
                           details_json=_clean(details))
        session.add(event)
        session.flush()
        return event
    except SQLAlchemyError as exc:
        log.error("Audit write failed for action %s: %s", action, type(exc).__name__)
        raise AuditWriteError(detail=str(exc)) from None


def record_standalone(db, **kwargs) -> None:
    """Record an event in its own transaction (used for blocked/failed attempts
    after the business transaction was rolled back). Failure is logged, and the
    caller's original error is still raised."""
    try:
        with db.session() as s:
            record(s, **kwargs)
    except Exception as exc:  # noqa: BLE001 - must not mask the caller's real error
        log.error("Could not record standalone audit event %s: %s",
                  kwargs.get("action"), type(exc).__name__)


def list_events(db, *, actions: list[str] | None = None, entity_type: str | None = None,
                actor: str | None = None, outcome: str | None = None,
                start: date | None = None, end: date | None = None,
                text: str | None = None, entity_id: str | None = None,
                limit: int = 500) -> list[dict]:
    with db.session() as s:
        q = select(AuditEvent)
        if actions:
            q = q.where(AuditEvent.action.in_(actions))
        if entity_type:
            q = q.where(AuditEvent.entity_type == entity_type)
        if entity_id:
            q = q.where(AuditEvent.entity_id == str(entity_id))
        if actor:
            q = q.where(AuditEvent.actor == actor)
        if outcome:
            q = q.where(AuditEvent.outcome == outcome)
        if start:
            q = q.where(AuditEvent.timestamp >= datetime.combine(start, datetime.min.time()))
        if end:
            q = q.where(AuditEvent.timestamp <= datetime.combine(end, datetime.max.time()))
        if text:
            like = f"%{text}%"
            q = q.where(or_(AuditEvent.message.ilike(like), AuditEvent.action.ilike(like)))
        q = q.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).limit(limit)
        return [{
            "event_id": e.event_id, "timestamp": e.timestamp, "actor": e.actor or "system",
            "action": e.action, "entity_type": e.entity_type, "entity_id": e.entity_id,
            "outcome": e.outcome, "previous_state": e.previous_state,
            "new_state": e.new_state, "message": e.message, "details": e.details_json,
        } for e in s.scalars(q)]


def distinct_values(db) -> dict[str, list[str]]:
    with db.session() as s:
        return {
            "actions": sorted(x for x in s.scalars(select(AuditEvent.action).distinct()) if x),
            "entity_types": sorted(x for x in s.scalars(select(AuditEvent.entity_type).distinct()) if x),
            "actors": sorted(x for x in s.scalars(select(AuditEvent.actor).distinct()) if x),
        }
