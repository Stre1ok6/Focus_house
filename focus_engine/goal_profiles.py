from __future__ import annotations

import re

from .models import GoalProfile
from .utils import extract_meaningful_tokens, normalize_spaces, normalize_text


DEFAULT_GOAL_TEXT = "围绕当前任务持续处理目标内容，减少与目标无关的页面切换"
GOAL_PLACEHOLDER = "例如：比较三款相机并整理预算和参数，避免切去聊天和短视频"
GOAL_EXAMPLES = [
    "比较三款相机并整理预算、参数和优缺点，避免切去聊天和短视频",
    "制定大阪旅行攻略并汇总住宿、交通和景点安排，不跳去无关推荐流",
    "阅读课程资料并整理本章重点，减少聊天和娱乐页面切换",
]

_GENERIC_SUPPORT_KEYWORDS = [
    "任务",
    "目标",
    "步骤",
    "资料",
    "正文",
    "内容",
    "结果",
    "页面",
    "记录",
    "整理",
    "study",
    "task",
    "goal",
    "document",
    "page",
    "result",
    "note",
]
_GENERIC_STRUCTURE_HINTS = [
    "标题-正文结构",
    "列表-详情结构",
    "步骤-结果结构",
    "对比-结论结构",
    "搜索-结果结构",
    "清单-记录结构",
]
_NEGATIVE_KEYWORDS = {
    "strong": ["短视频", "直播", "游戏", "douyin", "xhs", "reels"],
    "medium": ["聊天", "消息", "朋友圈", "微信", "qq", "微博", "私信"],
    "weak": ["评论", "推荐", "热榜", "娱乐", "刷屏"],
}
_DYNAMIC_HINT_RULES = [
    {
        "patterns": ["compare", "comparison", "spec", "price", "参数", "预算", "比价", "对比", "型号", "购买"],
        "support": ["参数", "预算", "型号", "价格", "优缺点", "规格", "购买要点"],
        "semantic": ["compare", "comparison", "spec", "price", "review", "性能", "评价"],
        "structure": ["参数-结论结构", "列表-详情结构", "对比-记录结构"],
    },
    {
        "patterns": ["travel", "trip", "route", "hotel", "flight", "攻略", "旅行", "住宿", "交通", "景点", "行程"],
        "support": ["酒店", "交通", "景点", "路线", "行程", "攻略", "出行安排"],
        "semantic": ["hotel", "flight", "route", "map", "travel guide", "攻略", "地图"],
        "structure": ["搜索-结果结构", "清单-安排结构", "地点-路线结构"],
    },
    {
        "patterns": ["study", "review", "lesson", "course", "chapter", "阅读", "学习", "复习", "课程", "章节", "资料"],
        "support": ["章节", "课件", "资料", "重点", "总结", "笔记", "课堂内容"],
        "semantic": ["lesson", "chapter", "highlight", "summary", "重点", "知识点"],
        "structure": ["标题-要点结构", "章节-内容结构", "资料-笔记结构"],
    },
    {
        "patterns": ["report", "analysis", "dashboard", "result", "data", "分析", "报表", "数据", "结果", "结论"],
        "support": ["数据", "指标", "结果", "结论", "分析记录", "报表信息"],
        "semantic": ["analysis", "dashboard", "report", "result", "metric", "trend", "图表"],
        "structure": ["图表-说明结构", "结果-结论结构", "指标-分析结构"],
    },
]


