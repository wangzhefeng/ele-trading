"""data_provider 层拥有的市场数据契约。

核心是 ``MarketDataSnapshot``：带版本与观测截止时刻（``as_of``）的市场数据
快照，是防前瞻偏差（look-ahead bias）的第一道防线——构造时即强制校验，
不合法的数据根本进不了下游预测/优化链路。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def _require_non_empty(value: str, field_name: str) -> None:
    """校验字符串字段非空（含纯空白），为空则抛 ``ValueError``。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(slots=True)
class MarketDataSnapshot:
    """某一截止时刻可见的版本化市场数据快照。

    默认所有行都是观测值（observation）；未来有效时刻的预测行只允许在
    显式携带 ``is_observation=False`` 时存在——即「晚于 as_of 的观测行」
    一律拒绝，从构造层面杜绝未来信息泄漏。
    """

    market: str                                  # 市场标识（如 "mengxi"）
    scope_type: str                              # 作用域类型（如 "node"/"zone"）
    scope_id: str                                # 作用域对象标识
    as_of: pd.Timestamp                          # 数据可见截止时刻（必须带时区）
    frame: pd.DataFrame                          # 数据本体，必含 timestamp / is_observation 列
    version: str                                 # 数据版本标识（溯源用）
    quality_flags: tuple[str, ...] = ()          # 质量标记（如 "degraded"）

    def __post_init__(self) -> None:
        # --- 标识字段非空校验 ---
        _require_non_empty(self.market, "market")
        _require_non_empty(self.scope_type, "scope_type")
        _require_non_empty(self.scope_id, "scope_id")
        _require_non_empty(self.version, "version")

        # --- as_of 必须带时区（ naive 时间戳无法判断"未来"） ---
        self.as_of = pd.Timestamp(self.as_of)
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")

        # --- frame 结构校验：DataFrame 且含 timestamp 列 ---
        if not isinstance(self.frame, pd.DataFrame):
            raise ValueError("frame must be a pandas DataFrame")
        if "timestamp" not in self.frame.columns:
            raise ValueError("frame must contain a timestamp column")

        # --- timestamp 必须带时区、单调递增、无重复 ---
        timestamps = pd.DatetimeIndex(self.frame["timestamp"])
        if timestamps.tz is None:
            raise ValueError("timestamp data must be timezone-aware")
        if not timestamps.is_monotonic_increasing:
            raise ValueError("timestamp data must be monotonic; unordered rows found")
        if not timestamps.is_unique:
            raise ValueError("timestamp data must be unique; duplicate rows found")

        # --- is_observation 必须为无缺失的严格布尔列 ---
        if "is_observation" not in self.frame.columns:
            raise ValueError("frame must contain an is_observation column")
        observation_mask = self.frame["is_observation"]
        if (
            observation_mask.isna().any()
            or not pd.api.types.is_bool_dtype(observation_mask.dtype)
        ):
            raise ValueError(
                "is_observation must be a non-null boolean column"
            )

        # --- 防前瞻核心校验：观测行不得晚于 as_of ---
        # （时区不兼容时比较会抛 TypeError，转成带说明的 ValueError）
        try:
            future_observations = observation_mask.to_numpy() & (
                timestamps > self.as_of
            )
        except TypeError as exc:
            raise ValueError(
                "timestamp data and as_of must use compatible timezones"
            ) from exc
        if future_observations.any():
            raise ValueError("observation rows cannot be newer than as_of")

        # 统一为 tuple，保证不可变
        self.quality_flags = tuple(self.quality_flags)
