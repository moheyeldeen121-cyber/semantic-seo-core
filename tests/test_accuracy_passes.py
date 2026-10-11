"""Precise mode: completeness pass (recall) + independent review (precision),
multilingual prompts and accent-insensitive matching."""
import json

import config
import services.claude as claude
from services.semantic import _normalize

TEXT = ("[Title]: محامي قضايا عمالية في الرياض\n\n[H1]: محامي قضايا عمالية في الرياض\n\n"
        "محامي عمالي متخصص في الفصل التعسفي ومكافأة نهاية الخدمة وتأخير الرواتب. جودة عالية وخبرة واسعة. "
        "نتابع الدعوى عبر منصة ناجز، وأمر الأداء عند التأخير.")


def _entity(name, **extra):
    return {"name": name, "source_term": name, "type": extra.pop("type", "Concept"),
            "scope": extra.pop("scope", "topic"), "importance": extra.pop("importance", "medium"), **extra}


FIRST = {"main_topic": "محامي قضايا عمالية", "clusters": [{"cluster_name": "القضايا العمالية", "pillar": "محامي قضايا عمالية",
         "entities": [_entity("محامي قضايا عمالية", type="Service", importance="high"),
                      _entity("الفصل التعسفي"), _entity("جودة عالية"),
                      _entity("أمر الأداء", lsi=["سند لأمر", "أوامر الأداء"])]}]}
RECALL = {"main_topic": "", "clusters": [{"cluster_name": "القضايا العمالية", "pillar": "", "entities": [
          _entity("محامي قضايا عمالية", lsi=["محامي عمالي"]), _entity("مكافأة نهاية الخدمة"),
          _entity("منصة ناجز", type="Service")]}]}
REVIEW = {"changes": [{"i": 2, "keep": False}, {"i": 3, "remove_lsi": ["سند لأمر"]}]}


def _fake(calls):
    def fake(prompt, system_prompt, model, api_key, api_url="", max_tokens=0, task_type="", return_usage=False,
             json_schema=None, tool_name="record_entities", document="", **kw):
        calls.append({"prompt": prompt, "model": model, "tool": tool_name, "document": document})
        if tool_name == claude.REVIEW_TOOL_NAME:
            names = [line.split(". ", 1)[1].split(" |")[0] for line in prompt.splitlines() if line[:1].isdigit()]
            assert names[2] == "جودة عالية" and names[3] == "أمر الأداء"
            payload = REVIEW
        elif "مراجعة الاكتمال" in prompt or "Completeness check" in prompt:
            payload = RECALL
        else:
            payload = FIRST
        return json.dumps(payload, ensure_ascii=False), {"input_tokens": 10, "output_tokens": 5, "stop_reason": "tool_use"}
    return fake


def test_precise_mode_adds_missed_entities_and_removes_noise(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "ACCURACY_MODE", "precise")
    monkeypatch.setattr(claude, "call_claude_api", _fake(calls))
    out = claude.extract_entities_for_source("موقعي", TEXT, "ar", "default", "sk-ant-x")
    names = {entity.name for entity in out.raw_entities}
    assert {"مكافأة نهاية الخدمة", "منصة ناجز"} <= names          # recall pass found them
    assert "جودة عالية" not in names                                  # review dropped marketing noise
    lawyer = next(e for e in out.raw_entities if e.name == "محامي قضايا عمالية")
    assert "محامي عمالي" in lawyer.lsi                                 # short search form kept as a variant
    order = next(e for e in out.raw_entities if e.name == "أمر الأداء")
    assert "سند لأمر" not in order.lsi and "أوامر الأداء" in order.lsi
    assert [c["tool"] for c in calls] == ["record_entities", "record_entities", claude.REVIEW_TOOL_NAME]
    assert calls[0]["document"] and calls[0]["document"] == calls[1]["document"]   # cached document reused
    assert calls[2]["model"] == config.ENTITY_REVIEW_MODEL
    assert out.usage["input_tokens"] == 30


def test_review_guard_rails(monkeypatch):
    calls = []
    greedy = {"changes": [{"i": i, "keep": False} for i in range(4)]}
    monkeypatch.setattr(config, "ACCURACY_MODE", "precise")

    def fake(prompt, *a, tool_name="record_entities", **k):
        calls.append(tool_name)
        if tool_name == claude.REVIEW_TOOL_NAME:
            return json.dumps(greedy), {}
        if "مراجعة الاكتمال" in prompt:
            raise RuntimeError("temporary overload")                    # recall failure never sinks a source
        return json.dumps(FIRST, ensure_ascii=False), {}
    monkeypatch.setattr(claude, "call_claude_api", fake)
    out = claude.extract_entities_for_source("موقعي", TEXT, "ar", "default", "sk-ant-x")
    assert len(out.raw_entities) + len(out.signal_entities) == 4        # >35% drops ignored entirely


