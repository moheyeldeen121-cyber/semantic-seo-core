"""
Excel Export
=============
Sheets: All Entities · العناقيد الدلالية · فجوات العناقيد · الفجوات الدلالية ·
كيانات موقعي · حالة قراءة المصادر · one sheet per competitor.

"In my site" / "in competitors" use the same Arabic-aware normalization as
the gap engine (exact lowercase matching used to show "—" for an entity the
page covers under a different spelling).
"""

import io
import re
from collections import Counter

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from models.schemas import FullAnalysisResult
from services.semantic import _normalize
from utils.security import safe_excel_value

_AMBER_FILL = PatternFill("solid", fgColor="B45309")
_STATUS_AR = {"covered": "مغطى", "partial": "تغطية جزئية", "missing": "غائب", "n/a": "—"}
_PRIORITY_AR = {"critical": "حرجة", "high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
_READ_AR = {"ok": "مقروء بالكامل", "partial": "قراءة جزئية", "blocked": "محجوب", "failed": "فشل"}
_PRIORITY_COLORS = {"critical": "FEE2E2", "high": "FEF3C7", "medium": "F0FDF4", "low": "FFFFFF"}

_BLUE_FILL = PatternFill("solid", fgColor="0101B1")
_ROSE_FILL = PatternFill("solid", fgColor="FF4B6E")
_GREEN_FILL = PatternFill("solid", fgColor="059669")
_GRAY_FILL = PatternFill("solid", fgColor="F1F5F9")
_WHITE_FONT = Font(color="FFFFFF", bold=True)
_DARK_FONT = Font(color="1E293B")
_BOLD_DARK = Font(color="1E293B", bold=True)
_CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
_RIGHT_ALIGN = Alignment(horizontal="right", vertical="center", wrap_text=True)


def _header_row(ws, headers: list[str], fill: PatternFill, row: int = 1):
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_idx, value=safe_excel_value(header))
        cell.fill = fill
        cell.font = _WHITE_FONT
        cell.alignment = _CENTER_ALIGN


def _write_cluster_breakdown_sheet(wb, sheet_name: str, clusters, fill: PatternFill):
    """Shared by the per-competitor sheets and the 'my site' sheet: one row
    per entity, grouped by the semantic cluster it belongs to."""
    ws = wb.create_sheet(sheet_name)
    ws.right_to_left = True
    _header_row(ws, ["العنقود الدلالي", "الكيان", "النوع", "الأهمية", "التكرار", "درجة الدلالة", "الدور", "مواضع الظهور"], fill)
    row_i = 2
    for cluster in clusters:
        for ent in cluster.entities:
            for col_i, val in enumerate(
                [cluster.cluster_name, ent.name, ent.entity_type,
                 ent.importance, ent.mentions, f"{ent.salience:.2f}",
                 {"core": "محوري", "supporting": "داعم"}.get(getattr(ent, "role", ""), ""),
                 "، ".join(getattr(ent, "placements", []) or [])], start=1
            ):
                cell = ws.cell(row=row_i, column=col_i, value=safe_excel_value(val))
                cell.alignment = _RIGHT_ALIGN
                if row_i % 2 == 0:
                    cell.fill = _GRAY_FILL
            row_i += 1
    ws.column_dimensions["A"].width = 25
    ws.column_dimensions["B"].width = 30
    for col in ["C", "D", "E", "F", "G"]:
        ws.column_dimensions[col].width = 15
    ws.column_dimensions["H"].width = 26
    return ws


def _write_rows(ws, start_row: int, rows: list[list], fills: list[PatternFill | None] | None = None) -> None:
    for offset, values in enumerate(rows):
        row_i = start_row + offset
        fill = (fills[offset] if fills else None) or (_GRAY_FILL if row_i % 2 == 0 else None)
        for col_i, val in enumerate(values, start=1):
            cell = ws.cell(row=row_i, column=col_i, value=safe_excel_value(val))
            cell.alignment = _RIGHT_ALIGN
            if fill:
                cell.fill = fill


