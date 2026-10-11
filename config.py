"""
Configuration
=============
Centralizes every configurable value used across the Semantic SEO Tool.
All defaults are IDENTICAL to the original single-file implementation —
this module only relocates constants, it does not change any values.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Anthropic Claude API Configuration
# Supports official Anthropic Messages API, Prompt Caching, and Hybrid Architecture.
# ─────────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", os.environ.get("CLAUDE_API_KEY", os.environ.get("NARA_API_KEY", "")))
ANTHROPIC_API_URL: str = os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")
ANTHROPIC_API_VERSION: str = os.environ.get("ANTHROPIC_API_VERSION", "2023-06-01")

# ─────────────────────────────────────────────────────────────────────────────
# Models — read from .env. Retired model IDs (e.g. left over from an old .env)
# fall back to the current default instead of failing every request.
# ─────────────────────────────────────────────────────────────────────────────
_FALLBACK_CLAUDE_MODEL = "claude-sonnet-5"
_RETIRED_CLAUDE_MODELS = {
    "claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
    "claude-3-5-haiku-20241022", "claude-3-opus-20240229", "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307", "claude-sonnet-4-20250514", "claude-opus-4-20250514",
    "claude-opus-4-1-20250805", "claude-2.1", "claude-2.0", "claude-instant-1.2",
}
_RETIRED_PREFIXES = ("claude-3-", "claude-3.", "claude-2", "claude-instant")


def is_retired_claude_model(model: str) -> bool:
    value = (model or "").strip().lower()
    return value in _RETIRED_CLAUDE_MODELS or value.startswith(_RETIRED_PREFIXES)


def _model_env(name: str, fallback: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.lower() in {"default", "hybrid-haiku-sonnet"}:
        return fallback
    if is_retired_claude_model(value):
        logging.getLogger("semantic-seo").warning(
            "%s=%s is a retired Anthropic model; using %s instead.", name, value, fallback)
        return fallback
    return value


CLAUDE_DEFAULT_MODEL: str = _model_env("CLAUDE_DEFAULT_MODEL", _FALLBACK_CLAUDE_MODEL)
CLAUDE_EXTRACTION_MODEL: str = _model_env("CLAUDE_EXTRACTION_MODEL", CLAUDE_DEFAULT_MODEL)
CLAUDE_BRIEF_MODEL: str = _model_env("CLAUDE_BRIEF_MODEL", CLAUDE_DEFAULT_MODEL)

# Deterministic sampling for structured extraction (0.0–0.1 recommended).
AI_TEMPERATURE: float = max(0.0, min(1.0, float(os.environ.get("AI_TEMPERATURE", "0.0") or 0.0)))
# precise = extraction + completeness pass + independent review (≈ +50% cost);
# standard = single extraction pass.
ACCURACY_MODE: str = os.environ.get("ACCURACY_MODE", "precise").strip().lower()
if ACCURACY_MODE not in {"precise", "standard"}:
    ACCURACY_MODE = "precise"
ENTITY_REVIEW_MODEL: str = _model_env("ENTITY_REVIEW_MODEL", "claude-haiku-4-5-20251001")
RECALL_MAX_TOKENS: int = int(os.environ.get("RECALL_MAX_TOKENS", 4096))
REVIEW_MAX_TOKENS: int = int(os.environ.get("REVIEW_MAX_TOKENS", 4096))
SUPPORTED_CONTENT_LANGUAGES = ("ar", "en", "fr", "de", "es", "pt", "it", "tr", "nl", "id", "ru")
CLAUDE_EXTRACTION_MAX_TOKENS: int = int(os.environ.get("CLAUDE_EXTRACTION_MAX_TOKENS", 8192))
# Characters of one source sent to the AI (the extraction itself may keep more).
AI_MAX_SOURCE_CHARS: int = int(os.environ.get("AI_MAX_SOURCE_CHARS", 120_000))

# Backward compatibility aliases
NARA_API_KEY: str = ANTHROPIC_API_KEY
NARA_API_URL: str = ANTHROPIC_API_URL
NARA_MODEL: str = CLAUDE_DEFAULT_MODEL

# ─────────────────────────────────────────────────────────────────────────────
# Mistral AI API Configuration (Dual-Engine / Free Tier Support)
# ─────────────────────────────────────────────────────────────────────────────
MISTRAL_API_KEY: str = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_API_URL: str = os.environ.get("MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions")
MISTRAL_DEFAULT_MODEL: str = os.environ.get("MISTRAL_DEFAULT_MODEL", "ministral-8b-2512")
MISTRAL_MAX_OUTPUT_TOKENS: int = int(os.environ.get("MISTRAL_MAX_OUTPUT_TOKENS", 4096))

# ─────────────────────────────────────────────────────────────────────────────
# Google Gemini API Configuration (Google AI Studio - 100% Free Tier)
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_URL: str = os.environ.get("GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
GEMINI_DEFAULT_MODEL: str = os.environ.get("GEMINI_DEFAULT_MODEL", "gemini-3.6-flash")


# ─────────────────────────────────────────────────────────────────────────────
# Extraction limits
# Thanks to Claude's 200k token context window, MAX_CHARS is raised to 120,000 chars
# (~15,000-20,000 words), allowing exhaustive full-article analysis with zero truncation.
# ─────────────────────────────────────────────────────────────────────────────
MAX_CHARS: int = int(os.environ.get("MAX_CHARS", 120_000))
MAX_COMPETITORS: int = int(os.environ.get("MAX_COMPETITORS", 30))
MAX_SERP_FILES: int = int(os.environ.get("MAX_SERP_FILES", 100))

# ─────────────────────────────────────────────────────────────────────────────
# Claude API HTTP & Retry Tuning
# ─────────────────────────────────────────────────────────────────────────────
CLAUDE_REQUEST_TIMEOUT: int = int(os.environ.get("CLAUDE_REQUEST_TIMEOUT", 180))
CLAUDE_MAX_RETRIES: int = int(os.environ.get("CLAUDE_MAX_RETRIES", 4))
CLAUDE_RETRY_BACKOFF_BASE: float = float(os.environ.get("CLAUDE_RETRY_BACKOFF_BASE", 1.5))

# Aliases for compatibility
NARA_REQUEST_TIMEOUT: int = CLAUDE_REQUEST_TIMEOUT
NARA_MAX_RETRIES: int = CLAUDE_MAX_RETRIES
NARA_SLOW_MAX_RETRIES: int = 2
NARA_RETRY_BACKOFF_BASE: float = CLAUDE_RETRY_BACKOFF_BASE

# ─────────────────────────────────────────────────────────────────────────────
# Upload size safety net (prevents unbounded uploads from crashing the server)
# ─────────────────────────────────────────────────────────────────────────────
MAX_CONTENT_LENGTH_MB: int = int(os.environ.get("MAX_CONTENT_LENGTH_MB", 50))

# ─────────────────────────────────────────────────────────────────────────────
# AI response cache — ON by default: re-running the SAME page with the SAME
# settings reuses the stored AI answer and costs nothing. Any change to the
# page text, model or instructions produces a new (paid) answer.
# ─────────────────────────────────────────────────────────────────────────────
ENABLE_CACHE: bool = os.environ.get("ENABLE_CACHE", "true").lower() == "true"
CACHE_DIR: str = os.environ.get("CACHE_DIR", ".cache")

# ─────────────────────────────────────────────────────────────────────────────
# Server
# ─────────────────────────────────────────────────────────────────────────────
PORT: int = int(os.environ.get("PORT", 5000))
# Off by default: the Werkzeug debugger must never be exposed accidentally.
DEBUG: bool = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# Static assets (index.html / script.js live here now)
# ─────────────────────────────────────────────────────────────────────────────
STATIC_DIR: str = os.environ.get("STATIC_DIR", "static")

# ─────────────────────────────────────────────────────────────────────────────
# Prompts directory
# ─────────────────────────────────────────────────────────────────────────────
PROMPTS_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_LEVEL: int = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
LOGGER_NAME: str = "semantic-seo"

# Missing configurations for app compatibility
APP_NAME: str = os.environ.get("APP_NAME", "Semantic SEO Tool")
APP_VERSION: str = os.environ.get("APP_VERSION", "3.6.0")
TRUST_PROXY_HEADERS: bool = os.environ.get("TRUST_PROXY_HEADERS", "false").lower() == "true"
ENFORCE_SAME_ORIGIN: bool = os.environ.get("ENFORCE_SAME_ORIGIN", "true").lower() == "true"
ENABLE_HSTS: bool = os.environ.get("ENABLE_HSTS", "true").lower() == "true"
HSTS_MAX_AGE_SECONDS: int = int(os.environ.get("HSTS_MAX_AGE_SECONDS", 31536000))
MAX_ACTIVE_JOBS: int = int(os.environ.get("MAX_ACTIVE_JOBS", 20))
# Access protection for hosted deployments (HTTP Basic Auth over HTTPS).
# When APP_PASSWORD is set, every page and API call requires these
# credentials; /health and /ready stay open for the host's health checks.
APP_USERNAME: str = os.environ.get("APP_USERNAME", "admin").strip() or "admin"
APP_PASSWORD: str = os.environ.get("APP_PASSWORD", "").strip()

RATELIMIT_STORAGE_URI: str = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
_default_rates = os.environ.get("DEFAULT_RATE_LIMITS", "300 per day,60 per hour")
DEFAULT_RATE_LIMITS = [r.strip() for r in _default_rates.split(",")]
CORS_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
MAX_FILE_BYTES: int = int(os.environ.get("MAX_FILE_BYTES", 15728640))
MAX_URL_LENGTH: int = int(os.environ.get("MAX_URL_LENGTH", 2000))
ENABLE_BROWSER_FALLBACK: bool = os.environ.get("ENABLE_BROWSER_FALLBACK", "true").lower() == "true"
ENABLE_JINA_FALLBACK: bool = os.environ.get("ENABLE_JINA_FALLBACK", "true").lower() == "true"

# Missing SERP config
SERP_CHUNK_CHAR_LIMIT = int(os.environ.get("SERP_CHUNK_CHAR_LIMIT", 18000))
SERP_MAP_MAX_TOKENS = int(os.environ.get("SERP_MAP_MAX_TOKENS", 2400))
SERP_PROFILE_BATCH_SIZE = int(os.environ.get("SERP_PROFILE_BATCH_SIZE", 8))
SERP_PROFILE_BATCH_WORKERS = int(os.environ.get("SERP_PROFILE_BATCH_WORKERS", 2))
SERP_PROFILE_MAX_TOKENS = int(os.environ.get("SERP_PROFILE_MAX_TOKENS", 4200))
SERP_PROFILE_BATCH_MAX_CHARS = int(os.environ.get("SERP_PROFILE_BATCH_MAX_CHARS", 76000))
SERP_LOCAL_PARAGRAPH_SAMPLES = int(os.environ.get("SERP_LOCAL_PARAGRAPH_SAMPLES", 90))
SERP_AI_PARAGRAPH_SAMPLES = int(os.environ.get("SERP_AI_PARAGRAPH_SAMPLES", 34))
SERP_PROFILE_EVIDENCE_CHAR_BUDGET = int(os.environ.get("SERP_PROFILE_EVIDENCE_CHAR_BUDGET", 12000))
SERP_PER_SOURCE_COVERAGE_ITEMS = int(os.environ.get("SERP_PER_SOURCE_COVERAGE_ITEMS", 14))
SERP_GLOBAL_TOPIC_LIMIT = int(os.environ.get("SERP_GLOBAL_TOPIC_LIMIT", 180))
SERP_GLOBAL_ENTITY_LIMIT = int(os.environ.get("SERP_GLOBAL_ENTITY_LIMIT", 220))
SERP_GLOBAL_QUESTION_LIMIT = int(os.environ.get("SERP_GLOBAL_QUESTION_LIMIT", 120))
SERP_AUDIT_MAX_TOKENS = int(os.environ.get("SERP_AUDIT_MAX_TOKENS", 4200))
SERP_FINAL_MAX_TOKENS = int(os.environ.get("SERP_FINAL_MAX_TOKENS", 8192))
SERP_MIN_TOPIC_COVERAGE = float(os.environ.get("SERP_MIN_TOPIC_COVERAGE", 0.30))
SERP_MIN_SOURCE_COVERAGE = float(os.environ.get("SERP_MIN_SOURCE_COVERAGE", 0.80))
MAX_URL_RESPONSE_BYTES = int(os.environ.get("MAX_URL_RESPONSE_BYTES", 15728640))
MAX_REDIRECTS = int(os.environ.get("MAX_REDIRECTS", 4))
_allowed_ports = os.environ.get("ALLOWED_URL_PORTS", "80,443")
ALLOWED_URL_PORTS = [int(p) for p in _allowed_ports.split(",") if p.strip().isdigit()] or [80, 443]
ENABLE_URL_MEMORY_CACHE = os.environ.get("ENABLE_URL_MEMORY_CACHE", "true").lower() == "true"
URL_MEMORY_CACHE_TTL_SECONDS = int(os.environ.get("URL_MEMORY_CACHE_TTL_SECONDS", 600))
URL_MEMORY_CACHE_MAX_CHARS = int(os.environ.get("URL_MEMORY_CACHE_MAX_CHARS", 12000000))
BROWSER_RENDER_TIMEOUT = int(os.environ.get("BROWSER_RENDER_TIMEOUT", 15))
BROWSER_NETWORK_IDLE_TIMEOUT = int(os.environ.get("BROWSER_NETWORK_IDLE_TIMEOUT", 5))
BROWSER_MAX_CONCURRENT = int(os.environ.get("BROWSER_MAX_CONCURRENT", 2))
MIN_ACCEPTABLE_EXTRACTED_CHARS = int(os.environ.get("MIN_ACCEPTABLE_EXTRACTED_CHARS", 1200))
STRONG_EXTRACTED_CHARS = int(os.environ.get("STRONG_EXTRACTED_CHARS", 4500))
STRONG_EXTRACTED_HEADINGS = int(os.environ.get("STRONG_EXTRACTED_HEADINGS", 3))
MAX_FETCH_WORKERS = int(os.environ.get("MAX_FETCH_WORKERS", 8))
MAX_SOURCE_WORKERS = int(os.environ.get("MAX_SOURCE_WORKERS", 4))
MAX_CHUNK_WORKERS = int(os.environ.get("MAX_CHUNK_WORKERS", 2))
NARA_CONNECT_TIMEOUT = int(os.environ.get("NARA_CONNECT_TIMEOUT", 10))
NARA_429_MAX_RETRIES = int(os.environ.get("NARA_429_MAX_RETRIES", 4))
NARA_429_MAX_BACKOFF_SECONDS = int(os.environ.get("NARA_429_MAX_BACKOFF_SECONDS", 24))
NARA_MAX_CONCURRENT_REQUESTS = int(os.environ.get("NARA_MAX_CONCURRENT_REQUESTS", 2))
NARA_MIN_REQUEST_INTERVAL = float(os.environ.get("NARA_MIN_REQUEST_INTERVAL", 0.75))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", 30 * 86400))
TEMP_FILE_TTL_SECONDS = int(os.environ.get("TEMP_FILE_TTL_SECONDS", 7200))
CONTENT_BRIEF_MAX_TOKENS = int(os.environ.get("CONTENT_BRIEF_MAX_TOKENS", 8192))
# How many independent competitors must mention a topic before it counts as
# a gap. 1 = report everything (very noisy); 2 = require corroboration.
MIN_GAP_SOURCES = int(os.environ.get("MIN_GAP_SOURCES", 2))
MIN_GAP_ENTITY_EVIDENCE = float(os.environ.get("MIN_GAP_ENTITY_EVIDENCE", 0.35))
# Minimum evidence score for an entity to count as "supported" in the
# quality score. 0.48 is the score of a weak partial match, so the bar sits
# above it deliberately.
MIN_SUPPORTED_EVIDENCE = float(os.environ.get("MIN_SUPPORTED_EVIDENCE", 0.6))
# Drop blocks written entirely in the other language (English chrome inside
# an Arabic page, and vice versa). Set to false for genuinely bilingual sites.
FILTER_FOREIGN_LANGUAGE_BLOCKS = os.environ.get("FILTER_FOREIGN_LANGUAGE_BLOCKS", "true").lower() == "true"
