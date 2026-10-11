"""Entity quality layer — cases taken from a real report, plus other niches
(store, clinic, hotel) to prove the rules are domain-agnostic."""
from models.schemas import EntityItem, SemanticCluster, SourceAnalysis
from services.entity_quality import refine_entities, separate_signals
from services.salience import apply_salience


def _e(name, lsi=None, kind="Concept", scope="topic", cluster="c", importance="medium"):
    return EntityItem(name=name, type=kind, importance=importance, salience=0.5, mentions=1,
                      cluster=cluster, lsi=lsi or [], scope=scope)


def _analysis(entities):
    return SourceAnalysis(source_label="s", raw_entities=entities,
                          clusters=[SemanticCluster(cluster_name="c", entities=entities)])


def _names(analysis):
    return [entity.name for entity in analysis.raw_entities]


LEGAL = ("[Title]: محامي قضايا عمالية في الرياض | مكتب آل عثمان للمحاماة\n\n[H1]: محامي قضايا عمالية في الرياض\n\n"
         "نراجع شروط عقد العمل والالتزامات التعاقدية، ونتابع التظلمات والشكاوى العمالية الإدارية. "
         "محامي محكمة عمالية يتولى صياغة عقود العمل. نعمل في الرياض وقد نسافر إلى الدمام.\n\n"
         "[H2]: فريق مكتب آل عثمان للمحاماة\n\n[H3]: يارا حكيم\n\nمحامية متخصصة.")


def test_heading_like_compounds_are_split_into_grounded_entities():
    out = refine_entities(_analysis([_e("شروط عقد العمل والالتزامات التعاقدية"),
                                     _e("التظلمات والشكاوى العمالية الإدارية"),
                                     _e("البيع والشراء")]), LEGAL)
    names = _names(out)
    assert "شروط عقد العمل" in names and "الالتزامات التعاقدية" in names
    assert "التظلمات" in names and "الشكاوى العمالية الإدارية" in names
    assert "البيع والشراء" in names          # a 2-word pair is a real concept, not a heading
    assert not any("والالتزامات" in name for name in names)


def test_duplicates_merge_and_ambiguous_variants_are_removed():
    out = refine_entities(_analysis([
        _e("محامي قضايا عمالية", ["محامي مكتب العمل", "محامي عمالي"], importance="high"),
        _e("محامي محكمة عمالية", ["محامي مكتب العمل"]),
        _e("محامي عقود", ["صياغة عقود العمل"]),
        _e("عقود العمل", ["عقد العمل", "صياغة عقود العمل"]),
        _e("المادة 83", ["بند عدم المنافسة"]),
        _e("بند عدم المنافسة"),
    ]), LEGAL)
    by_name = {entity.name: entity for entity in out.raw_entities}
    assert "محامي محكمة عمالية" not in by_name                       # merged into the canonical one
    assert "محامي محكمة عمالية" in by_name["محامي قضايا عمالية"].lsi
    assert "صياغة عقود العمل" not in by_name["محامي عقود"].lsi        # claimed by two unrelated entities
    assert "صياغة عقود العمل" not in by_name["عقود العمل"].lsi
    assert "عقد العمل" in by_name["عقود العمل"].lsi                   # true variant survives
    assert "بند عدم المنافسة" not in by_name["المادة 83"].lsi         # it is another entity, not a synonym


def test_site_brand_staff_and_incidental_mentions_leave_the_comparison():
    entities = [_e("محامي قضايا عمالية", importance="high"), _e("الرياض", kind="Place", importance="high"),
                _e("مكتب آل عثمان للمحاماة", kind="Organization"), _e("يارا حكيم", kind="Person", scope="brand"),
                _e("الدمام", kind="Place")]
    analysis = apply_salience(refine_entities(_analysis(entities), LEGAL), LEGAL)
    out = separate_signals(analysis, LEGAL, "https://alothmanlaw.sa/labor-lawyer-riyadh/")
    assert _names(out) == ["محامي قضايا عمالية", "الرياض"]
    scopes = {entity.name: entity.scope for entity in out.signal_entities}
    assert scopes == {"مكتب آل عثمان للمحاماة": "brand", "يارا حكيم": "brand", "الدمام": "context"}
    assert all(entity.name in _names(out) for cluster in out.clusters for entity in cluster.entities)