def _write_clusters_sheet(wb, result: FullAnalysisResult) -> None:
    clusters = list(getattr(result, "semantic_clusters", []) or [])
    if not clusters:
        return
    ws = wb.create_sheet("العناقيد الدلالية")
    ws.right_to_left = True
    _header_row(ws, ["#", "العنقود الدلالي", "الكيان المحوري (Pillar)", "الكيانات الداعمة", "مرادفات LSI",
                     "عدد الكيانات", "المنافسون المغطّون", "تغطية المنافسين", "تغطية موقعي", "الحالة", "فجوة؟", "الأولوية"],
                _BLUE_FILL)
    rows = []
    for cluster in clusters:
        supporting = "، ".join(
            f"{entity.name}{' ✓' if entity.covered_by_me else ''}" for entity in cluster.supporting_entities[:25])
        rows.append([
            cluster.cluster_id, cluster.cluster_name, cluster.pillar_entity, supporting,
            "، ".join(cluster.lsi_terms[:20]), cluster.entity_count,
            f"{cluster.competitor_count}/{cluster.competitor_total}",
            f"{round(cluster.competitor_coverage * 100)}%",
            f"{round(cluster.my_coverage * 100)}%" if cluster.status != "n/a" else "—",
            _STATUS_AR.get(cluster.status, cluster.status),
            "نعم" if cluster.is_gap else "لا",
            _PRIORITY_AR.get(cluster.priority, cluster.priority),
        ])
    _write_rows(ws, 2, rows)
    for col, width in zip("ABCDEFGHIJKL", (6, 30, 28, 60, 40, 12, 16, 16, 14, 14, 10, 12)):
        ws.column_dimensions[col].width = width

    gaps = [cluster for cluster in clusters if cluster.is_gap]
    ws_gap = wb.create_sheet("فجوات العناقيد")
    ws_gap.right_to_left = True
    note = ws_gap.cell(row=1, column=1, value=safe_excel_value(
        "عناقيد يغطيها عدد كافٍ من المنافسين المستقلين بينما صفحتك لا تغطيها أو تغطيها جزئياً — "
        "مرتبة حسب الأولوية. الكيانات الناقصة هي ما يلزم إضافته لبناء السلطة الموضوعية."))
    note.font = _BOLD_DARK
    note.alignment = _RIGHT_ALIGN
    _header_row(ws_gap, ["العنقود الدلالي", "الكيان المحوري", "الكيانات الناقصة في صفحتك", "مرادفات LSI",
                         "المنافسون المغطّون", "تغطية موقعي", "الحالة", "الأولوية"], _ROSE_FILL, row=3)
    gap_rows = [[
        cluster.cluster_name, cluster.pillar_entity, "، ".join(cluster.missing_entities[:30]),
        "، ".join(cluster.lsi_terms[:15]), f"{cluster.competitor_count}/{cluster.competitor_total}",
        f"{round(cluster.my_coverage * 100)}%", _STATUS_AR.get(cluster.status, cluster.status),
        _PRIORITY_AR.get(cluster.priority, cluster.priority),
    ] for cluster in gaps]
    fills = [PatternFill("solid", fgColor=_PRIORITY_COLORS.get(cluster.priority, "FFFFFF")) for cluster in gaps]
    _write_rows(ws_gap, 4, gap_rows, fills)
    for col, width in zip("ABCDEFGH", (30, 28, 60, 40, 16, 14, 14, 12)):
        ws_gap.column_dimensions[col].width = width


_SCOPE_AR = {"brand": "علامة الموقع وفريقه", "context": "ذكر عابر (سياق)"}


def _write_signals_sheet(wb, result: FullAnalysisResult) -> None:
    rows: list[list] = []
    for entity in getattr(result, "my_site_signals", []) or []:
        rows.append(["موقعي", entity.name, _SCOPE_AR.get(entity.scope, entity.scope), entity.entity_type, entity.mentions])
    for comp in result.competitor_breakdowns:
        for entity in getattr(comp, "signal_entities", []) or []:
            rows.append([comp.source_label, entity.name, _SCOPE_AR.get(entity.scope, entity.scope),
                         entity.entity_type, entity.mentions])
    if not rows:
        return
    ws = wb.create_sheet("إشارات العلامة والسياق")
    ws.right_to_left = True
    note = ws.cell(row=1, column=1, value=safe_excel_value(
        "كيانات لا تدخل في المقارنة: اسم الموقع وفريقه (إشارات موثوقية E-E-A-T) والذكر العابر."))
    note.font = _BOLD_DARK
    note.alignment = _RIGHT_ALIGN
    _header_row(ws, ["المصدر", "الكيان", "التصنيف", "النوع", "التكرار"], _AMBER_FILL, row=3)
    _write_rows(ws, 4, rows)
    for col, width in zip("ABCDE", (30, 34, 22, 14, 10)):
        ws.column_dimensions[col].width = width


def _write_report_sheet(wb, result: FullAnalysisResult) -> None:
    report = list(getattr(result, "extraction_report", []) or [])
    if not report:
        return
    ws = wb.create_sheet("حالة قراءة المصادر")
    ws.right_to_left = True
    _header_row(ws, ["المصدر", "الرابط/الملف", "حالة القراءة", "طريقة القراءة", "عدد الأحرف", "العناوين",
                     "جهة الحجب", "ملاحظة"], _AMBER_FILL)
    rows = [[item.label, item.url, _READ_AR.get(item.status, item.status), item.tier, item.chars, item.headings,
             item.provider, item.reason] for item in report]
    status_fill = {"blocked": PatternFill("solid", fgColor="FEE2E2"), "failed": PatternFill("solid", fgColor="FEE2E2"),
                   "partial": PatternFill("solid", fgColor="FEF3C7")}
    _write_rows(ws, 2, rows, [status_fill.get(item.status) for item in report])
    for col, width in zip("ABCDEFGH", (30, 50, 16, 14, 12, 10, 18, 60)):
        ws.column_dimensions[col].width = width


