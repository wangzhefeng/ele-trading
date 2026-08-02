"""单结算交易线共享的入口脚本引导：sys.path、样例加载、配置加载。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DATA_TRADING = PROJECT_ROOT / "data" / "trading"
MARKET_CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
RESULTS_TRADING = PROJECT_ROOT / "results" / "trading"

# 与 tests/test_day_ahead_coupled.py 一致的 BES 样例（96 点/15min 粒度）
SAMPLE_BESS = {
    "p_bcmax": 5.0,
    "p_bdmax": 5.0,
    "p_bceff": 0.95,
    "p_bdeff": 0.95,
    "socmin": 1.0,
    "socmax": 10.0,
    "socini": 5.0,
    "cap": 10.0,
}


def load_daily_samples() -> dict:
    """加载 data/trading/daily_sample_*.csv → {日期: DataFrame}。"""
    from ele_trading.trading.demo_fixtures import (
        SampleTradingDataProvider,
    )

    provider = SampleTradingDataProvider(DATA_TRADING)
    return {
        day: provider.frame_for_day(day)
        for day in provider.available_days
    }


def load_market_mode():
    """组合根：选择活动市场模式（v3 M4 / D-002，当前为单结算）。"""
    from ele_trading.markets.single_settlement.mode import (
        SINGLE_SETTLEMENT_MODE,
    )

    return SINGLE_SETTLEMENT_MODE
