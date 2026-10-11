"""Semantic SEO Tool application entry point."""

from __future__ import annotations

import logging
import hmac
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import config
from routes.analyze import active_job_count, bp as main_bp
from utils.limiter import limiter

import sys as _sys

# UTF-8 logging on every platform (Arabic in log lines used to crash cp1252
# consoles). Reconfigure the existing stream instead of wrapping its buffer:
# a TextIOWrapper around sys.stderr.buffer CLOSES stderr when it is garbage
# collected (module reload, failed import), after which every log call raises.
try:
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
_log_handler = logging.StreamHandler(_sys.stderr)
_log_handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
logging.basicConfig(level=config.LOG_LEVEL, handlers=[_log_handler])
logger = logging.getLogger(config.LOGGER_NAME)
_STARTED_AT = time.time()
_SAFE_REQUEST_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _request_id() -> str:
    candidate = request.headers.get("X-Request-ID", "").strip()[:80]
    if candidate and all(char in _SAFE_REQUEST_ID for char in candidate):
        return candidate
    return uuid.uuid4().hex


def _origin_key(value: str) -> tuple[str, str, int] | None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.lower(), port


def _same_origin_allowed() -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"} or not config.ENFORCE_SAME_ORIGIN:
        return True
    fetch_site = request.headers.get("Sec-Fetch-Site", "").strip().lower()
    if fetch_site == "cross-site":
        return False
    origin = request.headers.get("Origin", "").strip()
    if not origin:
        return True  # Non-browser clients and same-origin legacy requests.
    if _origin_key(origin) == _origin_key(request.host_url):
        return True
    return origin in set(config.CORS_ALLOWED_ORIGINS)


_AUTH_EXEMPT_PATHS = {"/health", "/ready", "/favicon.ico"}


def _auth_ok() -> bool:
    """Constant-time check of HTTP Basic credentials against .env."""
    if not config.APP_PASSWORD:
        return True
    auth = request.authorization
    if auth is None or auth.type != "basic":
        return False
    user_ok = hmac.compare_digest((auth.username or "").encode(), config.APP_USERNAME.encode())
    pass_ok = hmac.compare_digest((auth.password or "").encode(), config.APP_PASSWORD.encode())
    return user_ok and pass_ok


