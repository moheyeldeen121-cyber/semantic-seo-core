"""
Semantic Gap Analysis
======================
Originally relocated verbatim from app.py section 4; matching between "my
site" entities and competitor entities has since been hardened (see
_normalize / _covered_by_text below) because exact lowercase string
equality was letting the same concept — extracted with slightly different
wording by independent AI calls per source — slip through as a false
"gap" even when the client's own content already covers it heavily.
Priority thresholds and sort order are unchanged.
"""

import re
import unicodedata
from collections import Counter

import config
from models.schemas import EntityItem, SemanticGap, SourceAnalysis

# Arabic tashkeel (diacritics) + tatweel — purely decorative, safe to drop
# before comparing two entity names for equality.
_LATIN_MARKS = re.compile(r"[\u0300-\u036f]")
_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭـ]")

_STOPWORDS = {
    "في", "من", "على", "الى", "إلى", "عن", "مع", "هذا", "هذه", "ذلك", "تلك",
    "التي", "الذي", "او", "أو", "و", "ثم", "كل", "بين", "حول", "the", "a",
    "an", "of", "in", "on", "for", "and", "or", "to", "with", "by",
    # Other supported content languages (words shorter than 3 letters are
    # already ignored by _significant_words).
    "the", "les", "des", "une", "pour", "par", "avec", "dans", "sur", "est", "aux",   # fr
    "der", "die", "das", "und", "mit", "fur", "von", "den", "dem", "ein", "eine", "auf", "ist",  # de
    "los", "las", "del", "para", "por", "con", "una", "que", "como",                 # es / pt
    "com", "uma", "dos", "das",                                                     # pt
    "per", "della", "dei", "che",                                                   # it
    "het", "een", "van", "voor", "met", "zijn",                                     # nl
    "icin", "ile", "bir", "olan",                                                   # tr
    "dan", "yang", "untuk", "dengan", "dari",                                       # id
    "для", "как", "что", "или", "это",                                              # ru
}


# Arabic-Indic / Persian digits → ASCII, Persian letter forms → Arabic.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PERSIAN = str.maketrans({"ک": "ك", "ی": "ي", "ۀ": "ه", "ٱ": "ا", "ڤ": "ف", "گ": "ك", "پ": "ب", "چ": "ج"})

# Clitic + definite-article prefixes, longest first. "لل" is ل + ال with the
# alef elided ("للتسويق" = "ل" + "التسويق"). "كال" is deliberately absent:
# it collides with loanwords (كالسيوم، كالكوليتر).
_ARTICLE_PREFIXES = ("وبال", "فبال", "وال", "فال", "بال", "ولل", "فلل", "لل", "ال")


def _strip_definite_article(token: str) -> str:
    """Light, reversible-in-spirit stemming for the Arabic article and the
    conjunction/preposition clitics that attach to it.

    "الفنادق" / "والفنادق" / "بالفنادق" / "للفنادق" all key to "فنادق". The
    3-character floor keeps short real words (الله، والد، بالون) intact. The
    result is used ONLY as a comparison key — display names are never
    rewritten, so brand names are not damaged.
    """
    for prefix in _ARTICLE_PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            return token[len(prefix):]
    return token


