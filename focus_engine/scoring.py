from __future__ import annotations

import re
from collections import Counter

from .models import FrameAnalysis, GoalProfile
from .utils import clamp, normalize_spaces, normalize_text


OCR_REPLACEMENTS = {
    "1earn": "learn",
    "w0rd": "word",
    "examp1e": "example",
    "translatlon": "translation",
    "transiation": "translation",
    "defination": "definition",
    "definitlon": "definition",
    "meanlng": "meaning",
    "pronunciatlon": "pronunciation",
    "sentencc": "sentence",
    "gramrnar": "grammar",
    "1istening": "listening",
    "0f": "of",
    "th1s": "this",
}


def _dedupe_terms(terms: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = term.strip()
        if len(cleaned) < 2:
            continue
        normalized = normalize_text(cleaned)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(cleaned)
    return unique


def clean_ocr_text(text: str) -> str:
    text = normalize_spaces(text.replace("\r", "\n"))
    lines: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 2:
            continue
        if re.fullmatch(r"[\W_]+", line):
            continue
        for source, target in OCR_REPLACEMENTS.items():
            line = re.sub(source, target, line, flags=re.IGNORECASE)
        line = re.sub(r"[^\S\n]{2,}", " ", line)
        if len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", line)) < 2:
            continue
        if line not in seen:
            seen.add(line)
            lines.append(line)

    merged: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if len(line) <= 6 and len(lines) > 1:
            buffer.append(line)
            continue
        if buffer:
            merged.append(" ".join(buffer + [line]).strip())
            buffer = []
        else:
            merged.append(line)
    if buffer:
        merged.append(" ".join(buffer).strip())

    return "\n".join(item for item in merged if item)


def score_ocr_quality(text: str) -> float:
    if not text:
        return 0.0

    meaningful_chars = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))
    total_chars = max(len(text), 1)
    meaningful_ratio = meaningful_chars / total_chars
    symbol_ratio = len(re.findall(r"[^\w\s\u4e00-\u9fff]", text)) / total_chars
    useful_lines = sum(1 for line in text.splitlines() if len(line.strip()) >= 4)
    duplicate_lines = len(text.splitlines()) - len({line.strip() for line in text.splitlines() if line.strip()})

    score = (
        0.33 * clamp(meaningful_chars / 90.0)
        + 0.33 * clamp(meaningful_ratio)
        + 0.22 * clamp(useful_lines / 4.0)
        + 0.12 * clamp(1 - symbol_ratio - min(duplicate_lines / 6.0, 0.2))
    )
    return round(clamp(score), 3)


def window_consistency(items: list[FrameAnalysis], index: int, radius: int = 3) -> float:
    current = items[index]
    neighbors = [
        items[position]
        for position in range(max(0, index - radius), min(len(items), index + radius + 1))
        if position != index and items[position].image_valid
    ]
    if not neighbors:
        return 0.55

    focus_like_neighbors = [
        neighbor
        for neighbor in neighbors
        if neighbor.category_type == "focus"
        or (neighbor.base_relevance_score or neighbor.relevance_score) >= 0.64
    ]
    stable_focus_ratio = len(focus_like_neighbors) / len(neighbors)
    same_category_ratio = (
        sum(neighbor.category_label == current.category_label for neighbor in focus_like_neighbors)
        / max(len(focus_like_neighbors), 1)
    )

    overlaps: list[float] = []
    current_keywords = set(current.matched_keywords)
    for neighbor in neighbors:
        neighbor_keywords = set(neighbor.matched_keywords)
        union = current_keywords | neighbor_keywords
        if union:
            overlaps.append(len(current_keywords & neighbor_keywords) / len(union))
    keyword_overlap_score = sum(overlaps) / len(overlaps) if overlaps else 0.0

    current_base = current.base_relevance_score or current.relevance_score
    relevance_band_score = sum(
        clamp(1 - abs(current_base - (neighbor.base_relevance_score or neighbor.relevance_score)))
        for neighbor in neighbors
    ) / len(neighbors)

    score = (
        0.34 * stable_focus_ratio
        + 0.24 * same_category_ratio
        + 0.22 * keyword_overlap_score
        + 0.20 * relevance_band_score
    )
    if current.category_type == "focus":
        score += 0.06
    return round(clamp(score), 3)


