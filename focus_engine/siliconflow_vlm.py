from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
from urllib import error, request

import cv2
import numpy as np

from .config import AnalyzerConfig, DEFAULT_CONFIG
from .models import GoalProfile
from .utils import LRUCache, clamp, normalize_spaces


SYSTEM_PROMPT = """你是“截图专注度分析器”。你会看到用户上传的屏幕/学习截图。

任务：
1. 用相当于 OCR 的方式，按阅读顺序写出画面中可见的主要文字（中文为主，保留标题、正文、界面标签；看不清的用省略，不要编造）。
2. 根据用户目标与关键词，判断该截图是否服务于当前专注目标，并给出量化分数。

规则：
1. 只能依据截图中真实可见内容判断，禁止编造画面中不存在的文字或场景。
2. 若画面模糊、文字极少或难以辨认，应降低 ocr_quality 与 confidence，证据不足时 category_type 倾向 neutral。
3. 购物、攻略、地图、社交等内容是否相关完全取决于用户当前目标。
4. distraction_score 表示偏离风险；仅当明显跑题或证据弱时提高。
5. 只输出一个 JSON 对象，不要 Markdown，不要额外说明文字。
6. ocr_quality、semantic_match_score、keyword_hit_score、strong_hit_score、coverage_score、structure_score、app_context_score、relevance_score、distraction_score、confidence 均为 0–1，保留 3 位小数。
7. focus_score 为 0–100。
8. category_type 只能是 focus、neutral。
9. status 只能是 专注、轻微偏离、分心。
10. matched_keywords、strong_hits、scene_hits、support_hits、negative_hits、matched_context_patterns 均为数组，可空数组。
11. JSON 顶层必须包含字段：ocr_text（字符串）、ocr_quality（数字），以及下列评分字段。
"""


def _resize_for_api(image: np.ndarray, config: AnalyzerConfig) -> np.ndarray:
    height, width = image.shape[:2]
    max_edge = max(height, width)
    if max_edge <= config.max_image_edge:
        return image
    scale = config.max_image_edge / max_edge
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def _jpeg_data_url_and_digest(image: np.ndarray) -> tuple[str, str]:
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok or buf is None:
        raise RuntimeError("无法将截图编码为 JPEG。")
    raw = buf.tobytes()
    b64 = base64.standard_b64encode(raw).decode("ascii")
    digest = hashlib.sha256(raw).hexdigest()
    return f"data:image/jpeg;base64,{b64}", digest


def _extract_json_payload(content: str) -> str:
    """
    从一段可能“夹带额外文本/代码块”的输出中提取第一个完整的 JSON 对象。
    使用简易的括号计数，并正确处理字符串内的花括号/引号转义。
    """
    stripped = content.strip()
    start = stripped.find("{")
    if start == -1:
        raise RuntimeError("模型返回内容中未找到 '{'，无法提取 JSON。")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "\"":
                in_string = False
            continue

        if ch == "\"":
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]

    raise RuntimeError("模型返回 JSON 对象未闭合，可能是输出被截断。")