def _normalize(text: str) -> str:
    """Fold away the surface differences (diacritics, alef/ya/ta-marbuta
    variants, hamza carriers, Persian letter forms, Eastern digits, the
    definite article with its clitics, whitespace) that make the *same*
    Arabic word/phrase compare as unequal across independent AI calls."""
    text = (text or "").strip().lower().translate(_DIGITS).translate(_PERSIAN)
    # Latin accents (é → e, ü → u, ç → c, ı → i) so French/Spanish/Turkish/
    # German spellings with and without diacritics compare equal.
    text = _LATIN_MARKS.sub("", unicodedata.normalize("NFKD", text)).replace("ı", "i").replace("ß", "ss")
    text = unicodedata.normalize("NFC", text)
    text = _TASHKEEL.sub("", text)
    text = re.sub(r"[\u0625\u0623\u0622\u0627\u0671]", "\u0627", text)
    text = text.replace("\u0649", "\u064a").replace("\u0629", "\u0647").replace("\u0624", "\u0648").replace("\u0626", "\u064a")
    text = re.sub(r"[\u060c\u061b\u061f\u066a-\u066d\u06d4\u00ab\u00bb]", " ", text)
    text = re.sub(r"[^\w\s\u0600-\u06FF]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(_strip_definite_article(token) for token in text.split())


# Single-letter proclitics that attach without the article (و/ف/ب/ل) and
# the most frequent inflectional/pronominal suffixes. Used for MATCHING only
# (a text token may be "وتسويق" or "خدمات" while the entity is "تسويق" /
# "خدمه"), never for keys.
_PROCLITICS = ("وب", "ول", "فب", "فل", "و", "ف", "ب", "ل")
_SUFFIXES_SHORT = ("هما", "ها", "هم", "كم", "نا", "ات", "ه")  # remainder >= 3
_SUFFIXES_LONG = ("ون", "ين", "ان")                          # remainder >= 4


def token_forms(token: str) -> set[str]:
    """All base forms a (normalized) token can stand for."""
    forms = {token}
    if re.fullmatch(r"[a-z0-9]+", token):
        if len(token) > 4 and token.endswith("es"):
            forms.add(token[:-2])
        if len(token) > 3 and token.endswith("s"):
            forms.add(token[:-1])
        return forms
    for prefix in _PROCLITICS:
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            forms.add(token[len(prefix):])
    for form in list(forms):
        for suffix in _SUFFIXES_SHORT:
            if form.endswith(suffix) and len(form) - len(suffix) >= 3:
                forms.add(form[: -len(suffix)])
        for suffix in _SUFFIXES_LONG:
            if form.endswith(suffix) and len(form) - len(suffix) >= 4:
                forms.add(form[: -len(suffix)])
    return forms


def _significant_words(name: str) -> list[str]:
    return [w for w in _normalize(name).split() if len(w) >= 3 and w not in _STOPWORDS]


def _covered_by_text(name: str, normalized_source_text: str) -> bool:
    """True if every meaningful word of `name` appears in the client's own
    text *close together*.

    The previous version only asked whether each word appeared *somewhere* in
    the document, so an article about "تحسين تجربة المستخدم" that also
    happened to mention "محركات" and "البحث" hundreds of words apart was
    treated as already covering "تحسين محركات البحث" — hiding a real gap.
    Requiring the words to co-occur inside a small window makes coverage mean
    "the phrase is actually discussed", not "these letters exist on the page".
    """
    words = _significant_words(name)
    if not words:
        return False

    tokens = normalized_source_text.split()
    if not tokens:
        return False

    positions: list[list[int]] = []
    for word in words:
        hits = [index for index, token in enumerate(tokens)
                if token == word or token.startswith(word) or word in token]
        if not hits:
            return False
        positions.append(hits)

    # Smallest window that contains at least one occurrence of every word.
    flat = sorted((index, slot) for slot, hits in enumerate(positions) for index in hits)
    needed = len(words)
    counts: Counter = Counter()
    matched = 0
    best_span = None
    left = 0
    for right, (index, slot) in enumerate(flat):
        counts[slot] += 1
        if counts[slot] == 1:
            matched += 1
        while matched == needed:
            span = index - flat[left][0] + 1
            if best_span is None or span < best_span:
                best_span = span
            left_slot = flat[left][1]
            counts[left_slot] -= 1
            if counts[left_slot] == 0:
                matched -= 1
            left += 1
    if best_span is None:
        return False
    return best_span <= max(6, len(words) * 3)


# ─────────────────────────────────────────────────────────────────────────────
# Topic canonicalisation
#
# Every source is analysed by an INDEPENDENT AI call, so the same concept comes
# back spelled differently each time ("تعلم الآلة" / "تعلم الآلة الآلي",
# "شات بوت" / "شات بوت واتساب" / "بوت واتساب AI"). Counting gaps on exact
# string equality therefore made almost every entity look like it belonged to a
# single competitor — which is how a report ends up with 223 "gaps" of which
# 97% came from one source. Grouping spellings into topics before counting is
# what makes "how many independent competitors cover this?" a real number.
# ─────────────────────────────────────────────────────────────────────────────


# Words that qualify a term without changing what it is (all supported
# languages, normalized form). Kept deliberately small and domain-free.
_GENERIC_MODIFIERS = {
    _normalize(_w) for _w in (
        "خدمات", "خدمه", "احترافي", "احترافيه", "افضل", "متخصص", "متخصصه", "شامل", "شامله",
        "services", "service", "professional", "best", "expert", "top", "complete", "online",
        "servicios", "servicio", "profesional", "mejor", "prestations", "professionnel", "meilleur",
        "dienstleistungen", "professionelle", "beste", "hizmetleri", "profesyonel", "layanan", "услуги",
    )
}


def _same_topic(a_words: set[str], b_words: set[str]) -> bool:
    if not a_words or not b_words:
        return False
    if a_words == b_words:
        # Two names can reduce to the same single keyword while meaning
        # different things ("نموذج أ" / "نموذج ب"), because tokens shorter than
        # three characters are dropped. Require a second shared word before
        # treating a one-word match as the same topic.
        return len(a_words) >= 2
    smaller, larger = (a_words, b_words) if len(a_words) <= len(b_words) else (b_words, a_words)
    # A close variant: the same words plus ONE generic modifier ("خدمات",
    # "professional", "best"...). Any other extra word makes a narrower
    # sub-topic ("local SEO" is not "SEO", "محامي عمالي" is not "محامي"), and
    # merging those hid real gaps: mentioning the head term counted as
    # covering every sub-topic built on it.
    if smaller <= larger and len(larger) - len(smaller) == 1:
        (extra,) = tuple(larger - smaller)
        return bool(token_forms(extra) & _GENERIC_MODIFIERS)
    overlap = len(a_words & b_words) / len(a_words | b_words)
    return overlap >= 0.67


class TopicIndex:
    """Groups entity spellings into topics and tracks which sources use them."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._words: dict[str, set[str]] = {}
        self._alias_owner: dict[str, str] = {}
        self.sources: dict[str, set[str]] = {}
        self.mentions: Counter = Counter()
        self.display: dict[str, tuple[int, str]] = {}
        self.entity: dict[str, EntityItem] = {}
        self.evidence: dict[str, float] = {}

    def _find(self, key: str) -> str:
        while self._parent[key] != key:
            self._parent[key] = self._parent[self._parent[key]]
            key = self._parent[key]
        return key

    def _union(self, first: str, second: str) -> None:
        root_a, root_b = self._find(first), self._find(second)
        if root_a == root_b:
            return
        # Keep the shorter phrase as the group head so the display name stays
        # readable ("تعلم الآلة", not "نموذج تعلم آلي متقدم").
        if len(self._words[root_b]) < len(self._words[root_a]):
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a

    def add(self, name: str, aliases: list[str] | None = None) -> str:
        key = _normalize(name)
        if not key:
            return ""
        if key not in self._parent:
            self._parent[key] = key
            self._words[key] = set(_significant_words(key))
            words = self._words[key]
            for other in list(self._parent):
                if other == key:
                    continue
                if _same_topic(words, self._words[other]):
                    self._union(key, other)
            # This name may already have been declared as somebody else's
            # synonym before it showed up as an entity of its own.
            owner = self._alias_owner.get(key)
            if owner and owner in self._parent:
                self._union(key, owner)

        # The AI already hands us its own synonym list; using it is the
        # strongest matching signal available and it was previously ignored.
        for alias in aliases or []:
            alias_key = _normalize(alias)
            if not alias_key or alias_key == key:
                continue
            self._alias_owner.setdefault(alias_key, key)
            if alias_key in self._parent:
                self._union(key, alias_key)
        return self._find(key)

    def group_of(self, name: str) -> str:
        key = _normalize(name)
        return self._find(key) if key in self._parent else ""

    def record(self, entity: EntityItem, source_label: str) -> None:
        group = self.add(entity.name, entity.lsi)
        if not group:
            return
        self.sources.setdefault(group, set()).add(source_label)
        self.mentions[group] += entity.mentions
        self.evidence[group] = max(self.evidence.get(group, 0.0), float(entity.evidence_score or 0.0))
        self.entity.setdefault(group, entity)
        current = self.display.get(group)
        if current is None or entity.mentions > current[0]:
            self.display[group] = (entity.mentions, entity.name)

    def rebuild(self) -> None:
        """Re-key every dict after all unions are known."""
        for store in ("sources", "entity", "evidence"):
            merged: dict = {}
            for key, value in getattr(self, store).items():
                root = self._find(key)
                if store == "sources":
                    merged.setdefault(root, set()).update(value)
                elif store == "evidence":
                    merged[root] = max(merged.get(root, 0.0), value)
                else:
                    merged.setdefault(root, value)
            setattr(self, store, merged)

        mentions: Counter = Counter()
        for key, value in self.mentions.items():
            mentions[self._find(key)] += value
        self.mentions = mentions

        display: dict[str, tuple[int, str]] = {}
        for key, value in self.display.items():
            root = self._find(key)
            if root not in display or value[0] > display[root][0]:
                display[root] = value
        self.display = display


def build_topic_index(comp_analyses: list[SourceAnalysis],
                      my_entities: list[EntityItem] | None = None) -> TopicIndex:
    index = TopicIndex()
    for entity in my_entities or []:
        index.add(entity.name, entity.lsi)
    for position, analysis in enumerate(comp_analyses):
        label = analysis.source_label or f"source-{position}"
        for entity in analysis.raw_entities:
            index.record(entity, label)
    index.rebuild()
    return index


def _min_sources(competitor_count: int) -> int:
    """How many independent competitors must cover a topic for it to be a gap.

    With one or two sources there is no such thing as corroboration, so the
    threshold relaxes automatically instead of returning an empty report.
    """
    if competitor_count < 3:
        return 1
    return max(1, config.MIN_GAP_SOURCES)


def _analyze_topics(my_entities: list[EntityItem],
                    comp_analyses: list[SourceAnalysis],
                    my_text: str = "") -> tuple[list[str], list[EntityItem], list[dict]]:
    """Single pass behind BOTH the coverage figure and the gap list.

    Returning one result for both is what guarantees the report can never say
    "9 of 13 topics covered" next to a list of 3 gaps: by construction
    ``len(shared) == len(covered) + len(gap_candidates)``.
    """
    index = build_topic_index(comp_analyses, my_entities)
    threshold = _min_sources(len(comp_analyses))
    competitor_total = max(len(comp_analyses), 1)
    normalized_my_text = _normalize(my_text) if my_text else ""
    my_groups = {index.group_of(entity.name) for entity in my_entities}
    my_groups.discard("")

    shared: list[str] = []
    covered: list[EntityItem] = []
    candidates: list[dict] = []

    ordered = sorted(index.sources, key=lambda g: (-len(index.sources[g]), -index.mentions[g]))
    for group in ordered:
        source_entity = index.entity.get(group)
        if source_entity is None:
            continue
        source_count = len(index.sources[group])
        entity_type = source_entity.entity_type

        # Filters that decide the topic is not a usable signal at all — applied
        # BEFORE the shared/covered split so neither number is inflated.
        if source_count < threshold:
            continue
        if source_count < 2 and entity_type in ("Organization", "Person", "Place", "Product", "Event"):
            continue
        evidence = index.evidence.get(group, 0.0)
        if 0.0 < evidence < config.MIN_GAP_ENTITY_EVIDENCE:
            continue

        shared.append(group)

        if group in my_groups or (normalized_my_text and _covered_by_text(group, normalized_my_text)):
            covered.append(source_entity)
            continue

        total_mentions = index.mentions[group]
        coverage_ratio = source_count / competitor_total
        strong_consensus = coverage_ratio >= 0.7
        broad_and_heavy = source_count >= 3 and coverage_ratio >= 0.375 and total_mentions >= 8
        if strong_consensus or broad_and_heavy:
            priority = "critical"
        elif coverage_ratio >= 0.3 or source_count >= 3:
            priority = "high"
        else:
            priority = "medium"

        candidates.append({
            "name": index.display.get(group, (0, source_entity.name))[1],
            "type": entity_type,
            "lsi": list(source_entity.lsi),
            "competitor_count": source_count,
            "total_mentions": total_mentions,
            "priority": priority,
            "cluster": source_entity.cluster,
        })

    return shared, covered, candidates


def compute_topic_coverage(my_entities: list[EntityItem],
                           comp_analyses: list[SourceAnalysis],
                           my_text: str = "") -> dict:
    """How much of the competitors' SHARED agenda the client already covers.

    This is the number a content team actually wants: not "is the AI output
    trustworthy" but "of the topics the market agrees matter, how many are on
    my page?".
    """
    if not comp_analyses:
        return {"shared_topics": 0, "covered_topics": 0, "coverage_rate": 0.0}
    shared, covered, _candidates = _analyze_topics(my_entities, comp_analyses, my_text)
    return {
        "shared_topics": len(shared),
        "covered_topics": len(covered),
        "coverage_rate": (len(covered) / len(shared)) if shared else 0.0,
    }


def compute_semantic_gaps(
    my_entities: list[EntityItem],
    comp_analyses: list[SourceAnalysis],
    my_text: str = "",
) -> tuple[list[SemanticGap], list[EntityItem]]:
    """Topics the competitors agree on that the client's page does not cover.

    A "gap" requires corroboration from more than one independent source (see
    config.MIN_GAP_SOURCES). A topic only one of eight competitors talks about
    is that competitor's own brand, city, product, or niche — not a hole in the
    client's content — and reporting hundreds of those buried the handful of
    findings that were real.
    """
    if not comp_analyses:
        return [], []

    _shared, covered, candidates = _analyze_topics(my_entities, comp_analyses, my_text)
    gaps = [
        SemanticGap(
            name=item["name"],
            type=item["type"],
            lsi=item["lsi"],
            competitor_count=item["competitor_count"],
            total_mentions=item["total_mentions"],
            priority=item["priority"],
            cluster=item["cluster"],
        )
        for item in candidates
    ]
    priority_order = {"critical": 0, "high": 1, "medium": 2}
    gaps.sort(key=lambda gap: (priority_order[gap.priority], -gap.competitor_count, -gap.total_mentions))
    return gaps, covered


# ─────────────────────────────────────────────────────────────────────────────
# Master entity list (all sources, merged by topic, tiered)
# ─────────────────────────────────────────────────────────────────────────────

_MINE = "\x00mine"
_IMPORTANCE_ORDER = {"high": 3, "medium": 2, "low": 1}
_TIER_ORDER = {"core": 0, "supporting": 1, "long_tail": 2, "": 3}


def assign_tier(source_count: int, competitors: int, salience: float, in_mine: bool) -> str:
    """Core / supporting / long tail — the filter that separates the entities
    a page must cover from one-off mentions.

    * core: used by a meaningful share of the competitors (>= 30%, at least 2),
      or central on the client's own page (salience >= 0.6);
      with fewer than 3 competitors: every competitor uses it, or it is central.
    * supporting: used by >= 2 competitors, or reasonably salient (>= 0.4),
      or present on the client's page.
    * long_tail: a single mention in one source — kept, but not a priority.
    """
    import math

    if competitors >= 3:
        if source_count >= max(2, math.ceil(0.3 * competitors)) or (in_mine and salience >= 0.6):
            return "core"
    elif competitors >= 1:
        if (source_count >= competitors and salience >= 0.35) or salience >= 0.6:
            return "core"
    elif salience >= 0.6:
        return "core"
    if source_count >= 2 or salience >= 0.4 or in_mine:
        return "supporting"
    return "long_tail"


def build_master_entities(my_entities: list[EntityItem],
                          competitor_analyses: list[SourceAnalysis]) -> list[EntityItem]:
    """One row per topic across all sources (spelling variants, synonyms and
    translations merged with the same TopicIndex the gap engine uses), with
    competitor support and a tier."""
    index = TopicIndex()
    members: list[tuple[str, EntityItem]] = []
    for entity in my_entities:
        index.record(entity, _MINE)
        members.append((_MINE, entity))
    for analysis in competitor_analyses:
        label = analysis.source_label or "source"
        for entity in analysis.raw_entities:
            index.record(entity, label)
            members.append((label, entity))
    index.rebuild()

    grouped: dict[str, list[tuple[str, EntityItem]]] = {}
    for label, entity in members:
        group = index.group_of(entity.name)
        if group:
            grouped.setdefault(group, []).append((label, entity))

    competitors = len(competitor_analyses)
    master: list[EntityItem] = []
    for group, items in grouped.items():
        entities = [entity for _label, entity in items]
        mine = [entity for label, entity in items if label == _MINE]
        best = max(entities, key=lambda e: (e.salience, _IMPORTANCE_ORDER.get(e.importance, 2), e.mentions))
        display = index.display.get(group, (0, best.name))[1]
        merged = (mine[0] if mine else best).model_copy(deep=True)
        merged.name = display
        variants: list[str] = []
        seen = {_normalize(display)}
        for entity in entities:
            for term in [entity.name, *entity.lsi]:
                key = _normalize(term)
                if key and key not in seen:
                    seen.add(key)
                    variants.append(term)
        merged.lsi = variants[:50]
        merged.mentions = min(100_000, sum(entity.mentions for entity in entities))
        merged.salience = round(max(entity.salience for entity in entities), 3)
        merged.importance = max((entity.importance for entity in entities), key=lambda v: _IMPORTANCE_ORDER.get(v, 2))
        merged.evidence_score = max(entity.evidence_score for entity in entities)
        merged.source_count = len({label for label, _entity in items if label != _MINE})
        merged.tier = assign_tier(merged.source_count, competitors, merged.salience, bool(mine))
        master.append(merged)
    master.sort(key=lambda e: (_TIER_ORDER.get(e.tier, 3), -e.source_count, -e.salience, e.name))
    return master
