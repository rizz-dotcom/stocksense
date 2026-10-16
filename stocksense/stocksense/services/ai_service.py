"""Optional Gemini review of an existing, deterministically calculated proposal.

Gemini is advisory only:
* It receives already-calculated figures and is asked to *review* them.
* Its output is untrusted: it is schema-validated, size-limited and stored
  separately. Prices, quantities, totals or "approvals" it states are never
  applied. If it states a total that differs from the trusted total, a
  contradiction note is added.
* It cannot approve, change permissions, or trigger orders - no such code path
  exists.
* Calls happen only when a user presses the review button (never on every
  Streamlit rerun). Results are stored with the proposal version they reviewed.
"""
from __future__ import annotations

import json
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import select

from stocksense.config import AppConfig
from stocksense.exceptions import (AIAuthError, AIBlockedError, AIModelError,
                                   AINotConfiguredError, AIProviderError, AIRateLimitError,
                                   AIResponseError, AIServiceError, AITimeoutError, NotFoundError)
from stocksense.models import AIReview, Proposal
from stocksense.schemas import AIReviewOutput
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.utils.money import from_minor

log = logging.getLogger("stocksense.ai")

SYSTEM_INSTRUCTION = """You are a cautious procurement reviewer for a small restaurant.
You review a purchase proposal whose numbers were calculated by deterministic software.
Rules:
- Do NOT recalculate, change or invent prices, quantities, budgets or totals.
- Do NOT approve or reject. A human decides.
- Point out risks (stockouts before delivery, unfunded items, weak sales history,
  stale quotes, expiry), questionable assumptions and missing evidence.
- Suggest short, concrete questions the owner should answer.
- Only use facts present in the supplied data. Say when evidence is missing.
Respond ONLY with JSON matching the requested schema."""

_FORBIDDEN_KEYS = {"approve", "approved", "approval", "decision", "recommended_decision",
                   "new_quantities", "quantities", "budget", "new_budget", "prices", "order"}


def build_review_payload(detail: dict, live_report: dict | None = None) -> dict:
    current = detail["current"]
    validation = live_report or current["validation"]
    cur = detail["currency"]

    def money(minor):
        return None if minor is None else f"{from_minor(minor)} {cur}"

    forecasts = {str(f["product_id"]): f for f in
                 (detail["versions"][-1]["inputs"].get("forecasts") or [])}
    lines = []
    for item in current["items"]:
        fc = forecasts.get(str(item["product_id"]), {})
        lines.append({
            "sku": item["sku"], "product": item["product"], "supplier": item["supplier"],
            "quantity": item["quantity"], "suggested_quantity": item["suggested_qty"],
            "unit_price": money(item["unit_price_minor"]),
            "line_total": money(item["line_total_minor"]), "funded": item["funded"],
            "risk_level": item["risk_level"], "note": item["note"],
            "avg_daily_demand": fc.get("avg_daily_demand"),
            "days_of_cover": fc.get("days_cover"), "data_quality": fc.get("data_quality"),
            "sales_days_observed": fc.get("observations"),
            "fastest_lead_time_days": fc.get("min_lead_time_days"),
            "expiry_status": fc.get("expiry_status"),
        })
    return {
        "proposal": detail["reference"], "version": detail["current_version"],
        "status": detail["status"], "planning_horizon_days": detail["horizon_days"],
        "budget": money(detail["budget_minor"]), "trusted_total": money(current["total_minor"]),
        "currency": cur, "lines": lines,
        "deterministic_errors": validation.get("errors", []),
        "deterministic_warnings": validation.get("warnings", []),
        "data_note": "Demand figures are simple averages of recorded daily sales, not ML.",
    }


