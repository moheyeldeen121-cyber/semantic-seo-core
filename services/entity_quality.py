"""
Entity Quality Layer (domain-agnostic)
======================================

The AI extracts candidates; this module decides which of them are real,
comparable *topical* entities. Every rule is structural or linguistic — none
is tied to a niche — so it behaves the same on a law firm, a clinic, a store,
a hotel, or a SaaS blog.

Pre-salience (``refine_entities``)
----------------------------------
1. **Heading-like compounds** — "X والY …" / "X and Y" names with ≥3 words
   are split into their parts; a part is kept only if it occurs in the text.
2. **LSI hygiene** — a variant is dropped when it is the name of another
   entity, duplicates the entity itself, is too long, or is claimed by two
   entities whose names are unrelated (then it is a *related* concept,
   not a synonym of either).
3. **Near-duplicate merge** — two entities merge when one is listed as the
   other's variant, or when they share a variant and their names overlap.

Post-salience (``separate_signals``)
------------------------------------
4. **Scope** — ``topic`` entities feed clusters and gaps. ``brand`` (the
   publishing site itself, its staff/authors) and ``context`` (incidental
   mentions such as a city listed once) are moved to ``signal_entities``:
   still reported, never compared against competitors.
   * brand: AI scope, or name matching the title's site-name suffix / domain.
   * context: AI scope, or a Place/Person/Organization/Event mentioned once
     outside all headings.
   An entity in the Title/H1/H2 always stays ``topic`` — if the page is
   *about* it, it is the subject, whatever else it is.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from models.schemas import EntityItem, SemanticCluster, SourceAnalysis
from services.semantic import _covered_by_text, _normalize, _significant_words

_TITLE_SPLIT = re.compile(r"\s+[|｜\-–—:]\s+")
_HEADING = re.compile(r"^\[(Title|H1)\]\s*:\s*(.+)$", re.I | re.M)
_CONJUNCTION_EN = {"and", "&", "or"}
_INCIDENTAL_TYPES = {"Place", "Person", "Organization", "Event"}
# "brand" means the publisher itself or its people. A service, product or
# concept the site OFFERS is what the page is about, so it stays a topic.
_BRAND_TYPES = {"Organization", "Person"}
_SITE_CLUSTER_NAMES = {"الموقع والسياق", "site & context", "site and context"}
_STRUCTURAL = {"title", "h1", "h2"}
_MAX_LSI = 15
_IMPORTANCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _words(name: str) -> set[str]:
    return set(_significant_words(_normalize(name)))


def _name_similarity(first: str, second: str) -> float:
    a, b = _words(first), _words(second)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── 1. compounds ────────────────────────────────────────────────────────────

def _split_compound(name: str) -> list[str] | None:
    tokens = name.split()
    if len(tokens) < 3:
        return None  # "البيع والشراء", "Pros and Cons" are legitimate concepts
    for index in range(1, len(tokens)):
        token = tokens[index]
        lowered = token.lower()
        if lowered in _CONJUNCTION_EN:
            left, right = tokens[:index], tokens[index + 1:]
        elif token.startswith("وال") and len(token) > 4:
            left, right = tokens[:index], [token[1:], *tokens[index + 1:]]
        else:
            continue
        if left and right:
            return [" ".join(left), " ".join(right)]
    if "،" in name or "/" in name:
        parts = [part.strip() for part in re.split(r"[،/]", name) if part.strip()]
        return parts if len(parts) > 1 else None
    return None


def _clone(entity: EntityItem, name: str) -> EntityItem:
    return entity.model_copy(update={"name": name, "lsi": [], "source_term": name})


# ── 2 + 3. variants and duplicates ──────────────────────────────────────────

def _ambiguous_variants(entities: list[EntityItem]) -> set[str]:
    """Variants claimed by two entities with unrelated names are related
    concepts, not synonyms of either."""
    claims: dict[str, list[EntityItem]] = {}
    for entity in entities:
        own = _normalize(entity.name)
        seen: set[str] = set()
        for term in entity.lsi:
            key = _normalize(term)
            if not key or key == own or key in seen or len(term.split()) > 6:
                continue
            seen.add(key)
            claims.setdefault(key, []).append(entity)

    ambiguous: set[str] = set()
    for key, owners in claims.items():
        if len(owners) < 2:
            continue
        for i, first in enumerate(owners):
            for second in owners[i + 1:]:
                if _name_similarity(first.name, second.name) < 0.34:
                    ambiguous.add(key)
    return ambiguous


def _clean_variants(entities: list[EntityItem], extra_ambiguous: set[str] | None = None) -> None:
    names = {_normalize(entity.name): entity for entity in entities}
    ambiguous = _ambiguous_variants(entities) | (extra_ambiguous or set())
    for entity in entities:
        own = _normalize(entity.name)
        kept: list[str] = []
        seen = set()
        for term in entity.lsi:
            key = _normalize(term)
            if (not key or key == own or key in seen or len(term.split()) > 6
                    or key in ambiguous or (key in names and names[key] is not entity)):
                continue
            seen.add(key)
            kept.append(term.strip())
        entity.lsi = kept[:_MAX_LSI]


def _merge_duplicates(entities: list[EntityItem]) -> list[EntityItem]:
    parent = list(range(len(entities)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    keys = [_normalize(entity.name) for entity in entities]
    variants = [{_normalize(term) for term in entity.lsi} for entity in entities]
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            listed = keys[i] in variants[j] or keys[j] in variants[i]
            shared = bool(variants[i] & variants[j]) and _name_similarity(entities[i].name, entities[j].name) >= 0.34
            if listed or shared:
                parent[find(j)] = find(i)

    groups: dict[int, list[int]] = {}
    for index in range(len(entities)):
        groups.setdefault(find(index), []).append(index)

    merged: list[EntityItem] = []
    for root in sorted(groups):
        members = [entities[index] for index in groups[root]]
        canonical = max(members, key=lambda e: (_IMPORTANCE_RANK.get(e.importance, 2), e.salience, -len(e.name)))
        lsi = list(canonical.lsi)
        for other in members:
            if other is canonical:
                continue
            for term in [other.name, *other.lsi]:
                if _normalize(term) != _normalize(canonical.name) and _normalize(term) not in {_normalize(t) for t in lsi}:
                    lsi.append(term)
        canonical.lsi = lsi[:_MAX_LSI]
        merged.append(canonical)
    order = {id(entity): position for position, entity in enumerate(entities)}
    merged.sort(key=lambda entity: order[id(entity)])
    return merged


def _rebuild_clusters(entities: list[EntityItem], original: list[SemanticCluster]) -> list[SemanticCluster]:
    order = [cluster.cluster_name for cluster in original]
    grouped: dict[str, list[EntityItem]] = {}
    for entity in entities:
        grouped.setdefault(entity.cluster, []).append(entity)
    names = [name for name in order if name in grouped] + [name for name in grouped if name not in order]
    return [SemanticCluster(cluster_name=name, entities=grouped[name][:800]) for name in names]


def refine_entities(analysis: SourceAnalysis, text: str) -> SourceAnalysis:
    """Pre-salience cleanup: compounds, variant hygiene, duplicates."""
    updated = analysis.model_copy(deep=True)
    normalized_text = _normalize(text or "")
    # Judge ambiguity on the ORIGINAL list too: a compound that gets split
    # still testifies that its variants belong to more than one thing.
    original_ambiguous = _ambiguous_variants(list(updated.raw_entities))
    entities: list[EntityItem] = []
    known: set[str] = set()
    for entity in updated.raw_entities:
        parts = _split_compound(entity.name)
        if parts:
            for part in parts:
                key = _normalize(part)
                if key and key not in known and normalized_text and _covered_by_text(part, normalized_text):
                    known.add(key)
                    entities.append(_clone(entity, part))
            continue
        key = _normalize(entity.name)
        if key and key not in known:
            known.add(key)
            entities.append(entity)

    _clean_variants(entities, original_ambiguous)
    entities = _merge_duplicates(entities)
    updated.raw_entities = entities
    updated.clusters = _rebuild_clusters(entities, updated.clusters)
    return updated


# ── 4. scope ────────────────────────────────────────────────────────────────

def _site_names(text: str, source_url: str) -> tuple[list[str], str]:
    names: list[str] = []
    h1 = ""
    for match in _HEADING.finditer(text or ""):
        kind, value = match.group(1).lower(), match.group(2).strip()
        if kind == "h1" and not h1:
            h1 = value
        if kind == "title":
            parts = _TITLE_SPLIT.split(value)
            if len(parts) >= 2 and len(parts[-1].split()) <= 6:
                names.append(parts[-1])
    host = ""
    try:
        host = (urlsplit(source_url).hostname or "").lower()
    except ValueError:
        host = ""
    host = re.sub(r"^www\d?\.", "", host)
    label = host.split(".")[0] if host else ""
    return names, label if len(label) >= 4 else ""


def _is_site_brand(entity: EntityItem, site_names: list[str], domain_label: str) -> bool:
    for site in site_names:
        if _name_similarity(entity.name, site) >= 0.6:
            return True
    compact = re.sub(r"[^a-z0-9]", "", entity.name.lower())
    return bool(domain_label and len(compact) >= 4 and (compact in domain_label or domain_label in compact))


def separate_signals(analysis: SourceAnalysis, text: str, source_url: str = "") -> SourceAnalysis:
    """Post-salience: keep topical entities for comparison; move the site's
    own brand/staff and incidental context mentions to ``signal_entities``."""
    updated = analysis.model_copy(deep=True)
    site_names, domain_label = _site_names(text, source_url)
    h1_key = _normalize(next((m.group(2) for m in _HEADING.finditer(text or "") if m.group(1).lower() == "h1"), ""))

    topical: list[EntityItem] = []
    signals: list[EntityItem] = list(updated.signal_entities)
    for entity in updated.raw_entities:
        structural = bool(_STRUCTURAL & set(entity.placements))
        scope = entity.scope or "topic"
        site_brand = _is_site_brand(entity, site_names, domain_label)
        if scope == "brand" and not site_brand and entity.entity_type not in _BRAND_TYPES:
            scope = "topic"
        if scope == "topic" and site_brand:
            scope = "brand"
        if (scope == "topic" and entity.entity_type in _INCIDENTAL_TYPES
                and entity.mentions <= 1 and not (set(entity.placements) & {"title", "h1", "h2", "h3", "description"})):
            scope = "context"
        # The page's subject is never a side signal.
        key = _normalize(entity.name)
        if scope != "topic" and structural and not (scope == "brand" and h1_key and key not in h1_key):
            scope = "topic"
        entity.scope = scope
        if scope == "topic" and entity.cluster.strip().lower() in _SITE_CLUSTER_NAMES:
            arabic = bool(re.search(r"[\u0600-\u06FF]", entity.cluster))
            if entity.entity_type == "Place":
                entity.cluster = "النطاق الجغرافي" if arabic else "Geographic scope"
            else:
                entity.cluster = "مواضيع عامة" if arabic else "General topics"
        (topical if scope == "topic" else signals).append(entity)

    updated.raw_entities = topical
    updated.signal_entities = signals[:1000]
    updated.clusters = _rebuild_clusters(topical, updated.clusters)
    return updated
