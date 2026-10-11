import json
import re

import services.serp as serp


def _valid_outline_html() -> str:
    sections = ["<h1>Complete market architecture</h1><p>Meta description for the complete market architecture.</p>"]
    filler = " ".join(["detailed evidence based writing guidance"] * 14)
    for index in range(1, 11):
        sections.append(f"<h2>Topic {index}</h2><p>{filler} competitor {index}</p>")
        sections.append(f"<h3>Detail {index}</h3><p>{filler}</p>")
    sections.append("<h2>Frequently Asked Questions</h2><p>Useful questions from all sources.</p>")
    for index in range(1, 11):
        sections.append(f"<h3>Question {index}?</h3><p>{filler}</p>")
    return "<article>" + "".join(sections) + "</article>"


def test_fast_serp_pipeline_keeps_every_competitor(monkeypatch):
    monkeypatch.setattr(serp.config, "SERP_PROFILE_BATCH_SIZE", 5)
    monkeypatch.setattr(serp.config, "SERP_PROFILE_BATCH_WORKERS", 2)
    monkeypatch.setattr(serp.config, "SERP_MIN_TOPIC_COVERAGE", 0.0)
    monkeypatch.setattr(serp.config, "SERP_MIN_SOURCE_COVERAGE", 0.0)

    calls = {"profile": 0, "audit": 0, "outline": 0}

    def fake_call(prompt, system_prompt, model, api_key, api_url, max_tokens=4096):
        if "Competitor evidence:" in prompt or "أدلة المنافسين:" in prompt:
            calls["profile"] += 1
            match = re.search(r"(?:Competitor evidence:|أدلة المنافسين:)\s*(\[.*\])\s*$", prompt, re.S)
            payload = json.loads(match.group(1))
            return json.dumps({
                "profiles": [
                    {
                        "id": item["id"],
                        "topics": [f"Topic {int(item['id'][1:])}"],
                        "entities": [f"Entity {item['id']}"],
                        "questions": [f"Question about {item['id']}?"],
                        "proof_signals": [],
                        "commercial_elements": [],
                        "weaknesses": [],
                    }
                    for item in payload
                ]
            })
        if "Compact full-SERP evidence:" in prompt or "أدلة SERP الكاملة والمضغوطة:" in prompt:
            calls["audit"] += 1
            return "<article><h2>Search Intent Analysis</h2><p>Complete evidence.</p><h2>Blueprint Strategy</h2><p>Use every source.</p><h3>Mandatory Coverage Checklist</h3><ul><li>All sources</li></ul></article>"
        calls["outline"] += 1
        return _valid_outline_html()

    monkeypatch.setattr(serp, "call_nara_api", fake_call)

    competitors = []
    for index in range(1, 13):
        competitors.append({
            "url": f"https://example{index}.com/page",
            "title": f"Competitor {index}",
            "desc": f"Description {index}",
            "content": (
                f"[H1]: Topic {index}\n\n"
                f"[H2]: Section {index}\n\n"
                + (f"Topic {index} detailed paragraph with proof 2026 and pricing information. " * 30)
            ),
            "extraction_status": "full",
        })

    result = serp.analyze_serp_data(
        competitors,
        "Target topic",
        "Commercial Service Page",
        "en",
        "test-model",
        "test-key",
        "https://api.example.test",
    )

    assert len(result["profiles"]) == 12
    assert result["coverage"]["sources"] == 12
    assert result["coverage"]["sources_represented"] == 12
    assert result["coverage"]["ai_profile_batches"] == 3
    assert calls == {"profile": 3, "audit": 0, "outline": 1}
    assert result["coverage"]["topic_coverage"]["source_ratio"] == 1.0
    for index in range(1, 13):
        assert f"C{index:02d}" in result["html"]
        assert f"Competitor {index}" in result["html"]


def test_limited_source_is_kept_in_complete_audit():
    profile = serp._local_full_profile({
        "url": "https://blocked.example/page",
        "title": "Blocked competitor",
        "desc": "CSV description only",
        "content": "[Title]: Blocked competitor\n\n[Description]: CSV description only",
        "extraction_status": "limited",
        "extraction_error": "blocked",
    }, 1, "en")
    audit = serp._build_competitor_audit_html([profile], "en")
    assert "C01" in audit
    assert "Limited evidence" in audit
    assert "blocked.example" in audit


def test_shallow_seven_section_outline_is_repaired(monkeypatch):
    monkeypatch.setattr(serp.config, "SERP_PROFILE_BATCH_SIZE", 5)
    monkeypatch.setattr(serp.config, "SERP_PROFILE_BATCH_WORKERS", 1)
    monkeypatch.setattr(serp.config, "SERP_MIN_TOPIC_COVERAGE", 0.0)
    monkeypatch.setattr(serp.config, "SERP_MIN_SOURCE_COVERAGE", 0.0)
    outline_calls = 0

    def document(h2_count):
        filler = " ".join(["detailed useful evidence guidance"] * 18)
        parts = ["<article><h1>Target</h1><p>Meta description.</p>"]
        for index in range(h2_count):
            parts.append(f"<h2>Section {index}</h2><p>{filler}</p><h3>Detail {index}</h3><p>{filler}</p>")
        parts.append("<h2>Frequently Asked Questions</h2><p>FAQ.</p>")
        for index in range(10):
            parts.append(f"<h3>Question {index}?</h3><p>{filler}</p>")
        parts.append("</article>")
        return "".join(parts)

    def fake_call(prompt, system_prompt, model, api_key, api_url, max_tokens=4096):
        nonlocal outline_calls
        if "Competitor evidence:" in prompt:
            return json.dumps({"profiles": [{
                "id": "C01", "topics": ["Target coverage"], "entities": [],
                "questions": [], "proof_signals": [], "commercial_elements": [], "weaknesses": [],
            }]})
        if "Compact full-SERP evidence:" in prompt:
            return "<article><h2>Search Intent Analysis</h2><p>Intent.</p><h2>Blueprint Strategy</h2><p>Strategy.</p><h3>Mandatory Coverage Checklist</h3><ul><li>Target coverage</li></ul></article>"
        outline_calls += 1
        return document(7 if outline_calls == 1 else 10)

    monkeypatch.setattr(serp, "call_nara_api", fake_call)
    result = serp.analyze_serp_data([{
        "url": "https://example.com/page",
        "title": "Example",
        "desc": "Example description",
        "content": "[H1]: Target\n\n[H2]: Coverage\n\n" + ("Target coverage details 2026 pricing process. " * 80),
        "extraction_status": "full",
    }], "Target", "Commercial Service Page", "en", "model", "key", "api")

    assert outline_calls == 2
    assert result["coverage"]["outline_quality"]["counts"]["h2"] >= 10
