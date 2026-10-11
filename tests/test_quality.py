from models.schemas import EntityItem, SemanticGap, SourceAnalysis
from services.quality import (
    annotate_gap_confidence,
    annotate_source_analysis,
    build_analysis_quality,
    score_entity_evidence,
)


def _entity(name: str, lsi=None) -> EntityItem:
    return EntityItem(
        name=name,
        type="Concept",
        lsi=lsi or [],
        importance="high",
        salience=0.8,
        mentions=2,
        cluster="SEO",
    )


def test_entity_evidence_prefers_direct_phrase_and_alias_support():
    direct_score, direct_level = score_entity_evidence(
        _entity("نية البحث"),
        "يشرح هذا الدليل نية البحث وكيفية تحليلها.",
    )
    alias_score, alias_level = score_entity_evidence(
        _entity("تحليل الاستعلام", ["بحث الكلمات المفتاحية"]),
        "يشمل العمل بحث الكلمات المفتاحية واختيار الموضوعات.",
    )
    assert direct_score == 1.0
    assert direct_level == "verified"
    assert alias_score >= 0.8
    assert alias_level == "supported"


def test_source_annotation_updates_raw_and_cluster_entities():
    entity = _entity("تحسين محركات البحث")
    source = SourceAnalysis(
        source_label="Example",
        raw_entities=[entity],
        clusters=[{"cluster_name": "SEO", "entities": [entity]}],
    )
    updated = annotate_source_analysis(source, "دليل تحسين محركات البحث للمبتدئين")
    assert updated.raw_entities[0].evidence_score == 1.0
    assert updated.clusters[0].entities[0].evidence_level == "verified"
    assert source.raw_entities[0].evidence_score == 0.0  # copy, do not mutate caller


def test_gap_confidence_rises_with_independent_source_coverage():
    gaps = [
        SemanticGap(
            name="Entity A",
            type="Concept",
            competitor_count=4,
            total_mentions=8,
            priority="critical",
        ),
        SemanticGap(
            name="Entity B",
            type="Concept",
            competitor_count=1,
            total_mentions=1,
            priority="medium",
        ),
    ]
    updated = annotate_gap_confidence(gaps, competitor_count=4)
    assert updated[0].confidence > updated[1].confidence
    assert updated[0].evidence_level == "high"
    assert updated[1].evidence_level == "limited"


def test_quality_summary_is_bounded_and_transparent():
    source = annotate_source_analysis(
        SourceAnalysis(source_label="A", raw_entities=[_entity("Search intent")]),
        "Search intent is the main topic.",
    )
    quality = build_analysis_quality(None, [source], [], 3000, "en")
    assert 0 <= quality.score <= 100
    assert quality.supported_entities == 1
    assert quality.source_count == 1
    assert quality.warnings


def test_low_positive_evidence_is_excluded_from_semantic_gaps(monkeypatch):
    from services.semantic import compute_semantic_gaps
    import services.semantic as semantic

    weak = _entity("Hallucinated entity")
    weak.evidence_score = 0.18
    weak.evidence_level = "unverified"
    source = SourceAnalysis(source_label="A", raw_entities=[weak])
    monkeypatch.setattr(semantic.config, "MIN_GAP_ENTITY_EVIDENCE", 0.35)
    gaps, _covered = compute_semantic_gaps([], [source], "")
    assert gaps == []
