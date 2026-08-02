"""双结算（偏差带考核）模式的 MarketMode 装配（v3 M4 / D-002）。

把插件的配置加载与结算函数装配为 ``markets.protocol`` 的
``MarketMode``/``SettlementEngine`` 对象。注意：双结算当前为
规则引擎库，``compute_dr_settlement`` 无 DR 产品语义，显式抛
NotImplementedError；完整主链接入待报价契约设计（v4）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ele_trading.markets.dual_settlement import settlement as _settlement
from ele_trading.markets.dual_settlement.config_loader import (
    load_market_config,
)
from ele_trading.markets.dual_settlement.contracts import SettlementReport
from ele_trading.markets.protocol import SettlementEngine


class _DualSettlementEngine:
    """双结算结算引擎：委托插件结算函数实现 SettlementEngine 协议。"""

    def compute_energy_cost(
        self,
        q_real: np.ndarray,
        p_real: np.ndarray,
    ) -> np.ndarray:
        """实时电能构件：Q_real × p_real（量价结算 C 的实时分量）。"""
        q_real_arr = np.asarray(q_real, dtype=float)
        p_real_arr = np.asarray(p_real, dtype=float)
        if q_real_arr.shape != p_real_arr.shape:
            raise ValueError("settlement inputs must use identical shapes")
        return q_real_arr * p_real_arr

    def compute_contract_difference(
        self,
        q_long: np.ndarray,
        p_long: np.ndarray,
        *,
        p_ref: np.ndarray,
    ) -> np.ndarray:
        """中长期差价构件：Q_long × (p_long − p_ref)（C2 分解项）。"""
        q_long_arr = np.asarray(q_long, dtype=float)
        p_long_arr = np.asarray(p_long, dtype=float)
        p_ref_arr = np.asarray(p_ref, dtype=float)
        if not (
            q_long_arr.shape == p_long_arr.shape == p_ref_arr.shape
        ):
            raise ValueError("settlement inputs must use identical shapes")
        return q_long_arr * (p_long_arr - p_ref_arr)

    def build_settlement_report(self, **kwargs) -> SettlementReport:
        """双结算日报告：量价结算 + 日前偏差考核 + 中长期回收。

        必填关键字：c_daily / cpen_dayah / cpen_long / cost_baseline。
        """
        required = {"c_daily", "cpen_dayah", "cpen_long", "cost_baseline"}
        missing = required - set(kwargs)
        if missing:
            raise ValueError(
                f"dual settlement report requires {sorted(missing)}"
            )
        c_daily = float(kwargs["c_daily"])
        cpen_dayah = float(kwargs["cpen_dayah"])
        cpen_long = float(kwargs["cpen_long"])
        cost_baseline = float(kwargs["cost_baseline"])
        cost_daily = c_daily + cpen_dayah + cpen_long
        return SettlementReport(
            c_daily=c_daily,
            cpen_dayah=cpen_dayah,
            cpen_long=cpen_long,
            cost_daily=cost_daily,
            cost_baseline=cost_baseline,
            delta_cost=cost_baseline - cost_daily,
        )

    def compute_dr_settlement(
        self,
        *,
        committed_qty: float,
        executed_window_discharge_mwh: float,
        baseline_qty: float,
        config,
    ) -> tuple[float, float, float]:
        raise NotImplementedError(
            "dual_settlement 模式当前无 DR 产品结算语义"
        )


class _DualSettlementMode:
    """双结算市场模式：配置加载 + 结算引擎装配。"""

    name: str = "dual_settlement"
    settlement: SettlementEngine = _DualSettlementEngine()

    def load_config(self, path: str | Path):
        return load_market_config(path)


DUAL_SETTLEMENT_MODE = _DualSettlementMode()
