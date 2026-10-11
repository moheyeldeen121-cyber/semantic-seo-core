import io

import openpyxl

from models.schemas import EntityItem, FullAnalysisResult, SemanticGap, SourceAnalysis
from services.excel import build_excel_report


def test_generated_workbook_neutralizes_formula_injection():
    dangerous = EntityItem(
        name="=HYPERLINK(\"https://example.invalid\",\"x\")",
        type="Concept",
        importance="high",
        salience=0.9,
        mentions=2,
        cluster="@cluster",
        lsi=["+lsi"],
    )
    result = FullAnalysisResult(
        master_entities=[dangerous],
        my_site_entities=[dangerous],
        competitor_breakdowns=[SourceAnalysis(source_label="+Source", raw_entities=[dangerous])],
        semantic_gaps=[
            SemanticGap(
                name="-Danger",
                type="Concept",
                competitor_count=1,
                total_mentions=2,
                priority="high",
                cluster="=Cluster",
                lsi=["@gap-lsi"],
            )
        ],
        has_my_article=True,
        has_competitors=True,
    )

    payload = build_excel_report(result)
    workbook = openpyxl.load_workbook(io.BytesIO(payload), data_only=False)
    assert workbook["All Entities"]["A2"].value.startswith("'=")
    assert workbook["All Entities"]["B2"].value.startswith("'+")
    assert workbook["All Entities"]["C2"].value.startswith("'@")
    assert workbook["الفجوات الدلالية"]["A2"].value.startswith("'-")
    assert workbook["الفجوات الدلالية"]["B2"].value.startswith("'@")
    assert workbook["الفجوات الدلالية"]["C2"].value.startswith("'=")
