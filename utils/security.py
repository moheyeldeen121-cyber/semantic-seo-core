"""Security helpers for URLs, uploads, and spreadsheet exports."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

import config

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_DANGEROUS_EXCEL_PREFIXES = ("=", "+", "-", "@")

_URL_MESSAGES: dict[str, tuple[str, str]] = {
    "empty_or_too_long": ("الرابط فارغ أو أطول من الحد المسموح.", "The URL is empty or longer than the allowed limit."),
    "bad_scheme": ("الرابط يجب أن يبدأ بـ http:// أو https:// فقط.", "The URL must start with http:// or https:// only."),
    "no_hostname": ("الرابط لا يحتوي على اسم نطاق صالح.", "The URL does not contain a valid hostname."),
    "has_credentials": ("الروابط التي تحتوي على بيانات دخول غير مسموحة.", "URLs containing login credentials are not allowed."),
    "local_network": ("لا يمكن تحليل عناوين الشبكة المحلية.", "Local network addresses cannot be analyzed."),
    "bad_hostname": ("اسم النطاق غير صالح.", "Invalid hostname."),
    "bad_port": ("رقم المنفذ في الرابط غير صالح.", "Invalid port number in the URL."),
    "port_not_allowed": ("منفذ الرابط غير مسموح لأسباب أمنية.", "The URL port is not allowed for security reasons."),
    "dns_not_found": ("تعذر العثور على اسم النطاق.", "Could not resolve the domain name."),
    "private_ip": ("لا يمكن تحليل عناوين IP الخاصة أو المحلية أو المحجوزة.", "Private, local, or reserved IP addresses cannot be analyzed."),
}


def _url_error(key: str, language: str) -> ValueError:
    ar, en = _URL_MESSAGES[key]
    return ValueError(en if language == "en" else ar)


def validate_public_http_url(raw_url: str, language: str = "ar") -> str:
    """Validate a user-supplied URL before the server fetches it.

    Only public HTTP(S) hosts are allowed. Loopback, private, link-local,
    reserved, multicast, and other non-global addresses are rejected to reduce
    SSRF risk. Redirect targets must be validated separately by the caller.
    """
    url = (raw_url or "").strip()
    if not url or len(url) > config.MAX_URL_LENGTH:
        raise _url_error("empty_or_too_long", language)

    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise _url_error("bad_scheme", language)
    if not parsed.hostname:
        raise _url_error("no_hostname", language)
    if parsed.username or parsed.password:
        raise _url_error("has_credentials", language)

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal", ".localhost")):
        raise _url_error("local_network", language)
    if not _HOSTNAME_RE.fullmatch(hostname) and ":" not in hostname:
        raise _url_error("bad_hostname", language)

    try:
        port = parsed.port
    except ValueError as exc:
        raise _url_error("bad_port", language) from exc
    if port is not None and port not in config.ALLOWED_URL_PORTS:
        raise _url_error("port_not_allowed", language)

    try:
        literal_ip = ipaddress.ip_address(hostname)
        addresses = {literal_ip}
    except ValueError:
        try:
            resolved = socket.getaddrinfo(hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise _url_error("dns_not_found", language) from exc
        addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}

    if not addresses or any(not address.is_global for address in addresses):
        raise _url_error("private_ip", language)

    # Drop fragments because they are never sent to the origin and only create
    # duplicate cache/fetch entries. Preserve query parameters because pages may
    # legitimately depend on them.
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def safe_excel_value(value):
    """Prevent CSV/Excel formula injection from scraped or AI-generated text."""
    if not isinstance(value, str):
        return value
    cleaned = value.replace("\x00", "").strip("\ufeff")
    if cleaned.startswith(_DANGEROUS_EXCEL_PREFIXES):
        return "'" + cleaned
    return cleaned
