"""Fast, exhaustive SERP synthesis with deterministic full-page coverage.

The expensive path no longer sends every page chunk to the model separately.
Instead, every character is scanned locally first, then compact evidence packs
for several competitors are enriched in a small number of bounded AI calls.
This keeps all competitors represented, avoids oversized final prompts, and
reduces rate-limit pressure without sacrificing full-source coverage.
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

import config
from services.claude import call_claude_api

call_nara_api = call_claude_api


def _invoke_ai(prompt: str, system_prompt: str, model: str, api_key: str, api_url: str = "", max_tokens: int = 4096) -> str:
    """Invoke AI caller, delegating to monkeypatched call_nara_api if set in tests."""
    if call_nara_api is not call_claude_api:
        return call_nara_api(prompt, system_prompt, model, api_key, api_url, max_tokens=max_tokens)
    return call_claude_api(prompt, system_prompt, model, api_key, api_url, max_tokens=max_tokens)

from services.rich_content import combine_documents, html_to_plain_text, normalize_rich_document

logger = logging.getLogger(config.LOGGER_NAME)

_PROFILE_SYSTEM_AR = (
    "أنت محلل SERP دقيق وسريع. ستستلم أدلة مضغوطة ناتجة عن مسح كامل لكل صفحة. "
    "حسّن التصنيف واكتشف الموضوعات والكيانات والأسئلة المهمة دون اختراع معلومات. "
    "أعد JSON صالحاً فقط، ولا تحذف أي منافس مرفق."
)
_PROFILE_SYSTEM_EN = (
    "You are a precise, fast SERP analyst. You receive compact evidence produced by a full scan of every page. "
    "Improve classification and identify important topics, entities, and questions without inventing facts. "
    "Return valid JSON only and never omit an included competitor."
)

_AUDIT_SYSTEM_AR = (
    "أنت استراتيجي SERP خبير. أخرج HTML دلالياً نظيفاً فقط، بلا Markdown وبلا ``` وبلا رموز # أو **. "
    "استخدم حصراً: h2,h3,h4,p,ul,ol,li,strong,table,thead,tbody,tr,th,td,blockquote."
)
_AUDIT_SYSTEM_EN = (
    "You are an expert SERP strategist. Output clean semantic HTML only: no Markdown, code fences, #, or **. "
    "Use only h2,h3,h4,p,ul,ol,li,strong,table,thead,tbody,tr,th,td,blockquote."
)

_OUTLINE_SYSTEM_AR = (
    "أنت مهندس محتوى وSEO دلالي. أخرج مسودة HTML احترافية جاهزة للنسخ إلى Word. "
    "ممنوع Markdown تماماً. استخدم h1,h2,h3,h4,p,ul,ol,li,strong,table,thead,tbody,tr,th,td,blockquote فقط."
)
_OUTLINE_SYSTEM_EN = (
    "You are a semantic SEO content architect. Output a polished HTML draft ready for Word. "
    "Markdown is forbidden. Use only h1,h2,h3,h4,p,ul,ol,li,strong,table,thead,tbody,tr,th,td,blockquote."
)

_AR_STOPWORDS = {
    "هذا", "هذه", "ذلك", "تلك", "الذي", "التي", "الذين", "على", "إلى", "الى", "من", "في", "عن",
    "مع", "كان", "كانت", "يكون", "يمكن", "كما", "كل", "بعد", "قبل", "عند", "بين", "حتى", "أو", "او",
    "ثم", "لأن", "لان", "هناك", "هنا", "هو", "هي", "هم", "نحن", "أنت", "انت", "ما", "لا", "لم", "لن",
    "قد", "تم", "أي", "اي", "أكثر", "اقل", "أقل", "خلال", "حول", "ضمن", "أجل", "بشكل", "بواسطة",
}
_EN_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "are", "was", "were", "you",
    "your", "our", "their", "have", "has", "had", "can", "could", "will", "would", "should", "into", "about",
    "after", "before", "between", "through", "than", "then", "also", "more", "most", "some", "any", "all",
    "not", "but", "its", "they", "them", "who", "what", "when", "where", "how", "why", "which", "each",
}
_COMMERCIAL_RE = re.compile(
    r"سعر|تكلفة|باقة|اشتراك|عرض|ضمان|تسليم|مدة|خطوات|احجز|اطلب|تواصل|"
    r"pricing|price|cost|package|plan|quote|guarantee|delivery|timeline|book|buy|contact|cta|deliverable",
    re.I,
)
_PROOF_RE = re.compile(
    r"\d|%|٪|دراسة|حالة|عميل|تجربة|شهادة|خبرة|اعتماد|نتيجة|إحصا|"
    r"case study|client|testimonial|experience|certif|result|statistic|research|study",
    re.I,
)
_QUESTION_RE = re.compile(r"[^\n.!?؟]{8,220}[?؟]")
_HEADING_RE = re.compile(r"\[(H[1-6])\]\s*:\s*([^\n]+)", re.I)
_WORD_RE = re.compile(r"[A-Za-z\u0600-\u06FF][A-Za-z0-9\u0600-\u06FF\-+]{2,}")


@dataclass
class CompetitorProfile:
    source_id: str
    url: str
    title: str = ""
    description: str = ""
    extraction_status: str = "full"
    extraction_error: str = ""
    content_chars: int = 0
    words_scanned: int = 0
    chunks_analyzed: int = 0
    headings: list[dict] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    proof_signals: list[str] = field(default_factory=list)
    commercial_elements: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    evidence_paragraphs: list[str] = field(default_factory=list, repr=False)
    ai_enriched: bool = False
    failed_chunks: int = 0

    def as_prompt_dict(self) -> dict:
        """Compact, bounded representation safe for final synthesis prompts."""
        return {
            "id": self.source_id,
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "extraction_status": self.extraction_status,
            "content_chars": self.content_chars,
            "words_scanned": self.words_scanned,
            "headings": self.headings[:80],
            "topics": self.topics[:60],
            "entities": self.entities[:100],
            "questions": self.questions[:35],
            "proof_signals": self.proof_signals[:20],
            "commercial_elements": self.commercial_elements[:20],
            "observed_weaknesses": self.weaknesses[:20],
        }

    def as_public_dict(self) -> dict:
        payload = self.as_prompt_dict()
        payload.update({
            "chunks_analyzed": self.chunks_analyzed,
            "ai_enriched": self.ai_enriched,
            "extraction_error": self.extraction_error,
        })
        return payload


def _normalize_key(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9\u0600-\u06FF]+", " ", str(value or "").casefold())
    return re.sub(r"\s+", " ", value).strip()


def _dedupe(values: Iterable[str], limit: int = 1000) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = re.sub(r"\s+", " ", str(value or "")).strip(" \t\n-•|:")
        key = _normalize_key(clean)
        if not clean or len(clean) < 2 or key in seen:
            continue
        seen.add(key)
        output.append(clean[:600])
        if len(output) >= limit:
            break
    return output


def _split_complete_text(text: str, max_chars: int) -> list[str]:
    """Split without dropping characters, preferring structural boundaries."""
    text = text or ""
    if not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start, length = 0, len(text)
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            search_from = start + max_chars // 2
            candidates = [
                text.rfind("\n\n", search_from, end),
                text.rfind("\n", search_from, end),
                text.rfind(". ", search_from, end),
                text.rfind("؟ ", search_from, end),
                text.rfind("? ", search_from, end),
                text.rfind(" ", search_from, end),
            ]
            boundary = max(candidates)
            if boundary > start:
                marker = text[boundary:boundary + 2]
                end = boundary + (2 if marker in {"\n\n", ". ", "؟ ", "? "} else 1)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks or [text]


def _paragraphs_from_content(content: str) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", content or "")
    blocks = re.split(r"\n\s*\n+|(?=\[H[1-6]\]\s*:)", normalized, flags=re.I)
    paragraphs: list[str] = []
    for block in blocks:
        clean = re.sub(r"\s+", " ", block).strip()
        if len(clean) < 12:
            continue
        if len(clean) <= 2000:
            paragraphs.append(clean)
            continue
        paragraphs.extend(_split_complete_text(clean, 2000))
    return paragraphs


def _topic_sentences(paragraphs: list[str]) -> list[str]:
    """Turn long paragraphs into concise, evidence-preserving topic candidates."""
    output: list[str] = []
    for paragraph in paragraphs:
        cleaned = re.sub(r"^\[H[1-6]\]\s*:\s*", "", paragraph, flags=re.I).strip()
        parts = re.split(r"(?<=[.!?؟])\s+|\s*[؛;]\s*", cleaned)
        for part in parts:
            part = re.sub(r"\s+", " ", part).strip(" -•|:")
            if 24 <= len(part) <= 240:
                output.append(part)
    return output


def _even_sample(values: list[str], limit: int) -> list[str]:
    values = _dedupe(values, max(len(values), limit))
    if len(values) <= limit:
        return values
    if limit <= 1:
        return values[:1]
    indexes = {round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)}
    return [values[index] for index in sorted(indexes)]


def _extract_keyphrases(words: list[str], language: str, limit: int = 220) -> list[str]:
    stop = _EN_STOPWORDS if language == "en" else _AR_STOPWORDS
    cleaned = [word.casefold() for word in words if word.casefold() not in stop and len(word) > 2]
    unigram = Counter(cleaned)
    bigram = Counter()
    trigram = Counter()
    for index in range(len(cleaned) - 1):
        if cleaned[index] in stop or cleaned[index + 1] in stop:
            continue
        bigram[f"{cleaned[index]} {cleaned[index + 1]}"] += 1
    for index in range(len(cleaned) - 2):
        phrase = cleaned[index:index + 3]
        if any(item in stop for item in phrase):
            continue
        trigram[" ".join(phrase)] += 1

    scored: list[tuple[float, str]] = []
    scored.extend((count * 1.0, phrase) for phrase, count in unigram.items() if count >= 2)
    scored.extend((count * 2.2, phrase) for phrase, count in bigram.items() if count >= 2)
    scored.extend((count * 3.2, phrase) for phrase, count in trigram.items() if count >= 2)
    scored.sort(key=lambda item: (-item[0], -len(item[1]), item[1]))
    return _dedupe((phrase for _score, phrase in scored), limit)


def _local_full_profile(competitor: dict, index: int, language: str) -> CompetitorProfile:
    """Scan the entire extracted page locally before any AI call.

    This pass touches every word and provides a deterministic baseline, so no
    competitor disappears when the model context gets long or an enrichment
    request is rate-limited.
    """
    content = str(competitor.get("content") or "")
    source_id = f"C{index:02d}"
    extraction_status = str(competitor.get("extraction_status") or ("full" if content.strip() else "limited"))
    extraction_error = str(competitor.get("extraction_error") or "")[:500]
    profile = CompetitorProfile(
        source_id=source_id,
        url=str(competitor.get("url") or ""),
        title=str(competitor.get("title") or "")[:300],
        description=str(competitor.get("desc") or "")[:600],
        extraction_status=extraction_status,
        extraction_error=extraction_error,
        content_chars=len(content),
        chunks_analyzed=len(_split_complete_text(content, config.SERP_CHUNK_CHAR_LIMIT)),
    )

    headings = []
    for level, text in _HEADING_RE.findall(content):
        level_num = min(int(level[1]), 4)
        clean = re.sub(r"\s+", " ", text).strip()
        if clean:
            headings.append({"level": f"H{level_num}", "text": clean[:300]})
    if not headings and profile.title:
        headings.append({"level": "H1", "text": profile.title})
    profile.headings = headings

    paragraphs = _paragraphs_from_content(content)
    words = _WORD_RE.findall(content)
    profile.words_scanned = len(words)
    profile.evidence_paragraphs = paragraphs

    questions = list(_QUESTION_RE.findall(content))
    proof = [paragraph for paragraph in paragraphs if _PROOF_RE.search(paragraph)]
    commercial = [paragraph for paragraph in paragraphs if _COMMERCIAL_RE.search(paragraph)]

    heading_topics = [item["text"] for item in headings]
    paragraph_topics = [
        sentence for sentence in _topic_sentences(paragraphs)
        if not sentence.startswith(("[Title]", "[العنوان]", "[Description]", "[الوصف]"))
    ]
    # Even sampling prevents long pages from being represented only by their
    # opening sections; every part of the page contributes evidence.
    sampled_topics = _even_sample(paragraph_topics, config.SERP_LOCAL_PARAGRAPH_SAMPLES)
    profile.topics = _dedupe([*heading_topics, *sampled_topics], 260)
    profile.entities = _extract_keyphrases(words, language, 260)
    profile.questions = _dedupe(questions, 120)
    profile.proof_signals = _dedupe(proof, 80)
    profile.commercial_elements = _dedupe(commercial, 80)

    weaknesses = []
    if extraction_status != "full":
        weaknesses.append(
            "Limited extraction: only CSV metadata or partial page evidence was available."
            if language == "en" else
            "استخراج محدود: المتاح كان بيانات CSV أو جزءاً من الصفحة فقط."
        )
    if len(headings) < 3:
        weaknesses.append("Very limited visible heading hierarchy." if language == "en" else "التسلسل الظاهر للعناوين محدود جداً.")
    if not proof:
        weaknesses.append("No clear proof or evidence signals detected." if language == "en" else "لم تُرصد إشارات إثبات واضحة.")
    if not commercial:
        weaknesses.append("No clear conversion/pricing/process elements detected." if language == "en" else "لم تُرصد عناصر تحويل أو أسعار أو خطوات واضحة.")
    profile.weaknesses = weaknesses
    return _finalize_profile(profile)


def _finalize_profile(profile: CompetitorProfile) -> CompetitorProfile:
    heading_seen: set[tuple[str, str]] = set()
    headings: list[dict] = []
    for item in profile.headings:
        level = str(item.get("level") or "H2").upper()
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if level not in {"H1", "H2", "H3", "H4"} or not text:
            continue
        key = (level, _normalize_key(text))
        if key in heading_seen:
            continue
        heading_seen.add(key)
        headings.append({"level": level, "text": text[:300]})
    profile.headings = headings[:180]
    profile.topics = _dedupe(profile.topics, 260)
    profile.entities = _dedupe(profile.entities, 360)
    profile.questions = _dedupe(profile.questions, 140)
    profile.proof_signals = _dedupe(profile.proof_signals, 100)
    profile.commercial_elements = _dedupe(profile.commercial_elements, 100)
    profile.weaknesses = _dedupe(profile.weaknesses, 80)
    return profile


def _profile_evidence_pack(profile: CompetitorProfile) -> dict:
    paragraphs = _even_sample(profile.evidence_paragraphs, config.SERP_AI_PARAGRAPH_SAMPLES)
    # Prioritise factual and commercial paragraphs, then add evenly distributed
    # context so the pack still represents the full page journey.
    priority = _dedupe([*profile.proof_signals[:12], *profile.commercial_elements[:12]], 20)
    candidates = _dedupe([*priority, *paragraphs], config.SERP_AI_PARAGRAPH_SAMPLES + 20)
    evidence: list[str] = []
    budget = config.SERP_PROFILE_EVIDENCE_CHAR_BUDGET
    used = 0
    for value in candidates:
        compact = re.sub(r"\s+", " ", value).strip()[:520]
        if not compact:
            continue
        if evidence and used + len(compact) > budget:
            break
        evidence.append(compact)
        used += len(compact)
    return {
        "id": profile.source_id,
        "url": profile.url,
        "title": profile.title,
        "description": profile.description,
        "extraction_status": profile.extraction_status,
        "content_chars": profile.content_chars,
        "words_scanned": profile.words_scanned,
        "headings": profile.headings[:60],
        "local_keyphrases": profile.entities[:75],
        "detected_questions": profile.questions[:25],
        "evidence_paragraphs": evidence,
    }


def _profile_batch_prompt(profiles: list[CompetitorProfile], topic: str, language: str) -> str:
    data = json.dumps([_profile_evidence_pack(profile) for profile in profiles], ensure_ascii=False)
    if language == "en":
        return f"""Enrich the competitor profiles below for the target topic: {topic or 'the supplied topic'}.
