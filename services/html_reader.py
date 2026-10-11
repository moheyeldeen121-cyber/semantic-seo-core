"""
HTML Reading-Order Engine
=========================

A pure, network-free reader that turns one HTML document into the structured
block contract the rest of the tool consumes::

    [Title]: ...
    [Description]: ...

    [H1]: main heading
    body paragraph ...

    [H2]: section heading
    • list item

    [جدول]:
    cell | cell | cell

Why this module exists (root causes it removes)
-----------------------------------------------
The previous extractor produced out-of-order, partial text for four reasons:

1. **It emitted from a fixed tag whitelist** (``find_all(["p","ul",...])``).
   Text that lives directly inside ``<div>``/``<section>``/``<span>``
   containers — the norm on Salla/Zid/Shopify product pages, page builders
   and most Arabic CMS themes (``text<br><br>text``) — was never visited, and
   nested ``<li>`` items were emitted twice.
2. **Recovered content was appended at the end.** When the runner-up
   extractor, JSON-LD, or hydration payload contributed blocks, they were
   concatenated *after* the conclusion, so a missing middle section surfaced
   below the FAQ.
3. **Main-content detection preferred the biggest ancestor.** Scores were
   plain character totals, so a wrapper holding article + sidebar always beat
   the article itself; sidebar widgets were then interleaved with the body.
4. **Noise filters deleted real content.** ``<header>`` blocks holding the
   article H1, ``hidden`` accordion/tab panels, FAQ ``<button>`` questions,
   and ASP.NET pages wrapped entirely in ``<form>`` were removed wholesale.

The new design
--------------
* **One traversal, true document order.** The cleaned ``<body>`` is walked
  once, recursively, emitting a :class:`Block` for every heading, paragraph,
  list, table and *every run of loose text* — each block carries its DOM
  order index and its ancestor chain.
* **Content selection is a filter, never a re-ordering.** The main content
  is chosen with Readability-style paragraph scoring (score propagates to
  parent/grandparent, is penalised by link density, and can be promoted to a
  common ancestor or extended across sibling containers). The final output is
  simply the subset of blocks whose ancestor chain contains a selected node —
  so the reading order is the DOM order by construction.
* **Order-preserving merge.** Any block recovered from a second extractor is
  located in the full-page emission and inserted at its true DOM position.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

try:  # bs4 >= 4.9 exposes these; older versions simply lack them.
    from bs4.element import CData, Declaration, Doctype, ProcessingInstruction
except Exception:  # pragma: no cover - defensive for very old bs4
    CData = Declaration = Doctype = ProcessingInstruction = Comment  # type: ignore

try:
    from bs4.element import Script, Stylesheet, TemplateString  # bs4 >= 4.10
    _NON_TEXT_STRINGS: tuple = (Comment, CData, Declaration, Doctype, ProcessingInstruction,
                                Script, Stylesheet, TemplateString)
except Exception:  # pragma: no cover
    _NON_TEXT_STRINGS = (Comment, CData, Declaration, Doctype, ProcessingInstruction)

__all__ = [
    "Block",
    "ReadResult",
    "read_html",
    "read_fragment",
    "blocks_to_text",
    "block_key",
    "clean_line",
    "is_boilerplate",
    "make_soup",
    "page_meta",
    "text_of",
    "extract_hydration_blocks",
    "extract_schema_blocks",
    "looks_like_spa_shell",
]

# ─────────────────────────────────────────────────────────────────────────────
# Labels (bilingual). [Title]/[Description]/[H1..H6] are ALWAYS English — the
# SERP module and the routes parse those exact markers.
# ─────────────────────────────────────────────────────────────────────────────
_LABELS = {
    "ar": {"table": "جدول", "faq": "الأسئلة الشائعة"},
    "en": {"table": "Table", "faq": "Frequently Asked Questions"},
}


def _label(language: str, key: str) -> str:
    return _LABELS["en" if language == "en" else "ar"][key]


# ─────────────────────────────────────────────────────────────────────────────
# Tag taxonomy
# ─────────────────────────────────────────────────────────────────────────────
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Removed before anything else: they never carry readable body text.
_DROP_TAGS = (
    "script", "style", "noscript", "svg", "canvas", "template", "link", "meta",
    "iframe", "object", "embed", "video", "audio", "map", "picture", "source",
    "track", "input", "select", "textarea", "option", "optgroup", "datalist",
    "dialog", "base", "param", "head", "title", "img", "area", "progress", "meter",
)

# Inline elements: their text continues the current paragraph.
_INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "del", "dfn",
    "em", "font", "i", "ins", "kbd", "label", "mark", "nobr", "q", "rp", "rt",
    "ruby", "s", "samp", "small", "span", "strike", "strong", "sub", "sup",
    "time", "tt", "u", "var", "wbr", "output",
}

_CHROME_TAGS = ("nav", "header", "footer", "aside")

_NOISE_PATTERN = re.compile(
    r"(?:^|[\s_\-])("
    r"nav|navbar|navigation|menu|submenu|megamenu|mega-?menu|topbar|top-?bar|sidebar|side-?bar|"
    r"breadcrumbs?|crumbs|footer|site-?footer|colophon|masthead|site-?header|"
    r"site-?branding|branding|logo|header-?wrap|"
    r"cookie|consent|gdpr|ad|ads|adsense|advert|advertis\w*|sponsor\w*|"
    r"promo|popup|modal|overlay|lightbox|drawer|offcanvas|"
    r"newsletter|subscribe|subscription|signup|sign-?up|login|signin|sign-?in|"
    r"register|cart|checkout|basket|wishlist|minicart|"
    r"comment|comments|respond|reply|disqus|livefyre|"
    r"related|related-?posts?|related-?articles?|recommend\w*|more-?stories|"
    r"popular|trending|most-?read|read-?also|also-?like|you-?may-?like|"
    r"share|sharing|social|social-?icons|follow|share-?buttons?|addtoany|"
    r"sharedaddy|jp-relatedposts|essb|heateor|"
    r"pagination|pager|page-?numbers|prev-?next|post-?nav\w*|"
    r"tag-?cloud|tagcloud|widget|widgets|sidebar-?widget|"
    r"author-?box|about-?author|bio-?box|author-?info|"
    r"search-?form|searchbox|search-?bar|skip-?link|screen-?reader|sr-only|visually-?hidden|"
    r"toolbar|utility|meta-?nav|lang-?switch\w*|language-?switch\w*|"
    r"elementor-location-header|elementor-location-footer|"
    r"wp-block-latest-posts|wpcf7|mc4wp|"
    r"backtotop|back-?to-?top|scroll-?top|"
    r"reviews-?widget|newsletter-?form|toc|table-?of-?contents|ez-toc\w*|"
    r"whatsapp-?float|floating-?(?:btn|button|whatsapp)|chat-?widget"
    r")(?:[\s_\-]|$)",
    re.I,
)

# Containers that deliberately hide real content until the user clicks.
_COLLAPSIBLE_HINT = re.compile(
    r"accordion|collaps|tab-?pane|tab-?panel|tabpanel|tab-?content|panel|faq|answer|"
    r"question|details|toggle|read-?more|show-?more|more-?content|expand|spoiler|"
    r"product-?description|description|specification|spec|features?",
    re.I,
)

_NOISE_ROLES = {
    "navigation", "banner", "contentinfo", "complementary", "search",
    "menu", "menubar", "menuitem", "toolbar", "alertdialog", "dialog",
    "tablist", "form",
}

_CONTENT_HINT = re.compile(
    r"(?:^|[\s_\-])("
    r"articlebody|article-?body|article-?content|entry-?content|entry-?body|"
    r"post-?content|post-?body|single-?content|content-?body|main-?content|"
    r"td-post-content|tdb-block-inner|elementor-widget-theme-post-content|"
    r"wp-block-post-content|page-?content|product-?description|product-?details|rte|"
    r"story-?body|news-?body|text-?body|blog-?content|article|content|primary|main"
    r")(?:[\s_\-]|$)",
    re.I,
)

_BOILERPLATE_LINE = re.compile(
    r"^(?:"
    r"جميع\s+الحقوق\s+محفوظة.*|.*©.*|copyright.*|all\s+rights\s+reserved.*|"
    r"شارك(?:\s+هذا)?(?:\s+المقال|\s+الموضوع)?|شارك\s+على.*|"
    r"تابعنا(?:\s+على)?.*|تابعونا.*|"
    r"اشترك\s+(?:في|بـ).*|اشتراك\s+في\s+النشرة.*|"
    r"مقالات?\s+(?:ذات\s+صلة|مشابهة|مقترحة).*|(?:اقرأ|إقرأ)\s+(?:أيضا|أيضًا|ايضا|المزيد).*|"
    r"الأكثر\s+(?:قراءة|مشاهدة|رواجا).*|"
    r"سياسة\s+الخصوصية|شروط\s+(?:الاستخدام|الخدمة)|خريطة\s+الموقع|"
    r"اترك\s+(?:تعليقا|رداً|ردا).*|لن\s+يتم\s+نشر\s+عنوان\s+بريدك.*|"
    r"البريد\s+الإلكتروني\s*\*?|الاسم\s*\*?|الموقع\s+الإلكتروني\s*\*?|"
    r"(?:الصفحة\s+)?(?:التالي|السابق)(?:ة)?|"
    r"أضف\s+(?:إلى|الى)\s+السلة|اشتر(?:ي)?\s+الآن|"
    r"share\s+(?:this|on|via).*|follow\s+us.*|subscribe.*|"
    r"related\s+(?:posts?|articles?|stories).*|read\s+(?:also|more|next).*|"
    r"you\s+may\s+also\s+like.*|recommended\s+for\s+you.*|"
    r"privacy\s+policy|terms\s+(?:of|and)\s+(?:use|service|conditions)|sitemap|"
    r"leave\s+a\s+(?:comment|reply).*|your\s+email\s+address\s+will\s+not.*|"
    r"skip\s+to\s+(?:main\s+)?content.*|back\s+to\s+top|add\s+to\s+cart|buy\s+now|"
    r"(?:this\s+)?(?:website|site)\s+uses\s+cookies.*|we\s+use\s+cookies.*|"
    r"accept(?:\s+all)?(?:\s+cookies)?|منشور\s+في|نشر\s+في|"
    r"[\d\s/\-.:،,]+|[^\w\u0600-\u06FF]{1,40}"
    r")$",
    re.I,
)

_WS = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000\ufeff]+")
_ZERO_WIDTH = re.compile(r"[\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069\u00ad]")
_MULTI_NL = re.compile(r"\n{3,}")
_HEADING_MARKER = re.compile(r"^\[H([1-6])\]\s*:\s*", re.I)
_SENTENCE_END = re.compile(r"[.!?؟。…]\s*$")
_BR_MARK = "\u2028"  # internal line-break marker; never survives to the output

_MIN_CONTENT_CHARS = 140


# ─────────────────────────────────────────────────────────────────────────────
# Small text helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_soup(html: str) -> BeautifulSoup:
    """lxml when available (3-5x faster on large pages), html.parser otherwise."""
    try:
        return BeautifulSoup(html or "", "lxml")
    except Exception:
        return BeautifulSoup(html or "", "html.parser")


def clean_line(value: str) -> str:
    value = _ZERO_WIDTH.sub("", value or "")
    return _WS.sub(" ", value).strip(" \t|•·-–—:")


def text_of(node) -> str:
    try:
        return _WS.sub(" ", _ZERO_WIDTH.sub("", node.get_text(" ", strip=True))).strip()
    except Exception:
        return ""


def is_boilerplate(line: str) -> bool:
    stripped = clean_line(line)
    if not stripped:
        return True
    if len(stripped) > 130:
        return False
    return bool(_BOILERPLATE_LINE.fullmatch(stripped))


def block_key(block: str) -> str:
    """Comparison key for a block: markers, bullets and punctuation removed."""
    text = _HEADING_MARKER.sub("", (block or "").strip())
    text = re.sub(r"^\[[^\]]{1,20}\]\s*:\s*", "", text)
    text = text.replace("•", " ")
    return re.sub(r"[\W_]+", "", text.lower())[:220]


def _attr_blob(node: Tag) -> str:
    classes = node.get("class") or []
    if not isinstance(classes, list):
        classes = [str(classes)]
    return " ".join([*[str(c) for c in classes], str(node.get("id") or ""),
                     str(node.get("data-widget_type") or ""), str(node.get("data-component") or "")])


def _link_density(node: Tag, memo: dict[int, str] | None = None) -> float:
    total = len(_memo_text(node, memo)) if memo is not None else len(text_of(node))
    if total <= 0:
        return 1.0
    linked = 0
    for anchor in node.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        # In-page anchors ("#section") are navigation inside the article and
        # do not indicate a link farm.
        if href.startswith("#"):
            continue
        linked += len(text_of(anchor))
    return min(linked / total, 1.0)


def _memo_text(node: Tag, memo: dict[int, str] | None) -> str:
    if memo is None:
        return text_of(node)
    key = id(node)
    cached = memo.get(key)
    if cached is None:
        cached = text_of(node)
        memo[key] = cached
    return cached


def _is_text_string(node) -> bool:
    return isinstance(node, NavigableString) and not isinstance(node, _NON_TEXT_STRINGS)


# ─────────────────────────────────────────────────────────────────────────────
# Page metadata
# ─────────────────────────────────────────────────────────────────────────────

def page_meta(soup: BeautifulSoup) -> tuple[str, str]:
    """(title, description) — og:title is preferred because <title> usually
    carries the site-name suffix."""
    title = ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = clean_line(str(og_title.get("content")))
    if not title and soup.title and soup.title.string:
        title = clean_line(str(soup.title.string))
    if not title and soup.title:
        title = clean_line(soup.title.get_text(" ", strip=True))

    description = ""
    for finder in ({"name": "description"}, {"property": "og:description"},
                   {"name": "twitter:description"}):
        tag = soup.find("meta", attrs=finder)
        if tag and tag.get("content"):
            description = clean_line(str(tag.get("content")))
            break
    return title[:300], description[:600]


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w\u0600-\u06FF]{2,}", (text or "").lower())}


def _pick_primary_h1(soup: BeautifulSoup, title: str) -> Tag | None:
    """The H1 that names THIS page (not the logo H1 some themes emit)."""
    candidates = [h1 for h1 in soup.find_all("h1") if text_of(h1)]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if not title:
        unlinked = [h1 for h1 in candidates if h1.find_parent("a") is None and h1.find("a") is None]
        return max(unlinked or candidates, key=lambda h1: len(text_of(h1)))
    title_tokens = _tokens(title)
    best, best_score = None, -1.0
    for h1 in candidates:
        tokens = _tokens(text_of(h1))
        if not tokens:
            continue
        jaccard = len(tokens & title_tokens) / max(len(tokens | title_tokens), 1)
        if jaccard > best_score:
            best, best_score = h1, jaccard
    return best or candidates[0]


# ─────────────────────────────────────────────────────────────────────────────
# Pruning
# ─────────────────────────────────────────────────────────────────────────────

import threading as _threading

_prune_state = _threading.local()


def _contains_primary(node: Tag) -> bool:
    """True for the page H1 and every ancestor of it (precomputed id set)."""
    return id(node) in getattr(_prune_state, "protected", ())


def _is_collapsible(node: Tag) -> bool:
    if node.name in ("details", "summary"):
        return True
    role = str(node.get("role") or "").lower()
    if role in ("tabpanel", "region"):
        return True
    if _COLLAPSIBLE_HINT.search(_attr_blob(node)):
        return True
    parent = node.parent
    if isinstance(parent, Tag) and (_COLLAPSIBLE_HINT.search(_attr_blob(parent))
                                   or str(parent.get("role") or "").lower() == "tabpanel"):
        return True
    return False


def _is_hidden(node: Tag) -> bool:
    if node.get("aria-hidden") == "true" or node.has_attr("hidden"):
        return True
    style = str(node.get("style") or "")
    return bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", style, re.I))


def _prune(soup: BeautifulSoup, protected: set[int] | None = None) -> None:
    """Remove chrome while protecting the article, in four passes."""
    _prune_state.protected = set(protected or ())
    for tag in soup.find_all(_DROP_TAGS):
        if not tag.decomposed:
            tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # <button>: keep FAQ/accordion triggers (they ARE the questions), drop UI.
    for button in list(soup.find_all("button")):
        if button.decomposed:
            continue
        label = text_of(button)
        trigger = (button.has_attr("aria-expanded") or button.has_attr("aria-controls")
                   or _COLLAPSIBLE_HINT.search(_attr_blob(button))
                   or (isinstance(button.parent, Tag) and _COLLAPSIBLE_HINT.search(_attr_blob(button.parent)))
                   or label.endswith(("?", "؟")))
        if trigger and len(label) >= 8:
            button.name = "div"
        else:
            button.decompose()

    body = soup.body or soup
    memo: dict[int, str] = {}
    body_len = max(len(_memo_text(body, memo)), 1)

    # <form>: ASP.NET / Wix wrap the ENTIRE page in one form — unwrap those;
    # delete real forms (search, newsletter, comment, contact).
    for form in list(soup.find_all("form")):
        if form.decomposed:
            continue
        form_text = _memo_text(form, memo)
        if len(form_text) > body_len * 0.5 or len(form.find_all(["p", "h2", "h3"])) >= 3:
            form.unwrap()
        elif not _contains_primary(form):
            form.decompose()
    memo.clear()

    # Pass 1 — structural chrome, guarded by text mass and by the page H1.
    for tag in list(soup.find_all(_CHROME_TAGS)):
        if tag.decomposed:
            continue
        text_len = len(_memo_text(tag, memo))
        if text_len > body_len * 0.6:
            continue
        if _contains_primary(tag):
            continue
        in_article = tag.find_parent(["article", "main"]) is not None
        if tag.name == "header" and in_article:
            continue
        if tag.name == "aside" and in_article:
            prose = sum(len(text_of(p)) for p in tag.find_all("p") if len(text_of(p)) >= 60)
            if prose >= 150 and _link_density(tag, memo) < 0.3:
                continue
        tag.decompose()
    memo.clear()

    # Pass 2 — class/id/role/visibility signals.
    doomed: list[Tag] = []
    for tag in soup.find_all(True):
        if tag.decomposed or tag.name in ("body", "html", "main", "article"):
            continue
        if _contains_primary(tag):
            continue
        blob = _attr_blob(tag)
        if _is_hidden(tag):
            # Accordion/tab/read-more panels are hidden until clicked but are
            # real content; mobile menus are hidden too but are link farms.
            text = _memo_text(tag, memo)
            if _is_collapsible(tag) and _link_density(tag, memo) < 0.5:
                continue
            if len(text) >= 200 and _link_density(tag, memo) < 0.35 and not _NOISE_PATTERN.search(blob):
                continue
            doomed.append(tag)
            continue
        role = str(tag.get("role") or "").strip().lower()
        if role in _NOISE_ROLES:
            doomed.append(tag)
            continue
        if _NOISE_PATTERN.search(blob) and not _CONTENT_HINT.search(blob):
            if len(_memo_text(tag, memo)) < body_len * 0.45:
                doomed.append(tag)
    for tag in doomed:
        if not tag.decomposed:
            tag.decompose()
    memo.clear()

    # Pass 3 — link farms ("latest posts", tag lists, mega menus without a
    # recognisable class name): many links, short anchors, no prose.
    for tag in list(soup.find_all(["ul", "ol", "div", "section", "aside", "table"])):
        if tag.decomposed or _contains_primary(tag):
            continue
        text = _memo_text(tag, memo)
        if not text or len(text) > 2500:
            continue
        anchors = [a for a in tag.find_all("a") if not str(a.get("href") or "").startswith("#")]
        if len(anchors) < 3:
            continue
        if tag.find("h1") or len(tag.find_all(["h2", "h3", "h4"])) > 3:
            continue
        density = _link_density(tag, memo)
        if density > 0.75:
            tag.decompose()
            continue
        prose = [p for p in tag.find_all("p") if len(text_of(p)) >= 60]
        anchor_lengths = [len(text_of(a)) for a in anchors]
        mean_anchor = sum(anchor_lengths) / max(len(anchor_lengths), 1)
        if not prose and mean_anchor < 45 and len(text) < 600 and density > 0.45:
            tag.decompose()


# ─────────────────────────────────────────────────────────────────────────────
# Main-content detection (Readability-style, order-agnostic)
# ─────────────────────────────────────────────────────────────────────────────

_SCORABLE = ("p", "pre", "td", "blockquote", "li", "dd")


def _direct_text_len(node: Tag) -> int:
    total = 0
    for child in node.children:
        if _is_text_string(child):
            total += len(str(child).strip())
        elif isinstance(child, Tag) and child.name in _INLINE_TAGS:
            total += len(text_of(child))
    return total


def _class_weight(node: Tag) -> float:
    blob = _attr_blob(node)
    weight = 0.0
    if _CONTENT_HINT.search(blob):
        weight += 25
    if _NOISE_PATTERN.search(blob):
        weight -= 25
    if node.get("itemprop") == "articleBody":
        weight += 60
    return weight


def _base_score(node: Tag) -> float:
    name = node.name
    if name in ("article",):
        return 10.0
    if name in ("div", "section", "main"):
        return 5.0
    if name in ("pre", "td", "blockquote"):
        return 3.0
    if name in ("ol", "ul", "dl", "dd", "dt", "li", "form"):
        return -3.0
    if name in HEADING_TAGS or name == "th":
        return -5.0
    return 0.0


def _score_tree(root: Tag) -> dict[int, tuple[Tag, float]]:
    memo: dict[int, str] = {}
    scores: dict[int, list] = {}

    def ensure(node: Tag) -> list:
        entry = scores.get(id(node))
        if entry is None:
            entry = [node, _base_score(node) + _class_weight(node)]
            scores[id(node)] = entry
        return entry

    paragraphs: list[Tag] = list(root.find_all(_SCORABLE))
    for container in root.find_all(["div", "section", "article", "main", "span", "font", "center"]):
        if _direct_text_len(container) >= 40:
            paragraphs.append(container)

    for para in paragraphs:
        if para.decomposed:
            continue
        if para.name == "li" and para.find(["p", "div"]) is None and len(_memo_text(para, memo)) < 60:
            continue  # short menu-like list items are not prose
        text = _memo_text(para, memo) if para.name in _SCORABLE else ""
        if not text:
            text = "".join(str(c) for c in para.children if _is_text_string(c)).strip()
        if len(text) < 25:
            continue
        content = 1.0 + text.count(",") + text.count("،") + min(len(text) / 100.0, 3.0)
        ancestor = para.parent if para.name in _SCORABLE else para
        level = 0
        while isinstance(ancestor, Tag) and level < 4:
            if ancestor.name in ("html", "[document]"):
                break
            divider = 1.0 if level == 0 else (2.0 if level == 1 else level * 3.0)
            ensure(ancestor)[1] += content / divider
            ancestor = ancestor.parent
            level += 1

    final: dict[int, tuple[Tag, float]] = {}
    for key, (node, score) in scores.items():
        density = _link_density(node, memo)
        final[key] = (node, score * (1.0 - density))
    return final


def _select_content_nodes(root: Tag) -> list[Tag]:
    """Return the node(s) that hold the main content, in document order."""
    scores = _score_tree(root)
    if not scores:
        return [root]
    ranked = sorted(scores.values(), key=lambda item: item[1], reverse=True)
    top, top_score = ranked[0]
    if top_score <= 0:
        return [root]

    # Promotion 1 — several strong candidates sharing an ancestor mean the
    # article is split into sibling blocks (intro / body / FAQ).
    strong = [node for node, score in ranked[1:6] if score >= top_score * 0.75]
    if len(strong) >= 2:
        top_ancestors = [p for p in top.parents if isinstance(p, Tag)]
        for ancestor in top_ancestors:
            if ancestor is root or ancestor.name in ("body", "html"):
                break
            inside = sum(1 for node in strong if any(p is ancestor for p in node.parents))
            if inside >= 2:
                top = ancestor
                top_score = scores.get(id(ancestor), (ancestor, top_score))[1] or top_score
                break

    # Promotion 2 — walk up while the parent is (almost) as good.
    last = top_score
    parent = top.parent
    while isinstance(parent, Tag) and parent is not root and parent.name not in ("body", "html"):
        entry = scores.get(id(parent))
        if entry is None:
            parent = parent.parent
            continue
        parent_score = entry[1]
        if parent_score < last / 3:
            break
        if parent_score > last:
            top, top_score = parent, parent_score
            break
        last = parent_score
        parent = parent.parent

    # A wrapper whose only child is the candidate adds nothing.
    while (isinstance(top.parent, Tag) and top.parent is not root and top.parent.name not in ("body", "html")
           and len([c for c in top.parent.children if isinstance(c, Tag)]) == 1):
        top = top.parent

    # Siblings — include neighbouring containers that also hold body text.
    selected = [top]
    parent = top.parent
    if isinstance(parent, Tag):
        threshold = max(10.0, top_score * 0.2)
        top_classes = " ".join(top.get("class") or [])
        siblings = [child for child in parent.children if isinstance(child, Tag)]
        flags: list[bool] = []
        for sibling in siblings:
            if sibling is top:
                flags.append(True)
                continue
            bonus = top_score * 0.2 if top_classes and " ".join(sibling.get("class") or []) == top_classes else 0.0
            score = scores.get(id(sibling), (sibling, 0.0))[1] + bonus
            ok = score >= threshold
            if not ok and sibling.name in ("p", "div", "section", "blockquote", "ul", "ol", "table", "dl", "figure"):
                text = text_of(sibling)
                density = _link_density(sibling)
                if len(text) > 80 and density < 0.25:
                    ok = True
                elif 0 < len(text) <= 80 and density == 0 and _SENTENCE_END.search(text):
                    ok = True
            flags.append(ok)
        # Headings sitting between (or right before) included siblings belong
        # to the article: "<h2>…</h2><div class=body>…</div>" layouts.
        included_idx = [i for i, flag in enumerate(flags) if flag]
        if included_idx:
            first, last_i = included_idx[0], included_idx[-1]
            for i, sibling in enumerate(siblings):
                if flags[i]:
                    continue
                is_heading = sibling.name in HEADING_TAGS or (
                    sibling.find(HEADING_TAGS) is not None and len(text_of(sibling)) <= 300)
                if is_heading and (first <= i <= last_i or i == first - 1):
                    flags[i] = True
        selected = [sibling for sibling, flag in zip(siblings, flags) if flag]
    return selected or [top]


# ─────────────────────────────────────────────────────────────────────────────
# Document-order emission
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Block:
    kind: str                 # heading | para | list | table
    text: str                 # rendered text (with markers)
    order: int                # DOM order index — the ONLY ordering key
    level: int = 0
    anc: frozenset = field(default_factory=frozenset)

    @property
    def key(self) -> str:
        return block_key(self.text)

    @property
    def body_len(self) -> int:
        return len(_HEADING_MARKER.sub("", self.text))


def _is_layout_table(table: Tag) -> bool:
    if str(table.get("role") or "").lower() in ("presentation", "none"):
        return True
    if table.find("table"):
        return True
    rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
    cells = [cell for row in rows for cell in row.find_all(["td", "th"], recursive=False)]
    if not cells:
        return True
    for cell in cells:
        if cell.find(["p", "h1", "h2", "h3", "ul", "ol", "div"]) and len(text_of(cell)) > 300:
            return True
    if len(cells) <= 2 and sum(len(text_of(cell)) for cell in cells) > 400:
        return True
    return False


def _heading_level(node: Tag) -> int:
    if node.name in HEADING_TAGS:
        return int(node.name[1])
    try:
        return max(1, min(6, int(str(node.get("aria-level") or "2"))))
    except ValueError:
        return 2


class _Emitter:
    def __init__(self, language: str):
        self.language = language
        self.table_label = _label(language, "table")
        self.blocks: list[Block] = []
        self.inline: list[str] = []
        self.stack: list[int] = []
        self.order = 0
        self.last_level = 1

    # ── helpers ────────────────────────────────────────────────────────────
    def _next(self) -> int:
        self.order += 1
        return self.order

    def _anc(self) -> frozenset:
        return frozenset(self.stack)

    def add(self, kind: str, text: str, level: int = 0) -> None:
        if not text:
            return
        self.blocks.append(Block(kind=kind, text=text, order=self._next(), level=level, anc=self._anc()))

    def flush(self) -> None:
        if not self.inline:
            return
        raw = "".join(self.inline)
        self.inline = []
        # Two consecutive <br> end a paragraph; one <br> is a soft line break.
        paragraphs = re.split(rf"(?:\s*{_BR_MARK}\s*){{2,}}", raw)
        for paragraph in paragraphs:
            lines = [clean_line(line) for line in paragraph.split(_BR_MARK)]
            lines = [line for line in lines if line]
            if not lines:
                continue
            text = "\n".join(lines) if len(lines) > 1 else lines[0]
            if len(text) < 2 or is_boilerplate(text):
                continue
            self.add("para", text)

    # ── element handlers ───────────────────────────────────────────────────
    def heading(self, node: Tag, level: int) -> None:
        self.flush()
        text = clean_line(text_of(node))
        if 1 < len(text) <= 300:
            self.last_level = level
            self.add("heading", f"[H{level}]: {text}", level)

    def pseudo_heading(self, node: Tag) -> bool:
        """<p><strong>Short label</strong></p> is how many Arabic CMS themes
        write sub-headings. Promote it so the section structure survives."""
        children = [c for c in node.children if not (_is_text_string(c) and not str(c).strip())]
        if len(children) != 1 or not isinstance(children[0], Tag) or children[0].name not in ("strong", "b"):
            return False
        text = clean_line(text_of(node)).rstrip(":：")
        words = text.split()
        if not (2 <= len(text) <= 90 and 1 <= len(words) <= 12):
            return False
        if _SENTENCE_END.search(text) or "،" in text[-2:]:
            return False
        following = node.find_next_sibling(True)
        if following is None:
            return False
        if following.name not in ("ul", "ol", "table") and len(text_of(following)) < 60:
            return False
        level = min(self.last_level + 1, 4) if self.last_level >= 2 else 2
        self.flush()
        self.add("heading", f"[H{level}]: {text}", level)
        return True

    def list_block(self, node: Tag) -> None:
        self.flush()
        items: list[str] = []

        def collect(list_node: Tag, depth: int) -> None:
            for item in list_node.find_all("li", recursive=False):
                own: list[str] = []
                nested: list[Tag] = []
                for child in item.children:
                    if isinstance(child, Tag) and child.name in ("ul", "ol"):
                        nested.append(child)
                    elif isinstance(child, Tag):
                        own.append(text_of(child))
                    elif _is_text_string(child):
                        own.append(str(child))
                text = clean_line(" ".join(own))
                if text and not is_boilerplate(text) and len(text) <= 1200:
                    items.append(f"• {text}")
                for sub in nested:
                    if depth < 4:
                        collect(sub, depth + 1)

        collect(node, 0)
        if not items:  # malformed list (li not direct children)
            for item in node.find_all("li"):
                text = clean_line(text_of(item))
                if text and not is_boilerplate(text):
                    items.append(f"• {text}")
        if items:
            self.add("list", "\n".join(items[:200]))

    def table_block(self, node: Tag) -> None:
        self.flush()
        rows: list[str] = []
        for row in node.find_all("tr"):
            if row.find_parent("table") is not node:
                continue
            cells = [clean_line(text_of(cell)) for cell in row.find_all(["th", "td"], recursive=False)]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            caption = node.find("caption")
            head = f"[{self.table_label}]:"
            if caption and text_of(caption):
                head = f"[{self.table_label}]: {clean_line(text_of(caption))}"
            self.add("table", head + "\n" + "\n".join(rows[:250]))

    def dl_block(self, node: Tag) -> None:
        self.flush()
        lines: list[str] = []
        term = ""
        for child in node.find_all(["dt", "dd"]):
            text = clean_line(text_of(child))
            if not text:
                continue
            if child.name == "dt":
                if term:
                    lines.append(f"• {term}")
                term = text
            else:
                lines.append(f"• {term}: {text}" if term else f"• {text}")
                term = ""
        if term:
            lines.append(f"• {term}")
        if lines:
            self.add("list", "\n".join(lines[:200]))

    # ── traversal ──────────────────────────────────────────────────────────
    def walk(self, node: Tag, depth: int = 0) -> None:
        if depth > 250:  # pathological nesting guard
            return
        for child in list(node.children):
            if _is_text_string(child):
                text = str(child)
                if text.strip():
                    self.inline.append(text)
                elif self.inline:
                    self.inline.append(" ")
                continue
            if not isinstance(child, Tag) or child.decomposed:
                continue
            name = child.name
            if name == "br":
                self.inline.append(_BR_MARK)
                continue
            if name == "hr":
                self.flush()
                continue
            role = str(child.get("role") or "").lower()
            if name in HEADING_TAGS or role == "heading":
                self.stack.append(id(child))
                self.heading(child, _heading_level(child))
                self.stack.pop()
                continue
            if name in ("ul", "ol"):
                self.stack.append(id(child))
                self.list_block(child)
                self.stack.pop()
                continue
            if name == "table" and not _is_layout_table(child):
                self.stack.append(id(child))
                self.table_block(child)
                self.stack.pop()
                continue
            if name == "dl":
                self.stack.append(id(child))
                self.dl_block(child)
                self.stack.pop()
                continue
            if name == "pre":
                self.flush()
                self.stack.append(id(child))
                text = (child.get_text("\n") or "").strip()
                if text:
                    self.add("para", "\n".join(clean_line(line) for line in text.splitlines()
                                               if clean_line(line))[:4000])
                self.stack.pop()
                continue
            if name in _INLINE_TAGS:
                self.stack.append(id(child))
                self.walk(child, depth + 1)
                self.stack.pop()
                continue
            # Block-level or unknown/custom element (web components included).
            self.flush()
            self.stack.append(id(child))
            if name == "p" and self.pseudo_heading(child):
                self.stack.pop()
                continue
            self.walk(child, depth + 1)
            self.flush()
            self.stack.pop()


def _emit(root: Tag, language: str) -> list[Block]:
    emitter = _Emitter(language)
    emitter.stack.append(id(root))
    emitter.walk(root)
    emitter.flush()
    return emitter.blocks


def blocks_to_text(blocks: Iterable[Block | str]) -> str:
    parts = [block.text if isinstance(block, Block) else str(block) for block in blocks]
    return _MULTI_NL.sub("\n\n", "\n\n".join(part.strip() for part in parts if part and part.strip())).strip()


def dedupe_blocks(blocks: list[Block]) -> list[Block]:
    """Drop echoes (desktop+mobile copies of the same widget) without breaking
    legitimate repetition: long blocks are unique page-wide; headings and
    short blocks only lose *consecutive* duplicates (a comparison page may
    have an "المميزات" heading under every product)."""
    seen_long: set[str] = set()
    output: list[Block] = []
    previous = ""
    for block in blocks:
        key = block.key
        if not key:
            continue
        if key == previous:
            continue
        if block.kind != "heading" and len(key) >= 40:
            if key in seen_long:
                continue
            seen_long.add(key)
        output.append(block)
        previous = key
    # Drop a trailing heading that introduces nothing.
    while output and output[-1].kind == "heading" and len(output) > 1:
        output.pop()
    return output


# ─────────────────────────────────────────────────────────────────────────────
# Embedded payloads (JSON-LD, Next/Nuxt/Salla/Zid hydration) — ordered
# ─────────────────────────────────────────────────────────────────────────────

_VALUE_KEYS = {
    "name", "title", "headline", "alternativename", "description", "text",
    "articlebody", "body", "content", "answer", "question", "acceptedanswer",
    "summary", "caption", "subtitle", "excerpt", "shortdescription",
    "longdescription", "value", "label", "detail", "details", "abstract",
    "richtext", "html", "bodyhtml", "descriptionhtml", "content_html", "body_html",
}
_SKIP_KEYS = {
    "url", "src", "href", "image", "images", "logo", "thumbnail", "id", "_id",
    "sku", "slug", "email", "telephone", "phone", "token", "key", "hash",
    "class", "classname", "style", "css", "script", "path", "type", "@type",
    "@context", "locale", "lang", "currency", "sitekey", "apikey", "buildid",
    "assetprefix", "runtimeconfig", "i18n", "translations", "messages",
}
_SITEWIDE_SCHEMA_TYPES = {
    "website", "websiteelement", "sitenavigationelement", "breadcrumblist",
    "listitem", "organization", "corporation", "localbusiness", "ngo",
    "onlinestore", "store", "collectionpage", "profilepage", "searchaction",
    "searchresultspage", "wpheader", "wpfooter", "wpsidebar",
    "wpadblock", "imageobject", "videoobject", "logo", "contactpoint",
    "postaladdress", "aggregaterating", "offer", "brand", "person", "rating", "review",
}
_CONTENT_SCHEMA_TYPES = {
    "faqpage", "question", "answer", "howto", "howtostep", "recipe",
    "article", "newsarticle", "blogposting", "techarticle", "product", "course", "service",
}
_HYDRATION_HINTS = (
    "__NEXT_DATA__", "__NUXT__", "__NUXT_DATA__", "__INITIAL_STATE__", "__APOLLO_STATE__",
    "__salla_data__", "salla.config", "__ZID_DATA__", "zid.store", "__remixContext",
    "warmupData", "wixEmbedsAPI", "__PRELOADED_STATE__", "window.__data", "__GATSBY",
)
_HTML_LIKE = re.compile(r"<(?:p|h[1-6]|ul|ol|li|div|table|br|strong|section)\b", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _plain(value: str) -> str:
    value = _TAG_RE.sub(" ", value)
    value = (value.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
             .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return _WS.sub(" ", value).strip()


def _looks_useful(value: str) -> bool:
    if not value or len(value) < 3 or len(value) > 20000:
        return False
    if value.startswith(("http://", "https://", "data:", "//", "{", "[")):
        return False
    if " " not in value and re.fullmatch(r"[\w\-.:/#@]+", value):
        return False
    return len(re.findall(r"[A-Za-z\u0600-\u06FF]", value)) >= 3


def _schema_type(node: dict) -> str:
    raw = node.get("@type") or node.get("type") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw).strip().lower()


def json_candidates(payload: str) -> Iterable[Any]:
    """Yield every JSON value found in a <script> body — pure JSON (JSON-LD)
    or an assignment such as ``window.__ZID_DATA__ = {...};``."""
    payload = (payload or "").strip()
    if not payload:
        return
    try:
        yield json.loads(payload)
        return
    except Exception:
        pass
    index, length, found = 0, len(payload), 0
    while index < length and found < 12:
        char = payload[index]
        if char not in "{[":
            index += 1
            continue
        opener, closer = char, ("}" if char == "{" else "]")
        depth, in_string, escaped, end = 0, False, False, -1
        for cursor in range(index, length):
            current = payload[cursor]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == opener:
                depth += 1
            elif current == closer:
                depth -= 1
                if depth == 0:
                    end = cursor
                    break
        if end == -1:
            break
        chunk = payload[index:end + 1]
        if len(chunk) >= 12:
            try:
                yield json.loads(chunk)
                found += 1
            except Exception:
                pass
        index = end + 1


def walk_json(node: Any, out: list[str], depth: int = 0, skip_sitewide: bool = True,
              language: str = "ar") -> None:
    """Collect readable strings from a JSON value **in document order**.
    HTML-bearing strings (headless-CMS rich text) are parsed through the same
    reading-order emitter, so their headings and lists survive."""
    if depth > 14 or len(out) > 1500:
        return
    if isinstance(node, dict):
        if skip_sitewide and _schema_type(node) in _SITEWIDE_SCHEMA_TYPES:
            return
        for raw_key, value in node.items():
            key = str(raw_key).strip().lower()
            if key in _SKIP_KEYS:
                continue
            if isinstance(value, str):
                if len(value) >= 120 and _HTML_LIKE.search(value):
                    fragment = read_fragment(value, language)
                    if fragment:
                        out.extend(fragment)
                        continue
                if key in _VALUE_KEYS or len(value) >= 40:
                    text = _plain(value)
                    if _looks_useful(text):
                        out.append(text)
            else:
                walk_json(value, out, depth + 1, skip_sitewide, language)
    elif isinstance(node, list):
        for item in node[:500]:
            walk_json(item, out, depth + 1, skip_sitewide, language)


def _iter_schema_nodes(node: Any, depth: int = 0) -> Iterable[dict]:
    if depth > 6:
        return
    if isinstance(node, dict):
        yield node
        graph = node.get("@graph")
        if isinstance(graph, list):
            for item in graph[:200]:
                yield from _iter_schema_nodes(item, depth + 1)
        for key, value in node.items():
            if key == "@graph":
                continue
            if isinstance(value, (dict, list)):
                yield from _iter_schema_nodes(value, depth + 1)
    elif isinstance(node, list):
        for item in node[:200]:
            yield from _iter_schema_nodes(item, depth + 1)


def _unique(lines: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = block_key(line)
        if not key or key in seen or is_boilerplate(line):
            continue
        seen.add(key)
        output.append(line.strip())
    return output


def extract_schema_blocks(soup: BeautifulSoup, language: str = "ar") -> list[str]:
    """JSON-LD blocks that describe THIS page. FAQ pairs are rendered as a
    real FAQ section (question heading + answer) instead of loose strings."""
    faq: list[str] = []
    other: list[str] = []
    seen_questions: set[str] = set()
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)})[:25]:
        payload = script.string or script.get_text() or ""
        if not payload or len(payload) > 1_500_000:
            continue
        for candidate in json_candidates(payload):
            for node in _iter_schema_nodes(candidate):
                kind = _schema_type(node)
                if kind == "question":
                    question = _plain(str(node.get("name") or node.get("text") or ""))
                    answer_node = node.get("acceptedAnswer") or node.get("suggestedAnswer") or {}
                    if isinstance(answer_node, list):
                        answer_node = answer_node[0] if answer_node else {}
                    answer = ""
                    if isinstance(answer_node, dict):
                        raw_answer = str(answer_node.get("text") or "")
                        answer = " ".join(read_fragment(raw_answer, language)) if _HTML_LIKE.search(raw_answer) \
                            else _plain(raw_answer)
                    key = block_key(question)
                    if question and key not in seen_questions:
                        seen_questions.add(key)
                        faq.append(f"[H3]: {question}")
                        if answer:
                            faq.append(answer)
                elif kind in _CONTENT_SCHEMA_TYPES and kind not in ("faqpage", "answer"):
                    collected: list[str] = []
                    walk_json({k: v for k, v in node.items() if k not in ("mainEntity",)},
                              collected, skip_sitewide=True, language=language)
                    other.extend(collected)
    blocks: list[str] = []
    if faq:
        blocks.append(f"[H2]: {_label(language, 'faq')}")
        blocks.extend(faq)
    blocks.extend(_unique(other))
    return blocks


def extract_hydration_blocks(soup: BeautifulSoup, language: str = "ar") -> list[str]:
    """Content that lives in JSON rather than HTML: Next.js / Nuxt / Remix
    hydration, Salla and Zid store state, Apollo/Redux preloaded state."""
    collected: list[str] = []
    for script in soup.find_all("script")[:80]:
        script_type = str(script.get("type") or "").lower()
        script_id = str(script.get("id") or "")
        payload = script.string or script.get_text() or ""
        if not payload or len(payload) > 4_000_000:
            continue
        is_ld = "ld+json" in script_type
        is_json = script_type in {"application/json", "text/json"} or script_id.startswith("__")
        head = payload[:4000].lower()
        is_hydration = any(hint.lower() in head for hint in _HYDRATION_HINTS)
        if not (is_ld or is_json or is_hydration):
            continue
        for candidate in json_candidates(payload):
            walk_json(candidate, collected, language=language)
        if len(collected) > 1500:
            break
    return _unique(collected)[:600]


# ─────────────────────────────────────────────────────────────────────────────
# SPA shell detection
# ─────────────────────────────────────────────────────────────────────────────

_SPA_ROOT = re.compile(
    r"""id=["'](?:__next|__nuxt|app|root|___gatsby|svelte|main-app)["']|ng-version=|data-reactroot|"""
    r"""<salla-|<zid-|data-server-rendered|__remixContext|window\.__NUXT__""",
    re.I,
)