def build_excel_report(result: FullAnalysisResult) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws1 = wb.create_sheet("All Entities")
    ws1.right_to_left = True
    _header_row(ws1, ["الكيان", "مرادفات LSI", "العنقود الدلالي", "النوع", "المستوى", "الأهمية",
                      "درجة الدلالة", "في موقعي", "عدد المنافسين", "إجمالي التكرار"], _BLUE_FILL)
    my_names = {_normalize(e.name) for e in result.my_site_entities}
    for e in result.my_site_entities:
        my_names.update(_normalize(term) for term in e.lsi)
    tier_labels = {"core": "أساسي", "supporting": "داعم", "long_tail": "هامشي"}
    tier_fills = {"core": PatternFill("solid", fgColor="DBEAFE"), "long_tail": PatternFill("solid", fgColor="F1F5F9")}
    for row_i, ent in enumerate(result.master_entities, start=2):
        in_my = "✓" if ({_normalize(ent.name), *(_normalize(t) for t in ent.lsi)} & my_names) else "—"
        row_fill = tier_fills.get(ent.tier) or (_GRAY_FILL if row_i % 2 == 0 else None)
        for col_i, val in enumerate([ent.name, "، ".join(ent.lsi[:15]), ent.cluster, ent.entity_type,
                                      tier_labels.get(ent.tier, "—"), ent.importance, f"{ent.salience:.2f}",
                                      in_my, ent.source_count, ent.mentions], start=1):
            cell = ws1.cell(row=row_i, column=col_i, value=safe_excel_value(val))
            cell.alignment = _RIGHT_ALIGN
            if row_fill:
                cell.fill = row_fill
    ws1.freeze_panes = "A2"
    ws1.auto_filter.ref = f"A1:J{max(2, len(result.master_entities) + 1)}"
    ws1.column_dimensions["A"].width = 30
    ws1.column_dimensions["B"].width = 40
    ws1.column_dimensions["C"].width = 24
    for col in ["D", "E", "F", "G", "H", "I", "J"]:
        ws1.column_dimensions[col].width = 14

    _write_clusters_sheet(wb, result)

    if result.my_site_clusters:
        _write_cluster_breakdown_sheet(wb, "كيانات موقعي", result.my_site_clusters, _GREEN_FILL)

    ws2 = wb.create_sheet("الفجوات الدلالية")
    ws2.right_to_left = True
    # Only corroborated gaps reach this sheet, so state the rule in the file
    # itself — a reader opening the export months later needs that context.
    quality = getattr(result, "quality", None)
    gap_header_row = 1
    if quality is not None and getattr(quality, "shared_topics", 0):
        note = ws2.cell(
            row=1, column=1,
            value=safe_excel_value(
                f"تغطية المحتوى: {quality.covered_topics} من {quality.shared_topics} "
                f"موضوعاً يتفق عليها أكثر من منافس ({round(quality.coverage_rate * 100)}%)"
                " — لا تُدرج هنا إلا الفجوات التي ذكرها أكثر من منافس مستقل."
            ),
        )
        note.font = _BOLD_DARK
        note.alignment = _RIGHT_ALIGN
        gap_header_row = 3
    _header_row(ws2, ["الكيان", "مرادفات LSI", "العنقود الدلالي", "النوع", "الأولوية",
                      "عدد المنافسين", "إجمالي التكرار", "درجة الثقة"], _ROSE_FILL,
                row=gap_header_row)
    priority_colors = {"critical": "FEE2E2", "high": "FEF3C7", "medium": "F0FDF4"}
    for row_i, gap in enumerate(result.semantic_gaps, start=gap_header_row + 1):
        row_fill = PatternFill("solid", fgColor=priority_colors.get(gap.priority, "FFFFFF"))
        for col_i, val in enumerate([gap.name, "، ".join(gap.lsi), gap.cluster,
                                      gap.entity_type, gap.priority,
                                      gap.competitor_count, gap.total_mentions,
                                      f"{gap.confidence:.2f}"], start=1):
            cell = ws2.cell(row=row_i, column=col_i, value=safe_excel_value(val))
            cell.alignment = _RIGHT_ALIGN
            cell.fill = row_fill
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 34
    ws2.column_dimensions["C"].width = 24
    for col in ["D", "E", "F", "G", "H"]:
        ws2.column_dimensions[col].width = 18

    if result.content_brief:
        ws3 = wb.create_sheet("خطة المحتوى")
        ws3.right_to_left = True
        _header_row(ws3, ["هيكل المقال المقترح"], _GREEN_FILL)
        ws3.cell(row=2, column=1,
                 value=safe_excel_value(result.content_brief)).alignment = Alignment(wrap_text=True)
        ws3.column_dimensions["A"].width = 80
        ws3.row_dimensions[2].height = 400

    _write_signals_sheet(wb, result)
    _write_report_sheet(wb, result)

    for comp in result.competitor_breakdowns:
        safe_name = re.sub(r"[\\/*?\[\]:]", "", comp.source_label).strip("'")[:28] or "Source"
        _write_cluster_breakdown_sheet(wb, safe_name, comp.clusters, _BLUE_FILL)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
