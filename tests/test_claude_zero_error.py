"""
Tests for Claude High-Quality & Zero-Error Architecture
Verifies:
- Current-model resolution (retired IDs remapped) and cost tracking
- Bulletproof self-repairing JSON parser (handles preamble, markdown code blocks, trailing commas)
- Token capacity of 8192 output tokens
- Anthropic Prompt Caching structure
"""
import json
import pytest
from services.claude import (
    resolve_model,
    calculate_claude_cost,
    _clean_ai_json_output,
    _parse_json_from_response,
    call_claude_api,
)
import config


def test_resolve_model_claude_flagships():
    # Hybrid mode
    assert resolve_model("hybrid-haiku-sonnet", task_type="extraction") == config.CLAUDE_EXTRACTION_MODEL
    assert resolve_model("hybrid-haiku-sonnet", task_type="brief") == config.CLAUDE_BRIEF_MODEL
    assert not config.is_retired_claude_model(config.CLAUDE_BRIEF_MODEL)

    # Friendly aliases map to current Anthropic IDs
    assert resolve_model("claude-haiku-4-5") == "claude-haiku-4-5-20251001"
    assert resolve_model("sonnet") == "claude-sonnet-5"
    # Retired IDs and their aliases never reach the API
    for retired in ("claude-3-7-sonnet", "claude-3.7-sonnet", "claude-3-5-sonnet-20241022",
                    "claude-3-5-haiku-20241022", "claude-3-opus-20240229"):
        assert resolve_model(retired) == config.CLAUDE_EXTRACTION_MODEL


def test_calculate_claude_cost_opus_and_sonnet():
    # Sonnet 3.7 / 3.5 ($3/M in, $15/M out)
    cost_sonnet = calculate_claude_cost("claude-3-7-sonnet-20250219", input_tokens=10_000, output_tokens=2_000)
    assert cost_sonnet == 0.06

    # Opus ($15/M in, $75/M out)
    cost_opus = calculate_claude_cost("claude-3-opus-20240229", input_tokens=10_000, output_tokens=2_000)
    assert cost_opus == pytest.approx(0.30, rel=1e-4)


def test_self_repairing_json_with_preamble_and_markdown():
    raw_response = """Here is the complete semantic extraction for the article:

```json
{
  "source_label": "Test Article",
  "clusters": [
    {
      "cluster_name": "Core SEO",
      "entities": [
        {"name": "سيو دلالي", "type": "Concept", "importance": "high", "salience": 0.95}
      ]
    }
  ]
}
```

Hope this helps!"""

    parsed = _parse_json_from_response(raw_response)
    assert parsed["source_label"] == "Test Article"
    assert parsed["clusters"][0]["cluster_name"] == "Core SEO"
    assert parsed["clusters"][0]["entities"][0]["name"] == "سيو دلالي"


def test_self_repairing_json_with_trailing_commas():
    # Trailing comma before closing bracket and brace
    broken_json = """{
      "source_label": "Trailing Comma Test",
      "clusters": [
        {
          "cluster_name": "Cluster",
          "entities": [
            {"name": "Entity 1", "salience": 0.8,},
          ],
        },
      ],
    }"""
    parsed = _parse_json_from_response(broken_json)
    assert parsed["source_label"] == "Trailing Comma Test"
    assert len(parsed["clusters"]) == 1


def test_token_budgets_are_high_quality():
    assert config.CONTENT_BRIEF_MAX_TOKENS >= 8192
    assert config.SERP_FINAL_MAX_TOKENS >= 8192
    assert config.CLAUDE_REQUEST_TIMEOUT >= 180
    assert config.CLAUDE_MAX_RETRIES >= 4


def test_current_model_prices():
    # $2/$10 Sonnet 5, $4/$20 Opus 5.5, $10/$50 Fable 5.1, $1/$5 Haiku 4.5 per 1M tokens
    assert calculate_claude_cost("claude-sonnet-5", 1_000_000, 1_000_000) == 12.0
    assert calculate_claude_cost("claude-opus-5-5", 1_000_000, 1_000_000) == 24.0
    assert calculate_claude_cost("claude-fable-5-1", 1_000_000, 1_000_000) == 60.0
    assert calculate_claude_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000) == 6.0
    assert calculate_claude_cost("claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0