class SiliconFlowVlmScorer:
    """通过 SiliconFlow OpenAI 兼容接口调用千问等多模态模型，一次性完成「读图 + 评分」。"""

    def __init__(self, config: AnalyzerConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        self.cache = LRUCache(config.llm_cache_size)

    def status(self) -> dict[str, object]:
        return {
            "provider": "siliconflow",
            "configured": bool(self.config.siliconflow_api_key),
            "model": self.config.siliconflow_model,
            "base_url": self.config.siliconflow_base_url,
        }

    def vision_ready_message(self) -> tuple[bool, str]:
        if self.config.siliconflow_api_key:
            return True, f"SiliconFlow · {self.config.siliconflow_model}"
        return False, "请配置 SILICONFLOW_API_KEY（SiliconFlow 千问视觉模型）"

    def analyze(self, profile: GoalProfile, filename: str, image_bgr: np.ndarray, thumbnail_hash: str) -> dict[str, object]:
        if not self.config.siliconflow_api_key:
            return self._review_result("SiliconFlow API Key 未配置，请设置 SILICONFLOW_API_KEY。", 0.0)

        resized = _resize_for_api(image_bgr, self.config)
        try:
            data_url, jpeg_digest = _jpeg_data_url_and_digest(resized)
        except Exception as exc:
            return self._review_result(f"截图编码失败：{exc}", 0.0)

        cache_key = self._cache_key(profile, filename, thumbnail_hash, jpeg_digest)
        cached = self.cache.get(cache_key)
        if cached is not None:
            out = dict(cached)
            out["cache_hit"] = True
            return out

        system_prompt, user_text = self._build_text_prompt(profile, filename)
        started = time.perf_counter()
        try:
            payload_raw = self._request_completion(system_prompt, user_text, data_url, use_json_object=True)
            ocr_text = normalize_spaces(str(payload_raw.get("ocr_text") or ""))
            ocr_quality = self._coerce_score(payload_raw.get("ocr_quality"))
            if not ocr_text:
                ocr_quality = min(ocr_quality, 0.25)
            result = self._normalize_response(payload_raw, ocr_quality, ocr_text)
        except Exception as exc:
            return self._review_result(f"SiliconFlow 调用失败：{exc}", 0.0)

        result["score_breakdown"]["llm_latency_ms"] = float(int((time.perf_counter() - started) * 1000))
        result["score_breakdown"]["vlm_scored"] = 1.0
        self.cache.set(cache_key, dict(result))
        result["cache_hit"] = False
        return result

    def _cache_key(self, profile: GoalProfile, filename: str, thumb: str, jpeg_digest: str) -> str:
        payload = {
            "goal": profile.normalized_goal,
            "filename": filename,
            "thumb": thumb,
            "model": self.config.siliconflow_model,
            "jpeg_digest": jpeg_digest,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _build_text_prompt(self, profile: GoalProfile, filename: str) -> tuple[str, str]:
        output_schema = {
            "ocr_text": "从截图中可读到的主要文字，多行用换行",
            "ocr_quality": 0.85,
            "category_type": "focus",
            "category_label": "目标相关页面",
            "semantic_match_score": 0.832,
            "keyword_hit_score": 0.774,
            "strong_hit_score": 0.801,
            "coverage_score": 0.756,
            "structure_score": 0.689,
            "app_context_score": 0.743,
            "relevance_score": 0.812,
            "distraction_score": 0.103,
            "focus_score": 84.5,
            "confidence": 0.861,
            "status": "专注",
            "matched_keywords": ["目标关键词"],
            "strong_hits": ["证据"],
            "scene_hits": ["场景"],
            "support_hits": ["辅助"],
            "negative_hits": [],
            "matched_context_patterns": ["模式"],
            "positive_rule": "正向规则简述",
            "negative_rule": "",
            "reason": "简要结论",
        }
        lines = [
            f"当前目标：{profile.raw_goal}",
            "目标模式：用户自定义目标",
            f"截图文件名：{filename}",
            f"核心关键词：{json.dumps(profile.core_keywords[:16], ensure_ascii=False)}",
            f"场景关键词：{json.dumps(profile.scene_keywords[:16], ensure_ascii=False)}",
            f"辅助关键词：{json.dumps(profile.support_keywords[:16], ensure_ascii=False)}",
            f"语义关键词：{json.dumps(profile.semantic_keywords[:16], ensure_ascii=False)}",
            f"负向关键词：{json.dumps(profile.negative_keywords[:16], ensure_ascii=False)}",
            "请结合附图，输出严格符合下列 JSON 结构的对象（字段齐全）：",
            json.dumps(output_schema, ensure_ascii=False),
        ]
        return SYSTEM_PROMPT, "\n".join(lines)

    def _request_completion(
        self,
        system_prompt: str,
        user_text: str,
        image_data_url: str,
        *,
        use_json_object: bool,
    ) -> dict[str, Any]:
        endpoint = self._build_endpoint()
        payload: dict[str, Any] = {
            "model": self.config.siliconflow_model,
            "temperature": self.config.siliconflow_temperature,
            "max_tokens": self.config.siliconflow_max_tokens,
            "enable_thinking": self.config.siliconflow_enable_thinking,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
        }
        if use_json_object:
            payload["response_format"] = {"type": "json_object"}

        try:
            return self._post_chat_completion(endpoint, payload)
        except RuntimeError as exc:
            # 若模型输出被截断导致 JSON 解析失败，尝试提高 max_tokens 重试一次
            msg = str(exc)
            if ("JSON" in msg or "解析" in msg or "未闭合" in msg) and self.config.siliconflow_max_tokens < 3000:
                payload_retry = dict(payload)
                payload_retry["max_tokens"] = min(int(payload_retry.get("max_tokens", self.config.siliconflow_max_tokens) * 1.5), 3000)
                return self._post_chat_completion(endpoint, payload_retry)
            if use_json_object and "HTTP 400" in str(exc):
                payload.pop("response_format", None)
                return self._post_chat_completion(endpoint, payload)
            raise

    def _post_chat_completion(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {self.config.siliconflow_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.config.siliconflow_timeout_seconds) as response:
                raw_response = response.read().decode("utf-8")
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code}: {error_body[:400]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"网络请求失败：{exc.reason}") from exc

        try:
            body = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            snippet = raw_response[:400].replace("\n", "\\n")
            raise RuntimeError(f"SiliconFlow 返回了非 JSON 响应：{exc}. raw_snippet={snippet}") from exc
        message = ((body.get("choices") or [{}])[0].get("message") or {})
        content = str(message.get("content") or "").strip()
        if not content:
            raise RuntimeError("模型返回了空内容。")

        # 优先把 content 当作“纯 JSON”直接解析；失败再做花括号提取与二次解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            extracted = _extract_json_payload(content)
            try:
                return json.loads(extracted)
            except json.JSONDecodeError as exc:
                snippet = content[:400].replace("\n", "\\n")
                raise RuntimeError(
                    f"模型返回内容JSON解析失败：{exc}. content_snippet={snippet}"
                ) from exc

    def _build_endpoint(self) -> str:
        base_url = self.config.siliconflow_base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def _normalize_response(
        self,
        payload: dict[str, Any],
        ocr_quality: float,
        ocr_text: str,
    ) -> dict[str, object]:
        category_type = self._normalize_category(payload.get("category_type"))
        category_label = str(payload.get("category_label") or self._default_category_label(category_type)).strip()

        strong_hits = self._coerce_list(payload.get("strong_hits"), 8)
        scene_hits = self._coerce_list(payload.get("scene_hits"), 8)
        support_hits = self._coerce_list(payload.get("support_hits"), 8)
        negative_hits = self._coerce_list(payload.get("negative_hits"), 8)
        matched_keywords = self._coerce_list(payload.get("matched_keywords"), 12)
        if not matched_keywords:
            matched_keywords = self._coerce_list(strong_hits + scene_hits + support_hits, 12)
        matched_context_patterns = self._coerce_list(payload.get("matched_context_patterns"), 8)

        semantic_match_score = self._coerce_score(payload.get("semantic_match_score"))
        keyword_hit_score = self._coerce_score(payload.get("keyword_hit_score"))
        strong_hit_score = self._coerce_score(payload.get("strong_hit_score"))
        coverage_score = self._coerce_score(payload.get("coverage_score"))
        structure_score = self._coerce_score(payload.get("structure_score"))
        app_context_score = self._coerce_score(payload.get("app_context_score"))
        relevance_score = self._coerce_score(payload.get("relevance_score"))
        distraction_score = self._coerce_score(payload.get("distraction_score"))

        if keyword_hit_score == 0.0 and matched_keywords:
            keyword_hit_score = round(clamp(len(matched_keywords) / 6.0), 3)
        if strong_hit_score == 0.0 and strong_hits:
            strong_hit_score = round(clamp(len(strong_hits) / 4.0), 3)
        if coverage_score == 0.0 and matched_keywords:
            coverage_score = round(clamp(len(matched_keywords) / 8.0), 3)
        if app_context_score == 0.0:
            app_context_score = round(relevance_score, 3)

        focus_score = self._coerce_focus_score(payload.get("focus_score"))
        if focus_score == 0.0 and (relevance_score > 0.0 or distraction_score > 0.0):
            focus_score = round(clamp(0.72 * relevance_score + 0.28 * (1 - distraction_score)) * 100, 1)
        confidence = self._coerce_score(payload.get("confidence"))
        status = self._normalize_status(payload.get("status"), focus_score)

        positive_rule = str(payload.get("positive_rule") or "").strip()
        negative_rule = str(payload.get("negative_rule") or "").strip()
        if not positive_rule and strong_hits:
            positive_rule = f"主要正向证据：{strong_hits[0]}"
        if not negative_rule and negative_hits:
            negative_rule = f"主要偏离信号：{negative_hits[0]}"

        reason = str(payload.get("reason") or positive_rule or negative_rule or "模型已完成截图分析。").strip()
        focus_probability = round(clamp(focus_score / 100.0), 3)
        ocr_quality = round(clamp(ocr_quality), 3)

        return {
            "ocr_text": ocr_text,
            "ocr_quality": ocr_quality,
            "category_type": category_type,
            "category_label": category_label,
            "semantic_match_score": semantic_match_score,
            "keyword_hit_score": keyword_hit_score,
            "strong_hit_score": strong_hit_score,
            "coverage_score": coverage_score,
            "structure_score": structure_score,
            "app_context_score": app_context_score,
            "relevance_score": relevance_score,
            "distraction_score": distraction_score,
            "focus_score": focus_score,
            "focus_probability": focus_probability,
            "confidence": confidence,
            "status": status,
            "matched_keywords": matched_keywords,
            "strong_hits": strong_hits,
            "scene_hits": scene_hits,
            "support_hits": support_hits,
            "negative_hits": negative_hits,
            "matched_context_patterns": matched_context_patterns,
            "positive_rule": positive_rule,
            "negative_rule": negative_rule,
            "reason": reason,
            "fallback_reason": "",
            "review_required": False,
            "score_breakdown": {
                "semantic_score": semantic_match_score,
                "strong_hit_score": strong_hit_score,
                "coverage_score": coverage_score,
                "keyword_hit_score": keyword_hit_score,
                "structure_score": structure_score,
                "app_context_score": app_context_score,
                "ocr_quality_score": ocr_quality,
                "base_relevance_score": relevance_score,
                "distraction_score": distraction_score,
                "confidence": confidence,
                "llm_scored": 1.0,
            },
        }

    def _review_result(self, message: str, ocr_quality: float) -> dict[str, object]:
        return {
            "ocr_text": "",
            "ocr_quality": round(clamp(ocr_quality), 3),
            "category_type": "neutral",
            "category_label": "待复核场景",
            "semantic_match_score": 0.0,
            "keyword_hit_score": 0.0,
            "strong_hit_score": 0.0,
            "coverage_score": 0.0,
            "structure_score": 0.0,
            "app_context_score": 0.0,
            "relevance_score": 0.0,
            "distraction_score": 0.0,
            "focus_score": 0.0,
            "focus_probability": 0.0,
            "confidence": 0.0,
            "status": "待复核",
            "matched_keywords": [],
            "strong_hits": [],
            "scene_hits": [],
            "support_hits": [],
            "negative_hits": [],
            "matched_context_patterns": [],
            "positive_rule": "",
            "negative_rule": "",
            "reason": message,
            "fallback_reason": message,
            "review_required": True,
            "cache_hit": False,
            "score_breakdown": {
                "semantic_score": 0.0,
                "strong_hit_score": 0.0,
                "coverage_score": 0.0,
                "keyword_hit_score": 0.0,
                "structure_score": 0.0,
                "app_context_score": 0.0,
                "ocr_quality_score": round(clamp(ocr_quality), 3),
                "base_relevance_score": 0.0,
                "distraction_score": 0.0,
                "confidence": 0.0,
                "llm_scored": 0.0,
            },
        }

    def _normalize_category(self, value: Any) -> str:
        text = str(value or "neutral").strip().lower()
        if text in {"focus", "study", "learning", "goal_related", "related"}:
            return "focus"
        return "neutral"

    def _normalize_status(self, value: Any, focus_score: float) -> str:
        text = str(value or "").strip()
        if text in {"专注", "轻微偏离", "分心"}:
            return text
        if focus_score >= 74:
            return "专注"
        if focus_score >= 50:
            return "轻微偏离"
        return "分心"

    def _default_category_label(self, category_type: str) -> str:
        if category_type == "focus":
            return "目标相关场景"
        return "待确认场景"

    def _coerce_score(self, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number > 1.0 and number <= 100.0:
            number = number / 100.0
        return round(clamp(number), 3)

    def _coerce_focus_score(self, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if 0.0 <= number <= 1.0:
            number = number * 100.0
        return round(max(0.0, min(number, 100.0)), 1)

    def _coerce_list(self, value: Any, limit: int) -> list[str]:
        if isinstance(value, str):
            source = [value]
        elif isinstance(value, list):
            source = value
        else:
            source = []

        items: list[str] = []
        seen: set[str] = set()
        for raw in source:
            text = str(raw or "").strip()
            if not text:
                continue
            normalized = text.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            items.append(text)
            if len(items) >= limit:
                break
        return items
