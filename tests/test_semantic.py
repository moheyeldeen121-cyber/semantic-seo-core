from models.schemas import EntityItem, SourceAnalysis
from services.semantic import _covered_by_text, _normalize, compute_semantic_gaps


def entity(name: str, mentions: int = 1, salience: float = 0.7) -> EntityItem:
    return EntityItem(
        name=name,
        type="Concept",
        importance="high",
        salience=salience,
        mentions=mentions,
        cluster="اختبار",
    )


def test_arabic_normalization_does_not_destroy_real_words():
    assert _normalize("برنامج كتاب فندق لغة") == "برنامج كتاب فندق لغه"
    assert _normalize("الفنادق") == "فنادق"


def test_phrase_coverage_requires_reasonable_proximity():
    assert _covered_by_text("تحسين محركات البحث", _normalize("دليل تحسين محركات البحث للمبتدئين"))
    assert not _covered_by_text(
        "تحسين محركات البحث",
        _normalize("تحسين تجربة المستخدم " + "كلمة " * 20 + "محركات قوية ثم البحث العلمي"),
    )


def test_semantic_gaps_use_source_coverage_for_priority():
    competitors = [
        SourceAnalysis(source_label="A", raw_entities=[entity("بحث الكلمات", 3)]),
        SourceAnalysis(source_label="B", raw_entities=[entity("بحث الكلمات", 2)]),
        SourceAnalysis(source_label="C", raw_entities=[entity("موضوع آخر", 5)]),
    ]
    gaps, covered = compute_semantic_gaps([], competitors, my_text="")
    gap = next(item for item in gaps if _normalize(item.name) == _normalize("بحث الكلمات"))
    assert gap.competitor_count == 2
    assert gap.total_mentions == 5
    assert gap.priority == "high"
    assert covered == []


def test_entity_present_in_raw_article_is_not_reported_as_gap():
    competitors = [SourceAnalysis(source_label="A", raw_entities=[entity("نية البحث")])]
    gaps, covered = compute_semantic_gaps([], competitors, my_text="شرح شامل عن نية البحث للمستخدم")
    assert gaps == []
    assert [item.name for item in covered] == ["نية البحث"]


def test_chunk_splitter_preserves_all_words_and_prefers_boundaries():
    from services.nara import _split_text_into_chunks

    text = ("فقرة أولى تحتوي على كيان متخصص ومعلومة كاملة.\n\n" * 40).strip()
    chunks = _split_text_into_chunks(text, 180)
    assert len(chunks) > 1
    rebuilt = " ".join(" ".join(chunk.split()) for chunk in chunks)
    original = " ".join(text.split())
    assert rebuilt == original