def _build_decision_reason(item: FrameAnalysis) -> str:
    reasons: list[str] = []
    if item.base_decision_reason:
        reasons.append(item.base_decision_reason)
    elif item.positive_rule:
        reasons.append(item.positive_rule)
    elif item.strong_hit_score >= 0.72:
        reasons.append("命中了高置信度目标关键词")
    elif item.keyword_hit_score >= 0.38:
        reasons.append("命中了多组相关关键词")
    elif item.semantic_match_score >= 0.38:
        reasons.append("OCR 文本与目标语义接近")
    else:
        reasons.append("直接文本证据偏弱")

    if item.structure_score >= 0.55:
        reasons.append("页面结构与目标场景高度一致")

    if item.category_label:
        reasons.append(f"当前场景为“{item.category_label}”")

    if item.window_consistency_score >= 0.72:
        reasons.append("前后截图保持连续一致")
    elif item.window_consistency_score < 0.40 and not item.review_required:
        reasons.append("前后截图切换较频繁")

    if item.negative_rule:
        reasons.append(item.negative_rule)
    elif item.distraction_score >= 0.80:
        reasons.append("与当前目标偏离较明显")

    return "，".join(_dedupe_terms(reasons))


def _scored_items(items: list[FrameAnalysis]) -> list[FrameAnalysis]:
    return [item for item in items if item.image_valid and not item.review_required and item.status != "待复核"]


def finalize_frame_scores(items: list[FrameAnalysis]) -> list[FrameAnalysis]:
    if not items:
        return items

    for index, item in enumerate(items):
        if not item.image_valid:
            item.status = "无法分析"
            continue

        if item.review_required:
            item.window_consistency_score = 0.0
            item.focus_probability = round(clamp(item.focus_score / 100.0), 3) if item.focus_score else 0.0
            item.status = "待复核"
            if not item.decision_reason:
                item.decision_reason = item.fallback_reason or "模型评分失败，请检查 SILICONFLOW_API_KEY 与网络后重试。"
            item.score_breakdown.update(
                {
                    "window_consistency_score": 0.0,
                    "final_relevance_score": round(item.relevance_score, 3),
                    "focus_probability": round(item.focus_probability, 3),
                }
            )
            continue

        item.window_consistency_score = window_consistency(items, index)

        if item.scoring_source in {"deepseek", "siliconflow"}:
            item.relevance_score = round(clamp(item.base_relevance_score or item.relevance_score), 3)
            raw_focus_probability = item.focus_probability or clamp(item.focus_score / 100.0) or item.relevance_score
            if item.score_confidence < 0.35:
                smoothed_probability = raw_focus_probability
            else:
                smoothed_probability = clamp(0.88 * raw_focus_probability + 0.12 * item.window_consistency_score)
            item.focus_probability = round(smoothed_probability, 3)
            item.focus_score = round(item.focus_probability * 100, 1)
        else:
            base_relevance = item.base_relevance_score or item.relevance_score
            relevance = (
                0.76 * base_relevance
                + 0.17 * item.window_consistency_score
                + 0.07 * (1 - item.distraction_score)
            )

            neighbors = [
                neighbor
                for position, neighbor in enumerate(items)
                if abs(position - index) <= 3 and position != index and neighbor.image_valid and not neighbor.review_required
            ]
            focused_neighbors = [
                neighbor
                for neighbor in neighbors
                if neighbor.category_type == "focus"
                and (neighbor.base_relevance_score or neighbor.relevance_score) >= 0.64
            ]
            strong_negative_override = item.category_type == "distract" and item.distraction_score >= 0.85

            if neighbors and not strong_negative_override:
                focused_neighbor_ratio = len(focused_neighbors) / len(neighbors)
                if focused_neighbor_ratio >= 0.67 and item.category_type != "distract":
                    floor = 0.64 if max(item.strong_hit_score, item.structure_score, item.app_context_score) >= 0.40 else 0.60
                    if relevance < floor:
                        relevance = floor
                        if not item.positive_rule:
                            item.positive_rule = "连续多帧保持同类专注场景"

            item.relevance_score = round(clamp(relevance), 3)
            item.focus_probability = round(
                clamp(
                    0.58 * item.relevance_score
                    + 0.24 * item.app_context_score
                    + 0.12 * item.strong_hit_score
                    + 0.06 * (1 - item.distraction_score)
                ),
                3,
            )
            item.focus_score = round(clamp(item.focus_probability) * 100, 1)

        if item.focus_score >= 74:
            item.status = "专注"
        elif item.focus_score >= 50:
            item.status = "轻微偏离"
        else:
            item.status = "分心"

        item.decision_reason = _build_decision_reason(item)
        item.score_breakdown.update(
            {
                "window_consistency_score": round(item.window_consistency_score, 3),
                "final_relevance_score": round(item.relevance_score, 3),
                "focus_probability": round(item.focus_probability, 3),
            }
        )

    return items