The evidence was produced by a deterministic full-page scan, including evenly distributed paragraphs from beginning to end.

Return exactly one JSON object:
{{"profiles":[{{"id":"C01","topics":[],"entities":[],"questions":[],"proof_signals":[],"commercial_elements":[],"weaknesses":[]}}]}}

Rules:
- Return one item for EVERY supplied id, in the same order. Never omit a competitor.
- Use only evidence in that competitor object. Do not merge competitors.
- Topics must be specific coverage points, not generic marketing phrases.
- Keep useful local findings and add only evidence-supported refinements.
- Maximum per competitor: 30 topics, 45 entities, 20 questions, 15 proof signals, 15 commercial elements, 12 weaknesses.
- JSON only; no Markdown or commentary.

Competitor evidence:
{data}
"""
    return f"""حسّن ملفات المنافسين التالية للموضوع المستهدف: {topic or 'الموضوع المرفق'}.
الأدلة ناتجة عن مسح برمجي كامل للصفحة، مع فقرات موزعة من البداية حتى النهاية.

أعد كائن JSON واحداً بهذا الشكل:
{{"profiles":[{{"id":"C01","topics":[],"entities":[],"questions":[],"proof_signals":[],"commercial_elements":[],"weaknesses":[]}}]}}

قواعد:
- أعد عنصراً لكل id مرفق وبنفس الترتيب، ولا تحذف أي منافس.
- استخدم أدلة المنافس نفسه فقط ولا تخلط المنافسين.
- الموضوعات نقاط تغطية محددة وليست عبارات تسويقية عامة.
- حافظ على النتائج المحلية المفيدة وأضف تحسينات مدعومة بالأدلة فقط.
- الحد الأقصى لكل منافس: 30 موضوعاً، 45 كياناً، 20 سؤالاً، 15 إشارة إثبات، 15 عنصراً تجارياً، 12 فجوة.
- JSON فقط دون Markdown أو شرح.

