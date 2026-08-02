"""主链共享的组合式市场配置子对象词汇（v3 M2/M4）。

六个 typed config 子对象（market/scenario/bess/dr/monthly/solver）+
组合 ``MarketConfig`` + ``schema_version``，是主链各层
（positions/operations/demand_response/trading/backtest）统一理解的
配置词汇；各市场模式插件（``markets/<模式>/``）的 loader 负责把
自己的 YAML 装配为这些子对象，并叠加模式专属的身份校验
（如 single_settlement 的 market_name/settlement_mode 检查）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

# 当前配置 schema 版本；语义变化必须升版本并记录迁移方式（v3 §10.2）
CURRENT_SCHEMA_VERSION = 1


def _require_finite_non_negative(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be finite and non-negative")


@dataclass(slots=True)
class MarketSection:
    """市场与结算规则参数（含中长期回收规则）。"""

    market_name: str = "single_settlement"
    settlement_mode: str = "single_settlement"
    settle_periods: int = 96
    dt: float = 0.25

    long_recovery_lower_ratio: float = 0.90
    long_recovery_upper_ratio: float = 1.05
    long_recovery_multiplier: float = 1.20
    long_recovery_applies_to_storage: bool = True
    pos_tol_ratio: float = 0.05

    def __post_init__(self) -> None:
        if self.dt != 0.25:
            raise ValueError("dt must be 0.25 for 15-minute trading")
        if self.settle_periods <= 0 or 96 % self.settle_periods != 0:
            raise ValueError("settle_periods must be a positive divisor of 96")
        if not (
            0.0
            < self.long_recovery_lower_ratio
            < self.long_recovery_upper_ratio
        ):
            raise ValueError("invalid long-recovery ratio band")
        _require_finite_non_negative(
            "long_recovery_multiplier",
            self.long_recovery_multiplier,
        )
        _require_finite_non_negative("pos_tol_ratio", self.pos_tol_ratio)


@dataclass(slots=True)
class ScenarioSection:
    """场景生成与风险参数。"""

    two_stage_scenario_deviation_cost_positive: float = 0.25
    two_stage_scenario_deviation_cost_negative: float = 0.25
    scenario_method: str = "lhs"
    scenario_count: int = 20
    scenario_seed: int = 7
    scenario_cvar_alpha: float = 0.95
    scenario_cvar_weight: float = 0.0

    def __post_init__(self) -> None:
        if self.scenario_method not in {"lhs", "mc"}:
            raise ValueError("scenario_method must be lhs or mc")
        if self.scenario_count <= 0:
            raise ValueError("scenario_count must be positive")
        if not 0.0 < self.scenario_cvar_alpha < 1.0:
            raise ValueError("scenario_cvar_alpha must be within (0, 1)")
        for name in (
            "two_stage_scenario_deviation_cost_positive",
            "two_stage_scenario_deviation_cost_negative",
            "scenario_cvar_weight",
        ):
            _require_finite_non_negative(name, getattr(self, name))


@dataclass(slots=True)
class BessSection:
    """储能市场运行参数（物理参数本体见 ``optimization.BESSConfig``）。"""

    soc_terminal_min: float | None = None
    exclusive_charge_discharge: bool = True
    operational_power_margin: float = 0.80
    throughput_max_ratio: float = 1.0
    deg_cost_per_mwh: float = 0.0
    bess_market_role: str = "behind_meter"
    no_discharge_on_curtail: bool = False

    def __post_init__(self) -> None:
        for name in (
            "operational_power_margin",
            "throughput_max_ratio",
            "deg_cost_per_mwh",
        ):
            _require_finite_non_negative(name, getattr(self, name))
        if not 0.0 < self.operational_power_margin <= 1.0:
            raise ValueError("operational_power_margin must be within (0, 1]")


@dataclass(slots=True)
class DrSection:
    """需求响应产品参数。"""

    dr_aggregation: str = "aggregator"
    dr_compensation_per_mwh: float = 2000.0
    dr_penalty_per_mwh: float = 3000.0
    dr_minimum_margin: float = 0.0
    dr_minimum_response_mwh: float = 0.1
    dr_window_start: int = 72
    dr_window_end: int = 80
    dr_enabled: bool = False
    dr_baseline_mode: str = "auto"  # "auto" | "fixed"
    dr_baseline_mwh: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "dr_compensation_per_mwh",
            "dr_penalty_per_mwh",
            "dr_minimum_margin",
            "dr_minimum_response_mwh",
        ):
            _require_finite_non_negative(name, getattr(self, name))
        if not 0 <= self.dr_window_start < self.dr_window_end <= 96:
            raise ValueError("DR window must be within the 96-period day")
        if self.dr_baseline_mode not in {"auto", "fixed"}:
            raise ValueError("dr_baseline_mode must be auto or fixed")
        if self.dr_baseline_mode == "fixed" and self.dr_baseline_mwh <= 0.0:
            raise ValueError(
                "dr_baseline_mwh must be positive when dr_baseline_mode is fixed"
            )


@dataclass(slots=True)
class MonthlySection:
    """月度交易规则参数。"""

    monthly_price_floor: float = 0.0
    monthly_price_cap: float = 1500.0
    monthly_trade_unit_mwh: float = 1.0

    def __post_init__(self) -> None:
        _require_finite_non_negative(
            "monthly_trade_unit_mwh",
            self.monthly_trade_unit_mwh,
        )
        if self.monthly_price_floor >= self.monthly_price_cap:
            raise ValueError("monthly price floor must be below the cap")


@dataclass(slots=True)
class SolverSection:
    """求解器选择、时限与容差。"""

    solver_name: str = "cbc"
    solver_time_limit_seconds: float = 30.0
    solver_mip_gap: float = 0.0

    def __post_init__(self) -> None:
        if self.solver_name not in {"cbc", "glpk"}:
            raise ValueError("solver_name must be cbc or glpk")
        for name in (
            "solver_time_limit_seconds",
            "solver_mip_gap",
        ):
            _require_finite_non_negative(name, getattr(self, name))


@dataclass(slots=True)
class MarketConfig:
    """组合式市场配置：六个子对象 + schema_version。"""

    schema_version: int = CURRENT_SCHEMA_VERSION
    market: MarketSection = field(default_factory=MarketSection)
    scenario: ScenarioSection = field(default_factory=ScenarioSection)
    bess: BessSection = field(default_factory=BessSection)
    dr: DrSection = field(default_factory=DrSection)
    monthly: MonthlySection = field(default_factory=MonthlySection)
    solver: SolverSection = field(default_factory=SolverSection)

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {CURRENT_SCHEMA_VERSION}"
            )
