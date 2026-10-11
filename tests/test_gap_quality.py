"""Regression tests for the corroboration rules that replaced raw-mention gaps.

Before these rules a report of 8 competitors produced 223 "gaps" of which 97%
came from a single source — a competitor's own city, product, or brand — and
7 of the 8 "critical" ones had exactly one competitor behind them.
"""

from models.schemas import EntityItem, SourceAnalysis
from services.quality import build_analysis_quality
from services.semantic import build_topic_index, compute_semantic_gaps, compute_topic_coverage


def _entity(name: str, mentions: int = 1, kind: str = "Concept",
            lsi: list[str] | None = None) -> EntityItem:
    return EntityItem(name=name, type=kind, importance="high", salience=0.7,
                      mentions=mentions, cluster="اختبار", lsi=lsi or [])


def _sources(mapping: dict[str, list[EntityItem]]) -> list[SourceAnalysis]:
    return [SourceAnalysis(source_label=label, raw_entities=items)
            for label, items in mapping.items()]


def test_single_source_entity_is_not_a_gap_when_many_competitors_exist():
    competitors = _sources({
        f"C{index}": [_entity("أتمتة العمليات", 3)] for index in range(1, 6)
    })
    # One competitor also talks about its own city, a lot.
    competitors[0].raw_entities.append(_entity("الخبر", 30, kind="Place"))

    gaps, _covered = compute_semantic_gaps([], competitors, "")
    names = [gap.name for gap in gaps]
    assert "أتمتة العمليات" in names
    assert "الخبر" not in names, "a place named by one competitor is its own location, not a gap"


def test_high_mention_count_alone_cannot_make_a_gap_critical():
    competitors = _sources({
        "A": [_entity("منتج المنافس", 40, kind="Product")],
        "B": [_entity("أتمتة العمليات", 2)],
        "C": [_entity("أتمتة العمليات", 2)],
        "D": [_entity("أتمتة العمليات", 2)],
    })
    gaps, _covered = compute_semantic_gaps([], competitors, "")
    for gap in gaps:
        if gap.priority == "critical":
            assert gap.competitor_count >= 2


def test_spelling_variants_are_counted_as_one_topic():
    competitors = _sources({
        "A": [_entity("تعلم الآلة", 4)],
        "B": [_entity("خدمات تعلم الآلة", 3)],                       # generic modifier
        "C": [_entity("التعلم الآلي", 2, lsi=["تعلم الآلة"])],        # AI-declared variant
    })
    gaps, _covered = compute_semantic_gaps([], competitors, "")
    machine_learning = [gap for gap in gaps if "تعلم" in gap.name]
    assert len(machine_learning) == 1, "three spellings of one concept must collapse into one gap"
    assert machine_learning[0].competitor_count == 3
    assert machine_learning[0].total_mentions == 9


def test_a_narrower_subtopic_is_not_swallowed_by_its_head_term():
    competitors = _sources({
        "A": [_entity("تحسين محركات البحث المحلي", 4), _entity("SEO", 3)],
        "B": [_entity("تحسين محركات البحث المحلي", 3), _entity("technical SEO", 3)],
        "C": [_entity("technical SEO", 2), _entity("SEO", 2)],
    })
    mine = [_entity("تحسين محركات البحث", 5), _entity("SEO", 5)]
    gaps, _covered = compute_semantic_gaps(mine, competitors, "مقال عن تحسين محركات البحث و SEO")
    names = {gap.name for gap in gaps}
    assert "تحسين محركات البحث المحلي" in names and "technical SEO" in names


def test_ai_supplied_lsi_synonyms_merge_topics():
    index = build_topic_index(_sources({
        "A": [_entity("شات بوت واتساب", 5, lsi=["روبوت المحادثة"])],
        "B": [_entity("روبوت المحادثة", 4)],
    }))
    groups = {group for group, sources in index.sources.items() if len(sources) >= 2}
    assert groups, "an entity name matching another entity's LSI list is the same topic"


def test_coverage_rate_reflects_shared_topics_not_evidence_quality():
    competitors = _sources({
        "A": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
        "B": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
        "C": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
    })
    coverage = compute_topic_coverage([_entity("أتمتة المستودعات", 5)], competitors, "")
    assert coverage["shared_topics"] == 3
    assert coverage["covered_topics"] == 1
    assert round(coverage["coverage_rate"], 2) == 0.33


def test_coverage_and_gap_count_can_never_contradict_each_other():
    """shared == covered + gaps, by construction — the report used to be able
    to show "9 of 13 covered" beside a list of 3 gaps."""
    competitors = _sources({
        "A": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
        "B": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
        "C": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
    })
    mine = [_entity("أتمتة المستودعات", 5)]
    coverage = compute_topic_coverage(mine, competitors, "")
    gaps, _covered = compute_semantic_gaps(mine, competitors, "")
    assert coverage["shared_topics"] == coverage["covered_topics"] + len(gaps)


def test_quality_reports_coverage_separately_from_score():
    mine = SourceAnalysis(source_label="mine", raw_entities=[_entity("أتمتة المستودعات", 5)])
    competitors = _sources({
        "A": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
        "B": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
        "C": [_entity("أتمتة المستودعات", 3), _entity("خدمة العملاء", 3), _entity("التحول الرقمي", 3)],
    })
    gaps, _covered = compute_semantic_gaps(list(mine.raw_entities), competitors, "")
    quality = build_analysis_quality(mine, competitors, gaps, 30_000, "ar")
    assert quality.shared_topics == 3
    assert quality.covered_topics == 1
    assert round(quality.coverage_rate, 2) == 0.33
    # The headline score must not be dragged up by content coverage, nor
    # vice versa — they answer different questions.
    assert 0 <= quality.score <= 100
    assert any("تغطية" in warning for warning in quality.warnings)


def test_two_competitor_run_still_reports_gaps():
    """With fewer than three sources corroboration is impossible, so the
    threshold must relax instead of returning an empty report."""
    competitors = _sources({
        "A": [_entity("موضوع مهم", 4)],
        "B": [_entity("موضوع آخر", 4)],
    })
    gaps, _covered = compute_semantic_gaps([], competitors, "")
    assert len(gaps) == 2
