"""从历史数据中按价格角色解析唯一训练序列。"""

from __future__ import annotations

import pandas as pd

from ele_trading.forecasting.contracts import ForecastRequest
from ele_trading.domain.price_roles import PriceRole, normalize_price_role


def resolve_price_history(
    history: pd.DataFrame,
    request: ForecastRequest,
) -> tuple[PriceRole, pd.Series]:
    """解析角色列；价差由同时点实时价减日前价构造。"""
    raw_role = request.data.get(
        "price_role",
        request.data.get(
            "market_scope",
            PriceRole.REAL_TIME_SETTLEMENT.value,
        ),
    )
    role = normalize_price_role(str(raw_role))
    if role in {
        PriceRole.DAY_AHEAD_REFERENCE,
        PriceRole.DAY_AHEAD_SETTLEMENT,
    }:
        required = ("p_dayah",)
        values = history["p_dayah"] if "p_dayah" in history else None
    elif role is PriceRole.REAL_TIME_SETTLEMENT:
        required = ("p_real",)
        values = history["p_real"] if "p_real" in history else None
    elif role is PriceRole.SPREAD_DA_RT:
        required = ("p_dayah", "p_real")
        values = (
            history["p_real"] - history["p_dayah"]
            if set(required).issubset(history.columns)
            else None
        )
    else:
        required = ("p_long",)
        values = history["p_long"] if "p_long" in history else None
    if values is None:
        raise ValueError(
            f"price role {role.value!r} requires history columns {required}"
        )
    series = values.astype(float).copy()
    series.name = role.value
    return role, series
