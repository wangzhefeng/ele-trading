"""单结算模式的 MarketMode 装配（v3 M4 / D-002）。

把插件的配置加载与结算函数装配为 ``markets.protocol`` 的
``MarketMode``/``SettlementEngine`` 对象；app 入口（组合根）经
``SINGLE_SETTLEMENT_MODE`` 选择并注入主链。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ele_trading.markets.protocol import SettlementEngine
from ele_trading.markets.price_roles import PriceRole
from ele_trading.markets.single_settlement import settlement as _settlement
from ele_trading.markets.single_settlement.config_loader import (
    load_market_config,
)


class _SingleSettlementEngine:
    """单结算结算引擎：委托插件结算函数实现 SettlementEngine 协议。"""

    def compute_energy_cost(
        self,
        q_real: np.ndarray,
        p_real: np.ndarray,
    ) -> np.ndarray:
        return _settlement.compute_energy_cost(q_real, p_real)

    def compute_contract_difference(
        self,
        q_long: np.ndarray,
        p_long: np.ndarray,
        *,
        p_ref: np.ndarray,
    ) -> np.ndarray:
        return _settlement.compute_contract_difference(
            q_long,
            p_long,
            p_ref=p_ref,
        )

    def build_settlement_report(self, **kwargs):
        return _settlement.build_settlement_report(**kwargs)

    def compute_dr_settlement(
        self,
        *,
        committed_qty: float,
        executed_window_discharge_mwh: float,
        baseline_qty: float,
        config,
    ) -> tuple[float, float, float]:
        return _settlement.compute_dr_settlement(
            committed_qty=committed_qty,
            executed_window_discharge_mwh=executed_window_discharge_mwh,
            baseline_qty=baseline_qty,
            config=config,
        )


class _SingleSettlementMode:
    """单结算市场模式：配置加载 + 结算引擎装配。"""

    name: str = "single_settlement"
    settlement: SettlementEngine = _SingleSettlementEngine()
    price_roles: tuple[str, ...] = (
        PriceRole.DAY_AHEAD_REFERENCE.value,
        PriceRole.REAL_TIME_SETTLEMENT.value,
    )
    day_ahead_price_role: str = PriceRole.REAL_TIME_SETTLEMENT.value
    intraday_price_role: str = PriceRole.REAL_TIME_SETTLEMENT.value

    def load_config(self, path: str | Path):
        return load_market_config(path)


SINGLE_SETTLEMENT_MODE = _SingleSettlementMode()
