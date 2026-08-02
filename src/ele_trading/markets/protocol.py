"""市场模式协议（v3 M4 / D-002）。

主链（positions/operations/demand_response/trading/backtest）只依赖
本模块的 Protocol 与 ``markets.sections`` 的配置子对象词汇，
不 import 具体市场模式插件；模式选择在 app 入口（组合根）完成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class SettlementEngine(Protocol):
    """结算规则引擎：结算各项的唯一计费归属（v3 不变量 4）。

    报告类型由市场模式自行定义（单结算与双结算的报告字段不同），
    因此 ``build_settlement_report`` 以关键字参数接收模式专属输入。
    """

    def compute_energy_cost(
        self,
        q_real: np.ndarray,
        p_real: np.ndarray,
    ) -> np.ndarray:
        """时段电能成本构件。"""
        ...

    def compute_contract_difference(
        self,
        q_long: np.ndarray,
        p_long: np.ndarray,
        *,
        p_ref: np.ndarray,
    ) -> np.ndarray:
        """中长期差价构件。"""
        ...

    def build_settlement_report(self, **kwargs) -> Any:
        """按模式规则构建分项结算报告。"""
        ...

    def compute_dr_settlement(
        self,
        *,
        committed_qty: float,
        executed_window_discharge_mwh: float,
        baseline_qty: float,
        config: Any,
    ) -> tuple[float, float, float]:
        """DR 履约结算：(dr_adjustment, compensation, penalty)。"""
        ...


@runtime_checkable
class MarketMode(Protocol):
    """市场模式插件的装配协议（配置 + 结算引擎 + 策略注入点）。"""

    name: str
    settlement: SettlementEngine

    def load_config(self, path: str | Path) -> Any:
        """加载并校验本模式的组合式配置对象。"""
        ...


@runtime_checkable
class PriceRoleCapability(Protocol):
    """市场模式可选的价格语义能力，不扩大基础 MarketMode 协议。"""

    price_roles: tuple[str, ...]
    day_ahead_price_role: str
    intraday_price_role: str
