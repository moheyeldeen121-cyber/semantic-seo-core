"""Small TTL-backed temporary file store for exports and evidence text."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import config

_STORE_DIR = Path(tempfile.gettempdir()) / "semantic_seo_tool"
_STORE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
try:
    _STORE_DIR.chmod(0o700)
except OSError:
    pass
_DEFAULT_TTL_SECONDS = config.TEMP_FILE_TTL_SECONDS


def _protect_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def cleanup_expired(ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
    cutoff = time.time() - ttl_seconds
    try:
        for path in _STORE_DIR.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def write_bytes(prefix: str, suffix: str, data: bytes) -> tuple[str, Path]:
    cleanup_expired()
    token = f"{prefix}_{uuid.uuid4().hex}{suffix}"
    path = _STORE_DIR / token
    path.write_bytes(data)
    _protect_file(path)
    return token, path


def resolve_file(token: str) -> Path | None:
    cleanup_expired()
    if not token or os.path.basename(token) != token:
        return None
    path = _STORE_DIR / token
    return path if path.is_file() else None


def write_evidence(sources: dict[str, str]) -> str:
    cleanup_expired()
    token = f"evidence_{uuid.uuid4().hex}.json.gz"
    path = _STORE_DIR / token
    payload = json.dumps(sources, ensure_ascii=False).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=6) as handle:
        handle.write(payload)
    _protect_file(path)
    return token.removesuffix(".json.gz")


def read_evidence(token: str) -> dict[str, str] | None:
    cleanup_expired()
    if not token or not token.startswith("evidence_") or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in token):
        return None
    path = _STORE_DIR / f"{token}.json.gz"
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rb") as handle:
            value = json.loads(handle.read().decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def write_editor_content(content: str) -> str:
    """Store the user's editable source separately from competitor evidence.

    Keeping it behind a short-lived, unguessable token avoids inflating the SSE
    result payload and prevents an editor endpoint from exposing competitor
    source text by accident.
    """

    cleanup_expired()
    token = f"editor_{uuid.uuid4().hex}"
    path = _STORE_DIR / f"{token}.txt.gz"
    with gzip.open(path, "wb", compresslevel=6) as handle:
        handle.write(str(content or "").encode("utf-8"))
    _protect_file(path)
    return token


def read_editor_content(token: str) -> str | None:
    cleanup_expired()
    if (
        not token
        or not token.startswith("editor_")
        or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in token)
    ):
        return None
    path = _STORE_DIR / f"{token}.txt.gz"
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rb") as handle:
            return handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def active_job_count() -> int:
    cleanup_expired()
    count = 0
    try:
        for path in _STORE_DIR.glob("job_*.job"):
            if path.is_file():
                count += 1
    except OSError:
        pass
    return count


def register_job(job_id: str, cancel_token: str) -> None:
    cleanup_expired()
    if active_job_count() >= config.MAX_ACTIVE_JOBS:
        raise RuntimeError("job_capacity")
    path = _STORE_DIR / f"job_{job_id}.job"
    path.write_text(cancel_token, encoding="utf-8")
    _protect_file(path)


def unregister_job(job_id: str) -> None:
    path = _STORE_DIR / f"job_{job_id}.job"
    path.unlink(missing_ok=True)
    c_path = _STORE_DIR / f"job_{job_id}.cancelled"
    c_path.unlink(missing_ok=True)


def cancel_job(job_id: str, token: str) -> bool:
    cleanup_expired()
    path = _STORE_DIR / f"job_{job_id}.job"
    if not path.is_file():
        return False
    try:
        expected_token = path.read_text(encoding="utf-8").strip()
        import hmac
        if hmac.compare_digest(token, expected_token):
            c_path = _STORE_DIR / f"job_{job_id}.cancelled"
            c_path.write_text("1", encoding="utf-8")
            _protect_file(c_path)
            return True
    except OSError:
        pass
    return False


def is_job_cancelled(job_id: str) -> bool:
    c_path = _STORE_DIR / f"job_{job_id}.cancelled"
    return c_path.is_file()

