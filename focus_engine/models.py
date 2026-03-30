from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class GoalProfile:
    raw_goal: str
    normalized_goal: str
    goal_type: str
    keywords: list[str]
    negative_keywords: list[str]
    core_keywords: list[str] = field(default_factory=list)
    scene_keywords: list[str] = field(default_factory=list)
    support_keywords: list[str] = field(default_factory=list)
    semantic_keywords: list[str] = field(default_factory=list)
    structure_patterns: list[str] = field(default_factory=list)
    strong_negative_keywords: list[str] = field(default_factory=list)
    medium_negative_keywords: list[str] = field(default_factory=list)
    weak_negative_keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OcrResult:
    text: str
    quality_score: float
    engine: str
    cache_hit: bool = False
    used_fallback: bool = False
    warning: str | None = None
    thumbnail_hash: str = ""
    processing_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FrameAnalysis:
    index: int
    filename: str
    image_valid: bool
    width: int = 0
    height: int = 0
    thumbnail_hash: str = ""
    duplicate_of: str | None = None
    ocr_text: str = ""
    ocr_quality_score: float = 0.0
    semantic_match_score: float = 0.0
    keyword_hit_score: float = 0.0
    strong_hit_score: float = 0.0
    coverage_score: float = 0.0
    structure_score: float = 0.0
    app_context_score: float = 0.0
    window_consistency_score: float = 0.0
    base_relevance_score: float = 0.0
    relevance_score: float = 0.0
    focus_score: float = 0.0
    focus_probability: float = 0.0
    score_confidence: float = 0.0
    distraction_score: float = 0.0
    category_label: str = "未知场景"
    category_type: str = "neutral"
    matched_keywords: list[str] = field(default_factory=list)
    strong_hits: list[str] = field(default_factory=list)
    scene_hits: list[str] = field(default_factory=list)
    support_hits: list[str] = field(default_factory=list)
    negative_hits: list[str] = field(default_factory=list)
    matched_context_patterns: list[str] = field(default_factory=list)
    status: str = "未分析"
    decision_reason: str = ""
    base_decision_reason: str = ""
    fallback_reason: str = ""
    positive_rule: str = ""
    negative_rule: str = ""
    score_breakdown: dict[str, float] = field(default_factory=dict)
    cache_hit: bool = False
    used_fallback: bool = False
    processing_ms: int = 0
    warning: str | None = None
    scoring_source: str = "local"
    review_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