def test_same_rules_work_for_a_store_a_clinic_and_a_hotel():
    store = ("[Title]: آيفون 15 برو ماكس | متجر النخبة\n\n[H1]: آيفون 15 برو ماكس\n\n"
             "سياسة الاستبدال والاسترجاع خلال 14 يوماً. متجر النخبة يوفر الشحن المجاني. مقارنة مع سامسونج جالكسي.")
    out = separate_signals(apply_salience(refine_entities(_analysis([
        _e("آيفون 15 برو ماكس", ["iPhone 15 Pro Max"], kind="Product", importance="high"),
        _e("سياسة الاستبدال والاسترجاع الخاصة"),
        _e("متجر النخبة", kind="Organization"),
        _e("سامسونج جالكسي", kind="Product"),
    ]), store), store), store, "https://alnokhba-store.com/p/iphone")
    names = _names(out)
    assert "متجر النخبة" not in names and "سامسونج جالكسي" in names     # own store out, compared brand in
    assert "سياسة الاستبدال" in names

    clinic = ("[Title]: علاج السكري من النوع الثاني - عيادة الشفاء\n\n[H1]: علاج السكري من النوع الثاني\n\n"
              "الأنسولين والميتفورمين من أشهر العلاجات، وحمية منخفضة الكربوهيدرات تساعد.")
    out = refine_entities(_analysis([
        _e("السكري من النوع الثاني", ["الأنسولين", "السكري النوع 2"], importance="high"),
        _e("الأنسولين"), _e("الميتفورمين")]), clinic)
    diabetes = next(e for e in out.raw_entities if e.name == "السكري من النوع الثاني")
    assert diabetes.lsi == ["السكري النوع 2"]                          # a drug is not a synonym of the disease

    hotel = "[Title]: فنادق 5 نجوم في دبي\n\n[H1]: فنادق 5 نجوم في دبي\n\nفندق 5 نجوم قريب من برج خليفة."
    out = separate_signals(apply_salience(refine_entities(_analysis([
        _e("فندق 5 نجوم", ["فندق خمس نجوم"], importance="high"), _e("دبي", kind="Place", importance="high"),
        _e("برج خليفة", kind="Place")]), hotel), hotel), hotel, "https://hotels.example/dubai")
    assert "دبي" in _names(out)                                         # in Title/H1 → the subject
    assert [e.name for e in out.signal_entities] == ["برج خليفة"]       # mentioned once, in passing


def test_extraction_pipeline_applies_the_quality_layer(monkeypatch):
    import json
    import services.claude as claude
    payload = {"main_topic": "x", "clusters": [{"cluster_name": "ك", "pillar": "محامي قضايا عمالية", "entities": [
        {"name": "محامي قضايا عمالية", "source_term": "محامي قضايا عمالية", "type": "Service", "scope": "topic", "importance": "high"},
        {"name": "مكتب آل عثمان للمحاماة", "source_term": "مكتب آل عثمان للمحاماة", "type": "Organization", "scope": "topic", "importance": "high"},
        {"name": "شروط عقد العمل والالتزامات التعاقدية", "source_term": "", "type": "Concept", "scope": "topic", "importance": "medium"},
        {"name": "الرياض", "source_term": "الرياض", "type": "Place", "scope": "banana", "importance": "high"}]}]}
    monkeypatch.setattr(claude, "call_claude_api", lambda *a, **k: (json.dumps(payload, ensure_ascii=False), {"stop_reason": "tool_use"}))
    out = claude.extract_entities_for_source("موقعي", LEGAL, "ar", "default", "sk-ant-x",
                                            source_url="https://alothmanlaw.sa/labor-lawyer-riyadh/")
    assert {e.name for e in out.raw_entities} == {"محامي قضايا عمالية", "الرياض", "شروط عقد العمل", "الالتزامات التعاقدية"}
    assert [e.name for e in out.signal_entities] == ["مكتب آل عثمان للمحاماة"]


def test_site_offering_marked_brand_by_the_model_stays_a_topic():
    text = ("[Title]: محامي قضايا عمالية في الرياض | مكتب آل عثمان للمحاماة\n\n[H1]: محامي قضايا عمالية في الرياض\n\n"
            "نقدم استشارة قانونية عمالية. استشارة قانونية عمالية عن بعد. نعمل في الرياض.")
    entities = [_e("محامي قضايا عمالية", importance="high"),
                _e("استشارة قانونية عمالية", kind="Service", scope="brand"),        # Gemini did this
                _e("الرياض", kind="Place", scope="context", cluster="الموقع والسياق"),
                _e("مكتب آل عثمان للمحاماة", kind="Organization", scope="brand")]
    out = separate_signals(apply_salience(_analysis(entities), text), text, "https://alothmanlaw.sa/x")
    names = {e.name: e for e in out.raw_entities}
    assert "استشارة قانونية عمالية" in names                          # the site's own service is the subject
    assert names["الرياض"].cluster == "النطاق الجغرافي"               # no topic left in a "site & context" cluster
    assert [e.name for e in out.signal_entities] == ["مكتب آل عثمان للمحاماة"]