def summary_suggestions(profile: GoalProfile, items: list[FrameAnalysis]) -> list[str]:
    image_items = [item for item in items if item.image_valid]
    scored_items = _scored_items(items)
    if not image_items:
        return ["请先上传清晰截图，以便系统提取有效文本和场景证据。"]
    if not scored_items:
        return ["截图已收到，但 SiliconFlow 视觉评分暂不可用，请检查 API Key、模型名称与配额后重试。"]

    suggestions: list[str] = []
    focus_ratio = sum(item.status == "专注" for item in scored_items) / max(len(scored_items), 1)
    average_quality = sum(item.ocr_quality_score for item in scored_items) / max(len(scored_items), 1)
    average_strong_hit = sum(item.strong_hit_score for item in scored_items) / max(len(scored_items), 1)
    average_structure = sum(item.structure_score for item in scored_items) / max(len(scored_items), 1)

    if focus_ratio < 0.5:
        suggestions.append("尽量上传连续截图，并保留标题、章节名或页面主体内容，减少只截局部界面的情况。")
    if average_quality < 0.35:
        suggestions.append("优先上传正文区域更完整、更清晰的截图，这会明显提升 OCR 和评分稳定性。")
    if average_strong_hit < 0.35:
        suggestions.append(f"尽量让截图里出现与“{profile.raw_goal}”直接相关的标题、正文、步骤、结果或对比信息，减少只截局部按钮和工具栏。")
    if average_structure < 0.28:
        suggestions.append("尽量避免只截滚动过渡帧或局部弹窗，保留页面结构能帮助系统更准确判断场景。")
    if any(
        item.status != "专注"
        and (item.distraction_score >= 0.58 or item.relevance_score < 0.55 or item.category_type != "focus")
        for item in scored_items
    ):
        suggestions.append("可以回看那些与当前目标偏离较明显的截图，判断这类页面是否需要单独安排到别的时段处理。")
    if not suggestions:
        suggestions.append("当前任务线索比较稳定，可以继续沿用这组学习环境和截图方式。")
    return suggestions


def build_summary(profile: GoalProfile, items: list[FrameAnalysis], processing_ms: int) -> dict:
    image_items = [item for item in items if item.image_valid]
    scored_items = _scored_items(items)
    review_count = sum(item.review_required for item in image_items)
    focus_count = sum(item.status == "专注" for item in scored_items)
    drift_count = sum(item.status == "轻微偏离" for item in scored_items)
    distract_count = sum(item.status == "分心" for item in scored_items)
    cache_hits = sum(item.cache_hit for item in image_items)
    fallback_count = sum(item.used_fallback for item in image_items)
    unique_hashes = len({item.thumbnail_hash for item in image_items if item.thumbnail_hash})

    top_context = Counter(item.category_label for item in scored_items).most_common(1)
    top_distractor = Counter(
        item.category_label
        for item in scored_items
        if item.status != "专注"
        and (item.distraction_score >= 0.58 or item.relevance_score < 0.55 or item.category_type != "focus")
    ).most_common(1)

    return {
        "goal": profile.raw_goal,
        "goal_id": profile.goal_type,
        "goal_label": profile.raw_goal,
        "goal_type": profile.goal_type,
        "keywords": profile.keywords[:10],
        "total_images": len(items),
        "valid_images": len(image_items),
        "scored_images": len(scored_items),
        "review_count": review_count,
        "focus_count": focus_count,
        "drift_count": drift_count,
        "distract_count": distract_count,
        "focus_ratio": round((focus_count / max(len(scored_items), 1)) * 100, 1),
        "avg_focus_score": round(sum(item.focus_score for item in scored_items) / max(len(scored_items), 1), 1),
        "avg_relevance_score": round(sum(item.relevance_score for item in scored_items) / max(len(scored_items), 1), 3),
        "avg_ocr_quality": round(sum(item.ocr_quality_score for item in scored_items) / max(len(scored_items), 1), 3),
        "avg_keyword_score": round(sum(item.keyword_hit_score for item in scored_items) / max(len(scored_items), 1), 3),
        "avg_semantic_score": round(sum(item.semantic_match_score for item in scored_items) / max(len(scored_items), 1), 3),
        "avg_strong_hit_score": round(sum(item.strong_hit_score for item in scored_items) / max(len(scored_items), 1), 3),
        "avg_coverage_score": round(sum(item.coverage_score for item in scored_items) / max(len(scored_items), 1), 3),
        "avg_structure_score": round(sum(item.structure_score for item in scored_items) / max(len(scored_items), 1), 3),
        "top_context": top_context[0][0] if top_context else ("待复核" if review_count else "暂无明显场景"),
        "top_distractor": top_distractor[0][0] if top_distractor else ("待复核" if review_count and not scored_items else "无明显高频偏离场景"),
        "cache_hits": cache_hits,
        "fallback_count": fallback_count,
        "unique_hashes": unique_hashes,
        "processing_ms": processing_ms,
        "throughput_images_per_sec": round(len(scored_items) / max(processing_ms / 1000, 0.001), 2),
        "suggestions": summary_suggestions(profile, items),
    }
