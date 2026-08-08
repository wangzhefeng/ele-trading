"""data_provider 层拥有的市场数据契约。

核心是 ``MarketDataSnapshot``：带版本与观测截止时刻（``as_of``）的市场数据
快照，是防前瞻偏差（look-ahead bias）的第一道防线——构造时即强制校验，
不合法的数据根本进不了下游预测/优化链路。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd


def _require_non_empty(value: str, field_name: str) -> None:
    """校验字符串字段非空（含纯空白），为空则抛 ``ValueError``。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_timestamp(value: object, field_name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware timestamp")
    return timestamp


@dataclass(frozen=True, slots=True)
class DataAvailabilityRecord:
    """一项数据从业务发生到系统可消费的版本化时点证据。"""

    source_id: str
    event_time: pd.Timestamp
    published_at: pd.Timestamp
    available_at: pd.Timestamp
    version: str
    revision: int = 0
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.source_id, "source_id")
        _require_non_empty(self.version, "version")
        event_time = _require_timestamp(self.event_time, "event_time")
        published_at = _require_timestamp(self.published_at, "published_at")
        available_at = _require_timestamp(self.available_at, "available_at")
        if available_at < published_at:
            raise ValueError(
                "available_at cannot be earlier than published_at"
            )
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))

    def is_available_at(self, issue_time: pd.Timestamp) -> bool:
        """返回该版本在给定 issue time 是否已可消费。"""
        issue_time = _require_timestamp(issue_time, "issue_time")
        return bool(self.available_at <= issue_time)


@dataclass(frozen=True, slots=True)
class EvidenceCatalogEntry:
    """可审计外部证据的 owner、许可、保留与版本化可用性元数据。"""

    catalog_id: str
    artifact_type: str
    owner: str
    permission: str
    retention_policy: str
    availability: DataAvailabilityRecord

    def __post_init__(self) -> None:
        for field_name in (
            "catalog_id",
            "artifact_type",
            "owner",
            "permission",
            "retention_policy",
        ):
            _require_non_empty(getattr(self, field_name), field_name)
        if not isinstance(self.availability, DataAvailabilityRecord):
            raise ValueError("availability must be a DataAvailabilityRecord")


@dataclass(frozen=True, slots=True)
class DataCatalog:
    """外部数据、规则、网架、计量与账单的证据目录，不持有数据本体。"""

    entries: Mapping[str, EvidenceCatalogEntry]

    def __post_init__(self) -> None:
        if not isinstance(self.entries, Mapping) or not self.entries:
            raise ValueError("entries must be a non-empty mapping")
        entries = dict(self.entries)
        for catalog_id, entry in entries.items():
            _require_non_empty(catalog_id, "catalog entry ID")
            if not isinstance(entry, EvidenceCatalogEntry):
                raise ValueError("entries must contain EvidenceCatalogEntry objects")
            if entry.catalog_id != catalog_id:
                raise ValueError("catalog entry key must match catalog_id")
        object.__setattr__(self, "entries", entries)

    def require_available(
        self,
        catalog_id: str,
        *,
        as_of: pd.Timestamp,
    ) -> EvidenceCatalogEntry:
        """只返回在本时点已授权、可消费的外部证据。"""
        _require_non_empty(catalog_id, "catalog_id")
        try:
            entry = self.entries[catalog_id]
        except KeyError as exc:
            raise ValueError("catalog entry is not registered") from exc
        if entry.permission != "authorized":
            raise ValueError("catalog entry permission is not authorized")
        if not entry.availability.is_available_at(as_of):
            raise ValueError("catalog entry is not available at as_of")
        return entry


@dataclass(slots=True)
class MarketDataSnapshot:
    """某一截止时刻可见的版本化市场数据快照。

    默认所有行都是观测值（observation）；未来有效时刻的预测行只允许在
    显式携带 ``is_observation=False`` 时存在——即「晚于 as_of 的观测行」
    一律拒绝，从构造层面杜绝未来信息泄漏。
    """

    market: str                                  # 市场标识（如 "single_settlement"）
    scope_type: str                              # 作用域类型（如 "node"/"zone"）
    scope_id: str                                # 作用域对象标识
    as_of: pd.Timestamp                          # 数据可见截止时刻（必须带时区）
    frame: pd.DataFrame                          # 数据本体，必含 timestamp / is_observation 列
    version: str                                 # 数据版本标识（溯源用）
    quality_flags: tuple[str, ...] = ()          # 质量标记（如 "degraded"）
    availability: tuple[DataAvailabilityRecord, ...] = ()

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
        availability = tuple(self.availability)
        if not all(
            isinstance(item, DataAvailabilityRecord)
            for item in availability
        ):
            raise ValueError(
                "availability must contain DataAvailabilityRecord objects"
            )
        if any(item.available_at > self.as_of for item in availability):
            raise ValueError(
                "availability records cannot be newer than as_of"
            )
        self.availability = availability


@dataclass(frozen=True, slots=True)
class RuleSnapshot:
    """带发布时间与生效窗口的正式市场规则快照。"""

    market: str
    rule_version: str
    published_at: pd.Timestamp
    effective_from: pd.Timestamp
    effective_to: pd.Timestamp | None
    parameters: Mapping[str, object]
    source_document: str
    confirmed: bool

    def __post_init__(self) -> None:
        _require_non_empty(self.market, "market")
        _require_non_empty(self.rule_version, "rule_version")
        _require_non_empty(self.source_document, "source_document")
        published_at = _require_timestamp(self.published_at, "published_at")
        effective_from = _require_timestamp(
            self.effective_from,
            "effective_from",
        )
        effective_to = None
        if self.effective_to is not None:
            effective_to = _require_timestamp(
                self.effective_to,
                "effective_to",
            )
            if effective_to <= effective_from:
                raise ValueError(
                    "effective_to must be later than effective_from"
                )
        if not isinstance(self.parameters, Mapping):
            raise ValueError("parameters must be a mapping")
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmed must be a boolean")
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
        object.__setattr__(self, "parameters", dict(self.parameters))

    def is_known_at(self, decision_time: pd.Timestamp) -> bool:
        decision_time = _require_timestamp(decision_time, "decision_time")
        return bool(self.published_at <= decision_time)

    def is_effective_at(self, delivery_time: pd.Timestamp) -> bool:
        delivery_time = _require_timestamp(delivery_time, "delivery_time")
        return bool(
            self.effective_from <= delivery_time
            and (
                self.effective_to is None
                or delivery_time < self.effective_to
            )
        )
