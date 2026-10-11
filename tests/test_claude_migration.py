import json
import pytest
from services.claude import (
    resolve_model,
    calculate_claude_cost,
    start_usage_tracking,
    add_to_current_usage,
    get_current_usage,
    _parse_json_from_response,
    call_claude_api,
)
from models.schemas import UsageMetrics
import config


def test_resolve_model_hybrid_routing():
    # Hybrid mode maps extraction to Haiku and brief to Sonnet
    assert resolve_model("hybrid-haiku-sonnet", task_type="extraction") == config.CLAUDE_EXTRACTION_MODEL
    assert resolve_model("hybrid-haiku-sonnet", task_type="brief") == config.CLAUDE_BRIEF_MODEL
    assert resolve_model("", task_type="extraction") == config.CLAUDE_EXTRACTION_MODEL
    assert resolve_model("", task_type="brief") == config.CLAUDE_BRIEF_MODEL
    # Current explicit models pass through unchanged
    assert resolve_model("claude-sonnet-4-6", task_type="extraction") == "claude-sonnet-4-6"
    # Retired models (Claude 3.7 Sonnet was retired on 2026-02-19) fall back to the .env default
    assert resolve_model("claude-3-7-sonnet-20250219", task_type="extraction") == config.CLAUDE_EXTRACTION_MODEL


def test_calculate_claude_cost_haiku():
    # 10,000 input ($0.01) + 2,000 output ($0.01) = $0.02
    cost = calculate_claude_cost("claude-3-5-haiku-20241022", input_tokens=10_000, output_tokens=2_000)
    assert cost == 0.02

    # With 20,000 cache read ($0.002) + 5,000 cache creation ($0.00625)
    cost_cached = calculate_claude_cost(
        "claude-3-5-haiku-20241022",
        input_tokens=1_000,
        output_tokens=1_000,
        cache_read_tokens=20_000,
        cache_creation_tokens=5_000,
    )
    assert cost_cached == 0.01425


def test_calculate_claude_cost_sonnet():
    # 10,000 input ($0.03) + 2,000 output ($0.03) = $0.06
    cost = calculate_claude_cost("claude-3-5-sonnet-20241022", input_tokens=10_000, output_tokens=2_000)
    assert cost == 0.06


def test_usage_tracking_thread_safe_aggregation():
    start_usage_tracking()
    usage1 = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_creation_input_tokens": 500,
        "cache_read_input_tokens": 1500,
        "total_tokens": 3200,
        "cost_usd": 0.0035,
    }
    usage2 = {
        "input_tokens": 2000,
        "output_tokens": 400,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 3000,
        "total_tokens": 5400,
        "cost_usd": 0.0055,
    }
    add_to_current_usage(usage1)
    add_to_current_usage(usage2)

    total_usage = get_current_usage()
    assert isinstance(total_usage, UsageMetrics)
    assert total_usage.input_tokens == 3000
    assert total_usage.output_tokens == 600
    assert total_usage.cache_creation_input_tokens == 500
    assert total_usage.cache_read_input_tokens == 4500
    assert total_usage.total_tokens == 8600
    assert total_usage.cost_usd == pytest.approx(0.009, rel=1e-4)


def test_parse_json_from_markdown_code_fences():
    clean_json = '{"clusters": [{"cluster_name": "SEO", "entities": []}]}'
    wrapped = f"```json\n{clean_json}\n```"
    result = _parse_json_from_response(wrapped)
    assert "clusters" in result
    assert result["clusters"][0]["cluster_name"] == "SEO"


def test_call_claude_api_mock(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json

        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "content": [{"type": "text", "text": "{\"status\": \"ok\"}"}],
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 40,
                        "cache_creation_input_tokens": 100,
                        "cache_read_input_tokens": 0,
                    }
                }
            def raise_for_status(self):
                pass
        return MockResponse()

    monkeypatch.setattr("requests.post", fake_post)

    raw_text, usage = call_claude_api(
        prompt="Test Prompt",
        system_prompt="System instructions",
        model="claude-3-5-haiku-20241022",
        api_key="sk-ant-test-key",
        return_usage=True,
    )

    assert json.loads(raw_text) == {"status": "ok"}
    assert captured["headers"]["x-api-key"] == "sk-ant-test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    # Prompt caching is GA: no beta header is required any more.
    assert "anthropic-beta" not in captured["headers"]
    assert captured["json"]["temperature"] == config.AI_TEMPERATURE
    assert captured["json"]["system"][0]["cache_control"]["type"] == "ephemeral"
    assert usage["total_tokens"] == 260
    assert usage["cost_usd"] > 0
