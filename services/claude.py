"""
Anthropic Claude API Service (multi-engine: Claude · Mistral · Gemini)
======================================================================
* Model names come from ``.env`` (``CLAUDE_DEFAULT_MODEL`` etc.). Retired
  model IDs sent by old clients or left in an old ``.env`` are remapped to
  the configured default instead of failing every request.
* Deterministic sampling: ``AI_TEMPERATURE`` (default 0.0). If a model
  rejects the temperature parameter the request is retried without it.
* Guaranteed-valid JSON on Claude via forced tool use (``tool_choice``) with
  a JSON schema — no preamble, no code fences, no truncated objects.
  Other engines use JSON mode + a self-repairing / truncation-salvaging parser.
* Entity output is validated per entity: one malformed entity is dropped,
  it can no longer fail the whole source.
* Salience/mentions are recomputed from the text (services/salience.py).
* Prompt caching on the system prompt; live token & cost tracking.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import threading
import time
from typing import Any, Callable

import requests
from pydantic import ValidationError

import config
from models.schemas import EntityItem, SemanticCluster, SourceAnalysis, UsageMetrics
from utils import cache

_ALLOWED_TYPES = ("Person", "Organization", "Place", "Concept", "Service", "Event", "Product", "Standard")

logger = logging.getLogger(config.LOGGER_NAME)

# ─────────────────────────────────────────────────────────────────────────────
# Live Usage Tracking (Thread-safe accumulator per job/thread)
# ─────────────────────────────────────────────────────────────────────────────
_LOCAL = threading.local()


def start_usage_tracking() -> None:
    """Initialize a thread-local usage bucket for the current request/job."""
    _LOCAL.usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }


def get_current_usage() -> UsageMetrics:
    """Retrieve accumulated usage for the current thread as a UsageMetrics model."""
    if not hasattr(_LOCAL, "usage") or _LOCAL.usage is None:
        start_usage_tracking()
    return UsageMetrics(**_LOCAL.usage)


def add_to_current_usage(usage_dict: dict) -> None:
    """Add usage metrics to the thread-local accumulator."""
    if not hasattr(_LOCAL, "usage") or _LOCAL.usage is None:
        start_usage_tracking()
    _LOCAL.usage["input_tokens"] += usage_dict.get("input_tokens", 0)
    _LOCAL.usage["output_tokens"] += usage_dict.get("output_tokens", 0)
    _LOCAL.usage["cache_read_input_tokens"] += usage_dict.get(
        "cache_read_input_tokens", usage_dict.get("cache_read_tokens", 0)
    )
    _LOCAL.usage["cache_creation_input_tokens"] += usage_dict.get(
        "cache_creation_input_tokens", usage_dict.get("cache_creation_tokens", 0)
    )
    _LOCAL.usage["total_tokens"] += usage_dict.get("total_tokens", 0)
    _LOCAL.usage["cost_usd"] = round(_LOCAL.usage["cost_usd"] + usage_dict.get("cost_usd", 0.0), 6)



# ─────────────────────────────────────────────────────────────────────────────
# Prompt Loading & Context Building
# ─────────────────────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=4)
def _load_prompt_file(filename: str) -> str:
    path = f"{config.PROMPTS_DIR}/{filename}"
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()


def get_system_prompt(language: str) -> str:
    if language == "ar":
        return _load_prompt_file("arabic_system.txt")
    return _load_prompt_file("english_system.txt")


def _build_context_header(language: str, keyword: str, target_country: str) -> str:
    lines = []
    if language != "ar":
        naming = (
            "- Write every extracted entity \"name\" in English, even if the "
            "source text is in Arabic or mixes languages — translate it, don't "
            "leave it in the original script. Exception: keep universally-known "
            "brand/product names in their original Latin spelling (e.g. Google, "
            "iPhone).\n"
        ) if language == "en" else ""
        if keyword:
            lines.append(f"- Target keyword/topic for this analysis: {keyword}")
        if target_country:
            lines.append(f"- Target country/market: {target_country}")
        extra = (
            "\nUse this to judge Search Intent and prioritize entities relevant "
            "to this specific topic and market more highly.\n"
        ) if lines else ""
        return "### Context:\n" + "\n".join(lines) + extra + naming
    else:
        if keyword:
            lines.append(f"- الكلمة المفتاحية/الموضوع المستهدف لهذا التحليل: {keyword}")
        if target_country:
            lines.append(f"- الدولة/السوق المستهدف: {target_country}")
        extra = (
            "\nاستخدم هذا السياق لفهم نية البحث (Search Intent) بدقة أكبر، وأعطِ "
            "أولوية أعلى للكيانات المرتبطة بهذا الموضوع والسوق تحديداً.\n"
        ) if lines else ""
        return "### السياق:\n" + "\n".join(lines) + extra + (
            "- اكتب اسم كل كيان مستخرج (name) باللغة العربية حصراً، حتى لو كان "
            "النص الأصلي يحتوي على مصطلحات إنجليزية — ترجمها للعربية المتعارف "
            "عليها في المجال بدل تركها بحروفها الأصلية. الاستثناء الوحيد: أسماء "
            "العلامات التجارية/المنتجات العالمية المعروفة (مثل Google، iPhone) "
            "تُترك كما هي بالحروف اللاتينية.\n"
        )


_ENTITY_JSON_SHAPE = """{
  "main_topic": "...",
  "clusters": [
    {
      "cluster_name": "...",
      "pillar": "...",
      "entities": [
        {"name": "...", "source_term": "...", "type": "Concept", "scope": "topic",
         "importance": "high", "salience": 0.8, "lsi": ["..."], "related": ["..."]}
      ]
    }
  ]
}"""

ENTITY_TOOL_NAME = "record_entities"
ENTITY_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "main_topic": {"type": "string", "description": "The central topic of the text in a short phrase."},
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_name": {"type": "string", "description": "Short descriptive sub-topic name."},
                    "pillar": {"type": "string", "description": "Name of the core entity of this cluster (one of its entities)."},
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "source_term": {"type": "string", "description": "The entity exactly as written in the text (verbatim)."},
                                "type": {"type": "string", "enum": list(_ALLOWED_TYPES)},
                                "scope": {"type": "string", "enum": ["topic", "brand", "context"],
                                          "description": "topic = part of the subject; brand = the publishing site itself or its staff/authors; context = incidental mention."},
                                "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                                "salience": {"type": "number", "minimum": 0, "maximum": 1},
                                "lsi": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                                        "description": "Other names for EXACTLY the same entity (spelling, plural, article, abbreviation, English/transliteration, true synonym)."},
                                "related": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                                            "description": "Associated but DIFFERENT concepts. Never repeat them in lsi."},
                            },
                            "required": ["name", "source_term", "type", "scope", "importance"],
                        },
                    },
                },
                "required": ["cluster_name", "pillar", "entities"],
            },
        },
    },
    "required": ["main_topic", "clusters"],
}


_RULES_EN = """### Rules
- One entity = one thing: a noun / noun phrase of 1–4 words. Never a heading or sentence. Split compounds:
  "Returns and refunds policy" → "return policy" + "refund policy".
- `scope`: `topic` = part of the subject; `brand` = the publishing website/company itself and its own staff,
  authors or branches; `context` = incidental mention not part of the subject (a city listed once, a year,
  an unrelated initiative). Competitor or product brands the text is ABOUT are `topic`.