class AIReviewService:
    def __init__(self, config: AppConfig, *, client=None, http_client=None,
                 sleep: Callable[[float], None] = time.sleep, max_attempts: int = 3):
        self.config = config
        self._client = client
        self._http_client = http_client  # tests inject an httpx.Client with a mock transport
        self._sleep = sleep
        self.max_attempts = max_attempts

    @property
    def configured(self) -> bool:
        return self._client is not None or self.config.gemini_configured

    def _get_client(self):
        if self._client is None:
            if not self.config.gemini_configured:
                raise AINotConfiguredError()
            from google import genai
            from google.genai import types
            options = {"timeout": self.config.gemini_timeout_seconds * 1000}
            if self._http_client is not None:
                options["httpx_client"] = self._http_client
            # Works with both key formats: new "AQ." auth keys and older "AIza" keys.
            # The key format is never validated locally - Google owns that format.
            self._client = genai.Client(api_key=self.config.gemini_api_key,
                                        http_options=types.HttpOptions(**options))
        return self._client

    def _generate(self, prompt: str) -> str:
        from google.genai import types
        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION, temperature=0.2,
            max_output_tokens=2048, response_mime_type="application/json",
            response_json_schema=AIReviewOutput.model_json_schema())
        response = client.models.generate_content(model=self.config.gemini_model,
                                                  contents=prompt, config=config)
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise AIBlockedError()
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            reason = str(getattr(candidates[0], "finish_reason", "") or "")
            if "SAFETY" in reason or "PROHIBITED" in reason or "BLOCKLIST" in reason:
                raise AIBlockedError()
        text = getattr(response, "text", None)
        if not text or not str(text).strip():
            raise AIResponseError("Gemini returned an empty response.")
        return str(text)

    @staticmethod
    def _map_error(exc: Exception) -> AIServiceError:
        if isinstance(exc, AIServiceError):
            return exc
        try:
            from google.genai import errors as genai_errors
            import httpx
        except ImportError:  # pragma: no cover
            return AIProviderError(detail=type(exc).__name__)
        if isinstance(exc, httpx.TimeoutException) or isinstance(exc, TimeoutError):
            return AITimeoutError()
        if isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None) or 0
            message = str(getattr(exc, "message", "") or "").lower()
            status = str(getattr(exc, "status", "") or "").upper()
            details = str(getattr(exc, "details", "") or "")
            if "ACCESS_TOKEN_TYPE_UNSUPPORTED" in details or "ACCESS_TOKEN_TYPE_UNSUPPORTED" in message.upper():
                return AIAuthError(
                    "Google rejected this key type (ACCESS_TOKEN_TYPE_UNSUPPORTED). Create a new "
                    "key in Google AI Studio for a project with the Gemini API enabled, and keep "
                    "google-genai up to date (pip install -U google-genai).")
            if code in (401, 403) or "api key" in message or status in ("PERMISSION_DENIED",
                                                                        "UNAUTHENTICATED"):
                return AIAuthError()
            if code == 404 or status == "NOT_FOUND":
                return AIModelError()
            if code == 429 or status == "RESOURCE_EXHAUSTED":
                err = AIRateLimitError()
                if "quota" in message and ("exceed" in message or "exhaust" in message):
                    err = AIRateLimitError("Gemini quota exhausted. Check your plan/billing, "
                                           "then retry later.")
                    err.retryable = False
                return err
            if code == 400:
                return AIModelError("Gemini rejected the request (check GEMINI_MODEL).",
                                    detail=status)
            if code >= 500:
                return AIProviderError()
            return AIProviderError(detail=f"{code} {status}")
        if isinstance(exc, httpx.HTTPError) or isinstance(exc, ConnectionError):
            return AIProviderError("Could not reach Gemini (network problem). Retry later.")
        return AIProviderError(detail=type(exc).__name__)

    @staticmethod
    def parse_output(text: str, trusted_total_minor: int) -> dict:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            raw = json.loads(cleaned)
        except json.JSONDecodeError:
            raise AIResponseError("Gemini returned malformed JSON; the review was discarded.") from None
        if not isinstance(raw, dict):
            raise AIResponseError("Gemini returned an unexpected structure; discarded.")
        try:
            output = AIReviewOutput(**raw)
        except ValidationError as exc:
            missing = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
            raise AIResponseError(
                "Gemini's response was missing required fields or had invalid values "
                f"({', '.join(missing[:5])}); the review was discarded.") from None
        notes = []
        forbidden = sorted(k for k in raw if k.lower() in _FORBIDDEN_KEYS)
        if forbidden:
            notes.append("The AI tried to supply " + ", ".join(forbidden) + ". These were "
                         "ignored: the AI cannot approve, change quantities, prices or budgets.")
        if output.stated_total_cost is not None:
            try:
                stated = Decimal(str(output.stated_total_cost)).quantize(Decimal("0.01"))
            except InvalidOperation:
                stated = None
            trusted = from_minor(trusted_total_minor)
            if stated is None or abs(stated - trusted) > Decimal("0.01"):
                notes.append(f"The AI stated a total of {output.stated_total_cost}, which "
                             f"contradicts the calculated total {trusted}. The calculated "
                             "total is authoritative.")
        result = output.model_dump()
        result["system_notes"] = notes
        result["trusted_total_minor"] = trusted_total_minor
        return result

    def review(self, payload: dict, trusted_total_minor: int) -> dict:
        prompt = ("Review this purchase proposal data and respond in JSON.\n"
                  + json.dumps(payload, ensure_ascii=False, default=str))
        last: AIServiceError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                text = self._generate(prompt)
                return self.parse_output(text, trusted_total_minor)
            except Exception as exc:  # noqa: BLE001 - mapped to typed errors below
                err = self._map_error(exc)
                last = err
                log.warning("Gemini attempt %s failed: %s", attempt, err.status)
                if not err.retryable or attempt == self.max_attempts:
                    raise err from None
                self._sleep(min(2 ** (attempt - 1), 8))
        raise last or AIProviderError()  # pragma: no cover