def looks_like_spa_shell(html: str, visible_chars: int) -> bool:
    """A client-rendered page returns a near-empty DOM to a plain HTTP fetch."""
    if visible_chars >= 1500:
        return False
    if _SPA_ROOT.search(html[:200_000] or ""):
        return True
    scripts = len(re.findall(r"<script\b", html[:500_000], re.I))
    return scripts >= 12 and visible_chars < 600


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReadResult:
    title: str = ""
    description: str = ""
    blocks: list[Block] = field(default_factory=list)       # main content, DOM order
    page_blocks: list[Block] = field(default_factory=list)  # whole cleaned page, DOM order
    visible_chars: int = 0
    spa_shell: bool = False
    has_h1: bool = False

    def meta_lines(self) -> list[str]:
        lines = []
        if self.title:
            lines.append(f"[Title]: {self.title}")
        if self.description:
            lines.append(f"[Description]: {self.description}")
        return lines

    @property
    def text(self) -> str:
        return blocks_to_text(self.blocks)


def _lca_ancestor_ids(node: Tag) -> list[int]:
    return [id(node)] + [id(p) for p in node.parents if isinstance(p, Tag)]


def read_html(html: str, language: str = "ar") -> ReadResult:
    """Parse one HTML document and return its main content in reading order."""
    result = ReadResult()
    if not html or len(html.strip()) < 20:
        return result

    soup = make_soup(html)
    result.title, result.description = page_meta(soup)

    primary = _pick_primary_h1(soup, result.title)
    protected: set[int] = set()
    if primary is not None:
        primary["data-sr-primary"] = "1"
        protected = {id(primary), *(id(p) for p in primary.parents if isinstance(p, Tag))}

    _prune(soup, protected)
    body = soup.body or soup
    result.visible_chars = len(text_of(body))
    result.spa_shell = looks_like_spa_shell(html, result.visible_chars)

    page_blocks = _emit(body, language)
    result.page_blocks = page_blocks
    if not page_blocks:
        return result

    # Prefer an explicit article container when the page declares one.
    scope: Tag = body
    declared = body.find(attrs={"itemprop": "articleBody"})
    if declared is not None and len(text_of(declared)) >= 500:
        scope = declared
    else:
        mains = body.find_all(["main"]) + body.find_all(attrs={"role": "main"})
        mains = [m for m in mains if len(text_of(m)) >= 400]
        if len(mains) == 1:
            scope = mains[0]

    selected = _select_content_nodes(scope)
    selected_ids = {id(node) for node in selected}

    # H1 anchoring: include the title heading and — when it is close to the
    # body — the whole region between them (lead paragraph, byline-free intro).
    primary = soup.find(attrs={"data-sr-primary": "1"})
    if primary is not None:
        result.has_h1 = True
        primary_ids = set(_lca_ancestor_ids(primary))
        if not (primary_ids & selected_ids):
            first = selected[0]
            common = None
            for ancestor in [first, *[p for p in first.parents if isinstance(p, Tag)]]:
                if id(ancestor) in primary_ids:
                    common = ancestor
                    break
            if common is not None and common.name not in ("html", "[document]"):
                common_len = len(text_of(common))
                selected_len = sum(len(text_of(node)) for node in selected)
                if common is not body and selected_len >= common_len * 0.6:
                    selected_ids = {id(common)}
                else:
                    selected_ids.add(id(primary))
            else:
                selected_ids.add(id(primary))

    content = [block for block in page_blocks if block.anc & selected_ids]
    if not content:
        content = page_blocks
    result.blocks = dedupe_blocks(content)
    result.page_blocks = page_blocks
    return result


