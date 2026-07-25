from __future__ import annotations

from collections.abc import Iterable


# 电价类型内部统一使用英文编码；中文 CSV 或 YAML 输入在读取时映射到这些标准值。
CANONICAL_PRICE_TYPES: tuple[str, ...] = ("deep_valley", "valley", "flat", "peak", "sharp_peak")

_PRICE_TYPE_ALIASES: dict[str, str] = {
    "deep_valley": "deep_valley",
    "deep-valley": "deep_valley",
    "deep valley": "deep_valley",
    "深谷": "deep_valley",
    "深谷段": "deep_valley",
    "valley": "valley",
    "off_peak": "valley",
    "off-peak": "valley",
    "off peak": "valley",
    "谷": "valley",
    "谷段": "valley",
    "低谷": "valley",
    "低谷段": "valley",
    "flat": "flat",
    "normal": "flat",
    "平": "flat",
    "平段": "flat",
    "平时": "flat",
    "peak": "peak",
    "on_peak": "peak",
    "on-peak": "peak",
    "on peak": "peak",
    "峰": "peak",
    "峰段": "peak",
    "高峰": "peak",
    "高峰段": "peak",
    "sharp_peak": "sharp_peak",
    "sharp-peak": "sharp_peak",
    "sharp peak": "sharp_peak",
    "尖峰": "sharp_peak",
    "尖峰段": "sharp_peak",
}


def normalize_price_type(value: object) -> str:
    """把中文或英文电价类型统一为内部英文编码。

    例如 `谷`、`低谷` 会统一为 `valley`，`尖峰` 会统一为 `sharp_peak`。
    未登记的英文扩展类型会保留小写形式，便于后续场景扩展；未登记的中文类型直接报错。
    """

    if value is None:
        raise ValueError("price_type cannot be empty.")
    text = str(value).strip()
    if not text or text.lower() == "nan":
        raise ValueError("price_type cannot be empty.")

    key = text.lower()
    if key in _PRICE_TYPE_ALIASES:
        return _PRICE_TYPE_ALIASES[key]
    if text in _PRICE_TYPE_ALIASES:
        return _PRICE_TYPE_ALIASES[text]
    if text.isascii():
        return key
    raise ValueError(f"Unsupported Chinese price_type: {text}")


def normalize_price_types(values: Iterable[object]) -> tuple[str, ...]:
    """批量规范化电价类型，并保持首次出现顺序去重。"""

    normalized: list[str] = []
    for value in values:
        item = normalize_price_type(value)
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)