def test_connection(service: AIReviewService) -> dict:
    """Make one tiny request to prove the key and model work. Never retried, so a
    bad key costs a single call. Returns {'ok': bool, 'message': str}."""
    from google.genai import types
    if not service.configured:
        return {"ok": False, "message": AINotConfiguredError.default_message}
    try:
        client = service._get_client()
        response = client.models.generate_content(
            model=service.config.gemini_model, contents="Reply with the single word: ready",
            config=types.GenerateContentConfig(max_output_tokens=20, temperature=0))
        text = (getattr(response, "text", None) or "").strip()
        return {"ok": True, "message": f"Connected to {service.config.gemini_model}. "
                                       f"Gemini replied: {text[:40] or '(empty)'}"}
    except Exception as exc:  # noqa: BLE001 - mapped to a safe message
        err = service._map_error(exc)
        return {"ok": False, "message": f"{err.status}: {err.user_message}"}


def review_proposal(db, actor: UserContext, proposal_id: int, service: AIReviewService) -> dict:
    """Run and store an AI review for the proposal's *current* version."""
    from stocksense.services.proposal_service import get_proposal_detail, live_validation
    from stocksense.services.settings_service import get_settings
    require(actor, Permission.REQUEST_AI_REVIEW)
    if not get_settings(db).ai_enabled:
        raise AINotConfiguredError("AI review is switched off in Settings.")
    if not service.configured:
        raise AINotConfiguredError()
    detail = get_proposal_detail(db, proposal_id)
    report = live_validation(db, proposal_id).to_dict()
    payload = build_review_payload(detail, report)
    version = detail["current_version"]
    total = detail["current"]["total_minor"]
    try:
        result = service.review(payload, total)
        status, error = "OK", None
    except AIServiceError as exc:
        result, status, error = None, exc.status, exc.user_message
    with db.session() as s:
        if s.get(Proposal, proposal_id) is None:
            raise NotFoundError("Proposal not found.")
        row = AIReview(proposal_id=proposal_id, proposal_version=version,
                       requested_by=actor.username, model=service.config.gemini_model,
                       status=status, result_json=json.dumps(result) if result else None,
                       error_message=error)
        s.add(row)
        s.flush()
        audit_service.record(
            s, actor=actor.username,
            action="AI_REVIEW_COMPLETED" if status == "OK" else "AI_REVIEW_FAILED",
            entity_type="proposal", entity_id=str(proposal_id),
            outcome="SUCCESS" if status == "OK" else "FAILURE",
            message=f"AI review of v{version} ({service.config.gemini_model}): {status}."
                    + (f" {error}" if error else ""))
        review_id = row.id
    return {"id": review_id, "status": status, "version": version, "result": result,
            "error": error}


def latest_review_for_version(db, proposal_id: int, version: int) -> dict | None:
    with db.session() as s:
        row = s.scalar(select(AIReview).where(AIReview.proposal_id == proposal_id,
                                              AIReview.proposal_version == version)
                       .order_by(AIReview.id.desc()).limit(1))
        if row is None:
            return None
        return {"id": row.id, "status": row.status, "version": row.proposal_version,
                "model": row.model, "created_at": row.created_at,
                "result": json.loads(row.result_json) if row.result_json else None,
                "error": row.error_message}
