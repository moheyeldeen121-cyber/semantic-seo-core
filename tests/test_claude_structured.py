"""Structured (tool-use) extraction, tolerant parsing and env-driven sampling."""
import json

import config
import services.claude as claude


def test_entity_extraction_uses_forced_tool_call_and_env_temperature(monkeypatch):
    captured = {}
    tool_input = {"main_topic": "السيو التقني", "clusters": [
        {"cluster_name": "التحسين التقني", "pillar": "السيو التقني", "entities": [
            {"name": "السيو التقني", "source_term": "السيو التقني", "type": "Concept", "importance": "high", "salience": 0.9},
            {"name": "سرعة الموقع", "source_term": "سرعة الموقع", "type": "concept", "importance": "HIGH",
             "salience": 85, "lsi": "سرعة التحميل، سرعة الصفحة"},
            {"name": "x", "type": "Concept", "importance": "low"},                       # too short -> dropped
            {"name": "جملة طويلة جدا لا يمكن أن تكون كيانا دلاليا صحيحا أبدا", "type": "Concept"},  # sentence -> dropped
        ]}]}

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "tool_use", "name": "record_entities", "input": tool_input}],
                    "usage": {"input_tokens": 100, "output_tokens": 50}, "stop_reason": "tool_use"}

    def fake_post(url, headers, json, timeout):
        if not captured:
            captured.update(json)
        return Resp()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(config, "ACCURACY_MODE", "standard")
    analysis = claude.extract_entities_for_source(
        "مصدر", "[H1]: السيو التقني\n\nالسيو التقني يشمل سرعة الموقع وسرعة التحميل.", "ar",
        "default", "sk-ant-test")
    assert captured["tool_choice"] == {"type": "tool", "name": "record_entities"}
    assert captured["temperature"] == config.AI_TEMPERATURE
    assert captured["model"] == config.CLAUDE_EXTRACTION_MODEL
    names = [e.name for e in analysis.raw_entities]
    assert names == ["السيو التقني", "سرعة الموقع"]
    speed = analysis.raw_entities[1]
    assert speed.lsi == ["سرعة التحميل", "سرعة الصفحة"] and speed.grounded and speed.mentions >= 1
    assert analysis.main_topic == "السيو التقني"


def test_temperature_is_dropped_once_when_a_model_rejects_it(monkeypatch):
    payloads = []

    class Resp:
        def __init__(self, ok): self.ok = ok; self.status_code = 200 if ok else 400
        text = '{"error": {"message": "temperature is not supported for this model"}}'
        headers = {}
        def raise_for_status(self):
            if not self.ok:
                import requests
                raise requests.exceptions.HTTPError(response=self)
        def json(self): return {"content": [{"type": "text", "text": "ok"}], "usage": {}}

    def fake_post(url, headers, json, timeout):
        payloads.append(dict(json))
        return Resp(ok="temperature" not in json)

    monkeypatch.setattr("requests.post", fake_post)
    assert claude.call_claude_api("p", "s", "claude-sonnet-4-6", "sk-ant-x") == "ok"
    assert "temperature" in payloads[0] and "temperature" not in payloads[1]


def test_truncated_json_is_salvaged_up_to_the_last_complete_entity():
    raw = ('{"clusters": [{"cluster_name": "أ", "entities": [{"name": "كيان أول", "type": "Concept"},'
           ' {"name": "كيان ثان", "type": "Concept"}, {"name": "كيان مقطو')
    data = claude._parse_json_from_response(raw)
    assert [e["name"] for e in data["clusters"][0]["entities"]] == ["كيان أول", "كيان ثان"]
    analysis = claude._coerce_analysis(data, "x")
    assert len(analysis.raw_entities) == 2


def test_retired_env_model_falls_back(monkeypatch):
    monkeypatch.setenv("CLAUDE_TEST_MODEL", "claude-3-5-haiku-20241022")
    assert config._model_env("CLAUDE_TEST_MODEL", "claude-sonnet-4-6") == "claude-sonnet-4-6"
    monkeypatch.setenv("CLAUDE_TEST_MODEL", "claude-opus-5-5")
    assert config._model_env("CLAUDE_TEST_MODEL", "claude-sonnet-4-6") == "claude-opus-5-5"
