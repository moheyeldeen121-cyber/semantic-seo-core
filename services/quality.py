"""Deterministic evidence scoring and transparent analysis quality metrics.

The AI remains responsible for semantic extraction, while this module checks its
output against the actual extracted source text. The score is deliberately
conservative and explainable; it never invents evidence and does not silently
remove model output.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable

from models.schemas import AnalysisQuality, EntityItem, SemanticGap, SourceAnalysis
import config
from services.semantic import (
    _covered_by_text,
    _normalize,
    _significant_words,
    compute_topic_coverage,
)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_entity_evidence(entity: EntityItem, source_text: str) -> tuple[float, str]:
    """Return an explainable lexical evidence score for one entity.

    Exact normalized phrase support is strongest. LSI aliases and compact token
    overlap provide weaker support for translated or reordered phrases.
    """
    normalized_source = _normalize(source_text)
    if not normalized_source:
        return 0.0, "unverified"

    # The canonical name and the AI's verbatim ``source_term`` are primary
    # evidence; LSI aliases are secondary.
    primary = [entity.name, getattr(entity, "source_term", "") or ""]
    for candidate in primary:
        if candidate and _covered_by_text(candidate, normalized_source):
            return 1.0, "verified"
    for candidate in entity.lsi:
        if candidate and _covered_by_text(candidate, normalized_source):
            return 0.82, "supported"

    source_tokens = set(normalized_source.split())
    entity_words = _significant_words(entity.name)
    if not entity_words:
        return 0.0, "unverified"
    overlap = sum(1 for word in entity_words if word in source_tokens) / len(entity_words)
    if overlap >= 0.75:
        return 0.68, "supported"
    if overlap >= 0.5 and len(entity_words) >= 2:
        return 0.48, "weak"
    return 0.18, "unverified"


def annotate_source_analysis(analysis: SourceAnalysis, source_text: str) -> SourceAnalysis:
    """Copy a source analysis and attach evidence scores consistently."""
    updated = analysis.model_copy(deep=True)
    score_by_key: dict[str, tuple[float, str]] = {}

    for entity in updated.raw_entities:
        score, level = score_entity_evidence(entity, source_text)
        entity.evidence_score = score
        entity.evidence_level = level
        score_by_key[_normalize(entity.name)] = (score, level)

    for cluster in updated.clusters:
        for entity in cluster.entities:
            key = _normalize(entity.name)
            score, level = score_by_key.get(key, score_entity_evidence(entity, source_text))
            entity.evidence_score = score
            entity.evidence_level = level
    return updated


def annotate_gap_confidence(gaps: Iterable[SemanticGap], competitor_count: int) -> list[SemanticGap]:
    """Attach confidence based on independent-source coverage and mention support."""
    total = max(1, competitor_count)
    updated: list[SemanticGap] = []
    for gap in gaps:
        item = gap.model_copy(deep=True)
        source_ratio = min(1.0, item.competitor_count / total)
        mention_signal = min(1.0, math.log1p(item.total_mentions) / math.log(21))
        priority_signal = {"critical": 1.0, "high": 0.75, "medium": 0.5}.get(item.priority, 0.4)
        item.confidence = _bounded(0.62 * source_ratio + 0.23 * mention_signal + 0.15 * priority_signal)
        if item.confidence >= 0.78:
            item.evidence_level = "high"
        elif item.confidence >= 0.55:
            item.evidence_level = "medium"
        else:
            item.evidence_level = "limited"
        updated.append(item)
    return updated


def _entity_stats(analyses: Iterable[SourceAnalysis]) -> tuple[int, int, int, Counter[str]]:
    total = verified = supported = 0
    levels: Counter[str] = Counter()
    for analysis in analyses:
        for entity in analysis.raw_entities:
            total += 1
            levels[entity.evidence_level] += 1
            if entity.evidence_score >= 0.8:
                verified += 1
            # 0.48 is the score for a *weak* partial-token match; counting it
            # as "supported" inflated the evidence rate on every run.
            if entity.evidence_score >= config.MIN_SUPPORTED_EVIDENCE:
                supported += 1
    return total, verified, supported, levels


def build_analysis_quality(
    my_analysis: SourceAnalysis | None,
    competitor_analyses: list[SourceAnalysis],
    gaps: list[SemanticGap],
    source_characters: int,
    language: str,
    my_text: str = "",
    requested_sources: int = 0,
    failed_sources: list[str] | None = None,
    partial_sources: list[str] | None = None,
    cluster_gaps: int = 0,
) -> AnalysisQuality:
    """Two independent numbers, deliberately kept apart.

    ``score`` answers "can I trust this analysis?" — it measures how much of
    the AI's output is backed by text actually found in the sources. It says
    nothing about how good the client's content is, which is why a page
    covering a quarter of the market's topics could still score 98.

    ``coverage_rate`` answers the question a content team is actually asking:
    of the topics that MULTIPLE competitors agree matter, how many does the
    client's page cover?
    """
    analyses = ([my_analysis] if my_analysis else []) + list(competitor_analyses)
    total, verified, supported, levels = _entity_stats(analyses)
    evidence_rate = supported / total if total else 0.0
    verified_rate = verified / total if total else 0.0
    high_confidence_gaps = sum(1 for gap in gaps if gap.confidence >= 0.7)

    coverage = compute_topic_coverage(
        list(my_analysis.raw_entities) if my_analysis else [],
        list(competitor_analyses),
        my_text,
    )
    # Coverage and the gap list must never contradict each other on screen.
    # Whatever survived every filter (including the optional AI review) IS the
    # uncovered remainder, so derive the covered count from it rather than
    # letting two independent passes disagree by one or two topics.
    shared_topics = max(coverage["shared_topics"], len(gaps))
    covered_topics = max(0, shared_topics - len(gaps))
    coverage_rate = (covered_topics / shared_topics) if shared_topics else 0.0

    # Saturating at 5 sources and 20k characters handed out 34 free points to
    # every real run, which is why the score never dropped below the high 80s.
    source_score = min(1.0, len(analyses) / 8)
    depth_score = min(1.0, source_characters / 60_000)
    score = round(100 * (
        0.52 * evidence_rate
        + 0.24 * verified_rate
        + 0.14 * source_score
        + 0.10 * depth_score
    ))

    warnings: list[str] = []
    if not competitor_analyses:
        warnings.append("No competitor source was successfully analyzed." if language == "en" else "لم يتم تحليل أي مصدر منافس بنجاح.")
    elif len(competitor_analyses) < 3:
        warnings.append("Use at least three competitors for a more stable gap signal." if language == "en" else "استخدم ثلاثة منافسين على الأقل للحصول على إشارة فجوات أكثر ثباتًا.")
    if total and evidence_rate < 0.55:
        warnings.append("A noticeable share of AI entities has weak direct textual evidence; review the evidence drawer." if language == "en" else "نسبة ملحوظة من كيانات الذكاء الاصطناعي دليلها النصي المباشر ضعيف؛ راجع درج الأدلة.")
    if source_characters < 1_500:
        warnings.append("The extracted text is short, so coverage may be incomplete." if language == "en" else "النص المستخرج قصير، لذلك قد تكون التغطية غير مكتملة.")
    if shared_topics and coverage_rate < 0.5:
        warnings.append(
            f"Your page covers only {covered_topics} of {shared_topics} topics that several competitors share — the analysis score above measures evidence quality, not this."
            if language == "en" else
            f"صفحتك تغطي {covered_topics} فقط من {shared_topics} موضوعاً يتفق عليها أكثر من منافس — درجة الجودة أعلاه تقيس جودة الأدلة وليس هذه التغطية."
        )
    if failed_sources:
        listed = "، ".join(failed_sources[:4])
        warnings.append(
            f"{len(failed_sources)} source(s) could not be analyzed and are not in this report: {listed}"
            if language == "en" else
            f"تعذّر تحليل {len(failed_sources)} مصدر ولا يظهر في هذا التقرير: {listed}"
        )
    if partial_sources:
        listed = "، ".join(partial_sources[:4])
        warnings.append(
            f"{len(partial_sources)} source(s) were only partially readable (short text): {listed}"
            if language == "en" else
            f"{len(partial_sources)} مصدر قُرئ جزئياً فقط (نص قصير): {listed}"
        )
    if cluster_gaps:
        warnings.append(
            f"{cluster_gaps} semantic cluster(s) are covered by several competitors but missing or thin on your page."
            if language == "en" else
            f"{cluster_gaps} عنقود دلالي يغطيه أكثر من منافس بينما هو غائب أو ضعيف في صفحتك."
        )
    if not warnings:
        warnings.append("Quality checks passed with no major deterministic warning." if language == "en" else "اجتازت النتيجة فحوص الجودة دون تحذيرات حتمية رئيسية.")

    return AnalysisQuality(
        score=score,
        evidence_rate=round(evidence_rate, 4),
        verified_rate=round(verified_rate, 4),
        total_entities=total,
        verified_entities=verified,
        supported_entities=supported,
        unverified_entities=levels.get("unverified", 0),
        high_confidence_gaps=high_confidence_gaps,
        shared_topics=shared_topics,
        covered_topics=covered_topics,
        coverage_rate=round(coverage_rate, 4),
        source_count=len(analyses),
        requested_sources=max(requested_sources, len(analyses)),
        failed_sources=list(failed_sources or [])[:40],
        source_characters=source_characters,
        warnings=warnings[:30],
    )
