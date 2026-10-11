"""HTTP routes for extraction, entity analysis, and SERP synthesis."""

from __future__ import annotations

import concurrent.futures
import hmac
import csv
import io
import json
import logging
import re
import traceback
import threading
import uuid
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit
from werkzeug.utils import secure_filename

import time
from flask import Blueprint, Response, current_app, jsonify, request, send_from_directory, stream_with_context, send_file

from models.schemas import EntityItem, FullAnalysisResult, SemanticCluster, SourceAnalysis, SourceReport, UsageMetrics
from services.content_brief import generate_content_brief
from services.excel import build_excel_report
from utils.limiter import limiter
from services.extraction import (
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    ExtractionResult,
    extract_text_from_file,
    extract_text_from_url,
    extract_url,
)
from services.clustering import build_semantic_clusters, relabel_entities
from services.semantic import build_master_entities
from services.claude import (
    extract_entities_for_source,
    start_usage_tracking,
    get_current_usage,
    add_to_current_usage,
    resolve_model,
    is_mistral_engine,
    is_gemini_engine,
)
from services.semantic import _normalize, compute_semantic_gaps
from services.quality import annotate_gap_confidence, annotate_source_analysis, build_analysis_quality
from services.serp import analyze_serp_data
from services.verification import verify_gaps
from services.temp_store import (
    read_editor_content,
    read_evidence,
    resolve_file,
    write_bytes,
    write_editor_content,
    write_evidence,
)
import config

import gc
logger = logging.getLogger(config.LOGGER_NAME)
bp = Blueprint("main", __name__)
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_JOB_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")

from services.temp_store import (
    active_job_count,
    register_job,
    unregister_job,
    cancel_job as do_cancel_job,
    is_job_cancelled
)

class AnalysisCancelled(RuntimeError):
    pass

def _register_cancel_event(requested_job_id: str) -> tuple[str, str]:
    cancel_token = uuid.uuid4().hex
    job_id = requested_job_id if _JOB_RE.fullmatch(requested_job_id) else uuid.uuid4().hex
    register_job(job_id, cancel_token)
    return job_id, cancel_token

def _raise_if_cancelled(job_id: str, language: str) -> None:
    if is_job_cancelled(job_id):
        raise AnalysisCancelled(_m("analysis_cancelled", language))

# Bilingual user-facing strings for this module. Every message the client can
# see (validation errors, SSE progress narration, generic failure text) is
# picked from here based on the request's own `language` field instead of
# always defaulting to Arabic, so an English-mode session never sees stray
# Arabic text.
_MESSAGES: dict[str, tuple[str, str]] = {
    "invalid_model": ("اسم الموديل غير صالح.", "Invalid model name."),
    "unsupported_language": ("اللغة المطلوبة غير مدعومة.", "The requested language is not supported."),
    "api_key_required": ("لم يتم ضبط مفتاح API على الخادم. أضف ANTHROPIC_API_KEY في ملف .env ثم أعد تشغيل التطبيق.",
                         "No API key is configured on the server. Set ANTHROPIC_API_KEY in .env and restart the app."),
    "need_one_input": ("يرجى رفع ملف أو إدخال رابط واحد على الأقل.", "Please upload a file or enter at least one URL."),
    "my_site_extract_empty": ("لم يتم استخراج نص صالح من محتوى موقعك.", "No valid text could be extracted from your site's content."),
    "no_valid_source": ("لم يتم استخراج أي مصدر صالح للتحليل.", "No valid source could be extracted for analysis."),
    "my_site_analysis_failed": ("فشل تحليل موقعك: {0}", "Failed to analyze your site: {0}"),
    "unexpected_analyze_error": ("حدث خطأ غير متوقع أثناء التحليل.", "An unexpected error occurred during analysis."),
    "need_csv_file": ("يرجى رفع ملف CSV أو ملفات HTML للمنافسين.",
                      "Please upload a CSV file or competitor HTML files."),
    "serp_files_limit": ("الحد الأقصى {0} ملف للمنافسين.", "The competitor file limit is {0}."),
    "no_valid_serp_urls": ("لم يتم العثور على روابط صالحة في ملف CSV.", "No valid URLs were found in the CSV file."),
    "no_valid_serp_content": ("تعذر استخراج محتوى صالح من جميع روابط SERP.", "Could not extract valid content from any of the SERP URLs."),
    "unexpected_serp_error": ("حدث خطأ غير متوقع أثناء تحليل SERP.", "An unexpected error occurred during SERP analysis."),
    "progress_extract_start": ("استخراج النصوص والتحقق من المدخلات...", "Extracting text and validating inputs..."),
    "progress_my_url_extracting": ("ما زال استخراج رابط موقعك مستمراً...", "Still extracting your site's URL..."),
    "progress_competitors_extracting": ("ما زال استخراج روابط المنافسين مستمراً...", "Still extracting competitor URLs..."),
    "progress_extracted_count": ("تم استخراج {0}/{1}", "Extracted {0}/{1}"),
    "progress_extract_failed": ("تعذر استخراج {0} وتم تجاهله", "Could not extract {0}, skipped"),
    "progress_extract_blocked": ("محجوب: {0} يمنع القراءة الآلية ({1}) — تم استبعاده", "Blocked: {0} refuses automated reading ({1}) — excluded"),
    "progress_extract_partial": ("قراءة جزئية لـ {0} ({1} حرف)", "Partial read of {0} ({1} chars)"),
    "progress_clustering": ("بناء العناقيد الدلالية الموحّدة وتحليل فجواتها...", "Building unified semantic clusters and cluster gaps..."),
    "my_site_blocked": ("رابط موقعك محجوب عن القراءة الآلية ({0}). ارفع المقال كملف HTML/DOCX بدلاً منه.",
                        "Your URL refuses automated reading ({0}). Upload the article as an HTML/DOCX file instead."),
    "progress_ai_analyzing": ("تحليل {0} مصدر بالذكاء الاصطناعي...", "Analyzing {0} source(s) with AI..."),
    "progress_ai_still_analyzing": ("الذكاء الاصطناعي ما زال يحلل المصادر...", "AI is still analyzing the sources..."),
    "progress_source_analyzed": ("تم تحليل {0} ({1}/{2})", "Analyzed {0} ({1}/{2})"),
    "progress_source_failed": ("تعذر تحليل {0} وتم تجاهله", "Could not analyze {0}, skipped"),
    "progress_verifying_gaps": ("التحقق الإضافي من الفجوات...", "Extra verification of the gaps..."),
    "progress_verifying_still": ("ما زال التحقق من الفجوات مستمراً...", "Still verifying the gaps..."),
    "progress_building_brief": ("إنشاء خطة المحتوى...", "Generating the content plan..."),
    "progress_building_brief_still": ("ما زال إنشاء خطة المحتوى مستمراً...", "Still generating the content plan..."),
    "progress_building_excel": ("إنشاء تقرير Excel...", "Building the Excel report..."),
    "progress_analyze_done": ("اكتمل التحليل بنجاح", "Analysis completed successfully"),
    "progress_serp_extracting": ("تجهيز {1} مصدر ({0} رابط يحتاج جلب)...",
                                 "Preparing {1} source(s) ({0} URL(s) to fetch)..."),
    "progress_serp_extracting_still": ("ما زال استخراج صفحات SERP مستمراً...", "Still extracting SERP pages..."),
    "progress_serp_processed": ("تمت معالجة {0}/{1}", "Processed {0}/{1}"),
    "progress_serp_analyzing": ("تحليل تغطية المنافسين وإنشاء الهيكل...", "Analyzing competitor coverage and building the outline..."),
    "progress_serp_analyzing_still": ("ما زال تحليل SERP مستمراً...", "Still analyzing the SERP..."),
    "progress_serp_full_scan": ("تم مسح جميع المنافسين محلياً؛ تجهيز أدلة الذكاء الاصطناعي...", "All competitors were fully scanned locally; preparing AI evidence..."),
    "progress_serp_profile_batches": ("تحليل دفعة المنافسين {0}/{1}...", "Analyzing competitor batch {0}/{1}..."),
    "progress_serp_strategy": ("بناء الاستراتيجية وقائمة التغطية من جميع المنافسين...", "Building the strategy and coverage checklist from all competitors..."),
    "progress_serp_outline": ("إنشاء الهيكلة النهائية الشاملة...", "Generating the complete final architecture..."),
    "progress_serp_repair": ("إكمال الهيكلة لأنها خرجت أقل من العمق المطلوب...", "Completing the architecture because it was below the required depth..."),
    "progress_serp_building_word": ("توليد ملف Word...", "Generating the Word file..."),
    "progress_serp_done": ("اكتمل تحليل SERP", "SERP analysis completed"),
    "analysis_cancelled": ("تم إلغاء التحليل بواسطة المستخدم.", "Analysis was cancelled by the user."),
    "job_capacity": ("الخادم مشغول حالياً بالحد الأقصى من التحليلات المتزامنة. حاول بعد اكتمال أحدها.", "The server is currently at its maximum concurrent-analysis capacity. Try again after another job finishes."),
}