أدلة المنافسين:
{data}
"""


def _parse_json_object(raw: str) -> dict:
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("Could not parse the SERP profile response as JSON")


def _merge_ai_profile(profile: CompetitorProfile, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    for attr, key, limit in (
        ("topics", "topics", 260),
        ("entities", "entities", 360),
        ("questions", "questions", 140),
        ("proof_signals", "proof_signals", 100),
        ("commercial_elements", "commercial_elements", 100),
        ("weaknesses", "weaknesses", 80),
    ):
        values = payload.get(key) or []
        if isinstance(values, list):
            setattr(profile, attr, _dedupe([*getattr(profile, attr), *values], limit))
    profile.ai_enriched = True


def _enrich_profile_batch(profiles: list[CompetitorProfile], topic: str, language: str,
                          model: str, api_key: str, api_url: str,
                          cancel_check: Callable[[], bool] | None = None) -> None:
    if cancel_check and cancel_check():
        raise RuntimeError("تم إلغاء التحليل بواسطة المستخدم.")
    raw = _invoke_ai(
        _profile_batch_prompt(profiles, topic, language),
        _PROFILE_SYSTEM_EN if language == "en" else _PROFILE_SYSTEM_AR,
        model, api_key, api_url,
        max_tokens=config.SERP_PROFILE_MAX_TOKENS,
    )
    data = _parse_json_object(raw)
    by_id = {
        str(item.get("id") or ""): item
        for item in (data.get("profiles") or [])
        if isinstance(item, dict)
    }
    for profile in profiles:
        item = by_id.get(profile.source_id)
        if item is not None:
            _merge_ai_profile(profile, item)
        else:
            profile.failed_chunks += 1
            logger.warning("SERP batch response omitted %s; deterministic profile retained", profile.source_id)


def _batched(values: list[CompetitorProfile], size: int) -> list[list[CompetitorProfile]]:
    """Pack several competitors per call without exceeding a safe prompt budget."""
    batches: list[list[CompetitorProfile]] = []
    current: list[CompetitorProfile] = []
    current_chars = 0
    max_chars = config.SERP_PROFILE_BATCH_MAX_CHARS
    for profile in values:
        estimated = len(json.dumps(_profile_evidence_pack(profile), ensure_ascii=False)) + 300
        if current and (len(current) >= size or current_chars + estimated > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(profile)
        current_chars += estimated
    if current:
        batches.append(current)
    return batches


def _source_requirements(profiles: list[CompetitorProfile]) -> list[dict]:
    """Choose one material, preferably distinctive coverage point per source."""
    candidates_by_source: dict[str, list[str]] = {}
    frequency: Counter[str] = Counter()
    display: dict[str, str] = {}
    for profile in profiles:
        values = _dedupe(
            [item["text"] for item in profile.headings if item.get("level") in {"H2", "H3"}]
            + profile.topics,
            40,
        )
        if not values:
            values = _dedupe([profile.description, profile.title], 10)
        candidates_by_source[profile.source_id] = values
        for value in values:
            key = _normalize_key(value)
            if not key:
                continue
            frequency[key] += 1
            display.setdefault(key, value)

    requirements: list[dict] = []
    for profile in profiles:
        best_value = ""
        best_score = float("-inf")
        for position, value in enumerate(candidates_by_source.get(profile.source_id, [])):
            key = _normalize_key(value)
            tokens = [token for token in key.split() if len(token) > 2]
            if not tokens:
                continue
            distinctiveness = 90 / max(frequency.get(key, 1), 1)
            useful_length = min(len(tokens), 14) * 2
            heading_bonus = 12 if position < len(profile.headings) else 0
            overly_long_penalty = max(0, len(value) - 220) / 8
            score = distinctiveness + useful_length + heading_bonus - overly_long_penalty - position * 0.15
            if score > best_score:
                best_score = score
                best_value = value
        if not best_value:
            best_value = profile.title or profile.description or profile.url or profile.source_id
        requirements.append({
            "id": profile.source_id,
            "title": profile.title,
            "text": re.sub(r"\s+", " ", best_value).strip()[:320],
        })
    return requirements


def _aggregate_items(profiles: list[CompetitorProfile], attr: str, limit: int) -> list[dict]:
    sources: dict[str, set[str]] = defaultdict(set)
    display: dict[str, str] = {}
    for profile in profiles:
        for value in getattr(profile, attr):
            key = _normalize_key(value)
            if len(key) < 3:
                continue
            display.setdefault(key, str(value)[:300])
            sources[key].add(profile.source_id)
    ranked = sorted(
        sources,
        key=lambda key: (-len(sources[key]), -len(key.split()), -len(key), display[key].casefold()),
    )
    return [
        {"text": display[key], "source_count": len(sources[key]), "sources": sorted(sources[key])}
        for key in ranked[:limit]
    ]


def _global_evidence(profiles: list[CompetitorProfile]) -> dict:
    summaries = []
    for profile in profiles:
        coverage = _dedupe(
            [item["text"] for item in profile.headings if item.get("level") in {"H1", "H2", "H3"}]
            + profile.topics,
            config.SERP_PER_SOURCE_COVERAGE_ITEMS,
        )
        summaries.append({
            "id": profile.source_id,
            "host": urlsplit(profile.url).netloc or profile.url,
            "title": profile.title,
            "status": profile.extraction_status,
            "characters": profile.content_chars,
            "coverage": coverage,
            "questions": profile.questions[:8],
            "proof": profile.proof_signals[:6],
            "commercial": profile.commercial_elements[:6],
            "gaps": profile.weaknesses[:6],
        })
    return {
        "competitor_count": len(profiles),
        "competitors": summaries,
        "shared_topics": _aggregate_items(profiles, "topics", config.SERP_GLOBAL_TOPIC_LIMIT),
        "entities": _aggregate_items(profiles, "entities", config.SERP_GLOBAL_ENTITY_LIMIT),
        "questions": _aggregate_items(profiles, "questions", config.SERP_GLOBAL_QUESTION_LIMIT),
        "proof_signals": _aggregate_items(profiles, "proof_signals", 80),
        "commercial_elements": _aggregate_items(profiles, "commercial_elements", 80),
        "source_requirements": _source_requirements(profiles),
    }


def _audit_prompt(evidence: dict, topic: str, target_type: str, language: str) -> str:
    data = json.dumps(evidence, ensure_ascii=False)
    if language == "en":
        return f"""Create the strategic analysis for: {topic or 'the supplied topic'}.
