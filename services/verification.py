"""
Semantic Gap Verification Pass
================================
An optional extra AI call that reviews the already-computed semantic gaps
against the client's own site content and the topic context, before they
ever reach the report. The deterministic filters in services/semantic.py
(text-normalization, "covered by my own text" backstop, single-source
Organization/Person filter) already catch the common false-positive
patterns; this pass is a second, independent set of eyes that can catch
what pure string matching can't — e.g. a genuine synonym worded
completely differently, or a stray irrelevant entity that slipped through
because it never overlapped textually with the client's content.

Failure-safe by design: any error here just keeps the original gap list
unchanged rather than breaking the analysis.
"""

import json
import logging
import re

import config
from models.schemas import SemanticGap
from services.claude import call_claude_api
from services.semantic import _normalize

logger = logging.getLogger(config.LOGGER_NAME)

_VERIFY_SYSTEM_AR = (
    "أنت مدقق خبير في السيو الدلالي (Semantic SEO Auditor). مهمتك مراجعة "
    "قائمة \"فجوات دلالية\" تم استخراجها آلياً والتحقق من دقتها فقط — لا "
    "تخترع أي كيان جديد لم يكن موجوداً في القائمة المُعطاة."
)
_VERIFY_SYSTEM_EN = (
    "You are an expert Semantic SEO Auditor. Your task is to review an "
    "automatically generated list of \"semantic gaps\" and verify its "
    "accuracy only — never invent a new entity that wasn't already in the "
    "given list."
)


def _extract_json_array(raw: str) -> list:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"لم يمكن استخراج JSON من رد التحقق: {raw[:200]}")


def _build_verification_prompt(gaps: list[SemanticGap], my_text: str,
                                topic_context: str, language: str) -> str:
    # Only the fields the reviewer needs to JUDGE are sent. Counts, clusters,
    # and LSI terms are deterministic outputs of the local pass — sending them
    # invites the model to echo back altered values, which is exactly how the
    # cluster and synonym columns used to come back empty.
    payload = json.dumps(
        [{"name": gap.name, "type": gap.entity_type, "priority": gap.priority} for gap in gaps],
        ensure_ascii=False,
    )
    my_excerpt = my_text[:6000] if my_text else ""

    if language == "en":
        return f"""### Topic: {topic_context or "N/A"}

### Candidate semantic gaps (JSON, from an earlier automated pass):
{payload}

### Client's own site content (cross-check against this — anything
already substantively present here is NOT a real gap):
{my_excerpt or "(no client content was provided)"}

### Task:
Judge each candidate. Set "keep": false only when the entry:
- Is already substantively covered by the client's content above, even if worded differently there.
- Is not a genuine, topically relevant semantic entity (a stray brand/publisher name, a mistranslated term, or something nonsensical in context).
You may correct "type" or "priority" when clearly wrong. Never change "name" and never add entries.

Return pure JSON only — a list of {{"name": "...", "keep": true|false, "type": "...", "priority": "..."}}. No extra text."""

    return f"""### الموضوع: {topic_context or "غير محدد"}

### الفجوات الدلالية المرشحة (JSON من تحليل آلي سابق):
{payload}

### محتوى موقع العميل نفسه (قارن بهذا — أي كيان مغطّى فعلياً هنا ليس فجوة حقيقية):
{my_excerpt or "(لم يُقدَّم محتوى من العميل)"}

### المطلوب:
احكم على كل عنصر. اجعل "keep" تساوي false فقط إذا كان العنصر:
- مُغطّى فعلياً في محتوى العميل أعلاه، حتى لو بصياغة مختلفة هناك.
- ليس كياناً دلالياً حقيقياً متعلقاً بالموضوع (اسم علامة تجارية عرضي، خطأ ترجمة، أو كيان غير منطقي في السياق).
يمكنك تصحيح "type" أو "priority" إذا كانا خاطئين بوضوح. لا تغيّر "name" إطلاقاً ولا تضف عناصر جديدة.

أعِد JSON خالص فقط — قائمة من {{"name": "...", "keep": true|false, "type": "...", "priority": "..."}}. بدون أي نص إضافي."""


def verify_gaps(gaps: list[SemanticGap], my_text: str, topic_context: str,
                 language: str, model: str, api_key: str, api_url: str) -> list[SemanticGap]:
    """Best-effort second AI pass over the computed gaps.

    The reviewer may only DROP an entry or correct its type/priority. Every
    other field — competitor_count, total_mentions, cluster, LSI synonyms —
    is carried over from the deterministic pass untouched.

    An earlier version asked the model to return the whole gap objects and
    then rebuilt them from that response, so anything the model omitted came
    back as a schema default: every reviewed gap lost its semantic cluster
    ("غير مصنف") and its synonym list, and the competitor counts became
    whatever the model felt like echoing.

    Failure-safe: any error keeps the original list unchanged.
    """
    if not gaps:
        return gaps

    try:
        prompt = _build_verification_prompt(gaps, my_text, topic_context, language)
        sys_p = _VERIFY_SYSTEM_EN if language == "en" else _VERIFY_SYSTEM_AR
        raw = call_claude_api(prompt, sys_p, model, api_key, api_url, max_tokens=3000, task_type="verification")
        decisions = _extract_json_array(raw)
    except Exception as exc:
        logger.warning("Gap verification pass failed, keeping original gaps: %s", exc)
        return gaps

    by_name: dict[str, dict] = {}
    for item in decisions:
        if isinstance(item, dict) and item.get("name"):
            by_name[_normalize(str(item["name"]))] = item

    verified: list[SemanticGap] = []
    for gap in gaps:
        decision = by_name.get(_normalize(gap.name))
        # A gap the reviewer never mentioned is kept: silence is not a verdict.
        if decision is None:
            verified.append(gap)
            continue
        if decision.get("keep") is False:
            continue
        updated = gap.model_copy(deep=True)
        candidate_type = str(decision.get("type") or "").strip()
        if candidate_type:
            try:
                updated.entity_type = SemanticGap.normalize_type(candidate_type)
            except Exception:
                pass
        candidate_priority = str(decision.get("priority") or "").strip().lower()
        if candidate_priority in {"critical", "high", "medium"}:
            updated.priority = candidate_priority
        verified.append(updated)

    logger.info("Gap verification pass: %d -> %d gaps after review", len(gaps), len(verified))
    return verified
