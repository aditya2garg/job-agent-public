import json

import httpx
import pytest

from jobhunt import llm


def test_extract_json_from_fenced_text():
    out = llm._extract_json('Sure!\n```json\n{"fit_score": 77, "gaps": []}\n```')
    assert out["fit_score"] == 77


def test_extract_json_fails_cleanly():
    with pytest.raises(ValueError):
        llm._extract_json("no json here")


def test_normalize_coerces_shapes():
    a = llm._normalize({"fit_score": "88.4", "gaps": ["PyTorch"], "strengths": None})
    assert a["fit_score"] == 88 and a["gaps"][0]["gap"] == "PyTorch" and a["strengths"] == []
    assert llm._normalize({"fit_score": "abc"})["fit_score"] is None


def test_gemini_call_shape(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.headers["x-goog-api-key"]
        body = json.loads(request.content)
        seen["json_mode"] = body["generationConfig"]["responseMimeType"]
        seen["system"] = body["systemInstruction"]["parts"][0]["text"]
        parts = [{"text": "thinking...", "thought": True}, {"text": '{"fit_score": 81}'}]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = llm.call_model(client, ("gemini", "gemini-x"), "hi")
    assert out["fit_score"] == 81
    assert seen["path"].endswith("/models/gemini-x:generateContent") and seen["key"] == "k"
    assert seen["json_mode"] == "application/json" and "recruiter" in seen["system"]


def test_gemini_empty_response_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})))
    with pytest.raises(ValueError, match="no candidates"):
        llm.call_model(client, ("gemini", "m"), "hi")


def test_provider_detection(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert llm.provider() is None
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    assert llm.provider()[0] == "gemini"
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    monkeypatch.setenv("JOBHUNT_GEMINI_MODEL", "gemini-2.5-pro")
    assert llm.provider() == ("gemini", "gemini-2.5-pro")