def _m(key: str, language: str, *args) -> str:
    ar, en = _MESSAGES[key]
    template = en if language == "en" else ar
    return template.format(*args) if args else template


class _NamedBytesIO(io.BytesIO):
    """Seekable in-memory upload retaining the original filename."""

    def __init__(self, data: bytes, filename: str):
        super().__init__(data)
        self.filename = filename


@bp.route("/")
def serve_index():
    return send_from_directory(current_app.static_folder or "static", "index.html")


@bp.route("/health")
@limiter.exempt
def health():
    return jsonify({
        "status": "ok",
        "version": config.APP_VERSION,
        "active_jobs": active_job_count(),
        "capacity": config.MAX_ACTIVE_JOBS,
    })


@bp.route("/cancel/<job_id>", methods=["POST"])
@limiter.limit("30 per minute")
def cancel_job(job_id: str):
    if not _JOB_RE.fullmatch(job_id):
        return jsonify({"cancelled": False}), 400
    supplied_token = request.headers.get("X-Cancel-Token", "").strip() or request.form.get("token", "").strip()
    if not supplied_token:
        return jsonify({"cancelled": False}), 403
    if do_cancel_job(job_id, supplied_token):
        return jsonify({"cancelled": True})
    return jsonify({"cancelled": False}), 404


@bp.route("/download/<filename>")
@limiter.limit("20 per minute")
def download_file(filename):
    filename = secure_filename(filename)
    path = resolve_file(filename)
    if path is None:
        return "File not found or expired", 404
    name = "Semantic_SEO_Report.xlsx" if filename.endswith(".xlsx") else "SERP_Report.docx"
    return send_file(path, as_attachment=True, download_name=name)


