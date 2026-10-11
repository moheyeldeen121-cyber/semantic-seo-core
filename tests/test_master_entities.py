"""Master entity list: merged across sources, competitor support, tiers."""
from models.schemas import EntityItem, SourceAnalysis
from services.semantic import assign_tier, build_master_entities


def _e(name, lsi=None, salience=0.5, mentions=2, kind="Concept"):
    return EntityItem(name=name, type=kind, importance="medium", salience=salience,
                      mentions=mentions, cluster="c", lsi=lsi or [])


def _src(label, entities):
    return SourceAnalysis(source_label=label, raw_entities=entities)


def test_variants_across_sources_become_one_row_with_support_and_tier():
    comps = [
        _src("A", [_e("SEO", ["search engine optimization"], 0.9), _e("local SEO", ["local search optimization"]),
                   _e("honesty", salience=0.2, mentions=1)]),
        _src("B", [_e("Search Engine Optimization", ["SEO"], 0.8), _e("local search optimization")]),
        _src("C", [_e("SEO", salience=0.7), _e("technical SEO", salience=0.3)]),
        _src("D", [_e("SEO", salience=0.6), _e("keyword research", salience=0.3)]),
    ]
    mine = [_e("تحسين محركات البحث", ["SEO"], 0.95, 10)]
    master = build_master_entities(mine, comps)
    seo = [e for e in master if "SEO" in [e.name, *e.lsi]]
    assert len(seo) == 1, [e.name for e in master]
    assert seo[0].source_count == 4 and seo[0].tier == "core"
    assert "technical SEO" not in seo[0].lsi and "local SEO" not in seo[0].lsi   # sub-topics stay separate
    local = next(e for e in master if e.name.lower() in ("local seo", "local search optimization"))
    assert local.source_count == 2 and local.tier == "core"          # 2 of 4 competitors >= 30%
    honesty = next(e for e in master if e.name == "honesty")
    assert honesty.tier == "long_tail"
    assert [e.tier for e in master] == sorted([e.tier for e in master], key=["core", "supporting", "long_tail"].index)


def test_tier_rules_scale_with_competitor_count():
    assert assign_tier(3, 10, 0.2, False) == "core"         # 30% of 10
    assert assign_tier(2, 10, 0.2, False) == "supporting"
    assert assign_tier(1, 10, 0.2, False) == "long_tail"
    assert assign_tier(0, 10, 0.7, True) == "core"          # central on the client's page
    assert assign_tier(2, 2, 0.4, False) == "core"          # every competitor, with 2 competitors
    assert assign_tier(0, 0, 0.65, True) == "core"          # single-page analysis: by salience
    assert assign_tier(0, 0, 0.3, True) == "supporting"