Target content type: {target_type or 'Commercial Service Page'}.
The evidence contains ALL {evidence['competitor_count']} competitors. The competitor-by-competitor table is generated separately, so do not create or abbreviate that table.

Required HTML:
<h2>Search Intent Analysis</h2>
- Explain dominant, primary, secondary intent and decision criteria with h3 plus p/ul.
<h2>Cross-Competitor Findings</h2>
- Explain shared coverage, meaningful differentiators, evidence patterns, commercial patterns, and missing opportunities.
<h2>Blueprint Strategy</h2>
- Explain the logical reader journey, E-E-A-T, differentiation, conversion path, and content depth.
- End with <h3>Mandatory Coverage Checklist</h3> and a comprehensive ul built from all source coverage.

Use all competitors, including low-frequency source-specific items where materially relevant. HTML only; no Markdown.

Compact full-SERP evidence:
{data}
"""
    return f"""أنشئ التحليل الاستراتيجي للموضوع: {topic or 'الموضوع المرفق'}.
نوع المحتوى المستهدف: {target_type or 'صفحة خدمة'}.
الأدلة تشمل جميع المنافسين وعددهم {evidence['competitor_count']}. جدول المنافسين التفصيلي يُبنى برمجياً بشكل مستقل، لذلك لا تنشئه ولا تختصره.

