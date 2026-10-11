"""
Nara API compatibility shim.
All calls are seamlessly forwarded to Anthropic Claude (services.claude).
"""

from services.claude import (
    call_claude_api as call_nara_api,
    get_system_prompt,
    extract_entities_for_source,
    resolve_model,
    calculate_claude_cost,
    start_usage_tracking,
    get_current_usage,
    _split_text_into_chunks,
    _merge_source_analyses,
)

__all__ = [
    "call_nara_api",
    "get_system_prompt",
    "extract_entities_for_source",
    "resolve_model",
    "calculate_claude_cost",
    "start_usage_tracking",
    "get_current_usage",
    "_split_text_into_chunks",
    "_merge_source_analyses",
]