def read_fragment(html: str, language: str = "ar") -> list[str]:
    """Emit an HTML *fragment* (rich-text field, WP REST content) as ordered
    block strings, without main-content selection."""
    if not html or not html.strip():
        return []
    soup = make_soup(f"<html><body>{html}</body></html>")
    for tag in soup.find_all(_DROP_TAGS):
        if not tag.decomposed:
            tag.decompose()
    body = soup.body or soup
    return [block.text for block in dedupe_blocks(_emit(body, language))]


def merge_in_order(content: list[Block], page_blocks: list[Block], extra_texts: Iterable[str],
                   *, min_chars: int = 80, max_growth: float = 1.0) -> list[Block]:
    """Insert blocks another extractor found — at their TRUE DOM position.

    Each extra text is located inside the full-page emission. Only blocks that
    exist on the page, are substantial, and sit inside (or right next to) the
    content span are accepted; the result is re-sorted by DOM order, so a
    recovered middle section lands in the middle, never at the end.
    """
    if not content or not page_blocks:
        return content
    by_key: dict[str, Block] = {}
    for block in page_blocks:
        by_key.setdefault(block.key, block)
    have = {block.order for block in content}
    lo = min(have)
    hi = max(have)
    span = max(hi - lo, 1)
    window_lo, window_hi = lo - max(8, span // 4), hi + max(8, span // 4)
    budget = sum(block.body_len for block in content) * max_growth
    added: list[Block] = []
    used = 0
    for text in extra_texts:
        key = block_key(text)
        if len(key) < 12:
            continue
        match = by_key.get(key)
        if match is None:
            prefix = key[:80]
            match = next((b for b in page_blocks if prefix and (prefix in b.key or (b.key and b.key[:80] in key))), None)
        if match is None or match.order in have:
            continue
        is_table = match.kind == "table"
        if not is_table and match.body_len < min_chars and match.kind != "heading":
            continue
        if not (window_lo <= match.order <= window_hi):
            continue
        if used + match.body_len > budget:
            break
        added.append(match)
        have.add(match.order)
        used += match.body_len
    if not added:
        return content
    merged = sorted([*content, *added], key=lambda block: block.order)
    return dedupe_blocks(merged)