@bp.route("/evidence/<token>")
@limiter.limit("30 per minute")
def evidence_lookup(token):
    sources = read_evidence(token)
    if sources is None:
        return jsonify({"error": "Evidence expired or not found"}), 404
    entity = request.args.get("entity", "").strip()[:200]
    terms = [value.strip()[:200] for value in request.args.getlist("term") if value.strip()]
    if entity and entity not in terms:
        terms.insert(0, entity)
    terms = list(dict.fromkeys(terms))[:12]
    if not terms:
        return jsonify({"error": "Entity is required"}), 400

    results = []
    for label, text in sources.items():
        haystack = str(text or "")
        folded = haystack.casefold()
        snippets = []
        seen_windows = set()
        for term in terms:
            needle = term.casefold()
            start = 0
            while len(snippets) < 5:
                index = folded.find(needle, start)
                if index < 0:
                    break
                left = max(0, index - 240)
                right = min(len(haystack), index + len(term) + 240)
                window_key = (left // 80, right // 80)
                if window_key not in seen_windows:
                    seen_windows.add(window_key)
                    snippets.append({
                        "text": haystack[left:right].strip(),
                        "match_start": index - left,
                        "match_length": len(term),
                        "matched_term": term,
                    })
                start = index + max(1, len(term))
            if len(snippets) >= 5:
                break
        if snippets:
            results.append({"source": label, "snippets": snippets})
    return jsonify({"entity": entity or terms[0], "results": results})


@bp.route("/editor-content", methods=["POST"])
@limiter.limit("20 per minute")
def editor_content():
    """Return only the analysis request's short-lived editable source.

    The token is created in the same analysis response and expires with the
    temporary store. It is deliberately separate from the evidence token so a
    frontend bug cannot request full competitor documents through this route.
    """

    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token", "")).strip()
    content = read_editor_content(token)
    if content is None:
        response = jsonify({"error": "Editor source expired or not found"})
        response.status_code = 404
    else:
        response = jsonify({"content": content})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "windows-1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _looks_like_url(value: str) -> bool:
    value = (value or "").strip().lower()
    return value.startswith("http://") or value.startswith("https://")


def _marker_value(extracted: str, marker: str) -> str:
    """Pull a [Title]/[Description] line out of the structured extraction."""
    match = re.search(rf"^\[{marker}\]\s*:\s*(.+)$", extracted or "", re.M)
    return match.group(1).strip()[:300] if match else ""


def _title_from_extracted(extracted: str) -> str:
    title = _marker_value(extracted, "Title")
    if title:
        return title
    heading = re.search(r"^\[H1\]\s*:\s*(.+)$", extracted or "", re.M)
    return heading.group(1).strip()[:300] if heading else ""


def _description_from_extracted(extracted: str) -> str:
    return _marker_value(extracted, "Description")[:600]


def _parse_serp_csv(raw: bytes, limit: int) -> list[dict[str, str]]:
    rows = list(csv.reader(io.StringIO(_decode_csv(raw))))
    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    aliases = {
        "url": {"url", "link", "الرابط", "رابط", "page url"},
        "title": {"title", "العنوان", "عنوان"},
        "desc": {"description", "desc", "meta description", "الوصف", "وصف"},
    }

    def find_index(names: set[str]) -> int | None:
        for index, value in enumerate(header):
            if value in names:
                return index
        return None

    url_index = find_index(aliases["url"])
    title_index = find_index(aliases["title"])
    desc_index = find_index(aliases["desc"])
    data_rows = rows[1:] if url_index is not None else rows

    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in data_rows:
        if not row:
            continue
        if url_index is None:
            candidate_index = next((i for i, cell in enumerate(row) if _looks_like_url(cell)), None)
        else:
            candidate_index = url_index if url_index < len(row) else None
        if candidate_index is None:
            continue
        url = row[candidate_index].strip()
        if not _looks_like_url(url) or url in seen:
            continue
        seen.add(url)
        title = row[title_index].strip() if title_index is not None and title_index < len(row) else ""
        desc = row[desc_index].strip() if desc_index is not None and desc_index < len(row) else ""
        parsed.append({"url": url, "title": title, "desc": desc, "content": ""})
        if len(parsed) >= limit:
            break
    return parsed


def _parse_competitor_inputs() -> tuple[list, list[str]]:
    files = []
    urls: list[str] = []
    for upload in (item for item in request.files.getlist("competitors") if item.filename):
        if upload.filename and upload.filename.lower().endswith(".csv"):
            try:
                urls.extend(item["url"] for item in _parse_serp_csv(upload.read(), config.MAX_COMPETITORS))
            except Exception as exc:
                logger.warning("Failed to parse competitor CSV %s: %s", upload.filename, exc)
        else:
            files.append(upload)
    urls.extend(value.strip() for value in request.form.getlist("comp_urls") if value.strip())
    urls = list(dict.fromkeys(urls))
    files = files[:config.MAX_COMPETITORS]
    urls = urls[:max(0, config.MAX_COMPETITORS - len(files))]
    return files, urls


def _request_language() -> str:
    language = request.form.get("language", "ar").strip().lower()
    return language if language in {"ar", "en"} else "ar"


def _request_content_language() -> str:
    """Entity Tool content language: any supported language (SERP stays ar/en)."""
    language = request.form.get("language", "ar").strip().lower()
    return language if language in config.SUPPORTED_CONTENT_LANGUAGES else "ar"


def _request_ui_language() -> str:
    """The interface's own display language (the navbar toggle), kept
    separate from `language` — the language the user picked for the
    *generated content* (entities, gaps, SERP structure). Status/progress/
    error text is UI chrome and should always follow the interface language,
    not whatever content language happens to be selected, otherwise an
    English-UI session running an Arabic-content SERP analysis sees English
    labels but Arabic progress text. Falls back to `language` for older
    clients that don't send this field."""
    ui_language = request.form.get("ui_language", "").strip().lower()
    if ui_language in {"ar", "en"}:
        return ui_language
    return _request_language()


def _request_settings() -> tuple[str, str, bool, bool, bool, str, str]:
    language = _request_content_language()
    ui_language = _request_ui_language()
    model = request.form.get("model", config.CLAUDE_DEFAULT_MODEL).strip()
    if not _MODEL_RE.fullmatch(model):
        raise ValueError(_m("invalid_model", ui_language))
    if request.form.get("language", "ar").strip().lower() not in config.SUPPORTED_CONTENT_LANGUAGES:
        raise ValueError(_m("unsupported_language", ui_language))
    generate_brief = False
    verify = True
    deep_read = True
    keyword = request.form.get("keyword", "").strip()[:200]
    country = request.form.get("target_country", "").strip()[:120]
    return model, language, generate_brief, verify, deep_read, keyword, country


def _label_for_url(index: int, url: str, lang: str = "ar") -> str:
    host = urlsplit(url).hostname or ("URL" if lang == "en" else "رابط")
    prefix = "Competitor" if lang == "en" else "المنافس"
    return f"{prefix} {index} — {host}"[:240]


def _run_with_heartbeat(func: Callable, *args, heartbeat_message: str | Callable[[], str] = "المعالجة مستمرة...",
                        heartbeat_progress: Callable[[], dict] | None = None,
                        cancel_check: Callable[[], bool] | None = None,
                        cancel_message: str = "تم إلغاء التحليل بواسطة المستخدم.",
                        heartbeat_seconds: int = 15):
    """Run one blocking operation while keeping the client SSE connection alive."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, *args)
    try:
        while True:
            if cancel_check and cancel_check():
                future.cancel()
                raise AnalysisCancelled(cancel_message)
            try:
                return future.result(timeout=max(3, heartbeat_seconds))
            except concurrent.futures.TimeoutError:
                message = heartbeat_message() if callable(heartbeat_message) else heartbeat_message
                if heartbeat_progress is not None:
                    payload = dict(heartbeat_progress() or {})
                    payload.setdefault("message", message)
                    payload.setdefault("ts", int(time.time()))
                    yield _sse("progress", payload)
                else:
                    yield _sse("ping", {"message": message, "ts": int(time.time())})
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _dedupe_entities(entities: Iterable[EntityItem]) -> list[EntityItem]:
    result: dict[str, EntityItem] = {}
    for entity in entities:
        key = _normalize(entity.name)
        if not key:
            continue
        current = result.get(key)
        if current is None:
            result[key] = entity.model_copy(deep=True)
            continue
        combined_mentions = min(100_000, current.mentions + entity.mentions)
        combined_lsi = list(dict.fromkeys([*current.lsi, *entity.lsi]))[:50]
        best_evidence = entity if entity.evidence_score > current.evidence_score else current
        if entity.salience > current.salience:
            replacement = entity.model_copy(deep=True)
            replacement.mentions = combined_mentions
            replacement.lsi = combined_lsi
            replacement.evidence_score = best_evidence.evidence_score
            replacement.evidence_level = best_evidence.evidence_level
            result[key] = replacement
        else:
            current.mentions = combined_mentions
            current.lsi = combined_lsi
            current.evidence_score = best_evidence.evidence_score
            current.evidence_level = best_evidence.evidence_level
    return list(result.values())


def _file_result(file_obj, deep_read: bool, language: str) -> ExtractionResult:
    """Uploaded files go through the same status contract as URLs."""
    name = str(getattr(file_obj, "filename", "") or "file")
    try:
        text = extract_text_from_file(file_obj, deep_read, language)
    except Exception as exc:
        return ExtractionResult(url=name, status=STATUS_FAILED, tier="file", reason=str(exc)[:500])
    status = "ok" if len(text.strip()) >= config.MIN_ACCEPTABLE_EXTRACTED_CHARS else (STATUS_PARTIAL if text.strip() else STATUS_FAILED)
    return ExtractionResult(url=name, text=text, status=status, tier="file", chars=len(text),
                            headings=len(re.findall(r"^\[H[1-6]\]\s*:", text, re.M)))


def _url_result(url: str, deep_read: bool, language: str) -> ExtractionResult:
    try:
        return extract_url(url, deep_read, language)
    except Exception as exc:  # invalid / non-public URL
        return ExtractionResult(url=url, status=STATUS_FAILED, reason=str(exc)[:500])


def _report(label: str, result: ExtractionResult) -> SourceReport:
    return SourceReport(
        label=label[:240] or "source",
        url=(result.url or "")[:2048],
        status=result.status if result.status in {"ok", "partial", "blocked", "failed"} else "failed",
        tier=(result.tier or "")[:24],
        chars=max(0, int(result.chars or 0)),
        headings=max(0, int(result.headings or 0)),
        provider=(result.provider or "")[:60],
        reason=(result.reason or "")[:600],
    )


def _regroup_by_cluster(entities: list[EntityItem]) -> list[SemanticCluster]:
    grouped: dict[str, list[EntityItem]] = {}
    for entity in entities:
        grouped.setdefault(entity.cluster or "غير مصنف", []).append(entity)
    clusters = []
    for name, members in grouped.items():
        members.sort(key=lambda item: (-item.salience, item.name))
        clusters.append(SemanticCluster(cluster_name=name, entities=members[:800]))
    clusters.sort(key=lambda cluster: -sum(entity.salience for entity in cluster.entities))
    return clusters[:200]


@bp.route("/preview", methods=["POST"])
@limiter.limit("10 per minute")
def preview():
    my_file = request.files.get("my_article")
    my_url = request.form.get("my_url", "").strip()
    competitor_files, competitor_urls = _parse_competitor_inputs()
    ui_language = _request_ui_language()
    deep_read = request.form.get("deep_read", "false").strip().lower() == "true"

    tasks: list[tuple[int, str, Callable, tuple]] = []
    order = 0
    if my_url:
        tasks.append((order, "My Site (URL)" if ui_language == "en" else "موقعي (رابط)", _url_result,
                      (my_url, deep_read, ui_language)))
        order += 1
    elif my_file and my_file.filename:
        tasks.append((order, "My Site (File)" if ui_language == "en" else "موقعي (ملف)", _file_result,
                      (_NamedBytesIO(my_file.read(), my_file.filename), deep_read, ui_language)))
        order += 1

    for index, upload in enumerate(competitor_files, start=1):
        label = (f"Competitor {index} (File: {upload.filename[:100]})" if ui_language == "en"
                 else f"المنافس {index} (ملف: {upload.filename[:100]})")
        tasks.append((order, label, _file_result,
                      (_NamedBytesIO(upload.read(), upload.filename), deep_read, ui_language)))
        order += 1
    start_index = len(competitor_files) + 1
    for offset, url in enumerate(competitor_urls):
        index = start_index + offset
        tasks.append((order, _label_for_url(index, url, ui_language), _url_result, (url, deep_read, ui_language)))
        order += 1

    results: list[tuple[int, dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(config.MAX_FETCH_WORKERS, max(len(tasks), 1))) as executor:
        future_map = {
            executor.submit(func, *args): (position, label)
            for position, label, func, args in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            position, label = future_map[future]
            try:
                result = future.result()
                entry = {"label": label, "status": result.status, "tier": result.tier,
                         "provider": result.provider, "chars": result.chars, "headings": result.headings}
                if result.status in (STATUS_BLOCKED, STATUS_FAILED):
                    entry["error"] = result.reason
                else:
                    entry["text"] = result.text
                    if result.reason:
                        entry["note"] = result.reason
                results.append((position, entry))
            except Exception as exc:
                results.append((position, {"label": label, "status": STATUS_FAILED, "error": str(exc)}))

    results.sort(key=lambda item: item[0])
    return jsonify({"sources": [item[1] for item in results]})


def _server_api_key(model: str = "") -> str:
    """The API key comes ONLY from the server's .env.

    Any ``api_key`` field a client still sends is ignored on purpose: keys
    never travel through the browser, and nobody can make the server spend
    on a different account. The key follows the selected engine (a Gemini
    model uses GEMINI_API_KEY, a Mistral model MISTRAL_API_KEY).
    """
    selected = (model or "").strip().lower()
    if "gemini" in selected and getattr(config, "GEMINI_API_KEY", ""):
        return config.GEMINI_API_KEY.strip()
    if (selected.startswith(("mistral", "ministral", "open-mistral")) or "codestral" in selected) and config.MISTRAL_API_KEY:
        return config.MISTRAL_API_KEY.strip()
    return (
        config.ANTHROPIC_API_KEY
        or getattr(config, "GEMINI_API_KEY", "")
        or config.MISTRAL_API_KEY
        or ""
    ).strip()


@bp.route("/analyze", methods=["POST"])
@limiter.limit("5 per minute")
def analyze():
    started = time.monotonic()
    ui_language = _request_ui_language()
    api_key = _server_api_key(request.form.get("model", ""))
    if not api_key:
        return jsonify({"error": _m("api_key_required", ui_language)}), 400

    try:
        # `language` here is the *content* language for AI-generated output
        # (entities, gaps, brief) — kept separate from `ui_language` above,
        # which drives status/progress/error text.
        model, language, generate_brief, verify_flag, deep_read, keyword, country = _request_settings()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    is_gemini = is_gemini_engine(model, api_key)
    is_mistral = is_mistral_engine(model, api_key)
    if is_gemini:
        api_url = getattr(config, "GEMINI_API_URL", "")
    elif is_mistral:
        api_url = config.MISTRAL_API_URL
    else:
        api_url = config.ANTHROPIC_API_URL

    my_file = request.files.get("my_article")
    my_url = request.form.get("my_url", "").strip()
    competitor_files, competitor_urls = _parse_competitor_inputs()
    has_my_input = bool((my_file and my_file.filename) or my_url)
    if not has_my_input and not competitor_files and not competitor_urls:
        return jsonify({"error": _m("need_one_input", ui_language)}), 400

    requested_job_id = request.form.get("job_id", "").strip()
    try:
        job_id, cancel_token = _register_cancel_event(requested_job_id)
    except RuntimeError:
        return jsonify({"error": _m("job_capacity", ui_language), "code": "job_capacity"}), 503

    my_file_payload = (my_file.filename, my_file.read()) if my_file and my_file.filename else None
    competitor_payloads = [(upload.filename, upload.read()) for upload in competitor_files]

    def event_stream():
        def progress(percent: int, message: str):
            return _sse("progress", {"pct": percent, "message": message})

        try:
            start_usage_tracking()
            yield _sse("job", {"job_id": job_id, "cancel_token": cancel_token})
            _raise_if_cancelled(job_id, ui_language)
            yield progress(5, _m("progress_extract_start", ui_language))
            my_text = ""
            extraction_report: list[SourceReport] = []
            partial_sources: list[str] = []
            my_site_label = "My Site" if ui_language == "en" else "موقعي"
            my_result: ExtractionResult | None = None
            if my_url:
                my_result = yield from _run_with_heartbeat(
                    _url_result, my_url, deep_read, ui_language,
                    heartbeat_message=_m("progress_my_url_extracting", ui_language),
                    cancel_check=lambda: is_job_cancelled(job_id),
                    cancel_message=_m("analysis_cancelled", ui_language),
                )
            elif my_file_payload:
                filename, raw = my_file_payload
                my_result = _file_result(_NamedBytesIO(raw, filename), deep_read, ui_language)
            if my_result is not None:
                extraction_report.append(_report(my_site_label, my_result))
                if my_result.status == STATUS_BLOCKED:
                    raise ValueError(_m("my_site_blocked", ui_language, my_result.provider or "WAF"))
                if my_result.status == STATUS_FAILED:
                    raise ValueError(my_result.reason or _m("my_site_extract_empty", ui_language))
                my_text = my_result.text
                if my_result.status == STATUS_PARTIAL:
                    partial_sources.append(my_site_label)
            if has_my_input and not my_text.strip():
                raise ValueError(_m("my_site_extract_empty", ui_language))

            source_tasks: list[tuple[int, str, Callable, tuple]] = []
            for index, (filename, raw) in enumerate(competitor_payloads, start=1):
                label = (f"Competitor {index} — {filename[:100]}" if ui_language == "en"
                         else f"المنافس {index} — {filename[:100]}")
                source_tasks.append((index, label, _file_result,
                                     (_NamedBytesIO(raw, filename), deep_read, ui_language)))
            start_index = len(competitor_payloads) + 1
            for offset, url in enumerate(competitor_urls):
                index = start_index + offset
                source_tasks.append((index, _label_for_url(index, url, ui_language), _url_result,
                                     (url, deep_read, ui_language)))

            extracted: dict[int, tuple[str, str]] = {}
            # Every source the user submitted that did not make it into the
            # report, so the UI can say "7 of 8" instead of quietly showing 7.
            failed_sources: list[str] = []
            requested_sources = len(source_tasks)
            if source_tasks:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(config.MAX_FETCH_WORKERS, len(source_tasks))
                ) as executor:
                    pending = {
                        executor.submit(func, *args): (index, label)
                        for index, label, func, args in source_tasks
                    }
                    completed = 0
                    while pending:
                        done, _ = concurrent.futures.wait(
                            pending, timeout=15, return_when=concurrent.futures.FIRST_COMPLETED
                        )
                        if not done:
                            yield _sse("ping", {"message": _m("progress_competitors_extracting", ui_language)})
                            continue
                        for future in done:
                            index, label = pending.pop(future)
                            completed += 1
                            try:
                                result = future.result()
                                extraction_report.append(_report(label, result))
                                text = result.text if result.status not in (STATUS_BLOCKED, STATUS_FAILED) else ""
                                if result.status == STATUS_BLOCKED:
                                    failed_sources.append(f"{label} — {'Blocked' if ui_language == 'en' else 'محجوب'} ({result.provider})")
                                    yield progress(
                                        5 + int(10 * completed / len(source_tasks)),
                                        _m("progress_extract_blocked", ui_language, label, result.provider or "WAF"),
                                    )
                                elif text.strip():
                                    extracted[index] = (label, text)
                                    if result.status == STATUS_PARTIAL:
                                        partial_sources.append(label)
                                        message = _m("progress_extract_partial", ui_language, label, result.chars)
                                    else:
                                        message = _m("progress_extracted_count", ui_language, completed, len(source_tasks))
                                    yield progress(5 + int(10 * completed / len(source_tasks)), message)
                                else:
                                    # An empty extraction used to vanish without a
                                    # trace, so "8 competitors in, 7 in the report"
                                    # had no visible explanation anywhere.
                                    failed_sources.append(label)
                                    yield progress(
                                        5 + int(10 * completed / len(source_tasks)),
                                        _m("progress_extract_failed", ui_language, label),
                                    )
                            except Exception as exc:
                                logger.warning("Skipping competitor %s: %s", label, exc)
                                failed_sources.append(label)
                                yield progress(
                                    5 + int(10 * completed / len(source_tasks)),
                                    _m("progress_extract_failed", ui_language, label),
                                )

            competitor_texts = [extracted[index] for index in sorted(extracted)]
            sources = ([((0), my_site_label, my_text)] if my_text else []) + [
                (index + 1, label, text) for index, (label, text) in enumerate(competitor_texts)
            ]
            if not sources:
                raise ValueError(_m("no_valid_source", ui_language))

            yield progress(15, _m("progress_ai_analyzing", ui_language, len(sources)))
            my_analysis: Optional[SourceAnalysis] = None
            competitor_results: dict[int, SourceAnalysis] = {}
            source_workers = 1 if is_mistral else min(config.MAX_SOURCE_WORKERS, len(sources))
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=source_workers
            ) as executor:
                source_urls = {item.label: item.url for item in extraction_report}
                pending = {
                    executor.submit(
                        extract_entities_for_source, label, text, language, model,
                        api_key, api_url, keyword, country, lambda: is_job_cancelled(job_id),
                        source_url=source_urls.get(label, ""),
                    ): (order, label, text)
                    for order, label, text in sources
                }
                completed = 0
                while pending:
                    _raise_if_cancelled(job_id, ui_language)
                    done, _ = concurrent.futures.wait(
                        pending, timeout=15, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    if not done:
                        yield _sse("ping", {"message": _m("progress_ai_still_analyzing", ui_language)})
                        continue
                    for future in done:
                        order, label, source_text = pending.pop(future)
                        completed += 1
                        percent = 15 + int(50 * completed / len(sources))
                        try:
                            result = annotate_source_analysis(future.result(), source_text)
                            if hasattr(result, "usage") and result.usage:
                                add_to_current_usage(result.usage)
                            if order == 0 and label == my_site_label:
                                my_analysis = result
                            else:
                                competitor_results[order] = result
                            yield progress(percent, _m("progress_source_analyzed", ui_language, label, completed, len(sources)))
                        except Exception as exc:
                            if order == 0 and label == my_site_label:
                                raise RuntimeError(_m("my_site_analysis_failed", ui_language, exc)) from exc
                            logger.warning("Competitor AI analysis failed for %s: %s", label, exc)
                            failed_sources.append(label)
                            yield progress(percent, _m("progress_source_failed", ui_language, label))

            _raise_if_cancelled(job_id, ui_language)
            competitor_analyses = [competitor_results[index] for index in sorted(competitor_results)]
            my_entities = list(my_analysis.raw_entities) if my_analysis else []
            gaps: list = []
            if competitor_analyses:
                gaps, covered_missed = compute_semantic_gaps(my_entities, competitor_analyses, my_text)
                if my_analysis:
                    # These are entities the "my site" AI call missed but that
                    # are demonstrably present in the client's own text. They
                    # are real, so they count — but they belong to no AI
                    # cluster, and without one they showed up in the headline
                    # number while being absent from the Excel breakdown.
                    recovered_label = ("Recovered from your text" if ui_language == "en"
                                       else "مستخرج من نص موقعك")
                    recovered = [entity for entity in covered_missed
                                 if _normalize(entity.name) not in {_normalize(e.name) for e in my_entities}]
                    for entity in recovered:
                        entity.cluster = recovered_label
                    my_entities = _dedupe_entities([*my_entities, *covered_missed])
                    if recovered and my_analysis:
                        my_analysis.clusters.append(
                            SemanticCluster(cluster_name=recovered_label, entities=recovered)
                        )
            if not my_analysis:
                gaps = []

            master_entities = build_master_entities(my_entities, competitor_analyses)
            gc.collect()

            yield progress(72, _m("progress_clustering", ui_language))
            semantic_clusters = []
            try:
                semantic_clusters, cluster_names = build_semantic_clusters(
                    my_analysis, competitor_analyses, my_text if my_analysis else "")
                relabel_entities(master_entities, cluster_names)
                relabel_entities(my_entities, cluster_names)
                for gap in gaps:
                    label = cluster_names.get(_normalize(gap.name))
                    if label:
                        gap.cluster = label
            except Exception as exc:  # clustering must never sink the whole report
                logger.warning("Semantic clustering failed: %s", exc, exc_info=True)
                cluster_names = {}

            topic = keyword or (my_analysis.main_topic if my_analysis and my_analysis.main_topic else "") or (
                my_analysis.clusters[0].cluster_name if my_analysis and my_analysis.clusters else "")
            if verify_flag and gaps:
                yield progress(76, _m("progress_verifying_gaps", ui_language))
                gaps = yield from _run_with_heartbeat(
                    verify_gaps, gaps, my_text, topic, "ar" if language == "ar" else "en", model, api_key, api_url,
                    heartbeat_message=_m("progress_verifying_still", ui_language),
                    cancel_check=lambda: is_job_cancelled(job_id),
                    cancel_message=_m("analysis_cancelled", ui_language),
                )

            gaps = annotate_gap_confidence(gaps, len(competitor_analyses))
            for gap in gaps:
                label = cluster_names.get(_normalize(gap.name))
                if label:
                    gap.cluster = label

            brief = ""
            if generate_brief and (gaps or master_entities):
                yield progress(84, _m("progress_building_brief", ui_language))
                brief = yield from _run_with_heartbeat(
                    generate_content_brief, gaps, master_entities, bool(my_analysis), topic,
                    language, model, api_key, api_url, keyword, country,
                    heartbeat_message=_m("progress_building_brief_still", ui_language),
                    cancel_check=lambda: is_job_cancelled(job_id),
                    cancel_message=_m("analysis_cancelled", ui_language),
                )

            _raise_if_cancelled(job_id, ui_language)
            yield progress(92, _m("progress_building_excel", ui_language))
            quality = build_analysis_quality(
                my_analysis,
                competitor_analyses,
                gaps,
                len(my_text) + sum(len(text) for _label, text in competitor_texts),
                ui_language,
                my_text,
                requested_sources=requested_sources,
                failed_sources=failed_sources,
                partial_sources=partial_sources,
                cluster_gaps=sum(1 for cluster in semantic_clusters if cluster.is_gap),
            )
            usage_metrics = get_current_usage()
            if not isinstance(usage_metrics, UsageMetrics):
                usage_metrics = UsageMetrics(**usage_metrics)
            result = FullAnalysisResult(
                master_entities=master_entities,
                my_site_entities=my_entities,
                my_site_clusters=_regroup_by_cluster(my_entities) if my_analysis else [],
                competitor_breakdowns=competitor_analyses,
                semantic_gaps=gaps,
                content_brief=brief,
                has_my_article=bool(my_analysis),
                has_competitors=bool(competitor_analyses),
                quality=quality,
                usage_metrics=usage_metrics,
                semantic_clusters=semantic_clusters,
                extraction_report=extraction_report,
                my_site_signals=list(my_analysis.signal_entities) if my_analysis else [],
            )

            try:
                excel_bytes = build_excel_report(result)
                token, _path = write_bytes("report", ".xlsx", excel_bytes)
                excel_url = f"/download/{token}"
            except Exception as exc:
                logger.warning("Excel build failed: %s", exc)
                excel_url = ""

            payload = result.model_dump(by_alias=True)
            payload["excel_file"] = excel_url
            payload["editor_content_token"] = write_editor_content(my_text) if my_text else ""
            
            raw_texts_map = {}
            if my_text:
                raw_texts_map[my_site_label] = my_text
            for label, text in competitor_texts:
                raw_texts_map[label] = text
            payload["evidence_token"] = write_evidence(raw_texts_map) if raw_texts_map else ""
            payload["processing_seconds"] = round(time.monotonic() - started, 2)
            payload["analysis_version"] = config.APP_VERSION

            logger.info("POST /analyze finished in %.2fs", time.monotonic() - started)
            
            del raw_texts_map, competitor_texts, my_text
            gc.collect()
            
            yield progress(100, _m("progress_analyze_done", ui_language))
            yield _sse("done", payload)
        except AnalysisCancelled as exc:
            logger.info("Analysis job %s cancelled", job_id)
            yield _sse("error", {"error": str(exc), "cancelled": True})
        except Exception as exc:
            # Determine the real exception (unwrap UnicodeEncodeError from
            # a failed logger.error call on Windows cp1252 streams).
            _orig_exc = exc
            _orig_type = type(exc)
            _tb = traceback.format_exc()
            # Safe logging: encode to ascii so the logger never throws
            try:
                _safe = repr(str(exc)).encode("ascii", errors="replace").decode("ascii")
                logger.error("Unhandled /analyze error [%s]: %s", _orig_type.__name__, _safe)
            except Exception:
                pass
            # Show the real message to the user when it's a known error type
            if isinstance(exc, (ValueError, RuntimeError)):
                message = str(exc)
            else:
                message = _m("unexpected_analyze_error", ui_language)
            yield _sse("error", {"error": message})
        finally:
            unregister_job(job_id)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@bp.route("/analyze-serp", methods=["POST"])
@limiter.limit("5 per minute")
def analyze_serp():
    ui_language = _request_ui_language()
    api_key = _server_api_key(request.form.get("model", ""))
    if not api_key:
        return jsonify({"error": _m("api_key_required", ui_language)}), 400

    upload = request.files.get("serp_csv")
    serp_files = [item for item in request.files.getlist("serp_files") if item and item.filename]
    import json
    serp_urls = []
    try:
        serp_urls_raw = request.form.get("serp_urls", "[]")
        serp_urls = json.loads(serp_urls_raw)
    except Exception:
        pass

    if not (upload and upload.filename) and not serp_files and not serp_urls:
        return jsonify({"error": _m("need_csv_file", ui_language)}), 400
    if len(serp_files) + len(serp_urls) > config.MAX_SERP_FILES:
        return jsonify({"error": _m("serp_files_limit", ui_language, config.MAX_SERP_FILES)}), 400

    try:
        # `language` is the content language for the generated SERP structure
        # (and its Word export); `ui_language` drives status/progress text.
        model, language, _brief, _verify, deep_read, _keyword, _country = _request_settings()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    is_gemini = is_gemini_engine(model, api_key)
    is_mistral = is_mistral_engine(model, api_key)
    if is_gemini:
        serp_api_url = getattr(config, "GEMINI_API_URL", "")
    elif is_mistral:
        serp_api_url = config.MISTRAL_API_URL
    else:
        serp_api_url = config.ANTHROPIC_API_URL
    topic = request.form.get("topic_context", "").strip()[:240]
    target_type = request.form.get("target_type", "").strip()
    competitors: list[dict] = []
    if upload and upload.filename:
        competitors.extend(_parse_serp_csv(upload.read(), config.MAX_COMPETITORS))
        
    for index, url in enumerate(serp_urls):
        competitors.append({
            "url": url,
            "title": url,
            "desc": "",
            "content": "",
            "extraction_status": "pending",
            "extraction_error": "",
            "position": len(competitors) + 1,
        })

    # Read only the uploaded bytes before opening the stream. Text extraction
    # itself happens inside the SSE generator so the browser receives honest,
    # per-file progress instead of waiting at 2% and then jumping to 45%.
    serp_file_payloads: list[tuple[int, str, bytes]] = []
    for item in serp_files[:config.MAX_SERP_FILES]:
        filename = item.filename[:180]
        raw = item.read()
        index = len(competitors)
        competitors.append({
            "url": f"file://{filename}",
            "title": filename,
            "desc": "",
            "content": "",
            "extraction_status": "pending",
            "extraction_error": "",
            "position": index + 1,
        })
        serp_file_payloads.append((index, filename, raw))
    if not competitors:
        return jsonify({"error": _m("no_valid_serp_urls", ui_language)}), 400

    requested_job_id = request.form.get("job_id", "").strip()
    try:
        job_id, cancel_token = _register_cancel_event(requested_job_id)
    except RuntimeError:
        return jsonify({"error": _m("job_capacity", ui_language), "code": "job_capacity"}), 503

    def event_stream():
        def progress(percent: int, message: str, **meta):
            payload = {"pct": percent, "message": message}
            payload.update(meta)
            return _sse("progress", payload)

        try:
            start_usage_tracking()
            yield _sse("job", {"job_id": job_id, "cancel_token": cancel_token})
            _raise_if_cancelled(job_id, ui_language)
            pending_url_indexes = [
                index for index, competitor in enumerate(competitors)
                if not competitor.get("content") and _looks_like_url(competitor.get("url", ""))
            ]
            total_source_tasks = len(serp_file_payloads) + len(pending_url_indexes)
            yield progress(
                5,
                _m("progress_serp_extracting", ui_language, len(pending_url_indexes), len(competitors)),
                stage="sources",
                current=0,
                total=max(total_source_tasks, len(competitors)),
                ceiling=44,
            )
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, min(config.MAX_FETCH_WORKERS, total_source_tasks or 1))
            ) as executor:
                pending: dict[concurrent.futures.Future, tuple[str, int, str]] = {}
                for index, filename, raw in serp_file_payloads:
                    future = executor.submit(
                        extract_text_from_file,
                        _NamedBytesIO(raw, filename),
                        deep_read,
                        language,
                    )
                    pending[future] = ("file", index, filename)
                for index in pending_url_indexes:
                    url = competitors[index]["url"]
                    future = executor.submit(extract_text_from_url, url, deep_read, language)
                    pending[future] = ("url", index, url)
                completed = 0
                total_to_fetch = max(total_source_tasks, 1)
                while pending:
                    _raise_if_cancelled(job_id, ui_language)
                    done, _ = concurrent.futures.wait(
                        pending, timeout=15, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    if not done:
                        yield progress(
                            5 + int(35 * completed / total_to_fetch),
                            _m("progress_serp_extracting_still", ui_language),
                            stage="sources",
                            current=completed,
                            total=total_source_tasks,
                            ceiling=min(44, 8 + int(35 * (completed + 1) / total_to_fetch)),
                        )
                        continue
                    for future in done:
                        kind, index, source_label = pending.pop(future)
                        completed += 1
                        try:
                            extracted = future.result()
                            competitors[index]["content"] = extracted
                            if kind == "file":
                                competitors[index]["title"] = _title_from_extracted(extracted) or source_label
                                competitors[index]["desc"] = _description_from_extracted(extracted)
                            competitors[index]["extraction_status"] = (
                                "full" if len(extracted.strip()) >= config.MIN_ACCEPTABLE_EXTRACTED_CHARS else "limited"
                            )
                            competitors[index]["extraction_error"] = "" if extracted.strip() else "empty extraction"
                        except Exception as exc:
                            logger.warning("SERP %s extraction failed for %s: %s", kind, source_label, exc)
                            fallback_parts = []
                            if competitors[index].get("title"):
                                fallback_parts.append(f"[Title]: {competitors[index]['title']}")
                            if competitors[index].get("desc"):
                                fallback_parts.append(f"[Description]: {competitors[index]['desc']}")
                            competitors[index]["content"] = "\n\n".join(fallback_parts)
                            competitors[index]["extraction_status"] = "limited"
                            competitors[index]["extraction_error"] = str(exc)[:500]
                        yield progress(
                            5 + int(35 * completed / total_to_fetch),
                            _m("progress_serp_processed", ui_language, completed, total_to_fetch),
                            stage="sources",
                            current=completed,
                            total=total_source_tasks,
                            detail=source_label[:180],
                            ceiling=min(44, 8 + int(35 * (completed + 1) / total_to_fetch)),
                        )

            prepared = competitors
            del serp_file_payloads, pending_url_indexes
            gc.collect()

            yield progress(
                45,
                _m("progress_serp_analyzing", ui_language),
                stage="analysis",
                current=0,
                total=len(prepared),
                ceiling=50,
            )
            serp_stage = {
                "message": _m("progress_serp_analyzing_still", ui_language),
                "pct": 46,
                "ceiling": 50,
                "stage": "analysis",
                "current": 0,
                "total": len(prepared),
            }

            def update_serp_stage(stage: str, current: int, total: int) -> None:
                if stage == "full_scan":
                    serp_stage["message"] = _m("progress_serp_full_scan", ui_language)
                    serp_stage.update(pct=50, ceiling=54, stage="scan", current=current, total=total)
                elif stage == "profile_batches":
                    serp_stage["message"] = _m("progress_serp_profile_batches", ui_language, current, total)
                    ratio = current / max(total, 1)
                    serp_stage.update(
                        pct=54 + int(16 * ratio),
                        ceiling=min(72, 57 + int(16 * ratio)),
                        stage="profiles",
                        current=current,
                        total=total,
                    )
                elif stage == "strategy":
                    serp_stage["message"] = _m("progress_serp_strategy", ui_language)
                    serp_stage.update(pct=72, ceiling=77, stage="strategy", current=0, total=0)
                elif stage == "outline":
                    serp_stage["message"] = _m("progress_serp_outline", ui_language)
                    serp_stage.update(pct=77, ceiling=86, stage="outline", current=0, total=0)
                elif stage == "repair":
                    serp_stage["message"] = _m("progress_serp_repair", ui_language)
                    serp_stage.update(pct=86, ceiling=89, stage="repair", current=0, total=0)

            serp_result = yield from _run_with_heartbeat(
                analyze_serp_data, prepared, topic, target_type, language, model, api_key, serp_api_url,
                lambda: is_job_cancelled(job_id), update_serp_stage,
                heartbeat_message=lambda: serp_stage["message"],
                heartbeat_progress=lambda: serp_stage,
                cancel_check=lambda: is_job_cancelled(job_id),
                cancel_message=_m("analysis_cancelled", ui_language),
                heartbeat_seconds=4,
            )
            structure_html = serp_result.get("html", "")
            structure_text = serp_result.get("text", "")

            _raise_if_cancelled(job_id, ui_language)
            yield progress(
                90,
                _m("progress_serp_building_word", ui_language),
                stage="export",
                current=0,
                total=0,
                ceiling=98,
            )
            try:
                from services.docx_export import build_docx_report
                docx_bytes = build_docx_report(structure_html, language=language)
                token, _path = write_bytes("serp", ".docx", docx_bytes)
                docx_url = f"/download/{token}"
            except Exception as exc:
                logger.warning("Docx export failed: %s", exc)
                docx_url = ""

            del prepared
            gc.collect()

            yield progress(100, _m("progress_serp_done", ui_language), stage="done", ceiling=100)
            yield _sse("done", {
                "structure": structure_html,
                "structure_html": structure_html,
                "structure_text": structure_text,
                "sources_analyzed": sum(1 for item in competitors if item.get("extraction_status") == "full"),
                "sources_total": len(competitors),
                "profiles": serp_result.get("profiles", []),
                "coverage": serp_result.get("coverage", {}),
                "docx_file": docx_url,
                "usage_metrics": get_current_usage(),
            })
        except AnalysisCancelled as exc:
            logger.info("SERP analysis job %s cancelled", job_id)
            yield _sse("error", {"error": str(exc), "cancelled": True})
        except Exception as exc:
            _orig_exc = exc
            _orig_type = type(exc)
            _tb = traceback.format_exc()
            try:
                _safe = repr(str(exc)).encode("ascii", errors="replace").decode("ascii")
                logger.error("Unhandled /analyze-serp error [%s]: %s", _orig_type.__name__, _safe)
            except Exception:
                pass
            if isinstance(exc, (ValueError, RuntimeError)):
                message = str(exc)
            else:
                message = _m("unexpected_serp_error", ui_language)
            yield _sse("error", {"error": message})
        finally:
            unregister_job(job_id)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
