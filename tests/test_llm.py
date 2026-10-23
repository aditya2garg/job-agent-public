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


def test_anthropic_call_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-api-key"]
        body = json.loads(request.content)
        seen["model"] = body["model"]
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"fit_score": 81}'}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = llm.call_model(client, ("anthropic", "m1"), "hi")
    assert out["fit_score"] == 81 and seen["url"].endswith("/v1/messages") and seen["key"] == "k" and seen["model"] == "m1"


def test_openai_call_shape(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"fit_score": 64}'}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert llm.call_model(client, ("openai", "m"), "hi")["fit_score"] == 64


def test_provider_detection(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm.provider() is None
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert llm.provider()[0] == "openai"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm.provider()[0] == "anthropic"
