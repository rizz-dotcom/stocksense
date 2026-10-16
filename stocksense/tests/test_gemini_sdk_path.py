"""Runs the REAL google-genai SDK end to end against a simulated Gemini server
(httpx.MockTransport). Nothing leaves the machine, but the exact HTTP request
the SDK builds - URL, auth header, JSON schema - and Google's real error shapes
are exercised. A fake 'AQ.' auth key (the new default format) is used."""
import json

import httpx
import pytest

from stocksense.config import AppConfig
from stocksense.exceptions import AIAuthError, AIModelError, AIRateLimitError, AITimeoutError
from stocksense.services import ai_service

FAKE_AQ_KEY = "AQ.FakeTestKey_" + "x" * 40
GOOD = {"summary": "Patties are tight; delivery is next day.", "overall_risk": "medium",
        "warnings": ["Cheese is unfunded"], "questions_for_owner": ["Is a weekend rush expected?"],
        "questionable_assumptions": [], "missing_evidence": []}


def cfg():
    return AppConfig("sqlite://", None, None, FAKE_AQ_KEY, "gemini-3.5-flash", 5, 1000, "INFO")


def ok_body(text):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]},
                            "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
            "modelVersion": "gemini-3.5-flash"}


def err_body(code, status, message, reason=None):
    body = {"error": {"code": code, "status": status, "message": message}}
    if reason:
        body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                     "reason": reason}]
    return body


def make(responses):
    seen = []
    queue = list(responses)

    def handler(request: httpx.Request):
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx.Response(status, json=body)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    svc = ai_service.AIReviewService(cfg(), http_client=http, sleep=lambda s: None)
    return svc, seen


def test_real_sdk_request_shape_and_success():
    svc, seen = make([(200, ok_body(json.dumps(GOOD)))])
    out = svc.review({"proposal": "PR-1", "lines": []}, 100_000)
    assert out["overall_risk"] == "medium"
    req = seen[0]
    assert req.method == "POST"
    assert req.url.host == "generativelanguage.googleapis.com"
    assert req.url.path.endswith("/models/gemini-3.5-flash:generateContent")
    assert req.headers["x-goog-api-key"] == FAKE_AQ_KEY      # AQ. key sent as Google documents
    assert "key=" not in str(req.url)                        # never in the URL
    body = json.loads(req.content)
    gen = body["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    schema = gen.get("responseJsonSchema")
    assert schema and set(schema["required"]) >= {"summary", "overall_risk"}
    assert "systemInstruction" in body and "Do NOT approve" in json.dumps(body["systemInstruction"])


def test_access_token_type_unsupported_gets_clear_message():
    svc, seen = make([(401, err_body(401, "UNAUTHENTICATED",
                                     "Request had invalid authentication credentials.",
                                     "ACCESS_TOKEN_TYPE_UNSUPPORTED"))])
    with pytest.raises(AIAuthError) as exc:
        svc.review({}, 1)
    assert "ACCESS_TOKEN_TYPE_UNSUPPORTED" in exc.value.user_message and len(seen) == 1


def test_invalid_key_not_retried():
    svc, seen = make([(400, err_body(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."))])
    with pytest.raises(AIAuthError):
        svc.review({}, 1)
    assert len(seen) == 1


def test_unknown_model():
    svc, _ = make([(404, err_body(404, "NOT_FOUND", "models/gemini-x is not found"))])
    with pytest.raises(AIModelError):
        svc.review({}, 1)


def test_server_error_then_success():
    svc, seen = make([(503, err_body(503, "UNAVAILABLE", "overloaded")),
                      (200, ok_body(json.dumps(GOOD)))])
    assert svc.review({}, 1)["summary"] and len(seen) == 2


def test_rate_limit_exhausts_retries():
    svc, seen = make([(429, err_body(429, "RESOURCE_EXHAUSTED", "Rate limit reached"))] * 3)
    with pytest.raises(AIRateLimitError):
        svc.review({}, 1)
    assert len(seen) == 3


def test_network_timeout():
    svc, seen = make([httpx.ReadTimeout("timed out")] * 3)
    with pytest.raises(AITimeoutError):
        svc.review({}, 1)


def test_connection_check_success_and_failure():
    svc, _ = make([(200, ok_body("ready"))])
    assert ai_service.test_connection(svc) == {
        "ok": True, "message": "Connected to gemini-3.5-flash. Gemini replied: ready"}
    svc, _ = make([(401, err_body(401, "UNAUTHENTICATED", "bad", "ACCESS_TOKEN_TYPE_UNSUPPORTED"))])
    result = ai_service.test_connection(svc)
    assert not result["ok"] and "AUTH_ERROR" in result["message"]


def test_aq_keys_are_redacted_from_logs():
    from stocksense.logging_setup import redact
    assert FAKE_AQ_KEY not in redact(f"using key {FAKE_AQ_KEY} now")
