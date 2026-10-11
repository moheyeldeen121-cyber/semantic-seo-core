from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
TRANSLATIONS = (ROOT / "static" / "translations.js").read_text(encoding="utf-8")


def test_html_ids_are_unique_and_script_references_exist():
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    duplicates = [key for key, count in Counter(ids).items() if count > 1]
    assert duplicates == []

    referenced = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', SCRIPT))
    # toastHost is intentionally created on demand by showToast().
    assert sorted(referenced - set(ids) - {"toastHost"}) == []


def test_all_literal_translation_keys_exist_in_both_languages():
    keys = set(re.findall(r'\bt\(["\']([^"\']+)["\']', SCRIPT))
    keys.update(re.findall(r'data-i18n(?:-placeholder|-title)?="([^"]+)"', HTML))
    missing = [key for key in sorted(keys) if TRANSLATIONS.count(f"{key}:") < 2]
    assert missing == []


def test_live_editor_contract_is_complete():
    for function_name in (
        "initLiveEditor",
        "gradeLiveEditorContent",
        "applyLiveEditorSuggestion",
        "undoLiveEditorChange",
        "resetLiveEditorContent",
    ):
        assert f"function {function_name}" in SCRIPT

    for element_id in (
        "liveEditorContent",
        "liveEditorEntities",
        "liveScoreBadge",
        "editorSuggestionFilter",
        "undoEditorBtn",
        "resetEditorBtn",
    ):
        assert f'id="{element_id}"' in HTML