- `lsi` = other names for EXACTLY the same entity. `related` = associated but different concepts.
  ✓ "search engine optimization" → lsi ["SEO", "search optimization"]
  ✓ "5-star hotel" → lsi ["five-star hotel", "luxury hotel"]
  ✗ "type 2 diabetes" → lsi ["insulin"]  (related, not the same thing)
  ✗ "tenant rights" → lsi ["landlord rights"]  (the other party)
  ✗ "iPhone 15" → lsi ["iPhone 15 Pro"]  (a different model)
  ✗ "article 77" → lsi ["non-compete clause"]  (what it is about, not its name)
- `type`: Person | Organization | Place | Concept | Service | Event | Product | Standard
  (Standard = laws, regulations, standards, specifications, official guidelines).
- Each entity belongs to exactly ONE cluster. Do not invent entities that are not in the text."""

_RULES_AR = """### القواعد
- الكيان = شيء واحد: اسم أو مركب اسمي من 1 إلى 4 كلمات. ليس عنواناً ولا جملة. قسّم المركبات:
  "سياسة الاستبدال والاسترجاع" ← "سياسة الاستبدال" + "سياسة الاسترجاع".
- `scope`: ‏`topic` = جزء من موضوع النص؛ `brand` = الموقع أو الشركة الناشرة نفسها وموظفوها أو كتّابها أو فروعها؛
  `context` = ذكر عابر ليس من صلب الموضوع (مدينة ذُكرت مرة، سنة، مبادرة غير مرتبطة).
  العلامات التجارية أو المنتجات التي يتحدث عنها النص نفسه هي `topic`.
- `lsi` = أسماء أخرى لنفس الكيان تماماً. `related` = مفاهيم مرتبطة لكنها مختلفة.
  ✓ "تحسين محركات البحث" ← lsi ["السيو", "SEO"]
  ✓ "فندق 5 نجوم" ← lsi ["فنادق خمس نجوم", "فندق فاخر"]
  ✗ "السكري من النوع الثاني" ← lsi ["الأنسولين"]  (مفهوم مرتبط وليس نفس الشيء)
  ✗ "حقوق المستأجر" ← lsi ["حقوق المالك"]  (الطرف الآخر)
  ✗ "آيفون 15" ← lsi ["آيفون 15 برو"]  (موديل مختلف)
  ✗ "المادة 77" ← lsi ["بند عدم المنافسة"]  (موضوعها لا اسمها)
- `type`: Person | Organization | Place | Concept | Service | Event | Product | Standard
  (Standard = القوانين والأنظمة والمعايير والمواصفات والإرشادات الرسمية).
- كل كيان في عنقود واحد فقط. لا تخترع كيانات غير موجودة في النص."""


LANGUAGE_NAMES = {
    "ar": "Arabic", "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "pt": "Portuguese", "it": "Italian", "tr": "Turkish", "nl": "Dutch", "id": "Indonesian", "ru": "Russian",
}


def _prompt_language(language: str) -> str:
    """Arabic pages get Arabic instructions; every other language gets the
    English instructions plus an explicit "answer in <language>" rule."""
    return "ar" if language == "ar" else "en"


def _language_rule(language: str) -> str:
    if language in ("ar", "en"):
        return ""
    name = LANGUAGE_NAMES.get(language, language)
    return (f"\n- The text is in {name}. Write `name`, `source_term`, `lsi`, `related`, `main_topic` and "
            f"cluster names in {name}, exactly as used in the text.")


def _extraction_parts(label: str, text: str, language: str,
                      keyword: str = "", target_country: str = "") -> tuple[str, str]:
    """(document block, instructions). The document block is identical for
    every pass over the same source, so Claude's prompt cache serves the
    completeness pass at ~10% of the input price."""
    lang = _prompt_language(language)
    context_header = _build_context_header(language, keyword, target_country)
    if lang == "en":
        document = f"""## Source: {label}

{context_header}
### Text (extracted in reading order; [H1]/[H2]/[H3] are headings, • are list items, [Table] are tables):
<document>
{text}
</document>"""
    else:
        document = f"""## المصدر: {label}

{context_header}
### النص (مستخرج بترتيب القراءة؛ [H1]/[H2]/[H3] عناوين، • عناصر قوائم، [جدول] جداول):
<document>
{text}
</document>"""
    return document, lang


def _build_extraction_prompt(label: str, text: str, language: str,
                             keyword: str = "", target_country: str = "", json_in_text: bool = False) -> str:
    document, _lang = _extraction_parts(label, text, language, keyword, target_country)
    return document + "\n\n" + _extraction_task(language, json_in_text)


def _extraction_task(language: str, json_in_text: bool) -> str:
    lang = _prompt_language(language)
    if lang == "en":
        output = (f"Return ONLY a JSON object with exactly this structure (no prose, no code fences):\n{_ENTITY_JSON_SHAPE}"
                  if json_in_text else f"Call the `{ENTITY_TOOL_NAME}` tool with the result.")
        return f"""### Task
1. `main_topic`: the central topic of this text in a short phrase.
2. Extract EVERY semantic entity present in the text that matters for this topic (typically 15–60 depending
   on length), each with `name`, `source_term` (verbatim from the text), `type`, `scope`, `importance`
   (high | medium | low relative to the main topic), `salience` (initial estimate 0–1; it is re-measured),
   `lsi` and `related`.
3. Group the `topic` entities into 3–12 coherent semantic clusters (sub-topics). Give each a short
   descriptive `cluster_name` and set `pillar` to the cluster's core entity (one of its entities).
   Put `brand` and `context` entities in a cluster named "Site & context".

{_RULES_EN}{_language_rule(language)}

{output}"""
    output = (f"أعد كائن JSON فقط بهذا الهيكل تماماً (بدون أي شرح أو علامات ```):\n{_ENTITY_JSON_SHAPE}"
              if json_in_text else f"استدعِ الأداة `{ENTITY_TOOL_NAME}` بالنتيجة.")
    return f"""### المطلوب
1. `main_topic`: الموضوع الرئيسي للنص في عبارة قصيرة.
2. استخرج كل كيان دلالي موجود فعلاً في النص ومؤثر في هذا الموضوع (عادةً 15–60 كياناً حسب طول النص)، ولكل كيان:
   `name`، و`source_term` (كما ورد في النص حرفياً)، و`type`، و`scope`، و`importance` (high | medium | low بالنسبة للموضوع الرئيسي)،
   و`salience` (تقدير أولي 0–1 وسيُعاد قياسه من النص)، و`lsi`، و`related`.
3. جمّع كيانات `topic` في 3 إلى 12 عنقوداً دلالياً متماسكاً (مواضيع فرعية)، لكل عنقود اسم وصفي قصير `cluster_name`،
   و`pillar` = الكيان المحوري للعنقود (من كياناته). ضع كيانات `brand` و`context` في عنقود باسم "الموقع والسياق".

{_RULES_AR}

{output}"""


def _recall_task(language: str, existing: list[str], json_in_text: bool) -> str:
    """Second pass: find what the first pass missed (recall), same schema."""
    listed = "; ".join(existing[:250])
    lang = _prompt_language(language)
    if lang == "en":
        output = (f"Return ONLY a JSON object with this structure:\n{_ENTITY_JSON_SHAPE}"
                  if json_in_text else f"Call the `{ENTITY_TOOL_NAME}` tool with the result.")
        return f"""### Completeness check (second pass)
Already extracted: {listed}

