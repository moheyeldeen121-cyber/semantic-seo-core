import json
import time
import pytest
from services.claude import (
    is_mistral_engine,
    is_gemini_engine,
    resolve_model,
    calculate_mistral_cost,
    _throttle_mistral,
    call_mistral_api,
    call_gemini_api,
    call_claude_api,
    start_usage_tracking,
    get_current_usage,
)
import config


def test_is_engine_detection():
    # Mistral & Ministral & Codestral detection
    assert is_mistral_engine("ministral-3b-2512") is True
    assert is_mistral_engine("ministral-8b-2512") is True
    assert is_mistral_engine("codestral-2508") is True
    assert is_mistral_engine("mistral-large-2512") is True
    assert is_mistral_engine("claude-3-5-haiku-20241022") is False

    # Gemini detection
    assert is_gemini_engine("gemini-2.0-flash") is True
    assert is_gemini_engine("gemini-1.5-flash") is True
    assert is_gemini_engine("claude-3-5-haiku-20241022") is False

    # Key format detection
    gemini_key = "AIzaSyD-TestKey1234567890abcdef"
    mistral_key = "1234567890abcdef1234567890abcdef"
    claude_key = "sk-ant-api03-abcdef1234567890"

    assert is_gemini_engine("default", api_key=gemini_key) is True
    assert is_mistral_engine("default", api_key=mistral_key) is True
    assert is_mistral_engine("default", api_key=claude_key) is False


def test_resolve_model_multi_engine():
    mistral_key = "1234567890abcdef1234567890abcdef"
    gemini_key = "AIzaSyD-TestKey1234567890abcdef"

    assert resolve_model("default", api_key=mistral_key) == config.MISTRAL_DEFAULT_MODEL
    assert resolve_model("default", api_key=gemini_key) == config.GEMINI_DEFAULT_MODEL
    assert resolve_model("ministral-3b-2512") == "ministral-3b-2512"
    assert resolve_model("gemini-2.0-flash") == "gemini-2.0-flash"
    assert resolve_model("hybrid-haiku-sonnet", task_type="extraction") == config.CLAUDE_EXTRACTION_MODEL


def test_calculate_mistral_cost():
    cost_small = calculate_mistral_cost("ministral-8b-2512", input_tokens=10_000, output_tokens=2_000)
    assert cost_small == pytest.approx(0.0016, rel=1e-4)

    cost_large = calculate_mistral_cost("mistral-large-2512", input_tokens=10_000, output_tokens=2_000)
    assert cost_large == pytest.approx(0.032, rel=1e-4)


def test_throttle_mistral_timing(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    import services.claude as s_claude

    s_claude._last_mistral_time = time.monotonic()
    _throttle_mistral()

    assert len(sleeps) >= 1
    assert sleeps[0] > 0.8


def test_call_mistral_api_success(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json

        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "choices": [
                        {"message": {"role": "assistant", "content": '{"clusters": []}'}}
                    ],
                    "usage": {
                        "prompt_tokens": 500,
                        "completion_tokens": 150,
                        "total_tokens": 650,
                    }
                }
            def raise_for_status(self):
                pass
        return MockResponse()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    start_usage_tracking()
    content, usage = call_mistral_api(
        prompt="Test extraction",
        system_prompt="System prompt",
        model="ministral-8b-2512",
        api_key="mistral-test-key-32chars-long123",
        return_usage=True,
    )

    assert json.loads(content) == {"clusters": []}
    assert captured["url"] == config.MISTRAL_API_URL
    assert captured["headers"]["Authorization"] == "Bearer mistral-test-key-32chars-long123"
    assert captured["json"]["model"] == "ministral-8b-2512"
    assert usage["total_tokens"] == 650
    assert usage["cost_usd"] > 0


def test_call_gemini_api_success(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json

        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "choices": [
                        {"message": {"role": "assistant", "content": '{"clusters": []}'}}
                    ],
                    "usage": {
                        "prompt_tokens": 300,
                        "completion_tokens": 80,
                        "total_tokens": 380,
                    }
                }
            def raise_for_status(self):
                pass
        return MockResponse()

    monkeypatch.setattr("requests.post", fake_post)

    start_usage_tracking()
    content, usage = call_gemini_api(
        prompt="Test extraction",
        system_prompt="System prompt",
        model="gemini-2.0-flash",
        api_key="AIzaSyD-GoogleGeminiKey12345",
        return_usage=True,
    )

    assert json.loads(content) == {"clusters": []}
    assert "generativelanguage.googleapis.com" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer AIzaSyD-GoogleGeminiKey12345"
    assert captured["json"]["model"] == "gemini-2.0-flash"
    assert usage["cost_usd"] == 0.0


def test_call_claude_api_routes_dynamically(monkeypatch):
    called = []

    monkeypatch.setattr("services.claude.call_gemini_api", lambda *a, **kw: called.append("gemini") or ('{"gemini": 1}', {}))
    monkeypatch.setattr("services.claude.call_mistral_api", lambda *a, **kw: called.append("mistral") or ('{"mistral": 1}', {}))

    call_claude_api(prompt="T", system_prompt="S", model="gemini-2.0-flash", api_key="AIzaSyD-test")
    assert called[-1] == "gemini"

    call_claude_api(prompt="T", system_prompt="S", model="ministral-8b-2512", api_key="mistral-key-1234567890abcdef")
    assert called[-1] == "mistral"
