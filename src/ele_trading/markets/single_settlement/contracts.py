"""单结算模式的市场配置与结算报告契约。

迁移自原 ``trading/contracts.py``（纯移动，定义逐行不变）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ele_trading.domain.contracts import DecisionTrace


@dataclass(slots=True)
class MarketConfig:
    """单结算模式市场、运行与求解规则配置（字段与 YAML 一一对应）。"""

    market_name: str = "single_settlement"
    settlement_mode: str = "single_settlement"
    settle_periods: int = 96
    dt: float = 0.25

    long_recovery_lower_ratio: float = 0.90
    long_recovery_upper_ratio: float = 1.05
    long_recovery_multiplier: float = 1.20
    long_recovery_applies_to_storage: bool = True
    pos_tol_ratio: float = 0.05

    two_stage_scenario_deviation_cost_positive: float = 0.25
    two_stage_scenario_deviation_cost_negative: float = 0.25
    scenario_method: str = "lhs"
    scenario_count: int = 20
    scenario_seed: int = 7
    scenario_cvar_alpha: float = 0.95
    scenario_cvar_weight: float = 0.0

    soc_terminal_min: float | None = None
    exclusive_charge_discharge: bool = True
    operational_power_margin: float = 0.80
    throughput_max_ratio: float = 1.0
    deg_cost_per_mwh: float = 0.0
    bess_market_role: str = "behind_meter"
    no_discharge_on_curtail: bool = False

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

    monthly_price_floor: float = 0.0
    monthly_price_cap: float = 1500.0
    monthly_trade_unit_mwh: float = 1.0

    solver_name: str = "cbc"
    solver_time_limit_seconds: float = 30.0
    solver_mip_gap: float = 0.0


@dataclass(slots=True)
class SettlementReport:
    """Itemized active single-settlement result."""

    energy_cost: float
    contract_difference: float
    long_recovery: float
    dr_adjustment: float
    degradation_cost: float
    execution_adjustment: float
    total_cost: float
    baseline_cost: float
    delta_cost: float
    trace: DecisionTrace | None = None
