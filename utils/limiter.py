"""Rate limiter configuration with deployment-safe client identification."""
from __future__ import annotations

from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import config


def _client_key() -> str:
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or get_remote_address()
    return get_remote_address()


limiter = Limiter(
    key_func=_client_key,
    storage_uri=config.RATELIMIT_STORAGE_URI,
    default_limits=config.DEFAULT_RATE_LIMITS,
    headers_enabled=True,
)
