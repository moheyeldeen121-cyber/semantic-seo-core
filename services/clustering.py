"""
Cross-Source Semantic Clustering & Cluster Gaps
================================================

Why a second clustering layer
-----------------------------
Every source is analysed by an independent AI call, so each returns its own
cluster names ("أدوات السيو" here, "برامج تحسين المواقع" there). Comparing
clusters across sources — which is what "which topical clusters do the
competitors build that my page neglects?" requires — is impossible on those
per-source names. This module builds ONE set of clusters over all sources.

Pipeline (fully deterministic — same input, same output)
--------------------------------------------------------
1. **Topics.** Entity spellings are merged into topics with the gap engine's
   ``TopicIndex`` (normalization, close variants, AI synonyms).
2. **Affinity graph** between topics:
   * co-cluster ratio — how often two topics were put in the SAME AI cluster,
     divided by how often they appear in the same source (so topics that
     merely co-occur are not glued together);
   * lexical overlap — Jaccard of significant words (Arabic idafa phrases
     sharing a head noun, e.g. "تحسين محركات البحث" / "محركات البحث المحلية"),
     ignoring words so common they would link everything.
3. **Average-linkage agglomerative clustering** (threshold + size cap), which
   naturally resists one giant "everything" cluster.
4. **Per cluster:** the *pillar* (highest source coverage × salience ×
   centrality), *supporting entities*, *LSI terms* (all spellings and AI
   synonyms across sources), weighted competitor coverage, weighted coverage
   of the client's page (entities + raw text), status and priority.

A cluster is a **gap** only when at least ``_min_sources`` independent
competitors cover it and the client's page covers it partially or not at all.
"""

from __future__ import annotations

import heapq
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from models.schemas import ClusterEntity, EntityItem, SourceAnalysis, TopicalCluster
from services.semantic import (
    TopicIndex,
    _covered_by_text,
    _min_sources,
    _normalize,
    _significant_words,
)

MY_LABEL = "\x00mine"
_GENERIC_NAMES = {
    _normalize(name) for name in (
        "غير مصنف", "عام", "عامة", "متفرقات", "أخرى", "اخرى", "مفاهيم عامة", "مستخرج من نص موقعك",
        "general", "misc", "miscellaneous", "other", "others", "uncategorized", "recovered from your text",
    )
}
_TYPE_BONUS = {"Concept": 0.06, "Service": 0.06, "Product": 0.03, "Standard": 0.04}

LINK_THRESHOLD = 0.2
MAX_CLUSTER_SIZE = 35
MAX_TOPICS = 700


@dataclass
class _Topic:
    key: str
    name: str = ""
    entity_type: str = "Concept"
    sources: set[str] = field(default_factory=set)
    mentions: int = 0
    saliences: list[float] = field(default_factory=list)
    lsi: list[str] = field(default_factory=list)
    variants: set[str] = field(default_factory=set)
    ai_clusters: Counter = field(default_factory=Counter)
    ai_cluster_display: dict[str, str] = field(default_factory=dict)
    types: Counter = field(default_factory=Counter)
    weight: float = 0.0

    @property
    def salience(self) -> float:
        return sum(self.saliences) / len(self.saliences) if self.saliences else 0.0


def _collect_topics(analyses: list[tuple[str, SourceAnalysis]]) -> tuple[TopicIndex, dict[str, _Topic]]:
    index = TopicIndex()
    for label, analysis in analyses:
        for entity in analysis.raw_entities:
            index.record(entity, label)
    index.rebuild()

    topics: dict[str, _Topic] = {}
    for label, analysis in analyses:
        for entity in analysis.raw_entities:
            group = index.group_of(entity.name)
            if not group:
                continue
            topic = topics.setdefault(group, _Topic(key=group))
            topic.sources.add(label)
            topic.mentions += int(entity.mentions or 0)
            topic.saliences.append(float(entity.salience or 0.0))
            topic.variants.add(entity.name)
            if getattr(entity, "source_term", ""):
                topic.variants.add(entity.source_term)
            for alias in entity.lsi:
                if alias and alias not in topic.lsi:
                    topic.lsi.append(alias)
            topic.types[entity.entity_type] += 1
            cluster_key = _normalize(entity.cluster or "")
            if cluster_key:
                topic.ai_clusters[cluster_key] += 1
                topic.ai_cluster_display.setdefault(cluster_key, entity.cluster)
    for group, topic in topics.items():
        topic.name = index.display.get(group, (0, next(iter(topic.variants), group)))[1]
        topic.entity_type = topic.types.most_common(1)[0][0] if topic.types else "Concept"
    return index, topics


