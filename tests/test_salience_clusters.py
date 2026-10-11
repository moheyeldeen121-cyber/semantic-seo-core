"""Deterministic salience, Arabic morphology, and cross-source clusters."""
from models.schemas import EntityItem, SourceAnalysis
from services.clustering import build_semantic_clusters
from services.salience import apply_salience
from services.semantic import _normalize, token_forms


def _e(name, cluster="c", salience=0.5, lsi=None, kind="Concept"):
    return EntityItem(name=name, type=kind, importance="medium", salience=salience,
                      mentions=1, cluster=cluster, lsi=lsi or [])


def test_clitics_and_orthography_fold_to_one_key():
    assert _normalize("والفنادق") == _normalize("الفنادق") == _normalize("بالفنادق") == "فنادق"
    assert _normalize("للتسويق") == "تسويق"
    assert _normalize("الله") == "الله"            # short real word untouched
    assert _normalize("كالسيوم") == "كالسيوم"      # loanword untouched
    assert _normalize("إستراتيجية") == _normalize("استراتيجيه")
    assert "خدم" in token_forms("وخدمات") and "خدم" in token_forms("خدمه")


def test_salience_comes_from_placement_frequency_and_spread_not_the_ai_guess():
    text = ("[Title]: دليل السيو التقني\n\n[H1]: دليل السيو التقني\n\n"
            "السيو التقني أساس الظهور ويشمل سرعة الموقع.\n\n[H2]: سرعة الموقع\n\n"
            "تؤثر سرعة الموقع على الترتيب ولسرعة الموقع أثر على التجربة.\n\n[H2]: الروابط\n\n"
            "بناء الروابط مهم لسرعة الموقع أيضاً.")
    analysis = SourceAnalysis(source_label="x", raw_entities=[
        _e("السيو التقني", salience=0.1), _e("سرعة الموقع", salience=0.1),
        _e("بناء الروابط", salience=0.9), _e("الحوسبة الكمية", salience=0.99)])
    out = {e.name: e for e in apply_salience(analysis, text).raw_entities}
    assert out["السيو التقني"].salience > out["بناء الروابط"].salience
    assert "title" in out["السيو التقني"].placements and "h1" in out["السيو التقني"].placements
    assert out["سرعة الموقع"].mentions >= 4
    assert out["الحوسبة الكمية"].grounded is False and out["الحوسبة الكمية"].mentions == 0
    assert out["الحوسبة الكمية"].salience < 0.45


def test_clusters_unify_sources_and_report_cluster_gaps():
    comps = [
        SourceAnalysis(source_label="A", raw_entities=[_e("السيو التقني", "تقني"), _e("سرعة الموقع", "تقني"),
                                                       _e("الفهرسة", "تقني"), _e("بناء الروابط", "روابط"),
                                                       _e("الروابط الخلفية", "روابط", lsi=["باك لينك"])]),
        SourceAnalysis(source_label="B", raw_entities=[_e("السيو التقني", "SEO تقني"), _e("خريطة الموقع", "SEO تقني"),
                                                       _e("باك لينك", "لينك بلدنج"), _e("بناء الروابط", "لينك بلدنج")]),
        SourceAnalysis(source_label="C", raw_entities=[_e("الفهرسة", "الجانب التقني"), _e("خريطة الموقع", "الجانب التقني"),
                                                       _e("السيو التقني", "الجانب التقني"), _e("نية البحث", "كلمات")]),
    ]
    mine = SourceAnalysis(source_label="mine", raw_entities=[_e("نية البحث", "كلمات")])
    clusters, names = build_semantic_clusters(mine, comps, "مقال عن نية البحث")
    technical = next(c for c in clusters if c.pillar_entity == "السيو التقني")
    assert technical.is_gap and technical.status == "missing"
    assert technical.competitor_count == 3 and technical.priority == "critical"
    assert {"الفهرسة", "خريطة الموقع"} <= {e.name for e in technical.supporting_entities}
    links = next(c for c in clusters if "بناء الروابط" in [c.pillar_entity, *[e.name for e in c.supporting_entities]])
    assert "باك لينك" in [*links.lsi_terms, *[e.name for e in links.supporting_entities]]
    assert names[_normalize("الفهرسة")] == technical.cluster_name
    # deterministic
    again, _ = build_semantic_clusters(mine, comps, "مقال عن نية البحث")
    assert [c.model_dump() for c in again] == [c.model_dump() for c in clusters]


def test_single_competitor_trivia_does_not_become_a_cluster_gap():
    comps = [SourceAnalysis(source_label=l, raw_entities=[_e("أتمتة العمليات", "أتمتة"), _e("تكامل الأنظمة", "أتمتة")])
             for l in "ABCD"]
    comps[0].raw_entities.append(_e("مطعم الخبر", "أخرى", kind="Place"))
    clusters, _ = build_semantic_clusters(SourceAnalysis(source_label="m", raw_entities=[_e("موضوع", "x")]), comps, "")
    assert all("مطعم الخبر" not in [c.pillar_entity, *c.missing_entities] for c in clusters)
