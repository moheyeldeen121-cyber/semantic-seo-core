"""Validated schemas for AI output and API responses."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "EntityItem",
    "SemanticCluster",
    "SourceAnalysis",
    "SemanticGap",
    "AnalysisQuality",
    "FullAnalysisResult",
    "UsageMetrics",
    "ClusterEntity",
    "TopicalCluster",
    "SourceReport",
]

_WHITESPACE = re.compile(r"\s+")


def _clean_text(value: str, *, fallback: str = "", max_length: int = 240) -> str:
    value = _WHITESPACE.sub(" ", (value or "").replace("\x00", " ")).strip()
    return (value or fallback)[:max_length]


class EntityItem(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    entity_type: str = Field(..., alias="type")
    lsi: list[str] = Field(default_factory=list, max_length=50)
    importance: str = "medium"
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    mentions: int = Field(default=1, ge=0, le=100_000)
    cluster: str = Field(default="غير مصنف", max_length=160)
    evidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_level: str = Field(default="unverified", max_length=20)
    # Deterministic signals (services/salience.py). Defaults keep every older
    # payload and test valid.
    source_term: str = Field(default="", max_length=160)
    placements: list[str] = Field(default_factory=list, max_length=12)
    role: str = Field(default="", max_length=20)
    grounded: bool = True
    # topic = compared across sources; brand = the publishing site / its staff;
    # context = incidental mention. Only "topic" feeds clusters and gaps.
    scope: str = Field(default="topic", max_length=12)
    # Aggregate (master list) only: how many competitor pages use this topic,
    # and its tier — core / supporting / long_tail (services/semantic.py).
    source_count: int = Field(default=0, ge=0)
    tier: str = Field(default="", max_length=12)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = _clean_text(value, max_length=160)
        if len(cleaned) < 2:
            raise ValueError("Entity name is too short")
        return cleaned

    @field_validator("source_term")
    @classmethod
    def clean_source_term(cls, value: str) -> str:
        return _clean_text(value, max_length=160)

    @field_validator("tier")
    @classmethod
    def normalize_tier(cls, value: str) -> str:
        cleaned = _clean_text(value, max_length=12).lower()
        return cleaned if cleaned in {"core", "supporting", "long_tail", ""} else ""

    @field_validator("scope")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        cleaned = _clean_text(value, fallback="topic", max_length=12).lower()
        return cleaned if cleaned in {"topic", "brand", "context"} else "topic"

    @field_validator("role")
    @classmethod
    def normalize_role(cls, value: str) -> str:
        cleaned = _clean_text(value, max_length=20).lower()
        return cleaned if cleaned in {"core", "supporting", ""} else ""

    @field_validator("entity_type")
    @classmethod
    def normalize_type(cls, raw: str) -> str:
        allowed = {"Person", "Organization", "Place", "Concept", "Service", "Event", "Product", "Standard"}
        cleaned = _clean_text(raw, fallback="Concept", max_length=40).title()
        return cleaned if cleaned in allowed else "Concept"

    @field_validator("importance")
    @classmethod
    def normalize_importance(cls, raw: str) -> str:
        cleaned = _clean_text(raw, fallback="medium", max_length=20).lower()
        return cleaned if cleaned in {"high", "medium", "low"} else "medium"

    @field_validator("cluster")
    @classmethod
    def clean_cluster(cls, value: str) -> str:
        return _clean_text(value, fallback="غير مصنف", max_length=160)

    @field_validator("evidence_level")
    @classmethod
    def normalize_evidence_level(cls, value: str) -> str:
        cleaned = _clean_text(value, fallback="unverified", max_length=20).lower()
        return cleaned if cleaned in {"verified", "supported", "weak", "unverified"} else "unverified"

    model_config = {"populate_by_name": True, "extra": "ignore"}


class SemanticCluster(BaseModel):
    cluster_name: str = Field(..., min_length=1, max_length=160)
    entities: list[EntityItem] = Field(default_factory=list, max_length=800)

    @field_validator("cluster_name")
    @classmethod
    def clean_cluster_name(cls, value: str) -> str:
        return _clean_text(value, fallback="غير مصنف", max_length=160)

    model_config = {"extra": "ignore"}


class SourceAnalysis(BaseModel):
    source_label: str = Field(..., min_length=1, max_length=240)
    clusters: list[SemanticCluster] = Field(default_factory=list, max_length=200)
    raw_entities: list[EntityItem] = Field(default_factory=list, max_length=5_000)
    usage: dict = Field(default_factory=dict)
    main_topic: str = Field(default="", max_length=240)
    # Brand / context entities: reported, never compared (services/entity_quality.py).
    signal_entities: list[EntityItem] = Field(default_factory=list, max_length=1_000)

    @field_validator("source_label")
    @classmethod
    def clean_source_label(cls, value: str) -> str:
        return _clean_text(value, fallback="مصدر", max_length=240)

    model_config = {"extra": "ignore"}


class SemanticGap(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    entity_type: str = Field(alias="type")
    lsi: list[str] = Field(default_factory=list, max_length=50)
    competitor_count: int = Field(ge=1, le=10_000)
    total_mentions: int = Field(ge=0, le=1_000_000)
    priority: str
    cluster: str = Field(default="غير مصنف", max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_level: str = Field(default="limited", max_length=20)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean_text(value, max_length=160)

    @field_validator("entity_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        allowed = {"Person", "Organization", "Place", "Concept", "Service", "Event", "Product", "Standard"}
        cleaned = _clean_text(value, fallback="Concept", max_length=40).title()
        return cleaned if cleaned in allowed else "Concept"

    @field_validator("priority")
    @classmethod
    def normalize_priority(cls, value: str) -> str:
        cleaned = _clean_text(value, fallback="medium", max_length=20).lower()
        return cleaned if cleaned in {"critical", "high", "medium"} else "medium"

    @field_validator("cluster")
    @classmethod
    def clean_cluster(cls, value: str) -> str:
        return _clean_text(value, fallback="غير مصنف", max_length=160)

    @field_validator("evidence_level")
    @classmethod
    def normalize_gap_evidence_level(cls, value: str) -> str:
        cleaned = _clean_text(value, fallback="limited", max_length=20).lower()
        return cleaned if cleaned in {"high", "medium", "limited"} else "limited"

    model_config = {"populate_by_name": True, "extra": "ignore"}


class AnalysisQuality(BaseModel):
    score: int = Field(default=0, ge=0, le=100)
    evidence_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    verified_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    total_entities: int = Field(default=0, ge=0)
    verified_entities: int = Field(default=0, ge=0)
    supported_entities: int = Field(default=0, ge=0)
    unverified_entities: int = Field(default=0, ge=0)
    high_confidence_gaps: int = Field(default=0, ge=0)
    shared_topics: int = Field(default=0, ge=0)
    covered_topics: int = Field(default=0, ge=0)
    coverage_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    source_count: int = Field(default=0, ge=0)
    requested_sources: int = Field(default=0, ge=0)
    failed_sources: list[str] = Field(default_factory=list, max_length=40)
    source_characters: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class ClusterEntity(BaseModel):
    """One member of a cross-source topical cluster."""
    name: str = Field(..., min_length=1, max_length=160)
    entity_type: str = Field(default="Concept", alias="type", max_length=40)
    salience: float = Field(default=0.0, ge=0.0, le=1.0)
    competitor_count: int = Field(default=0, ge=0)
    covered_by_me: bool = False
    role: str = Field(default="supporting", max_length=20)

    model_config = {"populate_by_name": True, "extra": "ignore"}


class TopicalCluster(BaseModel):
    """A semantic cluster unified across ALL sources.

    ``pillar_entity`` is the core entity the cluster is built around,
    ``supporting_entities`` the sub-topics that give it depth, ``lsi_terms``
    the linguistic variants/synonyms seen across sources. Coverage figures
    are weighted by entity importance; ``is_gap`` means enough independent
    competitors cover the cluster while the client's page does not (or only
    partially does).
    """
    cluster_id: str = Field(..., min_length=1, max_length=20)
    cluster_name: str = Field(..., min_length=1, max_length=160)
    pillar_entity: str = Field(..., min_length=1, max_length=160)
    pillar_type: str = Field(default="Concept", max_length=40)
    supporting_entities: list[ClusterEntity] = Field(default_factory=list, max_length=60)
    lsi_terms: list[str] = Field(default_factory=list, max_length=40)
    entity_count: int = Field(default=0, ge=0)
    competitor_count: int = Field(default=0, ge=0)
    competitor_total: int = Field(default=0, ge=0)
    competitor_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    my_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = Field(default="n/a", max_length=12)
    is_gap: bool = False
    priority: str = Field(default="low", max_length=12)
    missing_entities: list[str] = Field(default_factory=list, max_length=40)
    sources: list[str] = Field(default_factory=list, max_length=120)
    weight: float = Field(default=0.0, ge=0.0)

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        cleaned = _clean_text(value, fallback="n/a", max_length=12).lower()
        return cleaned if cleaned in {"covered", "partial", "missing", "n/a"} else "n/a"

    @field_validator("priority")
    @classmethod
    def normalize_cluster_priority(cls, value: str) -> str:
        cleaned = _clean_text(value, fallback="low", max_length=12).lower()
        return cleaned if cleaned in {"critical", "high", "medium", "low"} else "low"

    model_config = {"extra": "ignore"}


class SourceReport(BaseModel):
    """How each input was read — shown in the UI and exported to Excel."""
    label: str = Field(..., min_length=1, max_length=240)
    url: str = Field(default="", max_length=2048)
    status: str = Field(default="ok", max_length=12)
    tier: str = Field(default="", max_length=24)
    chars: int = Field(default=0, ge=0)
    headings: int = Field(default=0, ge=0)
    provider: str = Field(default="", max_length=60)
    reason: str = Field(default="", max_length=600)

    model_config = {"extra": "ignore"}


class UsageMetrics(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    model_config = {"extra": "ignore"}

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class FullAnalysisResult(BaseModel):
    master_entities: list[EntityItem] = Field(default_factory=list)
    my_site_entities: list[EntityItem] = Field(default_factory=list)
    my_site_clusters: list[SemanticCluster] = Field(default_factory=list)
    competitor_breakdowns: list[SourceAnalysis] = Field(default_factory=list)
    semantic_gaps: list[SemanticGap] = Field(default_factory=list)
    content_brief: str = ""
    has_my_article: bool = False
    has_competitors: bool = False
    quality: AnalysisQuality = Field(default_factory=AnalysisQuality)
    usage_metrics: UsageMetrics = Field(default_factory=UsageMetrics)
    semantic_clusters: list[TopicalCluster] = Field(default_factory=list)
    my_site_signals: list[EntityItem] = Field(default_factory=list)
    extraction_report: list[SourceReport] = Field(default_factory=list)