HTML المطلوب:
<h2>تحليل نية البحث</h2>
- اشرح النية الغالبة والأساسية والثانوية ومعايير القرار باستخدام h3 ثم p/ul.
<h2>النتائج المشتركة بين المنافسين</h2>
- اشرح التغطية المشتركة، عناصر التميز، أنماط الإثبات، الأنماط التجارية، والفرص الناقصة.
<h2>استراتيجية الهيكلة المقترحة</h2>
- اشرح رحلة القارئ المنطقية وإشارات E-E-A-T والتميّز ومسار التحويل وعمق المحتوى.
- اختم بـ <h3>قائمة التغطية الإلزامية</h3> وقائمة ul شاملة مبنية على تغطية جميع المصادر.

استخدم جميع المنافسين، بما فيها العناصر الخاصة بمصدر واحد متى كانت مهمة. HTML فقط دون Markdown.

أدلة SERP الكاملة والمضغوطة:
{data}
"""


def _outline_prompt(evidence: dict, audit_text: str, topic: str, target_type: str, language: str) -> str:
    data = json.dumps(evidence, ensure_ascii=False)
    count = evidence["competitor_count"]
    if language == "en":
        return f"""Build the final, logically ordered, ready-to-write content architecture for: {topic or 'the supplied topic'}.
Target type: {target_type or 'Commercial Service Page'}.

The evidence represents all {count} competitors. Every competitor has a stable id and a source-specific coverage list. The architecture must synthesize the entire market, not only the first or most detailed sources.

Strict requirements:
1. Exactly one h1, followed by a p containing a strong meta description.
2. Logical reader journey: orientation/problem → decision criteria/core explanation → detailed solution coverage → process/examples/comparisons → proof/trust → objections/FAQ → next action.
3. Normally 10–18 meaningful h2 sections with rich h3/h4 detail and no level jumps.
4. Every heading must have useful p, ul, ol, table, sample copy, evidence need, or writer instruction beneath it.
5. Cover every high/shared topic and at least one material source-specific coverage item from every competitor id C01 through C{count:02d}. Merge duplicates under the best logical parent rather than creating one section per competitor.
6. Include entities, questions, proof, commercial elements, and gaps from the evidence. Do not drop low-frequency but strategically useful topics.
7. Include 10–15 useful FAQs as h3 with concise p answers.
8. For service pages include pricing logic, packages, deliverables, timeline, workflow, comparison, case-study framework, qualification, objections, guarantees/limits, and contextual CTAs. For articles include definitions, mechanisms, steps, examples, comparisons, mistakes, troubleshooting, measurement, and next steps.
9. After the architecture, include concise h2 sections for search intent, cross-competitor findings, blueprint strategy, and a mandatory coverage checklist. Derive them directly from the same full evidence; do not create a second competitor table.
10. Semantic HTML only. No Markdown, #, **, code fences, or literal H1/H2 labels.

Additional deterministic checklist:
{audit_text or 'Use the source_requirements list as a hard checklist: every id must materially affect the architecture.'}

Compact evidence from every competitor:
{data}
"""
    return f"""ابنِ الهيكلة النهائية المنطقية والجاهزة للكتابة للموضوع: {topic or 'الموضوع المرفق'}.
نوع المحتوى: {target_type or 'صفحة خدمة'}.

الأدلة تمثل جميع المنافسين وعددهم {count}. لكل منافس id ثابت وقائمة تغطية خاصة به. يجب أن تجمع الهيكلة السوق كله، لا أول المصادر أو أطولها فقط.

متطلبات صارمة:
1. h1 واحد بالضبط، وبعده p يحتوي على وصف تعريفي قوي.
2. رحلة قارئ منطقية: تمهيد/مشكلة → معايير القرار/الشرح الأساسي → التغطية التفصيلية → العملية والأمثلة والمقارنات → الثقة والإثبات → الاعتراضات والأسئلة → الخطوة التالية.
3. غالباً 10–18 عنوان h2 جوهرياً مع h3/h4 غنية بلا قفزات.
4. تحت كل عنوان محتوى مفيد من p أو ul أو ol أو جدول أو نص مقترح أو دليل مطلوب أو تعليمات كتابة.
5. غطِّ كل موضوع مشترك/مرتفع الأهمية، وموضوعاً خاصاً مهماً واحداً على الأقل من كل منافس من C01 حتى C{count:02d}. ادمج التكرار تحت أفضل قسم منطقي، ولا تنشئ قسماً لكل منافس.
6. ضمّن الكيانات والأسئلة وإشارات الإثبات والعناصر التجارية والفجوات الموجودة في الأدلة، ولا تسقط الموضوعات قليلة التكرار إذا كانت استراتيجية.
7. أضف 10–15 سؤالاً شائعاً مفيداً بصيغة h3 وتحت كل سؤال p مختصرة.
8. لصفحة الخدمة ضمّن منطق الأسعار والباقات والتسليمات والمدة والخطوات والمقارنة ودراسات الحالة والتأهيل والاعتراضات والضمانات/الحدود وCTA سياقية. للمقال ضمّن التعريفات والآلية والخطوات والأمثلة والمقارنات والأخطاء وحل المشكلات والقياس والخطوات التالية.
9. بعد الهيكلة أضف أقسام h2 مختصرة لتحليل نية البحث والنتائج المشتركة واستراتيجية المحتوى وقائمة التغطية الإلزامية، واستخرجها مباشرة من الأدلة نفسها دون إنشاء جدول منافسين ثانٍ.
10. HTML دلالي فقط دون Markdown أو # أو ** أو code fences أو كتابة H1/H2 كنص.

قائمة تحقق برمجية إضافية:
{audit_text or 'اعتبر source_requirements قائمة إلزامية: يجب أن يؤثر كل id فعلياً في الهيكلة.'}