Re-read the whole document, heading by heading, and return ONLY entities that are important and MISSING:
sub-topics, services/products, named standards, organizations, places, people and terms a reader or searcher of
this topic would expect. To add a short or alternative search form of an entity that already exists (for example
a 2-word head term used in the text for a longer entity), repeat that existing entity with only the new `lsi`.
Put each new entity in the most fitting existing cluster name, or a new cluster if none fits.
If nothing important is missing, return an empty `clusters` list.

{_RULES_EN}{_language_rule(language)}

{output}"""
    output = (f"أعد كائن JSON فقط بهذا الهيكل:\n{_ENTITY_JSON_SHAPE}"
              if json_in_text else f"استدعِ الأداة `{ENTITY_TOOL_NAME}` بالنتيجة.")
    return f"""### مراجعة الاكتمال (مرحلة ثانية)
الكيانات المستخرجة بالفعل: {listed}

أعد قراءة المستند كاملاً عنواناً عنواناً، وأعد فقط الكيانات المهمة الناقصة: المواضيع الفرعية، الخدمات والمنتجات،
الأنظمة والمعايير المسماة، الجهات، الأماكن، الأشخاص، والمصطلحات التي يتوقعها قارئ أو باحث عن هذا الموضوع.
لإضافة صيغة بحث قصيرة أو بديلة لكيان موجود (مثل مصطلح من كلمتين ورد في النص لكيان أطول)، كرّر الكيان الموجود
مع `lsi` الجديد فقط. ضع كل كيان جديد في أنسب عنقود موجود بنفس اسمه، أو عنقود جديد إن لم يناسبه أي عنقود.
إذا لم يكن هناك نقص مهم، أعد `clusters` فارغة.

{_RULES_AR}

{output}"""


# ─────────────────────────────────────────────────────────────────────────────
# Model Resolution & Cost Calculation (Claude & Mistral Dual-Engine)
# ─────────────────────────────────────────────────────────────────────────────
_mistral_lock = threading.Lock()
_last_mistral_time: float = 0.0


def is_gemini_engine(model: str = "", api_key: str = "") -> bool:
    """Determine if the request targets the Google Gemini API."""
    m = (model or "").strip().lower()
    k = (api_key or "").strip()
    if "gemini" in m:
        return True
    if k.startswith("AIzaSy") or (k.startswith("AIza") and len(k) >= 25):
        return True
    return False


def is_mistral_engine(model: str = "", api_key: str = "") -> bool:
    """Determine if the request targets the Mistral AI engine."""
    m = (model or "").strip().lower()
    k = (api_key or "").strip()

    if is_gemini_engine(model, api_key):
        return False

    # Explicit Mistral / Ministral / Codestral model name selected
    if (
        m.startswith("mistral")
        or m.startswith("ministral")
        or m.startswith("open-mistral")
        or "codestral" in m
        or "ministral" in m
    ):
        return True

    # User entered a key that is NOT an Anthropic Claude key and NOT a Google key
    if (
        k
        and not k.startswith("sk-ant-")
        and not k.startswith("sk-")
        and not k.startswith("AIza")
        and len(k) >= 20
    ):
        return True

    return False


_CLAUDE_SENTINELS = {"", "default", "hybrid-haiku-sonnet", "mistral-large"}
_CLAUDE_ALIASES = {
    "sonnet": "claude-sonnet-5",
    "claude-sonnet": "claude-sonnet-5",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-haiku-4.5": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-5-5",
    "fable": "claude-fable-5-1",
    "claude-opus-4.6": "claude-opus-4-6",
}


def _default_for_task(task_type: str) -> str:
    if task_type in ("brief", "strategy", "outline"):
        return config.CLAUDE_BRIEF_MODEL
    return config.CLAUDE_EXTRACTION_MODEL


def resolve_model(requested_model: str, task_type: str = "extraction", api_key: str = "") -> str:
    """Resolve the user's selection to an exact model ID for the right engine.

    Claude: "default"/empty → the model configured in ``.env``; friendly
    aliases → exact IDs; retired IDs (Claude 2 / 3 / 3.5 / 3.7, Sonnet/Opus
    4.0, Opus 4.1 …) → the configured default, with a warning in the log.
    """
    req = (requested_model or "").strip()
    if is_gemini_engine(req, api_key):
        return config.GEMINI_DEFAULT_MODEL if req.lower() in _CLAUDE_SENTINELS else req
    if is_mistral_engine(req, api_key):
        return config.MISTRAL_DEFAULT_MODEL if req.lower() in _CLAUDE_SENTINELS else req

    lowered = req.lower()
    if lowered in _CLAUDE_SENTINELS:
        return _default_for_task(task_type)
    if lowered in _CLAUDE_ALIASES:
        return _CLAUDE_ALIASES[lowered]
    if config.is_retired_claude_model(lowered):
        fallback = _default_for_task(task_type)
        logger.warning("Model %s is retired by Anthropic; using %s from .env instead.", req, fallback)
        return fallback
    return req


# USD per 1M tokens: (input, output, cache read, cache write). First match wins.
# Source: https://platform.claude.com/docs/en/about-claude/pricing (Sept 2026).
_CLAUDE_PRICES: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    (r"fable|mythos", (10.00, 50.00, 0.25, 12.50)),
    (r"opus-5-5", (4.00, 20.00, 0.20, 5.00)),
    (r"opus-(?:4-[5-9]|5)", (5.00, 25.00, 0.50, 6.25)),
    (r"opus", (15.00, 75.00, 1.50, 18.75)),          # legacy Opus 3 / 4.0 / 4.1
    (r"sonnet-(?:[5-9])", (2.00, 10.00, 0.20, 2.50)),
    (r"sonnet", (3.00, 15.00, 0.30, 3.75)),          # Sonnet 3.x / 4.x
    (r"haiku", (1.00, 5.00, 0.10, 1.25)),
)


def calculate_claude_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimated USD cost from Anthropic's published per-token prices."""
    m = (model or "").lower()
    prices = next((price for pattern, price in _CLAUDE_PRICES if re.search(pattern, m)), (3.00, 15.00, 0.30, 3.75))
    cost = (
        input_tokens * prices[0]
        + output_tokens * prices[1]
        + cache_read_tokens * prices[2]
        + cache_creation_tokens * prices[3]
    ) / 1_000_000
    return round(cost, 6)