def create_app() -> Flask:
    app = Flask(__name__, static_folder=config.STATIC_DIR, static_url_path="")
    if config.TRUST_PROXY_HEADERS:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.config.update(
        MAX_CONTENT_LENGTH=config.MAX_CONTENT_LENGTH_MB * 1024 * 1024,
        JSON_AS_ASCII=False,
    )

    if not config.APP_PASSWORD:
        logging.getLogger(config.LOGGER_NAME).warning(
            "APP_PASSWORD is not set: anyone who can reach this server can run analyses on your API key. "
            "Set APP_PASSWORD in .env before exposing the app to the internet.")

    limiter.init_app(app)
    if config.CORS_ALLOWED_ORIGINS:
        CORS(
            app,
            resources={r"/*": {"origins": config.CORS_ALLOWED_ORIGINS}},
            methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Request-ID", "X-Cancel-Token"],
            expose_headers=["X-Request-ID", "Server-Timing"],
        )
    from routes.scraper import bp as scraper_bp
    from routes.sitemap import bp as sitemap_bp
    app.register_blueprint(scraper_bp)
    app.register_blueprint(sitemap_bp)
    app.register_blueprint(main_bp)

    @app.before_request
    def begin_request():
        g.request_id = _request_id()
        g.request_started = time.monotonic()
        if request.path not in _AUTH_EXEMPT_PATHS and not _auth_ok():
            response = jsonify({"error": "Authentication required.", "code": "auth_required",
                                "request_id": g.request_id})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = 'Basic realm="Semantic SEO Tool", charset="UTF-8"'
            return response
        if not _same_origin_allowed():
            return jsonify({
                "error": "Cross-origin request blocked.",
                "code": "origin_blocked",
                "request_id": g.request_id,
            }), 403
        return None

    @app.after_request
    def add_response_headers(response):
        request_id = getattr(g, "request_id", uuid.uuid4().hex)
        elapsed_ms = max(0.0, (time.monotonic() - getattr(g, "request_started", time.monotonic())) * 1000)
        response.headers.setdefault("X-Request-ID", request_id)
        response.headers.setdefault("Server-Timing", f'app;dur={elapsed_ms:.1f}')
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com "
            "https://cdnjs.cloudflare.com; font-src 'self' https://fonts.gstatic.com "
            "https://cdnjs.cloudflare.com; img-src 'self' data:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "connect-src 'self' https://nominatim.openstreetmap.org https://api.mymemory.translated.net; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        if config.ENABLE_HSTS and request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", f"max-age={config.HSTS_MAX_AGE_SECONDS}; includeSubDomains")
        if request.path.startswith(("/analyze", "/preview", "/evidence", "/editor-content", "/download")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/favicon.ico")
    def favicon():
        return "", 204

    @app.get("/api/meta")
    @limiter.exempt
    def public_meta():
        return jsonify({
            "name": config.APP_NAME,
            "version": config.APP_VERSION,
            "has_server_api_key": bool(config.ANTHROPIC_API_KEY or getattr(config, "GEMINI_API_KEY", "") or config.MISTRAL_API_KEY),
            "gemini_model": config.GEMINI_DEFAULT_MODEL if getattr(config, "GEMINI_API_KEY", "") else "",
            "response_cache": bool(config.ENABLE_CACHE),
            "default_model": config.CLAUDE_DEFAULT_MODEL,
            "limits": {
                "max_file_bytes": config.MAX_FILE_BYTES,
                "max_request_mb": config.MAX_CONTENT_LENGTH_MB,
                "max_competitors": config.MAX_COMPETITORS,
                "max_serp_files": config.MAX_SERP_FILES,
                "max_url_length": config.MAX_URL_LENGTH,
            },
            "features": {
                "deep_read": True,
                "browser_fallback": config.ENABLE_BROWSER_FALLBACK,
                "reader_fallback": config.ENABLE_JINA_FALLBACK,
                "deterministic_evidence_scoring": True,
                "live_editor": True,
            },
        })

    @app.get("/ready")
    @limiter.exempt
    def readiness():
        prompt_files = [Path(config.PROMPTS_DIR) / name for name in ("arabic_system.txt", "english_system.txt")]
        checks = {
            "static": Path(config.STATIC_DIR, "index.html").is_file(),
            "prompts": all(path.is_file() for path in prompt_files),
        }
        active_jobs = active_job_count()
        ready = all(checks.values())
        return jsonify({
            "status": "ready" if ready else "degraded",
            "checks": checks,
            "capacity": {
                "active_jobs": active_jobs,
                "maximum": config.MAX_ACTIVE_JOBS,
                "accepting_jobs": active_jobs < config.MAX_ACTIVE_JOBS,
            },
        }), 200 if ready else 503

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(_exc):
        return jsonify({
            "error": f"حجم الطلب أكبر من الحد المسموح ({config.MAX_CONTENT_LENGTH_MB}MB).",
            "code": "request_too_large",
            "request_id": getattr(g, "request_id", ""),
        }), 413

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        if isinstance(exc, HTTPException):
            return jsonify({
                "error": exc.description,
                "code": exc.name.lower().replace(" ", "_"),
                "request_id": getattr(g, "request_id", ""),
            }), exc.code or 500
        logger.error("Unhandled exception request_id=%s: %s", getattr(g, "request_id", "-"), exc, exc_info=True)
        return jsonify({
            "error": "حدث خطأ غير متوقع في الخادم. يرجى المحاولة مرة أخرى.",
            "code": "internal_error",
            "request_id": getattr(g, "request_id", ""),
        }), 500

    return app


app = create_app()


if __name__ == "__main__":
    logger.info("Starting %s v%s on port %d (debug=%s)", config.APP_NAME, config.APP_VERSION, config.PORT, config.DEBUG)
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG, threaded=True)
