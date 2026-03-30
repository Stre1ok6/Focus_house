from __future__ import annotations

import cv2
import numpy as np

from .config import AnalyzerConfig, DEFAULT_CONFIG


def decode_image(file_bytes: bytes) -> np.ndarray | None:
    array = np.frombuffer(file_bytes, dtype=np.uint8)
    if array.size == 0:
        return None
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def crop_focus_region(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    top = int(height * 0.04)
    bottom = int(height * 0.96)
    left = int(width * 0.02)
    right = int(width * 0.98)
    return image[top:bottom, left:right]


def resize_for_ocr(image: np.ndarray, config: AnalyzerConfig) -> np.ndarray:
    height, width = image.shape[:2]
    max_edge = max(height, width)
    min_edge = min(height, width)

    if max_edge > config.max_image_edge:
        scale = config.max_image_edge / max_edge
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    if min_edge < config.min_image_edge:
        scale = config.min_image_edge / max(min_edge, 1)
        scale = min(scale, 1.8)
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    return image


def average_hash(image: np.ndarray, hash_size: int = 16) -> str:
    gray = cv2.cvtColor(crop_focus_region(image), cv2.COLOR_BGR2GRAY)
    thumb = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    mean = float(thumb.mean())
    bits = "".join("1" if pixel > mean else "0" for pixel in thumb.flatten())
    width = hash_size * hash_size // 4
    return hex(int(bits, 2))[2:].zfill(width)


def hash_distance(left: str, right: str) -> int:
    if not left or not right:
        return 999
    left_bits = bin(int(left, 16))[2:].zfill(len(left) * 4)
    right_bits = bin(int(right, 16))[2:].zfill(len(right) * 4)
    return sum(bit_left != bit_right for bit_left, bit_right in zip(left_bits, right_bits))


def configure_tesseract(config: AnalyzerConfig = DEFAULT_CONFIG) -> tuple[bool, str]:
    """向后兼容：原 Tesseract OCR 已改为 SiliconFlow 视觉模型，此处返回 API 配置状态。"""
    ready = bool(config.siliconflow_api_key)
    if ready:
        return True, f"SiliconFlow · {config.siliconflow_model}"
    return False, "请配置 SILICONFLOW_API_KEY"
