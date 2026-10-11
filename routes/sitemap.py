"""
Smart Internal Linking
======================

Finds pages on the client's own site that are the best link targets for each
entity of the analysis.

Why the previous version returned nothing
-----------------------------------------
* ``sitemap_index.xml`` lists *sitemaps*, not pages — it was never opened, so
  entities were compared against sitemap file addresses.
* Matching used the raw entity string inside the URL. Arabic entities never
  match English slugs (``/labor-lawyer-riyadh/``), and any word-order or
  article difference failed.
* The sitemap URL was fetched without the SSRF checks every other URL in the
  tool goes through.

How it works now
----------------
1. Sitemap indexes are followed (depth 2, <= 50 child sitemaps, .xml.gz
   supported); only URLs on the same site are kept. XML is parsed with
   defusedxml; every hop is validated as a public address.
2. Each page gets a cheap *slug* signature; the most promising pages (and
   then the rest, up to ``SITEMAP_MAX_PAGES``) have their ``<title>`` and
   ``<h1>`` read (first ~200 KB only, concurrently).
3. Entities and their variants are matched with the same Arabic-aware
   normalization as the analysis (article/clitics, hamza, ta-marbuta,
   plurals, accents). Full match in title/H1 > full match in slug > strong
   partial match. The analysed page itself and the homepage are excluded.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import logging
import re
from urllib.parse import unquote, urljoin, urlsplit

import requests
from defusedxml import ElementTree as SafeET
from flask import Blueprint, jsonify, request

import config
from services.semantic import _normalize, _significant_words, token_forms
from utils.security import validate_public_http_url

logger = logging.getLogger(config.LOGGER_NAME)
bp = Blueprint("sitemap", __name__)

MAX_CHILD_SITEMAPS = 50
MAX_SITEMAP_BYTES = 20 * 1024 * 1024
MAX_URLS = 50_000
PAGE_READ_BYTES = 200_000
MAX_PAGES = int(getattr(config, "SITEMAP_MAX_PAGES", 300))
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _site_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _get(url: str, limit: int) -> bytes:
    """GET with SSRF validation on every redirect hop and a byte cap."""
    current = validate_public_http_url(url)
    for _hop in range(max(1, config.MAX_REDIRECTS) + 1):
        response = requests.get(current, headers={"User-Agent": _UA, "Accept-Language": "ar,en;q=0.8"},
                                timeout=(6, 15), allow_redirects=False, stream=True)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            response.close()
            if not location:
                break
            current = validate_public_http_url(urljoin(current, location))
            continue
        response.raise_for_status()
        chunks, total = [], 0
        for chunk in response.iter_content(65_536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
        response.close()
        return b"".join(chunks)[:limit]
    raise requests.TooManyRedirects("Too many redirects")


def _xml_locs(raw: bytes) -> tuple[list[str], list[str]]:
    """Return (child sitemap URLs, page URLs) from one sitemap document."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)[:MAX_SITEMAP_BYTES]
    root = SafeET.fromstring(raw)
    is_index = root.tag.rsplit("}", 1)[-1].lower() == "sitemapindex"
    children, pages = [], []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not (element.text or "").strip():
            continue
        (children if is_index else pages).append(element.text.strip())
    return children, pages


def collect_urls(sitemap_url: str, fetch=None) -> list[str]:
    fetch = fetch or _get
    site = _site_key(sitemap_url)
    queue, seen_maps, pages = [(sitemap_url, 0)], set(), []
    while queue and len(seen_maps) <= MAX_CHILD_SITEMAPS and len(pages) < MAX_URLS:
        url, depth = queue.pop(0)
        if url in seen_maps:
            continue
        seen_maps.add(url)
        try:
            children, found = _xml_locs(fetch(url, MAX_SITEMAP_BYTES))
        except Exception as exc:
            if depth == 0:
                raise
            logger.info("Child sitemap skipped (%s): %s", url, exc)
            continue
        if depth < 2:
            queue.extend((child, depth + 1) for child in children if _site_key(child) == site)
        pages.extend(page for page in found if _site_key(page) == site)
    return list(dict.fromkeys(pages))[:MAX_URLS]


def _forms(text: str) -> set[str]:
    forms: set[str] = set()
    for word in _normalize(text).split():
        forms |= token_forms(word)
    return forms


def _slug_text(url: str) -> str:
    path = unquote(urlsplit(url).path)
    return re.sub(r"[-_/.]+", " ", re.sub(r"\.(html?|php|aspx?)$", "", path))


