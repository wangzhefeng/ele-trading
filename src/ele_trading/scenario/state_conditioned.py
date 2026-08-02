"""状态条件 t-Copula 联合场景与有证据的极端模板。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, cast

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

from ele_trading.forecasting.contracts import ForecastResult
from ele_trading.forecasting.market_state import MarketState, MarketStateForecast
from ele_trading.domain.price_roles import PriceRole

from .contracts import Scenario, ScenarioSet

if TYPE_CHECKING:
    from ele_trading.domain.contracts import MarketForecastBundle


_NON_NEGATIVE_TARGETS = {
    "load",
    "load_power",
    "wind",
    "wind_power",
    "pv",
    "pv_power",
    "solar",
    "solar_power",
}


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value.strip()


def _aware(value: pd.Timestamp | str, field_name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return cast(pd.Timestamp, timestamp)


def _stabilized_correlation(values: np.ndarray) -> np.ndarray:
    dimension = values.shape[1]
    if len(values) < 2 or dimension == 1:
        return np.eye(dimension)
    # 手动计算相关矩阵：常数列方差为零时不产生 RuntimeWarning，
    # 其相关定义为 0（对角保持 1），再特征值修正为半正定。
    centered = values - values.mean(axis=0)
    std = values.std(axis=0)
    covariance = centered.T @ centered / (len(values) - 1)
    scale = np.outer(std, std)
    correlation = np.zeros((dimension, dimension))
    valid = scale > 1e-16
    correlation[valid] = covariance[valid] / scale[valid]
    correlation = np.clip(correlation, -1.0, 1.0)
    correlation = (correlation + correlation.T) / 2.0
    np.fill_diagonal(correlation, 1.0)
    eigenvalues, eigenvectors = np.linalg.eigh(correlation)
    eigenvalues = np.maximum(eigenvalues, 1e-8)
    correlation = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
    diag_scale = np.sqrt(np.diag(correlation))
    correlation = correlation / np.outer(diag_scale, diag_scale)
    np.fill_diagonal(correlation, 1.0)
    return correlation


@dataclass(frozen=True, slots=True)
class ExtremeScenarioTemplate:
    """由历史事件/规则证据标定的加性压力模板。"""

    template_id: str
    state: str
    additive_shocks: Mapping[str, np.ndarray]
    calibrated_probability: float
    evidence_version: str
    rule_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_id", _non_empty(self.template_id, "template_id"))
        object.__setattr__(self, "evidence_version", _non_empty(self.evidence_version, "evidence_version"))
        object.__setattr__(self, "rule_version", _non_empty(self.rule_version, "rule_version"))
        try:
            state = MarketState(self.state).value
        except ValueError as exc:
            raise ValueError(f"unknown market state: {self.state!r}") from exc
        object.__setattr__(self, "state", state)
        probability = float(self.calibrated_probability)
        if not np.isfinite(probability) or not 0.0 < probability < 1.0:
            raise ValueError("calibrated_probability must be within (0, 1)")
        object.__setattr__(self, "calibrated_probability", probability)
        if not isinstance(self.additive_shocks, Mapping) or not self.additive_shocks:
            raise ValueError("additive_shocks must be a non-empty mapping")
        shocks: dict[str, np.ndarray] = {}
        for target, values in self.additive_shocks.items():
            target = _non_empty(target, "additive shock target")
            array = np.asarray(values, dtype=float).copy()
            if array.ndim != 1 or array.size == 0 or not np.isfinite(array).all():
                raise ValueError(
                    f"additive_shocks[{target!r}] must be a finite 1-D array"
                )
            array.setflags(write=False)
            shocks[target] = array
        object.__setattr__(self, "additive_shocks", shocks)


class StateConditionedScenarioBuilder:
    """用状态条件经验边际和 Student-t copula 生成联合场景。"""

    supports_market_state = True

    def __init__(
        self,
        *,
        residual_history: pd.DataFrame,
        state_labels: pd.Series,
        residual_as_of: pd.Timestamp,
        state_definition_version: str,
        t_copula_df: float = 5.0,
        min_state_samples: int = 8,
    ) -> None:
        if not isinstance(residual_history, pd.DataFrame) or residual_history.empty:
            raise ValueError("residual_history must be a non-empty DataFrame")
        if (
            not isinstance(residual_history.index, pd.DatetimeIndex)
            or residual_history.index.tz is None
            or residual_history.index.has_duplicates
            or not residual_history.index.is_monotonic_increasing
        ):
            raise ValueError(
                "residual_history index must be unique, ordered and timezone-aware"
            )
        if not residual_history.columns.is_unique:
            raise ValueError("residual_history columns must be unique")
        if not all(
            pd.api.types.is_numeric_dtype(dtype)
            for dtype in residual_history.dtypes
        ):
            raise ValueError("residual_history must contain numeric values")
        if not np.isfinite(residual_history.to_numpy(dtype=float)).all():
            raise ValueError("residual_history must contain finite values")
        if not isinstance(state_labels, pd.Series) or not state_labels.index.equals(
            residual_history.index
        ):
            raise ValueError(
                "state_labels must be a Series aligned with residual_history"
            )
        normalized_states: list[str] = []
        for value in state_labels.tolist():
            try:
                normalized_states.append(MarketState(str(value)).value)
            except ValueError as exc:
                raise ValueError(f"unknown market state label: {value!r}") from exc
        as_of = _aware(residual_as_of, "residual_as_of")
        if residual_history.index[-1] > as_of:
            raise ValueError("residual_history contains rows later than residual_as_of")
        if not np.isfinite(t_copula_df) or float(t_copula_df) <= 2.0:
            raise ValueError("t_copula_df must be finite and greater than 2")
        if not isinstance(min_state_samples, int) or min_state_samples < 2:
            raise ValueError("min_state_samples must be an integer >= 2")

        self.residual_history = residual_history.copy()
        self.state_labels = pd.Series(
            normalized_states,
            index=residual_history.index.copy(),
            dtype="object",
        )
        self.residual_as_of = as_of
        self.state_definition_version = _non_empty(
            state_definition_version,
            "state_definition_version",
        )
        self.t_copula_df = float(t_copula_df)
        self.min_state_samples = min_state_samples

    def _validate_inputs(
        self,
        forecasts: Mapping[str, ForecastResult],
        state_forecast: MarketStateForecast,
    ) -> tuple[dict[str, ForecastResult], pd.DatetimeIndex, pd.Timestamp]:
        if not isinstance(forecasts, Mapping) or not forecasts:
            raise ValueError("forecasts must be a non-empty mapping")
        normalized = dict(forecasts)
        if len(normalized) != len(forecasts):
            raise ValueError("forecast targets must be unique")
        missing_residuals = set(normalized) - set(self.residual_history.columns)
        if missing_residuals:
            raise ValueError(
                "residual_history missing forecast targets: "
                + ", ".join(sorted(missing_residuals))
            )
        issue_time: pd.Timestamp | None = None
        valid_index: pd.DatetimeIndex | None = None
        for target, result in normalized.items():
            _non_empty(target, "forecast target")
            if not isinstance(result, ForecastResult):
                raise ValueError(f"forecast {target!r} must be a ForecastResult")
            if issue_time is None:
                issue_time = result.request.issue_time
                valid_index = pd.DatetimeIndex(result.point.index)
            elif result.request.issue_time != issue_time:
                raise ValueError("all forecasts must share issue_time")
            elif not result.point.index.equals(valid_index):
                raise ValueError("all forecasts must share valid_time_index")
        assert issue_time is not None and valid_index is not None
        if self.residual_as_of > issue_time:
            raise ValueError("residual_as_of is later than issue_time")
        if state_forecast.issue_time != issue_time:
            raise ValueError("market state and target forecasts must share issue_time")
        if not state_forecast.valid_time_index.equals(valid_index):
            raise ValueError(
                "market state and target forecasts must share valid_time_index"
            )
        if state_forecast.state_definition_version != self.state_definition_version:
            raise ValueError("market state definition version does not match residual labels")
        return normalized, valid_index, issue_time

    def _state_samples(
        self,
        state: str,
        targets: tuple[str, ...],
    ) -> tuple[np.ndarray, bool]:
        mask = self.state_labels.to_numpy() == state
        state_rows = self.residual_history.loc[mask, list(targets)]
        fallback = len(state_rows) < self.min_state_samples
        rows = (
            self.residual_history.loc[:, list(targets)]
            if fallback
            else state_rows
        )
        return rows.to_numpy(dtype=float), fallback

    @staticmethod
    def _clip_if_physical(target: str, values: np.ndarray) -> np.ndarray:
        if target.lower() in _NON_NEGATIVE_TARGETS:
            return np.maximum(values, 0.0)
        return values

    def build(
        self,
        *,
        forecasts: Mapping[str, ForecastResult],
        market_state_forecast: MarketStateForecast,
        num_scenarios: int,
        random_seed: int,
        extreme_templates: tuple[ExtremeScenarioTemplate, ...] = (),
    ) -> ScenarioSet:
        """生成概率场景并注入有校准概率的极端模板。"""
        if not isinstance(num_scenarios, int) or num_scenarios <= 0:
            raise ValueError("num_scenarios must be a positive integer")
        if not isinstance(random_seed, (int, np.integer)):
            raise ValueError("random_seed must be an integer")
        forecasts, valid_index, issue_time = self._validate_inputs(
            forecasts,
            market_state_forecast,
        )
        templates = tuple(extreme_templates)
        if not all(isinstance(item, ExtremeScenarioTemplate) for item in templates):
            raise ValueError(
                "extreme_templates must contain ExtremeScenarioTemplate objects"
            )
        template_ids = [item.template_id for item in templates]
        if len(template_ids) != len(set(template_ids)):
            raise ValueError("extreme template IDs must be unique")
        template_probability = float(
            sum(item.calibrated_probability for item in templates)
        )
        if template_probability >= 1.0:
            raise ValueError("extreme template probabilities must sum to less than 1")

        targets = tuple(forecasts)
        state_cache: dict[str, tuple[np.ndarray, np.ndarray, bool]] = {}
        for state in MarketState:
            samples, fallback = self._state_samples(state.value, targets)
            correlation = _stabilized_correlation(samples)
            state_cache[state.value] = (samples, correlation, fallback)
        fallback_states = tuple(
            state for state, (_, _, fallback) in state_cache.items() if fallback
        )

        rng = np.random.default_rng(int(random_seed))
        regular_probability = (1.0 - template_probability) / num_scenarios
        scenarios: list[Scenario] = []
        sampled_states: dict[str, tuple[str, ...]] = {}
        source_versions = {
            target: result.model_version for target, result in forecasts.items()
        }
        units = {target: result.unit for target, result in forecasts.items()}

        for scenario_index in range(num_scenarios):
            arrays = {
                target: result.point.to_numpy(dtype=float).copy()
                for target, result in forecasts.items()
            }
            states_for_scenario: list[str] = []
            for time_position, (_, probabilities) in enumerate(
                market_state_forecast.probabilities.iterrows()
            ):
                state = str(rng.choice(probabilities.index, p=probabilities.to_numpy()))
                states_for_scenario.append(state)
                samples, correlation, _ = state_cache[state]
                cholesky = np.linalg.cholesky(correlation)
                normal = rng.normal(size=len(targets))
                scale = np.sqrt(rng.chisquare(self.t_copula_df) / self.t_copula_df)
                latent = cholesky @ normal / scale
                uniforms = student_t.cdf(latent, df=self.t_copula_df)
                for target_index, target in enumerate(targets):
                    residual = float(
                        np.quantile(
                            samples[:, target_index],
                            uniforms[target_index],
                            method="linear",
                        )
                    )
                    arrays[target][time_position] += residual
            trajectories = {
                target: pd.Series(
                    self._clip_if_physical(target, values),
                    index=valid_index,
                    name=target,
                )
                for target, values in arrays.items()
            }
            scenario_id = f"state-t:{scenario_index:04d}"
            sampled_states[scenario_id] = tuple(states_for_scenario)
            scenarios.append(
                Scenario(
                    scenario_id=scenario_id,
                    probability=regular_probability,
                    issue_time=issue_time,
                    trajectories=trajectories,
                    seed=int(random_seed) + scenario_index,
                    source_versions=source_versions,
                )
            )

        forced_ids: list[str] = []
        template_evidence: dict[str, dict[str, str]] = {}
        for template_index, template in enumerate(templates):
            unknown_targets = set(template.additive_shocks) - set(targets)
            if unknown_targets:
                raise ValueError(
                    f"template {template.template_id!r} has unknown targets: "
                    + ", ".join(sorted(unknown_targets))
                )
            arrays = {
                target: result.point.to_numpy(dtype=float).copy()
                for target, result in forecasts.items()
            }
            for target, shock in template.additive_shocks.items():
                if len(shock) != len(valid_index):
                    raise ValueError(
                        f"template {template.template_id!r} shock length for "
                        f"{target!r} must match horizon"
                    )
                arrays[target] += shock
            trajectories = {
                target: pd.Series(
                    self._clip_if_physical(target, values),
                    index=valid_index,
                    name=target,
                )
                for target, values in arrays.items()
            }
            scenario_id = f"extreme:{template.template_id}"
            forced_ids.append(scenario_id)
            sampled_states[scenario_id] = tuple(
                template.state for _ in range(len(valid_index))
            )
            template_evidence[scenario_id] = {
                "evidence_version": template.evidence_version,
                "rule_version": template.rule_version,
            }
            scenarios.append(
                Scenario(
                    scenario_id=scenario_id,
                    probability=template.calibrated_probability,
                    issue_time=issue_time,
                    trajectories=trajectories,
                    seed=int(random_seed) + num_scenarios + template_index,
                    source_versions=source_versions,
                )
            )

        return ScenarioSet(
            horizon=len(valid_index),
            valid_time_index=valid_index,
            units=units,
            scenarios=tuple(scenarios),
            metadata={
                "dependence_model": "state_conditioned_t_copula",
                "t_copula_df": self.t_copula_df,
                "residual_as_of": self.residual_as_of.isoformat(),
                "state_definition_version": self.state_definition_version,
                "state_model_version": market_state_forecast.model_version,
                "state_feature_version": market_state_forecast.feature_version,
                "sampled_states": sampled_states,
                "fallback_states": fallback_states,
                "forced_scenario_ids": tuple(forced_ids),
                "extreme_template_evidence": template_evidence,
                "random_seed": int(random_seed),
            },
        )

    def build_from_bundle(
        self,
        bundle: "MarketForecastBundle",
        *,
        num_scenarios: int,
        random_seed: int,
        extreme_templates: tuple[ExtremeScenarioTemplate, ...] = (),
    ) -> ScenarioSet:
        """将 v5 多价格 ``MarketForecastBundle`` 映射为场景目标。"""
        if bundle.market_state_forecast is None:
            raise ValueError(
                "state-conditioned scenarios require market_state_forecast"
            )
        forecasts: dict[str, ForecastResult] = {
            "price": bundle.price_forecast,
            "load": bundle.load_forecast,
            "wind_power": bundle.wind_forecast,
            "pv_power": bundle.pv_forecast,
        }
        day_ahead = bundle.price_forecasts.get(
            PriceRole.DAY_AHEAD_SETTLEMENT.value
        ) or bundle.price_forecasts.get(PriceRole.DAY_AHEAD_REFERENCE.value)
        if day_ahead is not None:
            forecasts["day_ahead_price"] = day_ahead
        real_time = bundle.price_forecasts.get(
            PriceRole.REAL_TIME_SETTLEMENT.value
        )
        if real_time is not None:
            forecasts["real_time_price"] = real_time
        return self.build(
            forecasts=forecasts,
            market_state_forecast=bundle.market_state_forecast,
            num_scenarios=num_scenarios,
            random_seed=random_seed,
            extreme_templates=extreme_templates,
        )