def calculate_mistral_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for Mistral AI models (nominal tracking)."""
    m = model.lower()
    if "large" in m:
        # Mistral Large: $2.00/M in, $6.00/M out
        cost = ((input_tokens * 2.00) + (output_tokens * 6.00)) / 1_000_000
    else:
        # Mistral Small / NeMo: $0.10/M in, $0.30/M out
        cost = ((input_tokens * 0.10) + (output_tokens * 0.30)) / 1_000_000
    return round(cost, 6)


def _throttle_mistral() -> None:
    """Ensure at least 1.1s between Mistral API calls to respect 1 RPS limit on Free Tier."""
    global _last_mistral_time
    with _mistral_lock:
        now = time.monotonic()
        elapsed = now - _last_mistral_time
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        _last_mistral_time = time.monotonic()


# ─────────────────────────────────────────────────────────────────────────────
# Mistral AI API Caller (Free Tier & Dual Engine Support)
# ─────────────────────────────────────────────────────────────────────────────
def call_mistral_api(
    prompt: str,
    system_prompt: str,
    model: str,
    api_key: str,
    api_url: str = "",
    max_tokens: int = 4096,
    task_type: str = "extraction",
    return_usage: bool = False,
) -> str | tuple[str, dict]:
    """Call Mistral AI chat completions API with 1 RPS rate limit protection and usage tracking."""
    resolved_api_key = api_key or config.MISTRAL_API_KEY
    if not resolved_api_key:
        raise RuntimeError("يرجى إدخال مفتاح Mistral API الخاص بك (من console.mistral.ai) للبدء.")

    resolved_model = resolve_model(model, task_type=task_type, api_key=resolved_api_key)
    if not (
        resolved_model.startswith("mistral")
        or resolved_model.startswith("ministral")
        or resolved_model.startswith("open-mistral")
        or "codestral" in resolved_model
    ):
        resolved_model = config.MISTRAL_DEFAULT_MODEL

    resolved_url = api_url or config.MISTRAL_API_URL

    # Optional response cache lookup
    cached = cache.get(system_prompt, prompt, resolved_model, max_tokens)
    if cached is not None:
        zero_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "model": resolved_model,
        }
        return (cached, zero_usage) if return_usage else cached

    effective_max_tokens = min(max_tokens, config.MISTRAL_MAX_OUTPUT_TOKENS)
    payload: dict[str, Any] = {
        "model": resolved_model,
        "max_tokens": effective_max_tokens,
        "temperature": config.AI_TEMPERATURE,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
    }

    if task_type == "extraction":
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {resolved_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    max_attempts = max(config.CLAUDE_MAX_RETRIES + 2, 4)
    content: str = ""
    usage_dict: dict = {}

    for attempt in range(1, max_attempts + 1):
        _throttle_mistral()
        call_start = time.monotonic()
        try:
            resp = requests.post(
                resolved_url,
                headers=headers,
                json=payload,
                timeout=config.CLAUDE_REQUEST_TIMEOUT,
            )

            if resp.status_code == 401:
                raise RuntimeError("مفتاح Mistral API غير صالح. يرجى التأكد من المفتاح المدخل من console.mistral.ai.")

            resp.raise_for_status()
            data = resp.json()

            # Extract generated text from choices
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                content = (msg.get("content") or "").strip()

            # Extract usage metrics
            raw_usage = data.get("usage") or {}
            in_tok = raw_usage.get("prompt_tokens", 0)
            out_tok = raw_usage.get("completion_tokens", 0)
            tot_tok = raw_usage.get("total_tokens", in_tok + out_tok)
            cost = calculate_mistral_cost(resolved_model, in_tok, out_tok)

            usage_dict = {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "total_tokens": tot_tok,
                "cost_usd": cost,
                "model": resolved_model,
            }

            # Accumulate into current thread session
            add_to_current_usage(usage_dict)

            logger.info(
                "Mistral API [%s] succeeded in %.2fs: %d in, %d out => $%.6f (nominal)",
                resolved_model, time.monotonic() - call_start, in_tok, out_tok, cost
            )
            break

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 500
            body = exc.response.text[:400] if exc.response is not None else ""
            headers = exc.response.headers if exc.response is not None else {}
            if status == 401:
                raise RuntimeError("مفتاح Mistral API غير صالح. يرجى التأكد من المفتاح المدخل من console.mistral.ai.") from exc

            # Inspect Retry-After and rate limit headers
            retry_after_str = headers.get("Retry-After", "")
            retry_delay = 0.0
            if retry_after_str:
                try:
                    retry_delay = float(retry_after_str)
                except ValueError:
                    pass

            # Transient errors: 429 (rate limit) or 5xx
            if attempt < max_attempts and (status == 429 or status >= 500):
                base_delay = 2.0 * (1.5 ** (attempt - 1))
                delay = max(base_delay, retry_delay)
                logger.warning("Mistral API HTTP %d (attempt %d/%d), retrying in %.1fs (rate-headers: remaining-req=%s, remaining-tok=%s): %s",
                               status, attempt, max_attempts, delay,
                               headers.get("x-ratelimit-remaining-requests", "?"),
                               headers.get("x-ratelimit-remaining-tokens", "?"),
                               body)
                time.sleep(delay)
                continue
            if status == 429:
                rem_req = headers.get("x-ratelimit-remaining-requests", "")
                rem_tok = headers.get("x-ratelimit-remaining-tokens", "")
                if rem_req == "0" or rem_tok == "0":
                    raise RuntimeError("رصيد باقة Mistral المجانية في حسابك غير مفعّل أو منتهي (0 remaining). يرجى التحقق من تفعيل رقم الهاتف وخطة Experiment في console.mistral.ai/limits.") from exc
                raise RuntimeError("تم تجاوز حد الطلبات في باقة Mistral المجانية (Rate limit exceeded). يرجى الانتظار ثوانٍ أو التحقق من console.mistral.ai.") from exc
            raise RuntimeError(f"Mistral API HTTP {status}: {body}") from exc

        except requests.exceptions.RequestException as exc:
            if attempt < max_attempts:
                delay = 2.0 * (1.5 ** (attempt - 1))
                logger.warning("Mistral API connection error (attempt %d/%d), retrying in %.1fs: %s",
                               attempt, max_attempts, delay, exc)
                time.sleep(delay)
                continue
            raise RuntimeError(f"خطأ في الاتصال بـ Mistral API: {exc}") from exc

    if not content:
        raise RuntimeError("Mistral API أعاد رداً فارغاً.")

    cache.set(system_prompt, prompt, resolved_model, max_tokens, content)
    return (content, usage_dict) if return_usage else content


# ─────────────────────────────────────────────────────────────────────────────
# Google Gemini API Caller (Google AI Studio Free Tier Support)
# ─────────────────────────────────────────────────────────────────────────────
def call_gemini_api(
    prompt: str,
    system_prompt: str,
    model: str,
    api_key: str,
    api_url: str = "",
    max_tokens: int = 4096,
    task_type: str = "extraction",
    return_usage: bool = False,
) -> str | tuple[str, dict]:
    """Call Google Gemini API via official OpenAI-compatible endpoint."""
    resolved_api_key = api_key or getattr(config, "GEMINI_API_KEY", "")
    if not resolved_api_key:
        raise RuntimeError("يرجى إدخال مفتاح Google Gemini API الخاص بك (من aistudio.google.com) للبدء.")

    resolved_model = model.strip() if "gemini" in model.lower() else getattr(config, "GEMINI_DEFAULT_MODEL", "gemini-2.0-flash")
    resolved_url = api_url or getattr(config, "GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")

    # Optional response cache lookup
    cached = cache.get(system_prompt, prompt, resolved_model, max_tokens)
    if cached is not None:
        zero_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "model": resolved_model,
        }
        return (cached, zero_usage) if return_usage else cached

    payload: dict[str, Any] = {
        "model": resolved_model,
        "max_tokens": max_tokens,
        "temperature": config.AI_TEMPERATURE,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
    }
    if re.match(r"gemini-[3-9]", resolved_model.lower()):
        # Gemini 3+ are thinking models: Google advises the default temperature,
        # and thinking tokens count against max_tokens, so keep thinking light
        # for structured extraction or the JSON gets truncated.
        payload.pop("temperature", None)
        payload["reasoning_effort"] = "low"

    headers = {
        "Authorization": f"Bearer {resolved_api_key}",
        "Content-Type": "application/json",
    }

    dropped_params = False
    max_attempts = max(config.CLAUDE_MAX_RETRIES, 3)
    content: str = ""
    usage_dict: dict = {}

    for attempt in range(1, max_attempts + 1):
        call_start = time.monotonic()
        try:
            resp = requests.post(
                resolved_url,
                headers=headers,
                json=payload,
                timeout=config.CLAUDE_REQUEST_TIMEOUT,
            )

            if resp.status_code == 401:
                raise RuntimeError("مفتاح Google Gemini API غير صالح. يرجى التأكد من المفتاح المنسوخ من aistudio.google.com.")

            resp.raise_for_status()
            data = resp.json()

            choices = data.get("choices") or []
            finish_reason = ""
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                content = (msg.get("content") or "").strip()
                finish_reason = str(choices[0].get("finish_reason") or "")

            raw_usage = data.get("usage") or {}
            in_tok = raw_usage.get("prompt_tokens", 0)
            out_tok = raw_usage.get("completion_tokens", 0)
            tot_tok = raw_usage.get("total_tokens", in_tok + out_tok)

            usage_dict = {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "total_tokens": tot_tok,
                "cost_usd": 0.0,
                "model": resolved_model,
                "stop_reason": "max_tokens" if finish_reason == "length" else finish_reason,
            }

            add_to_current_usage(usage_dict)

            logger.info(
                "Gemini API [%s] succeeded in %.2fs: %d in, %d out => Free Tier",
                resolved_model, time.monotonic() - call_start, in_tok, out_tok
            )
            break

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 500
            body = exc.response.text[:400] if exc.response is not None else ""
            if status == 401 or (status == 400 and ("valid api key" in body.lower() or "api_key_invalid" in body.lower())):
                raise RuntimeError("مفتاح Google Gemini API غير صالح. يرجى التأكد من نسخ المفتاح كاملاً من aistudio.google.com.") from exc
            if status == 400 and not dropped_params and any(
                    word in body.lower() for word in ("reasoning", "thinking", "temperature")):
                payload.pop("reasoning_effort", None)
                payload.pop("temperature", None)
                dropped_params = True
                continue
            if attempt < max_attempts and (status == 429 or status >= 500):
                delay = 2.0 * (1.5 ** (attempt - 1))
                time.sleep(delay)
                continue
            raise RuntimeError(f"Gemini API HTTP {status}: {body}") from exc

        except requests.exceptions.RequestException as exc:
            if attempt < max_attempts:
                delay = 2.0 * (1.5 ** (attempt - 1))
                time.sleep(delay)
                continue
            raise RuntimeError(f"خطأ في الاتصال بـ Gemini API: {exc}") from exc

    if not content:
        raise RuntimeError("Gemini API أعاد رداً فارغاً.")

    cache.set(system_prompt, prompt, resolved_model, max_tokens, content)
    return (content, usage_dict) if return_usage else content


# ─────────────────────────────────────────────────────────────────────────────
# Claude Messages API Caller (with Dynamic Multi-Engine Routing)
# ─────────────────────────────────────────────────────────────────────────────
def call_claude_api(
    prompt: str,
    system_prompt: str,
    model: str,
    api_key: str,
    api_url: str = "",
    max_tokens: int = 4096,
    task_type: str = "extraction",
    return_usage: bool = False,
    *,
    temperature: float | None = None,
    json_schema: dict | None = None,
    tool_name: str = ENTITY_TOOL_NAME,
    document: str = "",
) -> str | tuple[str, dict]:
    """Call Anthropic Messages API, Gemini, or Mistral with usage tracking.

    When ``json_schema`` is given and the engine is Claude, the model is
    forced to answer through a tool whose ``input_schema`` is that schema, and
    the tool input is returned as a JSON string — valid JSON by construction.

    ``document`` (optional) is sent as a separate, prompt-cached block before
    ``prompt`` so several passes over the same source reuse it cheaply.
    """
    if document and (is_gemini_engine(model, api_key) or is_mistral_engine(model, api_key)):
        prompt, document = document + "\n\n" + prompt, ""
    if is_gemini_engine(model, api_key):
        return call_gemini_api(
            prompt=prompt, system_prompt=system_prompt, model=model, api_key=api_key,
            api_url=api_url if api_url and "anthropic" not in api_url and "mistral" not in api_url else "",
            max_tokens=max_tokens, task_type=task_type, return_usage=return_usage,
        )
    if is_mistral_engine(model, api_key):
        return call_mistral_api(
            prompt=prompt, system_prompt=system_prompt, model=model, api_key=api_key,
            api_url=api_url if api_url and "anthropic" not in api_url and "google" not in api_url else "",
            max_tokens=max_tokens, task_type=task_type, return_usage=return_usage,
        )

    resolved_api_key = api_key or config.ANTHROPIC_API_KEY
    if not resolved_api_key:
        raise RuntimeError("يرجى إدخال مفتاح Anthropic Claude API (من console.anthropic.com) أو ضبطه في ملف .env.")

    resolved_model = resolve_model(model, task_type=task_type)
    resolved_url = api_url if api_url and "anthropic" in api_url else config.ANTHROPIC_API_URL
    sampling = config.AI_TEMPERATURE if temperature is None else max(0.0, min(1.0, float(temperature)))

    cache_key_prompt = document + "\n" + prompt + (f"\n<schema:{tool_name}>" if json_schema else "")
    cached = cache.get(system_prompt, cache_key_prompt, resolved_model, max_tokens)
    if cached is not None:
        zero_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
                      "total_tokens": 0, "cost_usd": 0.0, "model": resolved_model, "stop_reason": "cache"}
        return (cached, zero_usage) if return_usage else cached

    payload: dict[str, Any] = {
        "model": resolved_model,
        "max_tokens": max_tokens,
        "temperature": sampling,
        "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": (
            [{"type": "text", "text": document, "cache_control": {"type": "ephemeral"}},
             {"type": "text", "text": prompt}] if document else prompt)}],
    }
    if json_schema:
        payload["tools"] = [{"name": tool_name, "description": "Record the structured analysis result.",
                             "input_schema": json_schema}]
        payload["tool_choice"] = {"type": "tool", "name": tool_name}

    headers = {
        "x-api-key": resolved_api_key,
        "anthropic-version": config.ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }

    max_attempts = max(config.CLAUDE_MAX_RETRIES, 1)
    content: str = ""
    usage_dict: dict = {}
    dropped_temperature = False
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        call_start = time.monotonic()
        try:
            resp = requests.post(resolved_url, headers=headers, json=payload, timeout=config.CLAUDE_REQUEST_TIMEOUT)
            if resp.status_code == 401:
                raise RuntimeError("مفتاح Claude API غير صالح. يرجى التأكد من نسخه بدقة من console.anthropic.com.")
            resp.raise_for_status()
            data = resp.json()

            blocks = data.get("content") or []
            tool_inputs = [b.get("input") for b in blocks
                           if b.get("type") == "tool_use" and b.get("name") == tool_name]
            if json_schema and tool_inputs and isinstance(tool_inputs[0], (dict, list)):
                content = json.dumps(tool_inputs[0], ensure_ascii=False)
            else:
                content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()

            raw_usage = data.get("usage") or {}
            in_tok = int(raw_usage.get("input_tokens", 0) or 0)
            out_tok = int(raw_usage.get("output_tokens", 0) or 0)
            cache_read = int(raw_usage.get("cache_read_input_tokens", 0) or 0)
            cache_create = int(raw_usage.get("cache_creation_input_tokens", 0) or 0)
            cost = calculate_claude_cost(resolved_model, in_tok, out_tok, cache_read, cache_create)
            usage_dict = {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_create,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
                "total_tokens": in_tok + out_tok + cache_read + cache_create,
                "cost_usd": cost,
                "model": resolved_model,
                "stop_reason": data.get("stop_reason", ""),
            }
            add_to_current_usage(usage_dict)
            logger.info("Claude API [%s] ok in %.2fs: %d in (%d cached), %d out, stop=%s => $%.6f",
                        resolved_model, time.monotonic() - call_start, in_tok, cache_read, out_tok,
                        usage_dict["stop_reason"], cost)
            break

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 500
            body = exc.response.text[:600] if exc.response is not None else ""
            response_headers = exc.response.headers if exc.response is not None else {}
            lowered = body.lower()
            if status == 401:
                raise RuntimeError("مفتاح Claude API غير صالح. يرجى التأكد من نسخه بدقة من console.anthropic.com.") from exc
            if "credit balance is too low" in lowered:
                raise RuntimeError("رصيد حسابك في Claude API غير كافٍ. يرجى شحن الرصيد من Anthropic Console.") from exc
            if status == 400 and "temperature" in lowered and not dropped_temperature:
                # Some newer models fix their sampling parameters; retry once without it.
                payload.pop("temperature", None)
                dropped_temperature = True
                attempt -= 1
                continue
            if status == 404 or (status == 400 and "model" in lowered
                                 and ("not found" in lowered or "invalid" in lowered)):
                raise RuntimeError(
                    f"الموديل '{resolved_model}' غير متاح لحسابك أو تم إيقافه. عدّل CLAUDE_DEFAULT_MODEL في ملف .env "
                    f"(مثال: claude-sonnet-5). التفاصيل: {body[:200]}"
                ) from exc
            try:
                retry_delay = float(response_headers.get("Retry-After", "") or 0)
            except (TypeError, ValueError):
                retry_delay = 0.0
            if attempt < max_attempts and (status in (429, 529) or status >= 500):
                delay = max((config.CLAUDE_RETRY_BACKOFF_BASE ** attempt) + attempt * 0.5, retry_delay)
                logger.warning("Claude API HTTP %d (attempt %d/%d), retrying in %.1fs",
                               status, attempt, max_attempts, delay)
                time.sleep(delay)
                continue
            if status == 429:
                raise RuntimeError("تم تجاوز حد الطلبات في Claude API (Rate limit). انتظر قليلاً وحاول مجدداً.") from exc
            if status == 529:
                raise RuntimeError("خوادم Anthropic مشغولة حالياً (529 Overloaded). يرجى المحاولة بعد قليل.") from exc
            raise RuntimeError(f"Claude API HTTP {status}: {body}") from exc

        except requests.exceptions.RequestException as exc:
            if attempt < max_attempts:
                delay = config.CLAUDE_RETRY_BACKOFF_BASE ** attempt
                logger.warning("Claude API connection error (attempt %d/%d), retrying in %.1fs: %s",
                               attempt, max_attempts, delay, exc)
                time.sleep(delay)
                continue
            raise RuntimeError(f"خطأ في الاتصال بـ Claude API: {exc}") from exc

    if not content:
        raise RuntimeError("Claude API أعاد رداً فارغاً.")

    cache.set(system_prompt, cache_key_prompt, resolved_model, max_tokens, content)
    return (content, usage_dict) if return_usage else content


# Backward-compatible alias for existing modules
call_nara_api = call_claude_api


# ─────────────────────────────────────────────────────────────────────────────
# JSON Parsing with Markdown Code-Fence Stripping
# ─────────────────────────────────────────────────────────────────────────────
def _clean_ai_json_output(raw: str) -> str:
    """Remove markdown code blocks or wrapping text if present."""
    text = (raw or "").strip()
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if code_block_match:
        return code_block_match.group(1).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _repair_json_string(s: str) -> str:
    """Fix common minor JSON defects like trailing commas."""
    return re.sub(r",\s*(\}|\])", r"\1", s)


def _parse_json_from_response(raw: str) -> dict:
    cleaned = _clean_ai_json_output(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try repairing trailing commas
    try:
        return json.loads(_repair_json_string(cleaned))
    except json.JSONDecodeError:
        pass

    # Extract outermost JSON object
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(_repair_json_string(candidate))
            except json.JSONDecodeError:
                pass

    salvaged = _salvage_truncated_json(cleaned)
    if salvaged is not None:
        return salvaged

    raise ValueError(f"لم يمكن استخراج JSON من رد الذكاء الاصطناعي: {raw[:250]}")


def _salvage_truncated_json(text: str) -> dict | None:
    """Recover the complete part of a JSON object cut off by the token limit:
    close the open brackets right after the last complete object."""
    start = text.find("{")
    if start == -1:
        return None
    stack: list[str] = []
    in_string = escaped = False
    checkpoints: list[tuple[int, tuple[str, ...]]] = []
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack:
                break
            stack.pop()
            if char == "}":
                checkpoints.append((index, tuple(stack)))
    for index, remaining in reversed(checkpoints[-200:]):
        candidate = text[start:index + 1] + "".join(reversed(remaining))
        try:
            value = json.loads(_repair_json_string(candidate))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None



# ─────────────────────────────────────────────────────────────────────────────
# Entity Extraction for a Single Source (Full text without 9k chunking)
# ─────────────────────────────────────────────────────────────────────────────
def _add_usage(total: dict, usage: dict | None) -> None:
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value
        else:
            total[key] = value


def _clean_str(value: Any, limit: int = 160) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return re.sub(r"\s+", " ", value.replace("\x00", " ")).strip()[:limit]


def _coerce_entity(raw: Any, cluster_name: str) -> EntityItem | None:
    """Validate ONE entity; anything unusable is dropped instead of failing
    the whole source (pydantic rejects e.g. salience 1.2 or 51 LSI terms)."""
    if not isinstance(raw, dict):
        return None
    name = _clean_str(raw.get("name"), 200)
    if len(name) < 2 or len(name) > 120 or len(name.split()) > 8:
        return None
    entity_type = _clean_str(raw.get("type") or raw.get("entity_type") or "Concept", 40).title()
    if entity_type not in _ALLOWED_TYPES:
        entity_type = "Concept"
    importance = _clean_str(raw.get("importance") or "medium", 20).lower()
    if importance not in ("high", "medium", "low"):
        importance = "medium"
    try:
        salience = float(raw.get("salience", 0.5))
    except (TypeError, ValueError):
        salience = 0.5
    if salience > 1.0:
        salience = salience / 100.0
    salience = max(0.0, min(1.0, salience))
    lsi_raw = raw.get("lsi") or []
    if isinstance(lsi_raw, str):
        lsi_raw = re.split(r"[،,;|]", lsi_raw)
    lsi: list[str] = []
    seen: set[str] = set()
    for alias in lsi_raw if isinstance(lsi_raw, list) else []:
        alias = _clean_str(alias, 80)
        key = alias.lower()
        if len(alias) >= 2 and key not in seen and key != name.lower():
            seen.add(key)
            lsi.append(alias)
    try:
        return EntityItem(
            name=name,
            type=entity_type,
            importance=importance,
            salience=salience,
            mentions=1,
            lsi=lsi[:50],
            cluster=(cluster_name or "غير مصنف")[:160],
            source_term=_clean_str(raw.get("source_term"), 160),
            scope=_clean_str(raw.get("scope") or "topic", 12).lower(),
        )
    except Exception as exc:  # pydantic ValidationError or anything unexpected
        logger.debug("Dropping invalid entity %r: %s", name, exc)
        return None


def _coerce_analysis(data: Any, label: str, language: str = "ar") -> SourceAnalysis:
    """Build a SourceAnalysis from loosely-shaped model output."""
    from services.semantic import _normalize

    fallback_cluster = "General" if language == "en" else "عام"
    if isinstance(data, list):
        data = {"clusters": data}
    if not isinstance(data, dict):
        data = {}
    clusters_in = data.get("clusters") or []
    if isinstance(clusters_in, dict):
        clusters_in = [clusters_in]
    if not clusters_in and isinstance(data.get("entities"), list):
        clusters_in = [{"cluster_name": fallback_cluster, "entities": data["entities"]}]

    seen: dict[str, EntityItem] = {}
    clusters: list[SemanticCluster] = []
    for raw_cluster in (clusters_in[:200] if isinstance(clusters_in, list) else []):
        if not isinstance(raw_cluster, dict):
            continue
        name = _clean_str(raw_cluster.get("cluster_name") or raw_cluster.get("name"), 160) or fallback_cluster
        pillar_key = _normalize(_clean_str(raw_cluster.get("pillar"), 160))
        members: list[EntityItem] = []
        raw_entities = raw_cluster.get("entities") or []
        for raw_entity in (raw_entities[:800] if isinstance(raw_entities, list) else []):
            entity = _coerce_entity(raw_entity, name)
            if entity is None:
                continue
            key = _normalize(entity.name)
            if not key:
                continue
            existing = seen.get(key)
            if existing is not None:  # same entity listed twice: merge synonyms
                existing.lsi = list(dict.fromkeys([*existing.lsi, *entity.lsi]))[:50]
                continue
            seen[key] = entity
            members.append(entity)
        if not members:
            continue
        if pillar_key:  # pillar first inside its cluster
            members.sort(key=lambda item: 0 if _normalize(item.name) == pillar_key else 1)
        clusters.append(SemanticCluster(cluster_name=name, entities=members))
    raw_entities_all = [entity for cluster in clusters for entity in cluster.entities][:5000]
    return SourceAnalysis(
        source_label=_clean_str(label, 240) or "source",
        clusters=clusters,
        raw_entities=raw_entities_all,
        main_topic=_clean_str(data.get("main_topic"), 240),
    )


REVIEW_TOOL_NAME = "review_entities"
REVIEW_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "description": "ONLY entities that need a change. Omit entities that are fine.",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer", "description": "Entity number from the list."},
                    "keep": {"type": "boolean"},
                    "scope": {"type": "string", "enum": ["topic", "brand", "context"]},
                    "type": {"type": "string", "enum": list(_ALLOWED_TYPES)},
                    "remove_lsi": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["i"],
            },
        },
    },
    "required": ["changes"],
}


def _evidence_snippet(entity: EntityItem, text: str) -> str:
    for needle in (entity.source_term, entity.name):
        if needle and needle in text:
            index = text.index(needle)
            start = max(0, text.rfind("\n", 0, index) + 1, index - 90)
            end = min(len(text), index + len(needle) + 90)
            return re.sub(r"\s+", " ", text[start:end]).strip()[:200]
    return ""


def _review_prompt(analysis: SourceAnalysis, text: str, language: str) -> str:
    rows = []
    for index, entity in enumerate(analysis.raw_entities):
        lsi = "; ".join(entity.lsi[:8])
        rows.append(f'{index}. {entity.name} | {entity.entity_type} | scope={entity.scope} | lsi: {lsi or "-"} '
                    f'| evidence: "{_evidence_snippet(entity, text)}"')
    name = LANGUAGE_NAMES.get(language, language)
    return f"""You are auditing entity extraction for SEO topical analysis. Page language: {name}.
