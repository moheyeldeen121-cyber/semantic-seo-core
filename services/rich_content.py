"""Normalize AI-authored content into safe semantic HTML.

The UI, clipboard, and Word export all consume the same canonical document,
so raw Markdown markers never leak into the final experience even when a
model ignores the requested HTML-only format.
"""

from __future__ import annotations

import html
import re
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

_ALLOWED_TAGS = {
    "article", "section", "h1", "h2", "h3", "h4", "p", "ul", "ol", "li",
    "strong", "em", "blockquote", "table", "thead", "tbody", "tr", "th", "td",
    "hr", "br", "span",
}
_ALLOWED_ATTRS = {
    "article": {"dir", "lang"},
    "section": {"dir", "lang"},
    "span": {"class"},
    "table": {"dir"},
}
_SAFE_SPAN_CLASSES = {"meta-label", "content-note", "visual-suggestion", "cta-suggestion"}

_FENCE_RE = re.compile(r"^\s*```(?:html|markdown|md)?\s*|\s*```\s*$", re.I)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.+)$")
_NUMBER_RE = re.compile(r"^\d+[.)]\s+(.+)$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)+\s*\|?\s*$")
_INLINE_RE = re.compile(r"(\*\*.+?\*\*|__.+?__|\*[^*]+?\*|_[^_]+?_)")


def _inline_markdown(value: str) -> str:
    """Convert a deliberately tiny safe subset of inline Markdown."""
    out: list[str] = []
    for token in _INLINE_RE.split(value):
        if not token:
            continue
        escaped = html.escape(token)
        if (token.startswith("**") and token.endswith("**")) or (
            token.startswith("__") and token.endswith("__")
        ):
            out.append(f"<strong>{html.escape(token[2:-2].strip())}</strong>")
        elif (token.startswith("*") and token.endswith("*")) or (
            token.startswith("_") and token.endswith("_")
        ):
            out.append(f"<em>{html.escape(token[1:-1].strip())}</em>")
        else:
            out.append(escaped)
    return "".join(out)


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_to_semantic_html(raw: str, language: str = "ar") -> str:
    """Best-effort Markdown fallback used only when a model ignores HTML-only."""
    raw = _FENCE_RE.sub("", raw or "").strip()
    lines = raw.replace("\r\n", "\n").split("\n")
    output: list[str] = []
    paragraph: list[str] = []
    list_type: str | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(item.strip() for item in paragraph if item.strip())
            if text:
                output.append(f"<p>{_inline_markdown(text)}</p>")
        paragraph = []

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            output.append(f"</{list_type}>")
            list_type = None

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            flush_paragraph()
            close_list()
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)), 4)
            output.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            i += 1
            continue

        if "|" in line and i + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[i + 1].strip()):
            flush_paragraph()
            close_list()
            header_cells = _split_table_row(line)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            output.append("<table><thead><tr>" + "".join(
                f"<th>{_inline_markdown(cell)}</th>" for cell in header_cells
            ) + "</tr></thead><tbody>")
            for row in rows:
                output.append("<tr>" + "".join(f"<td>{_inline_markdown(cell)}</td>" for cell in row) + "</tr>")
            output.append("</tbody></table>")
            continue

        bullet = _BULLET_RE.match(line)
        numbered = _NUMBER_RE.match(line)
        if bullet or numbered:
            flush_paragraph()
            wanted = "ul" if bullet else "ol"
            if list_type != wanted:
                close_list()
                output.append(f"<{wanted}>")
                list_type = wanted
            output.append(f"<li>{_inline_markdown((bullet or numbered).group(1))}</li>")
            i += 1
            continue

        if line.startswith(">"):
            flush_paragraph()
            close_list()
            output.append(f"<blockquote>{_inline_markdown(line.lstrip('> ').strip())}</blockquote>")
            i += 1
            continue

        if re.fullmatch(r"[-*_]{3,}", line):
            flush_paragraph()
            close_list()
            output.append("<hr>")
            i += 1
            continue

        paragraph.append(line)
        i += 1

    flush_paragraph()
    close_list()
    direction = "rtl" if language == "ar" else "ltr"
    return f'<article dir="{direction}" lang="{language}">' + "".join(output) + "</article>"


def _sanitize_tree(soup: BeautifulSoup) -> None:
    dangerous = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript"}
    for tag in list(soup.find_all(True)):
        name = tag.name.lower()
        if name in dangerous:
            tag.decompose()
            continue
        if name not in _ALLOWED_TAGS:
            tag.unwrap()
            continue
        allowed = _ALLOWED_ATTRS.get(name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
        if name == "span" and "class" in tag.attrs:
            classes = [value for value in tag.get("class", []) if value in _SAFE_SPAN_CLASSES]
            if classes:
                tag["class"] = classes
            else:
                del tag.attrs["class"]


def _remove_empty_nodes(soup: BeautifulSoup) -> None:
    for tag in reversed(list(soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "blockquote"]))):
        if not tag.get_text(" ", strip=True) and not tag.find(["br"]):
            tag.decompose()


def _repair_heading_jumps(soup: BeautifulSoup) -> None:
    """Prevent visually illogical H2→H4 jumps while preserving text order."""
    previous_level = 0
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        level = int(heading.name[1])
        if previous_level and level > previous_level + 1:
            level = previous_level + 1
            heading.name = f"h{level}"
        previous_level = level


def normalize_rich_document(raw: str, language: str = "ar") -> str:
    """Return canonical, sanitized, semantic HTML for UI/copy/export."""
    cleaned = _FENCE_RE.sub("", raw or "").strip()
    looks_html = bool(re.search(r"<(?:article|section|h[1-4]|p|ul|ol|table)\b", cleaned, re.I))
    candidate = cleaned if looks_html else markdown_to_semantic_html(cleaned, language)

    soup = BeautifulSoup(candidate, "html.parser")
    _sanitize_tree(soup)
    _remove_empty_nodes(soup)
    _repair_heading_jumps(soup)

    article = soup.find("article")
    if article is None:
        article = soup.new_tag("article")
        for child in list(soup.contents):
            article.append(child.extract())
        soup.append(article)
    article["dir"] = "rtl" if language == "ar" else "ltr"
    article["lang"] = language

    # Strip literal Markdown heading/bold markers that occasionally survive in
    # model text despite HTML instructions.
    for text_node in list(article.find_all(string=True)):
        if not isinstance(text_node, NavigableString):
            continue
        value = str(text_node)
        value = re.sub(r"^\s*#{1,6}\s+", "", value)
        value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
        value = re.sub(r"__(.+?)__", r"\1", value)
        if value != str(text_node):
            text_node.replace_with(value)

    return str(article)


def html_to_plain_text(document_html: str) -> str:
    soup = BeautifulSoup(document_html or "", "html.parser")
    lines: list[str] = []
    for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "th", "td", "blockquote"]):
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        if node.name == "li":
            text = f"• {text}"
        lines.append(text)
    return "\n\n".join(lines)


def combine_documents(parts: Iterable[str], language: str = "ar") -> str:
    direction = "rtl" if language == "ar" else "ltr"
    inner: list[str] = []
    for part in parts:
        normalized = normalize_rich_document(part, language)
        soup = BeautifulSoup(normalized, "html.parser")
        article = soup.find("article")
        if article:
            inner.extend(str(child) for child in article.contents)
    return normalize_rich_document(
        f'<article dir="{direction}" lang="{language}">' + "".join(inner) + "</article>",
        language,
    )