def _topic_weights(topics: dict[str, _Topic], competitor_labels: list[str]) -> None:
    total = max(len(competitor_labels), 1)
    for topic in topics.values():
        competitor_hits = len(topic.sources - {MY_LABEL})
        ratio = competitor_hits / total if competitor_labels else (1.0 if topic.sources else 0.0)
        mention_signal = min(1.0, math.log1p(topic.mentions) / math.log(21))
        topic.weight = round(0.45 * ratio + 0.35 * topic.salience + 0.20 * mention_signal, 4)


def _affinity_graph(topics: dict[str, _Topic], analyses: list[tuple[str, SourceAnalysis]],
                    index: TopicIndex) -> dict[str, dict[str, float]]:
    keys = set(topics)
    co_cluster: Counter = Counter()
    for _label, analysis in analyses:
        by_cluster: dict[str, set[str]] = defaultdict(set)
        for entity in analysis.raw_entities:
            group = index.group_of(entity.name)
            if group in keys:
                by_cluster[_normalize(entity.cluster or "") or "_"].add(group)
        for members in by_cluster.values():
            ordered = sorted(members)
            if len(ordered) > 60:
                ordered = ordered[:60]
            for i, first in enumerate(ordered):
                for second in ordered[i + 1:]:
                    co_cluster[(first, second)] += 1

    words = {key: set(_significant_words(key)) for key in keys}
    document_frequency: Counter = Counter()
    for word_set in words.values():
        document_frequency.update(word_set)
    common_limit = max(4, int(len(keys) * 0.25))
    postings: dict[str, list[str]] = defaultdict(list)
    for key, word_set in words.items():
        for word in word_set:
            if document_frequency[word] <= common_limit:
                postings[word].append(key)

    lexical: dict[tuple[str, str], float] = {}
    for members in postings.values():
        members = sorted(members)
        for i, first in enumerate(members):
            for second in members[i + 1:]:
                pair = (first, second)
                if pair in lexical:
                    continue
                union = words[first] | words[second]
                if union:
                    lexical[pair] = len(words[first] & words[second]) / len(union)

    graph: dict[str, dict[str, float]] = defaultdict(dict)
    for pair in set(co_cluster) | set(lexical):
        first, second = pair
        together = len(topics[first].sources & topics[second].sources)
        co = co_cluster.get(pair, 0) / max(together, 1)
        lex = lexical.get(pair, 0.0)
        lex = lex if lex >= 0.34 else 0.0
        weight = min(1.0, 0.7 * co + 0.6 * lex)
        if weight >= 0.12:
            graph[first][second] = weight
            graph[second][first] = weight
    return graph


def _agglomerate(keys: list[str], graph: dict[str, dict[str, float]]) -> list[list[str]]:
    """Average-linkage clustering on a sparse graph (deterministic)."""
    members: dict[int, list[str]] = {i: [key] for i, key in enumerate(keys)}
    position = {key: i for i, key in enumerate(keys)}
    links: dict[int, dict[int, float]] = defaultdict(dict)
    for key, neighbours in graph.items():
        if key not in position:
            continue
        a = position[key]
        for other, weight in neighbours.items():
            if other in position:
                links[a][position[other]] = weight

    heap: list[tuple[float, int, int]] = []
    for a, neighbours in links.items():
        for b, total in neighbours.items():
            if a < b:
                heapq.heappush(heap, (-total, a, b))

    while heap:
        negative, a, b = heapq.heappop(heap)
        if a not in members or b not in members:
            continue
        total = links[a].get(b, 0.0)
        average = total / (len(members[a]) * len(members[b]))
        if abs(-negative - average) > 1e-9:
            if average >= LINK_THRESHOLD:
                heapq.heappush(heap, (-average, a, b))
            continue
        if average < LINK_THRESHOLD:
            continue
        if len(members[a]) + len(members[b]) > MAX_CLUSTER_SIZE:
            continue
        # merge b into a
        members[a].extend(members.pop(b))
        for c, weight in list(links[b].items()):
            if c == a:
                continue
            links[a][c] = links[a].get(c, 0.0) + weight
            links[c][a] = links[a][c]
            links[c].pop(b, None)
        links[a].pop(b, None)
        links.pop(b, None)
        for c, weight in links[a].items():
            if c in members:
                average = weight / (len(members[a]) * len(members[c]))
                if average >= LINK_THRESHOLD:
                    heapq.heappush(heap, (-average, min(a, c), max(a, c)))
    return [group for _, group in sorted(members.items())]


