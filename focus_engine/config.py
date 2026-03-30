from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_local_env(path: Path = _ENV_FILE) -> None:
    if not path.exists():
        return

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_load_local_env()


@dataclass(frozen=True)
class AnalyzerConfig:
    supported_extensions: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
    max_uploads: int = _env_int("FOCUS_MAX_UPLOADS", 30)
    max_workers: int = max(1, min(_env_int("FOCUS_MAX_WORKERS", 4), 8))
    max_image_edge: int = _env_int("FOCUS_MAX_IMAGE_EDGE", 1800)
    min_image_edge: int = _env_int("FOCUS_MIN_IMAGE_EDGE", 960)
    ocr_cache_size: int = _env_int("FOCUS_OCR_CACHE_SIZE", 128)
    llm_cache_size: int = _env_int("FOCUS_LLM_CACHE_SIZE", 256)
    thumbnail_hash_size: int = _env_int("FOCUS_HASH_SIZE", 16)
    duplicate_distance_threshold: int = _env_int("FOCUS_DUPLICATE_THRESHOLD", 6)
    session_window_size: int = _env_int("FOCUS_SESSION_WINDOW", 8)
    session_ttl_seconds: int = _env_int("FOCUS_SESSION_TTL", 1800)
    session_default_duration_minutes: int = max(1, _env_int("FOCUS_SESSION_DEFAULT_DURATION_MINUTES", 25))
    session_min_duration_minutes: int = max(1, _env_int("FOCUS_SESSION_MIN_DURATION_MINUTES", 1))
    session_max_duration_minutes: int = max(1, _env_int("FOCUS_SESSION_MAX_DURATION_MINUTES", 180))
    enable_temporal_smoothing: bool = _env_bool("FOCUS_ENABLE_TEMPORAL_SMOOTHING", True)
    # SiliconFlow OpenAI 兼容 Chat Completions（千问等多模态模型），见 https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions
    siliconflow_api_key: str = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    siliconflow_base_url: str = os.environ.get(
        "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
    ).strip()
    siliconflow_model: str = (
        os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen3.5-35B-A3B").strip()
        or "Qwen/Qwen3.5-35B-A3B"
    )
    siliconflow_timeout_seconds: int = max(10, _env_int("SILICONFLOW_TIMEOUT_SECONDS", 120))
    siliconflow_temperature: float = max(0.0, min(_env_float("SILICONFLOW_TEMPERATURE", 0.2), 2.0))
    siliconflow_max_tokens: int = max(400, _env_int("SILICONFLOW_MAX_TOKENS", 1200))
    # SiliconFlow：enable_thinking=false 为非思考模式（部分 Qwen/DeepSeek/GLM 等支持），见官方 Chat Completions 文档
    siliconflow_enable_thinking: bool = _env_bool("SILICONFLOW_ENABLE_THINKING", False)


DEFAULT_CONFIG = AnalyzerConfig()