أدلة مضغوطة من كل منافس:
{data}
"""


def _build_competitor_audit_html(profiles: list[CompetitorProfile], language: str) -> str:
    """Build one guaranteed row per competitor without relying on model output."""
    if language == "en":
        heading = "Competitor Content Audit — Complete Source List"
        headers = ("Competitor", "Extraction", "Coverage", "Strengths", "Depth / Conversion Gaps")
    else:
        heading = "مراجعة محتوى المنافسين — القائمة الكاملة"
        headers = ("المنافس", "حالة الاستخراج", "التغطية", "نقاط القوة", "فجوات العمق / التحويل")

    rows = []
    for profile in profiles:
        host = urlsplit(profile.url).netloc or profile.url or profile.source_id
        label = f"{profile.source_id} — {profile.title or host}"
        status = (
            ("Full page" if language == "en" else "صفحة كاملة")
            if profile.extraction_status == "full" else
            ("Limited evidence" if language == "en" else "أدلة محدودة")
        )
        coverage = _dedupe(
            [item["text"] for item in profile.headings if item.get("level") in {"H1", "H2", "H3"}]
            + profile.topics,
            8,
        )
        strengths = []
        if len(profile.headings) >= 6:
            strengths.append("Clear content hierarchy" if language == "en" else "تسلسل محتوى واضح")
        if profile.proof_signals:
            strengths.append("Uses proof/evidence signals" if language == "en" else "يستخدم إشارات إثبات")
        if profile.commercial_elements:
            strengths.append("Has commercial/conversion elements" if language == "en" else "يحتوي على عناصر تجارية/تحويل")
        if not strengths:
            strengths.append("Provides baseline topical coverage" if language == "en" else "يوفر تغطية أساسية للموضوع")
        gaps = profile.weaknesses[:5] or [
            "No major deterministic gap detected; validate differentiation manually."
            if language == "en" else
            "لم تُرصد فجوة آلية كبيرة؛ يلزم تأكيد التميز تحريرياً."
        ]

        def list_html(items: list[str]) -> str:
            return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"

        source_link = html.escape(profile.url)
        competitor_cell = f'<strong>{html.escape(label)}</strong><p>{source_link}</p>'
        rows.append(
            "<tr>"
            f"<td>{competitor_cell}</td>"
            f"<td>{html.escape(status)}<p>{profile.content_chars:,} chars · {profile.words_scanned:,} words</p></td>"
            f"<td>{list_html(coverage or [profile.description or profile.title or host])}</td>"
            f"<td>{list_html(strengths)}</td>"
            f"<td>{list_html(gaps)}</td>"
            "</tr>"
        )

    header_html = "".join(f"<th>{html.escape(value)}</th>" for value in headers)
    return (
        f"<article><h2>{html.escape(heading)}</h2>"
        f"<p><strong>{len(profiles)}</strong> "
        + ("competitors are listed below with no model-side truncation.</p>" if language == "en" else "منافساً مدرجون أدناه دون اختصار من الموديل.</p>")
        + f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(rows)}</tbody></table></article>"
    )


def _outline_quality(document_html: str) -> dict:
    """Measure whether the generated architecture is complete and usable."""
    soup = BeautifulSoup(document_html or "", "html.parser")
    headings = soup.find_all(["h1", "h2", "h3", "h4"])
    counts = {level: len(soup.find_all(level)) for level in ("h1", "h2", "h3", "h4")}
    orphan_headings: list[str] = []
    previous_level = 0
    heading_jumps = 0
    for heading in headings:
        level = int(heading.name[1])
        if previous_level and level > previous_level + 1:
            heading_jumps += 1
        previous_level = level
        has_support = False
        sibling = heading.next_sibling
        while sibling is not None:
            if getattr(sibling, "name", None) in {"h1", "h2", "h3", "h4"}:
                break
            if getattr(sibling, "name", None) in {"p", "ul", "ol", "table", "blockquote"} and sibling.get_text(" ", strip=True):
                has_support = True
                break
            sibling = sibling.next_sibling
        if not has_support:
            orphan_headings.append(heading.get_text(" ", strip=True)[:160])

    faq_count = 0
    in_faq = False
    for heading in headings:
        text = heading.get_text(" ", strip=True).casefold()
        if heading.name == "h2":
            in_faq = any(term in text for term in ("faq", "frequently asked", "الأسئلة الشائعة", "اسئلة شائعة", "أسئلة شائعة"))
            continue
        if in_faq and heading.name in {"h3", "h4"}:
            faq_count += 1

    words = len(_WORD_RE.findall(soup.get_text(" ", strip=True)))
    issues: list[str] = []
    if counts["h1"] != 1:
        issues.append(f"expected exactly one h1, found {counts['h1']}")
    if counts["h2"] < 8:
        issues.append(f"outline is too shallow: {counts['h2']} h2 sections")
    if counts["h3"] < 8:
        issues.append(f"outline lacks detailed subsections: {counts['h3']} h3 sections")
    if faq_count < 8:
        issues.append(f"FAQ coverage is incomplete: {faq_count} questions")
    if orphan_headings:
        issues.append(f"{len(orphan_headings)} headings have no supporting content")
    if heading_jumps:
        issues.append(f"{heading_jumps} heading-level jumps")
    if words < 650:
        issues.append(f"document is too brief: {words} words")
    score = (
        min(counts["h2"], 12) * 3
        + min(counts["h3"], 24) * 2
        + min(faq_count, 12) * 2
        + min(words // 100, 18)
        - len(orphan_headings) * 4
        - heading_jumps * 3
        - abs(counts["h1"] - 1) * 8
    )
    return {
        "valid": not issues,
        "issues": issues,
        "counts": counts,
        "faq_count": faq_count,
        "orphan_headings": orphan_headings[:20],
        "heading_jumps": heading_jumps,
        "words": words,
        "score": score,
    }


def _coverage_phrase_present(normalized_document: str, phrase: str) -> bool:
    tokens = [token for token in _normalize_key(phrase).split() if len(token) > 2]
    if not tokens:
        return True
    required = max(1, math.ceil(len(tokens) * 0.60))
    return sum(1 for token in tokens if token in normalized_document) >= required


def _topic_coverage_quality(document_html: str, evidence: dict) -> dict:
    text = _normalize_key(BeautifulSoup(document_html or "", "html.parser").get_text(" ", strip=True))
    candidates = evidence.get("shared_topics", [])[:80]
    matched = []
    missing = []
    for item in candidates:
        phrase = str(item.get("text", ""))
        (matched if _coverage_phrase_present(text, phrase) else missing).append(phrase)
    ratio = len(matched) / max(len(matched) + len(missing), 1)

    requirements = evidence.get("source_requirements") or []
    if not requirements:
        requirements = [
            {"id": source.get("id"), "text": (source.get("coverage") or [""])[0]}
            for source in evidence.get("competitors", [])
        ]
    represented_sources = []
    missing_requirements = []
    for requirement in requirements:
        phrase = str(requirement.get("text") or "")
        if _coverage_phrase_present(text, phrase):
            represented_sources.append(requirement.get("id"))
        else:
            missing_requirements.append(requirement)
    source_ratio = len(represented_sources) / max(len(requirements), 1)
    return {
        "topic_ratio": round(ratio, 3),
        "source_ratio": round(source_ratio, 3),
        "matched_topics": len(matched),
        "checked_topics": len(matched) + len(missing),
        "missing_topics": missing[:30],
        "represented_sources": represented_sources,
        "missing_sources": [item.get("id") for item in missing_requirements][:50],
        "missing_source_requirements": missing_requirements[:50],
    }


def _inject_missing_source_coverage(document_html: str, evidence: dict, coverage: dict,
                                    language: str) -> str:
    """Guarantee every source changes the architecture without stacking random headings.

    Missing source-specific items are inserted as writer requirements under the
    most semantically related existing H2. This preserves the reader journey
    while making source coverage deterministic rather than model-dependent.
    """
    missing = list(coverage.get("missing_source_requirements") or [])
    # Shared high-value topics are handled by the same deterministic merge, so
    # coverage gaps do not require another expensive model round trip.
    for index, topic in enumerate((coverage.get("missing_topics") or [])[:20], start=1):
        missing.append({"id": f"T{index:02d}", "title": "", "text": topic})
    if not missing:
        return document_html
    soup = BeautifulSoup(document_html or "", "html.parser")
    h2_nodes = []
    for heading in soup.find_all("h2"):
        heading_text = _normalize_key(heading.get_text(" ", strip=True))
        if any(term in heading_text for term in ("faq", "frequently asked", "الأسئلة الشائعة", "اسئلة شائعة")):
            continue
        section_parts = [heading.get_text(" ", strip=True)]
        sibling = heading.next_sibling
        while sibling is not None:
            if getattr(sibling, "name", None) == "h2":
                break
            if hasattr(sibling, "get_text"):
                section_parts.append(sibling.get_text(" ", strip=True))
            sibling = sibling.next_sibling
        h2_nodes.append((heading, _normalize_key(" ".join(section_parts))))
    if not h2_nodes:
        article = soup.find("article") or soup
        heading = soup.new_tag("h2")
        heading.string = "متطلبات التغطية المستخلصة" if language != "en" else "Evidence-derived coverage requirements"
        article.append(heading)
        h2_nodes = [(heading, _normalize_key(heading.get_text(" ", strip=True)))]

    grouped: dict[int, list[dict]] = defaultdict(list)
    for offset, requirement in enumerate(missing):
        phrase = str(requirement.get("text") or "").strip()
        phrase_tokens = set(token for token in _normalize_key(phrase).split() if len(token) > 2)
        scores = []
        for index, (_heading, section_text) in enumerate(h2_nodes):
            section_tokens = set(section_text.split())
            overlap = len(phrase_tokens & section_tokens)
            scores.append((overlap, -index, index))
        best_overlap, _neg_index, best_index = max(scores) if scores else (0, 0, offset % len(h2_nodes))
        if best_overlap == 0:
            best_index = offset % len(h2_nodes)
        grouped[best_index].append(requirement)

    for index, requirements in grouped.items():
        heading = h2_nodes[index][0]
        existing = None
        sibling = heading.next_sibling
        while sibling is not None:
            if getattr(sibling, "name", None) == "h2":
                break
            if isinstance(sibling, Tag) and sibling.name == "ul" and sibling.get("data-source-coverage") == "true":
                existing = sibling
                break
            sibling = sibling.next_sibling
        coverage_list = existing or soup.new_tag("ul")
        if isinstance(coverage_list, Tag):
            coverage_list["data-source-coverage"] = "true"
        if existing is None:
            heading.insert_after(coverage_list)
        for requirement in requirements:
            item = soup.new_tag("li")
            prefix = "Include evidence-based coverage of: " if language == "en" else "تغطية مطلوبة مبنية على أدلة المنافسين: "
            item.string = prefix + str(requirement.get("text") or "").strip()
            if isinstance(coverage_list, Tag):
                coverage_list.append(item)
    return str(soup)


def _repair_outline_prompt(current_html: str, quality: dict, coverage: dict, evidence: dict,
                           audit_text: str, topic: str, target_type: str, language: str) -> str:
    issues = "; ".join(quality.get("issues", []))
    missing_topics = json.dumps(coverage.get("missing_topics", []), ensure_ascii=False)
    missing_sources = ", ".join(coverage.get("missing_sources", []))
    evidence_json = json.dumps(evidence, ensure_ascii=False)
    if language == "en":
        return f"""Rewrite and complete the architecture below.