def _dedupe_terms(terms: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = normalize_spaces(str(term or ""))
        if len(cleaned) < 2:
            continue
        normalized = normalize_text(cleaned)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(cleaned)
    return unique


def normalize_goal_input(goal: str | None) -> str:
    return normalize_spaces(str(goal or ""))[:160]


def _extract_goal_phrases(goal: str) -> list[str]:
    normalized_goal = normalize_goal_input(goal)
    phrases = re.split(r"[\n,，;；、|/]+|(?:并且|并|以及|然后|同时|and|then)", normalized_goal)
    return _dedupe_terms([phrase for phrase in phrases if len(phrase.strip()) >= 2])


def _extract_emphasized_terms(goal: str) -> list[str]:
    quoted = re.findall(r"[\"“”'‘’《》【】\[\(](.*?)[\"“”'‘’》】\]\)]", goal)
    return _dedupe_terms(quoted)


def _collect_dynamic_hints(goal: str) -> tuple[list[str], list[str], list[str]]:
    normalized = normalize_text(goal)
    support: list[str] = []
    semantic: list[str] = []
    structure: list[str] = []

    for rule in _DYNAMIC_HINT_RULES:
        if any(pattern in normalized for pattern in rule["patterns"]):
            support.extend(rule["support"])
            semantic.extend(rule["semantic"])
            structure.extend(rule["structure"])

    return _dedupe_terms(support), _dedupe_terms(semantic), _dedupe_terms(structure)


def _filter_negative_terms(goal: str, aliases: list[str], terms: list[str]) -> list[str]:
    goal_haystack = normalize_text(" ".join([goal] + aliases))
    filtered: list[str] = []
    for term in terms:
        normalized = normalize_text(term)
        if not normalized:
            continue
        if normalized in goal_haystack or goal_haystack in normalized:
            continue
        filtered.append(term)
    return _dedupe_terms(filtered)


def build_goal_profile(goal: str) -> GoalProfile:
    raw_goal = normalize_goal_input(goal)
    if not raw_goal:
        raise ValueError("missing_goal")

    phrases = _extract_goal_phrases(raw_goal)
    emphasized_terms = _extract_emphasized_terms(raw_goal)
    tokens = _dedupe_terms(extract_meaningful_tokens(raw_goal))
    dynamic_support, dynamic_semantic, dynamic_structure = _collect_dynamic_hints(raw_goal)

    aliases = _dedupe_terms(emphasized_terms + tokens[:12] + phrases[:6])
    support_keywords = _dedupe_terms(_GENERIC_SUPPORT_KEYWORDS + dynamic_support + phrases[:4] + tokens[:6])
    core_keywords = _dedupe_terms([raw_goal] + emphasized_terms[:8] + phrases[:8] + tokens[:10] + dynamic_support[:6])
    scene_keywords = _dedupe_terms(phrases[1:10] + tokens[10:22] + dynamic_semantic[:8] + dynamic_support[:4])
    semantic_keywords = _dedupe_terms(tokens[2:20] + phrases[2:10] + dynamic_semantic[:10] + emphasized_terms[:4])
    structure_patterns = _dedupe_terms(_GENERIC_STRUCTURE_HINTS + dynamic_structure)

    strong_negative_keywords = _filter_negative_terms(raw_goal, aliases, _NEGATIVE_KEYWORDS["strong"])
    medium_negative_keywords = _filter_negative_terms(raw_goal, aliases, _NEGATIVE_KEYWORDS["medium"])
    weak_negative_keywords = _filter_negative_terms(raw_goal, aliases, _NEGATIVE_KEYWORDS["weak"])
    negative_keywords = _dedupe_terms(strong_negative_keywords + medium_negative_keywords + weak_negative_keywords)
    keywords = _dedupe_terms(core_keywords + scene_keywords + support_keywords + semantic_keywords + aliases)

    return GoalProfile(
        raw_goal=raw_goal,
        normalized_goal=normalize_text(raw_goal),
        goal_type="general",
        keywords=keywords,
        negative_keywords=negative_keywords,
        core_keywords=core_keywords,
        scene_keywords=scene_keywords,
        support_keywords=support_keywords,
        semantic_keywords=semantic_keywords,
        structure_patterns=structure_patterns,
        strong_negative_keywords=strong_negative_keywords,
        medium_negative_keywords=medium_negative_keywords,
        weak_negative_keywords=weak_negative_keywords,
        aliases=aliases,
    )
