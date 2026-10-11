"""Small opt-in disk cache with TTL and atomic writes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time

import config

logger = logging.getLogger(config.LOGGER_NAME)


def _cache_key(system_prompt: str, prompt: str, model: str, max_tokens: int, api_url: str) -> str:
    payload = json.dumps(
        {
            "system_prompt": system_prompt,
            "prompt": prompt,
            "model": model,
            "max_tokens": max_tokens,
            "api_url": api_url,
            "temperature": 0.1,
            "schema_version": 2,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(config.CACHE_DIR, f"{key}.json")


def get(system_prompt: str, prompt: str, model: str, max_tokens: int, api_url: str = "") -> str | None:
    if not config.ENABLE_CACHE:
        return None
    key = _cache_key(system_prompt, prompt, model, max_tokens, api_url)
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        if time.time() - os.path.getmtime(path) > config.CACHE_TTL_SECONDS:
            os.remove(path)
            return None
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        response = data.get("response")
        return response if isinstance(response, str) else None
    except Exception as exc:
        logger.warning("Cache read failed for key %s: %s", key, exc)
        return None


def set(system_prompt: str, prompt: str, model: str, max_tokens: int, response: str,
        api_url: str = "") -> None:
    if not config.ENABLE_CACHE:
        return
    try:
        os.makedirs(config.CACHE_DIR, mode=0o700, exist_ok=True)
        key = _cache_key(system_prompt, prompt, model, max_tokens, api_url)
        path = _cache_path(key)
        fd, temp_path = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=config.CACHE_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"response": response, "created_at": time.time()}, handle, ensure_ascii=False)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception as exc:
        logger.warning("Cache write failed: %s", exc)