Quality issues: {issues}
Missing topic coverage: {missing_topics}
Competitor ids not yet represented by a material coverage item: {missing_sources}

Preserve useful content, restore missing evidence in logical reader order, use exactly one h1, at least 10 meaningful h2 sections, rich h3/h4, support under every heading, and 10–15 FAQs. Merge duplicates. Return one complete semantic HTML article only.

Topic: {topic}
Type: {target_type}
Audit: {audit_text}
Current architecture: {current_html}
Evidence: {evidence_json}
"""
    return f"""أعد كتابة وإكمال الهيكلة أدناه.
مشكلات الجودة: {issues}
الموضوعات الناقصة: {missing_topics}
معرّفات المنافسين التي لم تظهر لها تغطية مادية بعد: {missing_sources}

حافظ على المحتوى المفيد، وأعد الموضوعات الناقصة في ترتيب منطقي لرحلة القارئ، واستخدم h1 واحداً و10 عناوين h2 جوهرية على الأقل وتفاصيل h3/h4 ومحتوى تحت كل عنوان و10–15 سؤالاً شائعاً. ادمج التكرار. أخرج article HTML دلالياً كاملاً فقط.

الموضوع: {topic}
النوع: {target_type}
التحليل: {audit_text}
الهيكلة الحالية: {current_html}
الأدلة: {evidence_json}
"""


def analyze_serp_data(competitor_data: list[dict], topic_context: str, target_type: str,
                      language: str, model: str, api_key: str, api_url: str,
                      cancel_check: Callable[[], bool] | None = None,
                      progress_callback: Callable[[str, int, int], None] | None = None) -> dict:
    if not competitor_data:
        message = "No competitor data was available." if language == "en" else "لا توجد بيانات منافسين لتحليلها."
        html_doc = normalize_rich_document(f"<article><p>{message}</p></article>", language)
        return {"html": html_doc, "text": html_to_plain_text(html_doc), "profiles": [], "coverage": {}}

    if cancel_check and cancel_check():
        raise RuntimeError("تم إلغاء التحليل بواسطة المستخدم.")

    def notify(stage: str, current: int = 0, total: int = 0) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(stage, current, total)
        except Exception:
            logger.debug("SERP progress callback failed", exc_info=True)

    # Phase 1: deterministic full scan. Fast, bounded, and guaranteed to keep
    # every source even when extraction was partial.
    profiles = [_local_full_profile(item, index, language) for index, item in enumerate(competitor_data, start=1)]
    notify("full_scan", len(profiles), len(profiles))

    # Phase 2: compact batch enrichment. This replaces dozens of per-chunk API
    # calls with a handful of larger, controlled requests.
    enrichable_profiles = [profile for profile in profiles if profile.words_scanned >= 25]
    batches = _batched(enrichable_profiles, config.SERP_PROFILE_BATCH_SIZE)
    ai_batch_failures = 0
    if batches:
        completed_batches = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(config.SERP_PROFILE_BATCH_WORKERS, len(batches))
        ) as executor:
            futures = {
                executor.submit(
                    _enrich_profile_batch, batch, topic_context, language,
                    model, api_key, api_url, cancel_check,
                ): batch
                for batch in batches
            }
            for future in concurrent.futures.as_completed(futures):
                if cancel_check and cancel_check():
                    for pending in futures:
                        pending.cancel()
                    raise RuntimeError("تم إلغاء التحليل بواسطة المستخدم.")
                try:
                    future.result()
                except Exception as exc:
                    ai_batch_failures += 1
                    for profile in futures[future]:
                        profile.failed_chunks += 1
                    logger.warning("SERP profile batch failed; deterministic full-scan profiles retained: %s", exc)
                completed_batches += 1
                notify("profile_batches", completed_batches, len(batches))

    profiles = [_finalize_profile(profile) for profile in profiles]
    evidence = _global_evidence(profiles)

    if cancel_check and cancel_check():
        raise RuntimeError("تم إلغاء التحليل بواسطة المستخدم.")
    # Strategy and architecture are synthesized in one final call. This removes
    # a full upstream round trip while keeping the same complete evidence in
    # context and asking for both strategic analysis and the final structure.
    notify("strategy", 0, 0)
    notify("outline", 0, 0)
    audit_text = ""
    outline_raw = _invoke_ai(
        _outline_prompt(evidence, audit_text, topic_context, target_type, language),
        _OUTLINE_SYSTEM_EN if language == "en" else _OUTLINE_SYSTEM_AR,
        model, api_key, api_url,
        max_tokens=config.SERP_FINAL_MAX_TOKENS,
    )
    outline_html = normalize_rich_document(outline_raw, language)
    quality = _outline_quality(outline_html)
    topic_coverage = _topic_coverage_quality(outline_html, evidence)

    # Only spend one extra model call for severe failures. Minor formatting
    # imperfections no longer force a long repair round trip.
    severe = (
        quality["counts"]["h1"] != 1
        or quality["counts"]["h2"] < 9
        or quality["counts"]["h3"] < 8
        or quality["faq_count"] < 8
        or quality["words"] < 650
        or quality["heading_jumps"] > 2
        or len(quality["orphan_headings"]) > 3
    )
    if severe and not (cancel_check and cancel_check()):
        notify("repair", 0, 0)
        logger.warning(
            "SERP outline repair triggered: %s; topic=%.2f source=%.2f",
            "; ".join(quality["issues"]),
            topic_coverage["topic_ratio"],
            topic_coverage["source_ratio"],
        )
        try:
            repaired_raw = _invoke_ai(
                _repair_outline_prompt(
                    outline_html, quality, topic_coverage, evidence, audit_text,
                    topic_context, target_type, language,
                ),
                _OUTLINE_SYSTEM_EN if language == "en" else _OUTLINE_SYSTEM_AR,
                model, api_key, api_url,
                max_tokens=config.SERP_FINAL_MAX_TOKENS,
            )
            repaired_html = normalize_rich_document(repaired_raw, language)
            repaired_quality = _outline_quality(repaired_html)
            repaired_coverage = _topic_coverage_quality(repaired_html, evidence)
            current_score = quality["score"] + topic_coverage["topic_ratio"] * 50 + topic_coverage["source_ratio"] * 40
            repaired_score = repaired_quality["score"] + repaired_coverage["topic_ratio"] * 50 + repaired_coverage["source_ratio"] * 40
            if repaired_score >= current_score:
                outline_html = repaired_html
                quality = repaired_quality
                topic_coverage = repaired_coverage
        except Exception as exc:
            logger.warning("SERP outline repair failed; keeping best response: %s", exc)

    # A final deterministic pass guarantees one material requirement from every
    # competitor and restores important shared topics without another AI call.
    topic_coverage = _topic_coverage_quality(outline_html, evidence)
    if topic_coverage["source_ratio"] < 1.0 or topic_coverage["topic_ratio"] < config.SERP_MIN_TOPIC_COVERAGE:
        outline_html = normalize_rich_document(
            _inject_missing_source_coverage(outline_html, evidence, topic_coverage, language),
            language,
        )
        quality = _outline_quality(outline_html)
        topic_coverage = _topic_coverage_quality(outline_html, evidence)

    competitor_audit_html = normalize_rich_document(_build_competitor_audit_html(profiles, language), language)
    document_html = combine_documents([outline_html, competitor_audit_html], language)

    full_sources = sum(1 for profile in profiles if profile.extraction_status == "full")
    limited_sources = len(profiles) - full_sources
    return {
        "html": document_html,
        "text": html_to_plain_text(document_html),
        "profiles": [profile.as_public_dict() for profile in profiles],
        "coverage": {
            "sources": len(profiles),
            "sources_full": full_sources,
            "sources_limited": limited_sources,
            "sources_represented": len(topic_coverage.get("represented_sources", [])),
            "characters_read": sum(profile.content_chars for profile in profiles),
            "words_scanned": sum(profile.words_scanned for profile in profiles),
            "chunks_analyzed": sum(profile.chunks_analyzed for profile in profiles),
            "ai_profile_batches": len(batches),
            "ai_enrichment_skipped_sources": len(profiles) - len(enrichable_profiles),
            "ai_profile_batch_failures": ai_batch_failures,
            "fallback_chunks": sum(profile.failed_chunks for profile in profiles),
            "outline_quality": quality,
            "topic_coverage": topic_coverage,
        },
    }
