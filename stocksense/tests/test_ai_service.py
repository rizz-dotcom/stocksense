"""All Gemini calls are mocked - no network or paid API call is made."""
import json
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors

from stocksense.config import AppConfig
from stocksense.exceptions import (AIAuthError, AINotConfiguredError, AIRateLimitError,
                                   AIResponseError, AITimeoutError)
from stocksense.services import ai_service, proposal_service

GOOD = {"summary": "Patty stock is tight.", "overall_risk": "high",
        "warnings": ["Lead time close to stockout"], "questions_for_owner": ["Can you defrost?"],
        "questionable_assumptions": [], "missing_evidence": ["Weekend demand"]}


def cfg(key="test-key"):
    return AppConfig("sqlite://", None, None, key, "gemini-test-model", 5, 1000, "INFO")


class FakeModels:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(text=out, prompt_feedback=None, candidates=[])


def service(outputs, key="test-key"):
    models = FakeModels(outputs)
    svc = ai_service.AIReviewService(cfg(key), client=SimpleNamespace(models=models),
                                     sleep=lambda s: None)
    return svc, models


def api_error(code, status, message):
    return genai_errors.ClientError(code, {"error": {"code": code, "status": status,
                                                     "message": message}})


def test_missing_key_means_not_configured(db, ready_proposal, admin):
    svc = ai_service.AIReviewService(cfg(key=None))
    assert not svc.configured
    with pytest.raises(AINotConfiguredError):
        ai_service.review_proposal(db, admin, ready_proposal["proposal_id"], svc)
    # core features still work without AI
    assert proposal_service.submit_proposal(db, admin, ready_proposal["proposal_id"], 1) == "PENDING_APPROVAL"


def test_successful_mocked_review_is_stored_with_version(db, ready_proposal, admin):
    svc, models = service([json.dumps(GOOD)])
    out = ai_service.review_proposal(db, admin, ready_proposal["proposal_id"], svc)
    assert out["status"] == "OK" and out["version"] == 1 and models.calls == 1
    stored = ai_service.latest_review_for_version(db, ready_proposal["proposal_id"], 1)
    assert stored["result"]["overall_risk"] == "high" and stored["model"] == "gemini-test-model"


def test_review_of_old_version_is_not_reused(db, ready_proposal, admin):
    svc, _ = service([json.dumps(GOOD)])
    ai_service.review_proposal(db, admin, ready_proposal["proposal_id"], svc)
    proposal_service.revise_proposal(db, admin, ready_proposal["proposal_id"], 1,
                                     [{"product_id": ready_proposal["product_id"],
                                       "supplier_id": ready_proposal["supplier_id"],
                                       "quantity": 60}], "edit")
    assert ai_service.latest_review_for_version(db, ready_proposal["proposal_id"], 2) is None


def test_malformed_json_is_discarded(db, ready_proposal, admin):
    svc, _ = service(["{not json"])
    out = ai_service.review_proposal(db, admin, ready_proposal["proposal_id"], svc)
    assert out["status"] == "INVALID_RESPONSE" and out["result"] is None


def test_missing_fields_rejected():
    with pytest.raises(AIResponseError):
        ai_service.AIReviewService.parse_output(json.dumps({"summary": "x"}), 100)
    with pytest.raises(AIResponseError):
        ai_service.AIReviewService.parse_output(json.dumps({**GOOD, "overall_risk": "panic"}), 100)


def test_empty_response():
    svc, _ = service([""])
    with pytest.raises(AIResponseError):
        svc.review({}, 100)


def test_timeout_is_retried_then_reported():
    svc, models = service([httpx.ReadTimeout("slow")] * 3)
    with pytest.raises(AITimeoutError):
        svc.review({}, 100)
    assert models.calls == 3


def test_rate_limit_retry_then_success():
    svc, models = service([api_error(429, "RESOURCE_EXHAUSTED", "Rate limit"), json.dumps(GOOD)])
    assert svc.review({}, 100)["summary"] == GOOD["summary"] and models.calls == 2


def test_quota_exhaustion_is_not_retried():
    svc, models = service([api_error(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota")])
    with pytest.raises(AIRateLimitError):
        svc.review({}, 100)
    assert models.calls == 1


def test_invalid_key_is_not_retried():
    svc, models = service([api_error(400, "INVALID_ARGUMENT", "API key not valid.")])
    with pytest.raises(AIAuthError):
        svc.review({}, 100)
    assert models.calls == 1


def test_ai_contradicting_trusted_calculation_is_flagged_and_ignored(db, ready_proposal, admin):
    evil = {**GOOD, "stated_total_cost": 1.0, "decision": "APPROVE",
            "new_quantities": {"BUN": 9999}}
    svc, _ = service([json.dumps(evil)])
    out = ai_service.review_proposal(db, admin, ready_proposal["proposal_id"], svc)
    notes = " ".join(out["result"]["system_notes"])
    assert "contradicts" in notes and "cannot approve" in notes
    d = proposal_service.get_proposal_detail(db, ready_proposal["proposal_id"])
    assert d["status"] == "DRAFT" and d["current"]["items"][0]["quantity"] == 70
    assert d["decisions"] == []


def test_ai_disabled_in_settings(db, ready_proposal, admin):
    from stocksense.services import settings_service
    settings_service.update_settings(db, admin, {"ai_enabled": False})
    svc, models = service([json.dumps(GOOD)])
    with pytest.raises(AINotConfiguredError):
        ai_service.review_proposal(db, admin, ready_proposal["proposal_id"], svc)
    assert models.calls == 0


def test_viewer_cannot_request_review(db, ready_proposal, viewer):
    from stocksense.exceptions import PermissionDeniedError
    svc, _ = service([json.dumps(GOOD)])
    with pytest.raises(PermissionDeniedError):
        ai_service.review_proposal(db, viewer, ready_proposal["proposal_id"], svc)
