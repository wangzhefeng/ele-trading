"""市场状态特征快照、概率结果与可解释 logistic provider。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import cast

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class MarketState(str, Enum):
    NORMAL = "normal"
    STRAINED = "strained"
    CONGESTED = "congested"
    EXTREME = "extreme"


def _aware_timestamp(
    value: pd.Timestamp | str,
    field_name: str,
) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware timestamp")
    return cast(pd.Timestamp, timestamp)


def _validated_feature_frame(frame: pd.DataFrame, field_name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"{field_name} must be a non-empty DataFrame")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError(f"{field_name} must use a timezone-aware DatetimeIndex")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError(f"{field_name} index must be unique and monotonic")
    if not frame.columns.is_unique or any(not str(item).strip() for item in frame.columns):
        raise ValueError(f"{field_name} columns must be unique and non-empty")
    try:
        values = frame.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain numeric values") from exc
    if not np.isfinite(values).all():
        raise ValueError(f"{field_name} must contain finite values")
    return frame.astype(float).copy()


@dataclass(frozen=True, slots=True)
class MarketStateFeatureSnapshot:
    """决策时刻可见、对未来有效时段对齐的物理/市场特征。"""

    as_of: pd.Timestamp
    version: str
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        as_of = _aware_timestamp(self.as_of, "as_of")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must not be empty")
        frame = _validated_feature_frame(self.frame, "frame")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "frame", frame)


@dataclass(frozen=True, slots=True)
class MarketStateForecast:
    """每个有效时段的 normal/strained/congested/extreme 概率。"""

    issue_time: pd.Timestamp
    valid_time_index: pd.DatetimeIndex
    probabilities: pd.DataFrame
    state_definition_version: str
    feature_as_of: pd.Timestamp
    model_version: str
    model_kind: str
    feature_version: str
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        issue_time = _aware_timestamp(self.issue_time, "issue_time")
        feature_as_of = _aware_timestamp(self.feature_as_of, "feature_as_of")
        if feature_as_of > issue_time:
            raise ValueError("feature_as_of must not be later than issue_time")
        index = pd.DatetimeIndex(self.valid_time_index)
        if index.tz is None or index.empty or index.has_duplicates:
            raise ValueError("valid_time_index must be non-empty, aware, and unique")
        if not index.is_monotonic_increasing:
            raise ValueError("valid_time_index must be monotonic increasing")
        expected_columns = tuple(state.value for state in MarketState)
        probabilities = self.probabilities.copy()
        if not probabilities.index.equals(index):
            raise ValueError("probabilities must align with valid_time_index")
        if tuple(probabilities.columns) != expected_columns:
            raise ValueError("probability columns must match canonical market states")
        values = probabilities.to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0.0).any() or (values > 1.0).any():
            raise ValueError("state probabilities must be finite and within [0, 1]")
        if not np.allclose(values.sum(axis=1), 1.0, atol=1e-9, rtol=0.0):
            raise ValueError("state probabilities must sum to 1 for each period")
        for field_name in (
            "state_definition_version",
            "model_version",
            "model_kind",
            "feature_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.model_kind not in {"physical", "price_regime_proxy"}:
            raise ValueError("model_kind must be physical or price_regime_proxy")
        object.__setattr__(self, "issue_time", issue_time)
        object.__setattr__(self, "feature_as_of", feature_as_of)
        object.__setattr__(self, "valid_time_index", index)
        object.__setattr__(self, "probabilities", probabilities)
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))


class LogisticMarketStateProvider:
    """使用决策时刻可得特征训练的多项 logistic 状态门控。"""

    def __init__(
        self,
        training_features: pd.DataFrame,
        labels: pd.Series,
        *,
        feature_as_of: pd.Timestamp,
        state_definition_version: str,
        model_kind: str,
        model_version: str = "logistic-market-state-v1",
    ) -> None:
        features = _validated_feature_frame(training_features, "training_features")
        if not isinstance(labels, pd.Series) or not labels.index.equals(features.index):
            raise ValueError("labels must be a Series aligned with training_features")
        normalized_labels = labels.astype(str)
        allowed = {state.value for state in MarketState}
        unknown = set(normalized_labels.unique()) - allowed
        if unknown:
            raise ValueError(f"unknown market-state labels: {sorted(unknown)}")
        if normalized_labels.nunique() < 2:
            raise ValueError("market-state training requires at least two classes")
        if model_kind not in {"physical", "price_regime_proxy"}:
            raise ValueError("model_kind must be physical or price_regime_proxy")
        price_tokens = ("price", "p_real", "p_dayah", "spread")
        price_only = all(
            any(token in str(column).lower() for token in price_tokens)
            for column in features.columns
        )
        if model_kind == "physical" and price_only:
            raise ValueError(
                "price-only state features must use model_kind='price_regime_proxy'"
            )
        self.feature_as_of = _aware_timestamp(feature_as_of, "feature_as_of")
        for field_name, value in (
            ("state_definition_version", state_definition_version),
            ("model_version", model_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        self.state_definition_version = state_definition_version
        self.model_version = model_version
        self.model_kind = model_kind
        self.feature_names = tuple(str(item) for item in features.columns)
        self._pipeline = Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=1000, random_state=0)),
            ]
        )
        self._pipeline.fit(features.loc[:, self.feature_names], normalized_labels)

    def forecast(
        self,
        issue_time: pd.Timestamp,
        snapshot: MarketStateFeatureSnapshot,
    ) -> MarketStateForecast:
        issue_time = _aware_timestamp(issue_time, "issue_time")
        if self.feature_as_of > issue_time or snapshot.as_of > issue_time:
            raise ValueError("market-state feature_as_of is later than issue_time")
        if tuple(str(item) for item in snapshot.frame.columns) != self.feature_names:
            raise ValueError("market-state feature columns must match training columns")
        raw = self._pipeline.predict_proba(snapshot.frame.loc[:, self.feature_names])
        model = self._pipeline.named_steps["model"]
        classes = tuple(str(item) for item in model.classes_)
        probabilities = pd.DataFrame(
            0.0,
            index=snapshot.frame.index,
            columns=tuple(state.value for state in MarketState),
        )
        for column_index, state in enumerate(classes):
            probabilities.loc[:, state] = raw[:, column_index]
        feature_as_of = max(self.feature_as_of, snapshot.as_of)
        flags = (f"model_kind:{self.model_kind}",)
        return MarketStateForecast(
            issue_time=issue_time,
            valid_time_index=pd.DatetimeIndex(snapshot.frame.index),
            probabilities=probabilities,
            state_definition_version=self.state_definition_version,
            feature_as_of=feature_as_of,
            model_version=self.model_version,
            model_kind=self.model_kind,
            feature_version=snapshot.version,
            quality_flags=flags,
        )
