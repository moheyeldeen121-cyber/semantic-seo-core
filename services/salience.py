"""
Deterministic Entity Salience
=============================

The AI decides *what* the entities are; this module decides *how important*
each one is in THIS document, from evidence in the text itself — not from a
number the model guessed.

Signals (all measured on the structured extraction, Arabic-aware):

* **Placement** — Title, H1, meta description, H2, H3+, and the lead (the
  first body paragraphs). Combined as a probabilistic OR, so an entity in the
  title *and* an H2 scores higher than one in either alone.
* **Frequency** — occurrences in headings + body, log-scaled against the
  document's own distribution (so long articles do not inflate everything).
* **Spread** — share of H2 sections that mention the entity: topical
  necessity rather than one heavily-repeated paragraph.
* **Earliness** — how soon the entity first appears.

Matching uses the same normalization as the gap engine (article/clitics,
hamza/ta-marbuta/alef-maqsura folding, diacritics) plus proclitic/suffix
tolerance (و/ف/ب/ل, ات/ون/ين/ها/هم...), and the AI-supplied ``source_term``
(the verbatim surface form in the text) and LSI variants.

Final salience = 0.75 × deterministic + 0.25 × AI prior when the entity is
found in the text. An entity that cannot be found is kept but marked
``grounded = False`` and its salience is capped at 0.4 × the AI prior.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from models.schemas import EntityItem, SourceAnalysis
from services.semantic import _normalize, token_forms

_ZONE_WEIGHTS = {"title": 1.0, "h1": 1.0, "h2": 0.7, "lead": 0.6, "description": 0.55, "h3": 0.5}
_IMPORTANCE_PRIOR = {"high": 0.9, "medium": 0.6, "low": 0.35}
_MARKER = re.compile(r"^\[(Title|Description|H[1-6])\]\s*:\s*(.*)$", re.I | re.S)
_OTHER_MARKER = re.compile(r"^\[[^\]]{1,24}\]\s*:?\s*", re.S)
_SENTINEL = "\x00"


@dataclass
class _Doc:
    tokens: list[str] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)      # zone per token
    sections: list[int] = field(default_factory=list)   # H2-section index per token
    body_rank: list[int] = field(default_factory=list)  # position among body tokens (-1 = not body)
    index: dict[str, list[int]] = field(default_factory=dict)
    section_count: int = 0
    body_tokens: int = 0


def parse_document(text: str, lead_tokens: int = 120) -> _Doc:
    doc = _Doc()
    section = 0
    seen_h1 = False
    body_seen = 0
    for raw_block in re.split(r"\n\s*\n", text or ""):
        block = raw_block.strip()
        if not block:
            continue
        match = _MARKER.match(block)
        zone = "body"
        content = block
        if match:
            marker, content = match.group(1).lower(), match.group(2)
            if marker == "title":
                zone = "title"
            elif marker == "description":
                zone = "description"
            elif marker == "h1":
                zone = "h1"
                seen_h1 = True
            elif marker == "h2":
                zone = "h2"
                section += 1
            else:
                zone = "h3"
        else:
            content = _OTHER_MARKER.sub("", block).replace("•", " ")
        words = _normalize(content).split()
        if not words:
            continue
        for word in words:
            position = len(doc.tokens)
            doc.tokens.append(word)
            actual_zone = zone
            rank = -1
            if zone == "body":
                rank = body_seen
                body_seen += 1
                if rank < lead_tokens and (seen_h1 or section == 0):
                    actual_zone = "lead"
            doc.zones.append(actual_zone)
            doc.sections.append(section)
            doc.body_rank.append(rank)
            for form in token_forms(word):
                doc.index.setdefault(form, []).append(position)
        doc.tokens.append(_SENTINEL)          # phrases never cross blocks
        doc.zones.append("sep")
        doc.sections.append(section)
        doc.body_rank.append(-1)
    doc.section_count = section
    doc.body_tokens = body_seen
    return doc


def _phrase_positions(doc: _Doc, phrase: str) -> list[int]:
    words = _normalize(phrase).split()
    if not words or len(words) > 8:
        return []
    wanted = [token_forms(word) for word in words]
    starts: set[int] = set()
    for form in wanted[0]:
        starts.update(doc.index.get(form, ()))
    hits: list[int] = []
    for start in sorted(starts):
        ok = True
        for offset in range(1, len(words)):
            position = start + offset
            if position >= len(doc.tokens):
                ok = False
                break
            if not (token_forms(doc.tokens[position]) & wanted[offset]):
                ok = False
                break
        if ok:
            hits.append(start)
    return hits


@dataclass
class SalienceSignals:
    frequency: int = 0
    placements: list[str] = field(default_factory=list)
    spread: float = 0.0
    first_rank: float = 1.0
    deterministic: float = 0.0


def measure(doc: _Doc, surface_forms: list[str]) -> SalienceSignals:
    positions: set[int] = set()
    for surface in surface_forms:
        if surface and len(surface.strip()) >= 2:
            positions.update(_phrase_positions(doc, surface))
    signals = SalienceSignals()
    if not positions:
        return signals
    zones = {doc.zones[p] for p in positions}
    signals.placements = [zone for zone in ("title", "h1", "description", "h2", "h3", "lead") if zone in zones]
    signals.frequency = sum(1 for p in positions if doc.zones[p] not in ("title", "description"))
    if doc.section_count >= 2:
        sections = {doc.sections[p] for p in positions if doc.sections[p] > 0}
        signals.spread = len(sections) / doc.section_count
    ranks = [doc.body_rank[p] for p in positions if doc.body_rank[p] >= 0]
    if ranks and doc.body_tokens:
        signals.first_rank = min(ranks) / max(doc.body_tokens, 1)
    elif signals.placements:
        signals.first_rank = 0.0
    return signals


def _placement_score(placements: list[str]) -> float:
    miss = 1.0
    for zone in placements:
        miss *= 1.0 - _ZONE_WEIGHTS.get(zone, 0.0)
    return 1.0 - miss


def apply_salience(analysis: SourceAnalysis, text: str, keyword: str = "") -> SourceAnalysis:
    """Recompute salience/mentions/placements/role for every entity of one
    source from the source's own structured text. Returns a copy."""
    updated = analysis.model_copy(deep=True)
    entities = list(updated.raw_entities)
    for cluster in updated.clusters:
        entities.extend(cluster.entities)
    if not entities or not (text or "").strip():
        return updated

    doc = parse_document(text)
    keyword_forms: set[str] = set()
    for word in _normalize(keyword or "").split():
        keyword_forms |= token_forms(word)

    measured: dict[str, SalienceSignals] = {}
    for entity in entities:
        key = _normalize(entity.name)
        if key in measured:
            continue
        surfaces = [entity.name, getattr(entity, "source_term", "") or "", *entity.lsi[:12]]
        measured[key] = measure(doc, surfaces)

    frequencies = sorted(signal.frequency for signal in measured.values() if signal.frequency)
    reference = frequencies[int(len(frequencies) * 0.9) - 1] if len(frequencies) >= 5 else (max(frequencies) if frequencies else 1)
    reference = max(reference, 6)

    for entity in entities:
        signal = measured.get(_normalize(entity.name)) or SalienceSignals()
        ai_prior = 0.5 * float(entity.salience or 0.0) + 0.5 * _IMPORTANCE_PRIOR.get(entity.importance, 0.6)
        if signal.frequency or signal.placements:
            placement = _placement_score(signal.placements)
            frequency = min(1.0, math.log1p(signal.frequency) / math.log1p(reference))
            spread = signal.spread if doc.section_count >= 2 else min(1.0, frequency * 0.8)
            early = 1.0 - min(1.0, signal.first_rank)
            deterministic = 0.40 * placement + 0.30 * frequency + 0.18 * spread + 0.12 * early
            if keyword_forms:
                name_forms: set[str] = set()
                for word in _normalize(entity.name).split():
                    name_forms |= token_forms(word)
                if name_forms & keyword_forms:
                    deterministic = min(1.0, deterministic + 0.05)
            salience = 0.75 * deterministic + 0.25 * ai_prior
            entity.mentions = max(1, signal.frequency)
            entity.grounded = True
        else:
            salience = 0.4 * ai_prior
            entity.mentions = 0
            entity.grounded = False
        entity.salience = round(max(0.0, min(1.0, salience)), 3)
        entity.placements = list(signal.placements)
        if not entity.grounded:
            entity.importance = "low"
        else:
            entity.importance = "high" if entity.salience >= 0.6 else ("medium" if entity.salience >= 0.36 else "low")
        structural = any(zone in signal.placements for zone in ("title", "h1", "h2"))
        entity.role = "core" if (entity.salience >= 0.6 or (structural and entity.salience >= 0.45)) else "supporting"
    return updated