def _covered_by_me(topic: _Topic, normalized_my_text: str) -> bool:
    if MY_LABEL in topic.sources:
        return True
    if not normalized_my_text:
        return False
    for surface in [topic.name, *sorted(topic.variants), *topic.lsi[:8]]:
        if surface and _covered_by_text(surface, normalized_my_text):
            return True
    return False


def _cluster_name(member_topics: list[_Topic], pillar: _Topic) -> str:
    votes: Counter = Counter()
    display: dict[str, str] = {}
    for topic in member_topics:
        for key, count in topic.ai_clusters.items():
            if not key or key in _GENERIC_NAMES:
                continue
            from_competitors = len(topic.sources - {MY_LABEL}) > 0
            descriptive = 0.05 * min(len(key.split()), 4)
            votes[key] += count * (0.5 + topic.weight) * (1.0 if from_competitors else 0.8) + descriptive
            display.setdefault(key, topic.ai_cluster_display.get(key, key))
    if votes:
        best, score = votes.most_common(1)[0]
        if score >= 0.3 * sum(votes.values()) or len(member_topics) <= 2:
            return display[best]
    return pillar.name


def build_semantic_clusters(
    my_analysis: SourceAnalysis | None,
    competitor_analyses: list[SourceAnalysis],
    my_text: str = "",
) -> tuple[list[TopicalCluster], dict[str, str]]:
    """Return (clusters, {normalized entity name → cluster name})."""
    competitor_labels = [analysis.source_label or f"source-{i}" for i, analysis in enumerate(competitor_analyses)]
    analyses: list[tuple[str, SourceAnalysis]] = []
    if my_analysis is not None:
        analyses.append((MY_LABEL, my_analysis))
    analyses.extend(zip(competitor_labels, competitor_analyses))
    if not analyses:
        return [], {}

    index, topics = _collect_topics(analyses)
    if not topics:
        return [], {}
    _topic_weights(topics, competitor_labels)

    # Trivial single-competitor mentions do not shape the topical map when
    # there are enough competitors to know better.
    many = len(competitor_labels) >= 3
    kept = {
        key: topic for key, topic in topics.items()
        if not many or len(topic.sources) >= 2 or MY_LABEL in topic.sources or topic.salience >= 0.45
    }
    if len(kept) > MAX_TOPICS:
        kept = dict(sorted(kept.items(), key=lambda item: -item[1].weight)[:MAX_TOPICS])
    keys = sorted(kept, key=lambda key: (-kept[key].weight, key))

    graph = _affinity_graph(kept, analyses, index)
    groups = _agglomerate(keys, graph)

    has_mine = my_analysis is not None
    normalized_my_text = _normalize(my_text) if (has_mine and my_text) else ""
    threshold = _min_sources(len(competitor_labels)) if competitor_labels else 1
    total_competitors = len(competitor_labels)

    clusters: list[TopicalCluster] = []
    name_map: dict[str, str] = {}
    used_names: Counter = Counter()
    for group in groups:
        member_topics = [kept[key] for key in group]
        if len(member_topics) == 1:
            only = member_topics[0]
            competitor_hits = len(only.sources - {MY_LABEL})
            if competitor_hits < 2 and not (MY_LABEL in only.sources and only.salience >= 0.5):
                continue  # isolated, uncorroborated topic — stays in the entity table only

        def centrality(topic: _Topic) -> float:
            neighbours = graph.get(topic.key, {})
            return sum(weight for other, weight in neighbours.items() if other in group)

        max_central = max((centrality(topic) for topic in member_topics), default=0.0) or 1.0
        total_sources = max(len(analyses), 1)

        def pillar_score(topic: _Topic) -> float:
            return (0.45 * len(topic.sources) / total_sources + 0.35 * topic.salience
                    + 0.20 * centrality(topic) / max_central + _TYPE_BONUS.get(topic.entity_type, 0.0))

        member_topics.sort(key=lambda topic: (-topic.weight, topic.name))
        pillar = max(member_topics, key=lambda topic: (pillar_score(topic), topic.weight, topic.name))
        name = _cluster_name(member_topics, pillar)
        if used_names[_normalize(name)]:
            name = f"{name} — {pillar.name}"
        used_names[_normalize(name)] += 1

        weights = {topic.key: max(topic.weight, 0.05) for topic in member_topics}
        weight_total = sum(weights.values())
        covered = {topic.key: _covered_by_me(topic, normalized_my_text) for topic in member_topics} if has_mine else {}

        covering = 0
        for label in competitor_labels:
            share = sum(weights[t.key] for t in member_topics if label in t.sources) / weight_total
            if share >= 0.3 or label in pillar.sources:
                covering += 1
        competitor_ratio = covering / total_competitors if total_competitors else 0.0
        my_coverage = (sum(weights[key] for key, flag in covered.items() if flag) / weight_total) if has_mine else 0.0

        if not has_mine:
            status = "n/a"
        elif my_coverage >= 0.6:
            status = "covered"
        elif my_coverage >= 0.25:
            status = "partial"
        else:
            status = "missing"
        is_gap = has_mine and status != "covered" and covering >= threshold and total_competitors > 0

        if not is_gap:
            priority = "low"
        elif status == "missing":
            if competitor_ratio >= 0.6 or (competitor_ratio >= 0.4 and covering >= 3):
                priority = "critical"
            elif competitor_ratio >= 0.3:
                priority = "high"
            else:
                priority = "medium"
        else:
            priority = "high" if competitor_ratio >= 0.6 else "medium"

        lsi_seen = {_normalize(topic.name) for topic in member_topics}
        lsi_terms: list[str] = []
        for topic in member_topics:
            for term in [*sorted(topic.variants), *topic.lsi]:
                key = _normalize(term)
                if not key or key in lsi_seen or len(term) > 80:
                    continue
                lsi_seen.add(key)
                lsi_terms.append(term.strip())
        missing = [topic.name for topic in member_topics
                   if has_mine and not covered.get(topic.key)
                   and len(topic.sources - {MY_LABEL}) >= min(threshold, 2)]

        supporting = [
            ClusterEntity(
                name=topic.name,
                type=topic.entity_type,
                salience=round(min(1.0, topic.salience), 3),
                competitor_count=len(topic.sources - {MY_LABEL}),
                covered_by_me=bool(covered.get(topic.key)),
                role="supporting",
            )
            for topic in member_topics if topic is not pillar
        ][:60]

        sources = sorted({label for topic in member_topics for label in topic.sources if label != MY_LABEL})
        cluster = TopicalCluster(
            cluster_id=f"c{len(clusters) + 1}",
            cluster_name=name[:160],
            pillar_entity=pillar.name[:160],
            pillar_type=pillar.entity_type,
            supporting_entities=supporting,
            lsi_terms=lsi_terms[:40],
            entity_count=len(member_topics),
            competitor_count=covering,
            competitor_total=total_competitors,
            competitor_coverage=round(competitor_ratio, 4),
            my_coverage=round(min(1.0, my_coverage), 4),
            status=status,
            is_gap=is_gap,
            priority=priority,
            missing_entities=missing[:40],
            sources=sources[:120],
            weight=round(weight_total, 4),
        )
        clusters.append(cluster)
        for topic in member_topics:
            name_map[topic.key] = cluster.cluster_name
            for variant in topic.variants:
                name_map.setdefault(_normalize(variant), cluster.cluster_name)

    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    clusters.sort(key=lambda c: (0 if c.is_gap else 1, rank.get(c.priority, 4),
                                 -(c.weight * (0.5 + c.competitor_coverage)), c.cluster_name))
    for position, cluster in enumerate(clusters, start=1):
        cluster.cluster_id = f"c{position}"
    return clusters, name_map


def cluster_for(name: str, name_map: dict[str, str], index: TopicIndex | None = None) -> str:
    key = _normalize(name)
    if key in name_map:
        return name_map[key]
    if index is not None:
        group = index.group_of(name)
        if group in name_map:
            return name_map[group]
    return ""


def relabel_entities(entities: list[EntityItem], name_map: dict[str, str]) -> None:
    """Give entities the unified cluster name (in place) when one exists."""
    for entity in entities:
        label = cluster_for(entity.name, name_map)
        if label:
            entity.cluster = label