def test_standard_mode_is_a_single_call(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "ACCURACY_MODE", "standard")
    monkeypatch.setattr(claude, "call_claude_api", _fake(calls))
    claude.extract_entities_for_source("موقعي", TEXT, "ar", "default", "sk-ant-x")
    assert len(calls) == 1


def test_other_languages_keep_their_own_wording():
    document, _ = claude._extraction_parts("Source", "[H1]: Hôtel à Paris", "fr", "hôtel paris", "France")
    task = claude._extraction_task("fr", json_in_text=False)
    assert "The text is in French" in task and "translate it" not in document
    assert "Write every extracted entity" not in document
    assert "translate it" in claude._extraction_parts("S", "x", "en", "", "")[0]
    assert "arabic" not in claude.get_system_prompt("fr").lower()[:40]
    assert _normalize("Hôtel à Paris") == _normalize("hotel a paris")
    assert _normalize("Straße") == _normalize("strasse") and _normalize("İstanbul'da") != ""
    assert _normalize("الإستراتيجية") == _normalize("الاستراتيجيه")    # Arabic folding unchanged


def test_french_analysis_is_accepted_end_to_end(monkeypatch):
    import routes.analyze as analyze
    from services.extraction import ExtractionResult
    monkeypatch.setattr(analyze.config, "ANTHROPIC_API_KEY", "sk-ant-server")
    monkeypatch.setattr(config, "ACCURACY_MODE", "standard")
    page = "[H1]: Avocat en droit du travail à Paris\n\nUn avocat en droit du travail défend le salarié licencié."
    monkeypatch.setattr(analyze, "_url_result", lambda url, d, l: ExtractionResult(url=url, text=page, status="ok", chars=90))
    fr = {"main_topic": "avocat droit du travail", "clusters": [{"cluster_name": "Droit du travail", "pillar": "avocat en droit du travail",
          "entities": [_entity("avocat en droit du travail", importance="high"), _entity("salarié licencié")]}]}
    monkeypatch.setattr(claude, "call_claude_api", lambda *a, **k: (json.dumps(fr), {}))
    import services.verification as verification
    monkeypatch.setattr(verification, "call_claude_api", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")))
    from app import app
    body = app.test_client().post("/analyze", data={"model": "default", "language": "fr", "ui_language": "en",
                                                    "my_url": "https://a.example/x", "comp_urls": ["https://b.example/y"]}).get_data(as_text=True)
    assert "event: done" in body and "avocat en droit du travail" in body


def test_document_is_sent_as_a_cached_block(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"content": [{"type": "text", "text": "ok"}], "usage": {}}

    def fake_post(url, headers, json, timeout):
        sent.update(json)
        return Resp()
    monkeypatch.setattr("requests.post", fake_post)
    claude.call_claude_api("task", "system", "claude-sonnet-5", "sk-ant-x", document="the page text")
    blocks = sent["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "the page text", "cache_control": {"type": "ephemeral"}}
    assert blocks[1] == {"type": "text", "text": "task"}


def test_gemini_thinking_models_and_truncation_signal(monkeypatch):
    sent = []

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body, self.text = status, body, json.dumps(body)
        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.exceptions.HTTPError(response=self)
        def json(self): return self._body

    def fake_post(url, headers, json, timeout):
        sent.append(dict(json))
        if len(sent) == 1:
            return Resp(400, {"error": {"message": "Unknown field reasoning_effort"}})
        return Resp(200, {"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}], "usage": {}})
    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(config, "ENABLE_CACHE", False)
    _text, usage = claude.call_gemini_api("p", "s", "gemini-3.6-flash", "AIza-x", return_usage=True)
    assert "temperature" not in sent[0] and sent[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in sent[1]                              # retried without the parameter
    assert usage["stop_reason"] == "max_tokens"                           # truncation now triggers the bigger budget


def test_review_runs_on_gemini_with_json_output(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "ACCURACY_MODE", "precise")

    def fake(prompt, system_prompt, model, api_key, api_url="", tool_name="record_entities", json_schema=None, **k):
        calls.append((model, json_schema is None, "Return ONLY a JSON object" in prompt and '"changes"' in prompt))
        if '"changes"' in prompt:
            return json.dumps(REVIEW), {}
        if "مراجعة الاكتمال" in prompt:
            return json.dumps({"clusters": []}), {}
        return json.dumps(FIRST, ensure_ascii=False), {}
    monkeypatch.setattr(claude, "call_claude_api", fake)
    out = claude.extract_entities_for_source("موقعي", TEXT, "ar", "gemini-3.6-flash", "AIza-x")
    assert calls[-1] == ("gemini-3.6-flash", True, True)
    assert "جودة عالية" not in {e.name for e in out.raw_entities}