def _page_head(url: str, fetch=None) -> tuple[str, str]:
    fetch = fetch or _get
    try:
        raw = fetch(url, PAGE_READ_BYTES).decode("utf-8", errors="replace")
    except Exception:
        return "", ""

    def clean(match):
        return re.sub(r"\s+", " ", _TAGS.sub(" ", match.group(1))).strip()[:200] if match else ""

    return clean(_TITLE.search(raw)), clean(_H1.search(raw))


def _variants(entity: dict) -> list[str]:
    values = [entity.get("name", ""), *(entity.get("lsi") or [])[:8]]
    return [value for value in dict.fromkeys(str(v).strip() for v in values) if value]


def _score(variant: str, head_forms: set[str], slug_forms: set[str]) -> float:
    words = _significant_words(variant) or _normalize(variant).split()
    if not words:
        return 0.0
    in_head = sum(1 for word in words if token_forms(word) & head_forms)
    in_slug = sum(1 for word in words if token_forms(word) & slug_forms)
    if in_head == len(words):
        return 1.0
    if in_slug == len(words):
        return 0.9
    best = max(in_head, in_slug) / len(words)
    return round(best * 0.7, 3) if len(words) >= 3 and best >= 0.67 else 0.0


def match_entities(entities: list[dict], pages: list[dict], exclude: set[str]) -> dict[str, list[dict]]:
    matches: dict[str, list[dict]] = {}
    for entity in entities:
        scored = []
        for page in pages:
            if page["key"] in exclude:
                continue
            best, anchor = 0.0, ""
            for variant in _variants(entity):
                value = _score(variant, page["head_forms"], page["slug_forms"])
                if value > best:
                    best, anchor = value, variant
            if best >= 0.6:
                scored.append({"url": page["url"], "display_url": unquote(page["url"]),
                               "title": page["title"] or page["h1"], "score": best, "anchor": anchor})
        scored.sort(key=lambda item: (-item["score"], len(item["url"])))
        if scored:
            matches[entity["name"]] = scored[:3]
    return matches


def _page_key(url: str) -> str:
    return f"{_site_key(url)}{urlsplit(url).path.rstrip('/')}"


@bp.route("/api/sitemap-match", methods=["POST"])
def match_sitemap():
    payload = request.get_json(silent=True) or {}
    sitemap_url = str(payload.get("sitemap_url", "") or "").strip()
    exclude_url = str(payload.get("exclude_url", "") or "").strip()
    entities = []
    for item in (payload.get("entities") or [])[:300]:
        if isinstance(item, str) and item.strip():
            entities.append({"name": item.strip()[:160], "lsi": []})
        elif isinstance(item, dict) and str(item.get("name", "")).strip():
            entities.append({"name": str(item["name"]).strip()[:160],
                             "lsi": [str(v)[:80] for v in (item.get("lsi") or [])[:8]]})
    if not sitemap_url or not entities:
        return jsonify({"error": "Sitemap URL and entities required"}), 400
    try:
        validate_public_http_url(sitemap_url)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        urls = collect_urls(sitemap_url)
    except requests.exceptions.RequestException as exc:
        return jsonify({"error": f"Failed to fetch sitemap: {exc}"}), 502
    except Exception as exc:
        logger.warning("Sitemap parse failed for %s: %s", sitemap_url, exc)
        return jsonify({"error": "Invalid sitemap (not a readable XML sitemap)."}), 400

    entity_forms: set[str] = set()
    for entity in entities:
        for variant in _variants(entity):
            entity_forms |= _forms(variant)
    candidates = [{"url": url, "key": _page_key(url), "slug_forms": _forms(_slug_text(url))} for url in urls]
    candidates.sort(key=lambda page: -len(page["slug_forms"] & entity_forms))
    to_read = candidates[:MAX_PAGES]

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        heads = list(pool.map(lambda page: _page_head(page["url"]), to_read))
    for page, (title, h1) in zip(to_read, heads):
        page.update(title=title, h1=h1, head_forms=_forms(f"{title} {h1}"))

    exclude = {_page_key(exclude_url)} if exclude_url else set()
    exclude |= {page["key"] for page in to_read if not urlsplit(page["url"]).path.strip("/")}
    matches = match_entities(entities, to_read, exclude)
    return jsonify({"matches": matches, "total_urls": len(urls), "scanned_pages": len(to_read)})