Main topic of the page: {analysis.main_topic or "(not given)"}

Entities (number. name | type | scope | lsi | evidence from the page):
{chr(10).join(rows)}

Return ONLY the entities that need a change:
- keep=false when it is not a real entity: a generic word, marketing phrase, sentence, section heading,
  navigation/footer item, or something unrelated to the page's subject.
- scope: topic = part of the subject; brand = the publishing site/company itself or its own staff, authors,
  branches, contact details; context = incidental mention not part of the subject.
- type: fix it if clearly wrong (Standard = laws, regulations, standards, specifications, official guidelines).
- remove_lsi: variants that are NOT another name for exactly the same thing (a related concept, the opposite
  party, a different product/model, a broader or narrower thing, or what the entity is about).
Judge meaning in {name}. When unsure, leave the entity unchanged.
Call the `{REVIEW_TOOL_NAME}` tool."""


def _apply_review(analysis: SourceAnalysis, data: Any, text: str) -> SourceAnalysis:
    """Apply review verdicts with guard rails: never drop an entity from the
    Title/H1, and ignore drops entirely if the reviewer tries to remove more
    than 35% of the list (a sign it misunderstood the page)."""
    from services.semantic import _normalize

    changes = data.get("changes") if isinstance(data, dict) else None
    if not isinstance(changes, list) or not analysis.raw_entities:
        return analysis
    entities = list(analysis.raw_entities)
    headline = _normalize(" ".join(re.findall(r"^\[(?:Title|H1)\]\s*:\s*(.+)$", text or "", re.M)))
    drops: set[int] = set()
    for change in changes:
        if not isinstance(change, dict):
            continue
        try:
            index = int(change.get("i"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(entities):
            continue
        entity = entities[index]
        if change.get("keep") is False and not (headline and _normalize(entity.name) in headline):
            drops.add(index)
        scope = str(change.get("scope") or "").lower()
        if scope in ("topic", "brand", "context"):
            entity.scope = scope
        kind = str(change.get("type") or "")
        if kind in _ALLOWED_TYPES:
            entity.entity_type = kind
        removed = {_normalize(str(term)) for term in (change.get("remove_lsi") or []) if term}
        if removed:
            entity.lsi = [term for term in entity.lsi if _normalize(term) not in removed]
    if drops and len(drops) > 0.35 * len(entities):
        logger.warning("Review wanted to drop %d/%d entities of %s; drops ignored", len(drops), len(entities),
                       analysis.source_label)
        drops = set()
    kept = [entity for index, entity in enumerate(entities) if index not in drops]
    kept_ids = {id(entity) for entity in kept}
    analysis.raw_entities = kept
    analysis.clusters = [
        SemanticCluster(cluster_name=cluster.cluster_name,
                        entities=[entity for entity in cluster.entities if id(entity) in kept_ids])
        for cluster in analysis.clusters
        if any(id(entity) in kept_ids for entity in cluster.entities)
    ]
    return analysis


def _merge_recall(base: SourceAnalysis, extra: SourceAnalysis) -> SourceAnalysis:
    """Fold second-pass entities into the first-pass result."""
    from services.semantic import _normalize

    by_name = {_normalize(entity.name): entity for entity in base.raw_entities}
    by_variant = {_normalize(term): entity for entity in base.raw_entities for term in entity.lsi}
    clusters = {_normalize(cluster.cluster_name): cluster for cluster in base.clusters}
    for entity in extra.raw_entities:
        key = _normalize(entity.name)
        if key in by_name:
            target = by_name[key]
            known = {_normalize(term) for term in target.lsi}
            for term in entity.lsi:
                if _normalize(term) not in known and _normalize(term) != key:
                    target.lsi.append(term)
                    known.add(_normalize(term))
                    by_variant[_normalize(term)] = target
            target.lsi = target.lsi[:50]
            continue
        if key in by_variant:
            continue
        cluster_key = _normalize(entity.cluster)
        cluster = clusters.get(cluster_key)
        if cluster is None:
            cluster = SemanticCluster(cluster_name=entity.cluster, entities=[])
            base.clusters.append(cluster)
            clusters[cluster_key] = cluster
        else:
            entity.cluster = cluster.cluster_name
        cluster.entities.append(entity)
        base.raw_entities.append(entity)
        by_name[key] = entity
    return base


def extract_entities_for_source(
    label: str,
    text: str,
    language: str,
    model: str,
    api_key: str,
    api_url: str = "",
    keyword: str = "",
    target_country: str = "",
    cancel_check: Callable[[], bool] | None = None,
    source_url: str = "",
) -> SourceAnalysis:
    """Extract entities and clusters for one source, then measure salience.

    Claude answers through a forced tool call (schema-valid JSON). Other
    engines use JSON mode with the salvaging parser. One retry with a larger
    output budget happens only when the answer was truncated or unusable.
    """
    from services.entity_quality import refine_entities, separate_signals
    from services.salience import apply_salience

    if cancel_check and cancel_check():
        raise RuntimeError("Analysis cancelled")

    limit = max(2000, config.AI_MAX_SOURCE_CHARS)
    safe_text = text if len(text) <= limit else text[:limit].rsplit("\n\n", 1)[0]
    use_tool = not (is_gemini_engine(model, api_key) or is_mistral_engine(model, api_key))
    document, _lang = _extraction_parts(label, safe_text, language, keyword, target_country)
    prompt = _extraction_task(language, json_in_text=not use_tool)
    sys_p = get_system_prompt(language)
    budgets = [config.CLAUDE_EXTRACTION_MAX_TOKENS, max(config.CLAUDE_EXTRACTION_MAX_TOKENS * 2, 16000)]
    logger.info("Extracting source %s: %d chars (tool=%s)", label, len(safe_text), use_tool)

    analysis: SourceAnalysis | None = None
    usage_total: dict = {}
    last_error: Exception | None = None
    for attempt, budget in enumerate(budgets):
        if cancel_check and cancel_check():
            raise RuntimeError("Analysis cancelled")
        raw_out, usage_info = call_claude_api(
            prompt, sys_p, model, api_key, api_url,
            max_tokens=budget, task_type="extraction", return_usage=True,
            json_schema=ENTITY_TOOL_SCHEMA if use_tool else None, document=document,
        )
        _add_usage(usage_total, usage_info)
        truncated = (usage_info or {}).get("stop_reason") == "max_tokens"
        try:
            candidate = _coerce_analysis(_parse_json_from_response(raw_out), label, language)
        except (ValueError, ValidationError) as exc:
            last_error = exc
            candidate = None
        if candidate is not None and candidate.raw_entities:
            analysis = candidate
            if not truncated:
                break
        if attempt == 0:
            logger.warning("Source %s: %s — retrying with a larger output budget", label,
                           "truncated output" if truncated else f"unusable output ({last_error})")
    if analysis is None:
        raise RuntimeError(f"تعذّر استخراج كيانات صالحة من المصدر {label}: {last_error or 'لا توجد كيانات'}")

    precise = config.ACCURACY_MODE == "precise"
    if precise:  # recall: find what the first pass missed (document served from prompt cache)
        try:
            if cancel_check and cancel_check():
                raise RuntimeError("Analysis cancelled")
            recall_out, recall_usage = call_claude_api(
                _recall_task(language, [entity.name for entity in analysis.raw_entities], json_in_text=not use_tool),
                sys_p, model, api_key, api_url, max_tokens=config.RECALL_MAX_TOKENS, task_type="extraction",
                return_usage=True, json_schema=ENTITY_TOOL_SCHEMA if use_tool else None, document=document,
            )
            _add_usage(usage_total, recall_usage)
            extra = _coerce_analysis(_parse_json_from_response(recall_out), label, language)
            before = len(analysis.raw_entities)
            analysis = _merge_recall(analysis, extra)
            logger.info("Source %s: completeness pass added %d entities", label, len(analysis.raw_entities) - before)
        except RuntimeError as exc:
            if "cancelled" in str(exc).lower():
                raise
            logger.warning("Completeness pass failed for %s (kept first pass): %s", label, exc)
        except Exception as exc:
            logger.warning("Completeness pass failed for %s (kept first pass): %s", label, exc)

    analysis = refine_entities(analysis, safe_text)

    if precise and len(analysis.raw_entities) >= 3:  # precision: independent review
        try:
            review_prompt = _review_prompt(analysis, safe_text, language)
            if not use_tool:  # Gemini / Mistral: same engine, JSON in the text
                review_prompt = review_prompt.replace(
                    f"Call the `{REVIEW_TOOL_NAME}` tool.",
                    'Return ONLY a JSON object: {"changes": [{"i": 0, "keep": false, "scope": "topic", '
                    '"type": "Concept", "remove_lsi": ["..."]}]} (only entities that need a change).')
            review_out, review_usage = call_claude_api(
                review_prompt, get_system_prompt("en"),
                config.ENTITY_REVIEW_MODEL if use_tool else model, api_key, api_url,
                max_tokens=config.REVIEW_MAX_TOKENS, task_type="extraction", return_usage=True,
                json_schema=REVIEW_TOOL_SCHEMA if use_tool else None, tool_name=REVIEW_TOOL_NAME,
            )
            _add_usage(usage_total, review_usage)
            analysis = _apply_review(analysis, _parse_json_from_response(review_out), safe_text)
        except Exception as exc:
            logger.warning("Review pass failed for %s (kept unreviewed entities): %s", label, exc)

    analysis = apply_salience(analysis, safe_text, keyword)
    analysis = separate_signals(analysis, safe_text, source_url)
    analysis.usage = usage_total
    return analysis


def _split_text_into_chunks(text: str, max_chunk_chars: int) -> list[str]:
    """Split long text into whitespace-aligned chunks no larger than
    max_chunk_chars. Provided for compatibility and testing."""
    if len(text) <= max_chunk_chars:
        return [text]
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + max_chunk_chars, n)
        if end < n:
            break_at = text.rfind(" ", start, end)
            if break_at > start:
                end = break_at
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks or [text]


def _merge_source_analyses(label: str, analyses: list[SourceAnalysis]) -> SourceAnalysis:
    """Combine per-chunk analyses of the same source into one SourceAnalysis:
    clusters are concatenated, entities are deduplicated by name (mentions
    summed, highest-salience version of the entity kept)."""
    merged_clusters: list[SemanticCluster] = []
    entity_map: dict[str, EntityItem] = {}

    for analysis in analyses:
        merged_clusters.extend(analysis.clusters)
        for ent in analysis.raw_entities:
            key = ent.name.strip().lower()
            existing = entity_map.get(key)
            if existing is None:
                entity_map[key] = ent
                continue
            best = existing if existing.salience >= ent.salience else ent
            entity_map[key] = EntityItem(
                name=best.name,
                entity_type=best.entity_type,
                importance=best.importance,
                salience=best.salience,
                mentions=existing.mentions + ent.mentions,
                cluster=best.cluster,
            )

    return SourceAnalysis(
        source_label=label,
        clusters=merged_clusters,
        raw_entities=list(entity_map.values()),
    )

