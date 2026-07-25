"""蒙西 forecast-aware 回测入口（v1.3 §10）：30 天日历回测 + 报告落盘。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "trading"))

from _bootstrap import MENGXI_YAML, RESULTS_TRADING, SAMPLE_BESS, load_daily_samples

from ele_trading.evaluation.backtest import run_mengxi_backtest_calendar
from ele_trading.trading.config_loader import load_market_config
from ele_trading.utils.log_util import logger


def main() -> None:
    parser = argparse.ArgumentParser(description="蒙西 30 天 forecast-aware 回测")
    parser.add_argument("--mode", default=None, choices=["A", "B", "C"], help="覆盖配置中的 dayahead.mode")
    parser.add_argument("--rolling-step", type=int, default=12, help="日内重优化步长（刻）")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    config = load_market_config(MENGXI_YAML)
    mode = args.mode or config.dayahead_mode
    calendar = load_daily_samples()
    logger.info(f"=== 蒙西回测: {len(calendar)} 天, mode={mode}, rolling_step={args.rolling_step} ===")

    t0 = time.time()
    report = run_mengxi_backtest_calendar(
        calendar, SAMPLE_BESS, config, mode=mode, seed=args.seed, rolling_step=args.rolling_step
    )
    elapsed = time.time() - t0

    total_delta = report.delta_cost.sum()
    logger.info(f"总 ΔCost = {total_delta:,.1f} 元 (策略相对基准节约, 期望 > 0)")
    logger.info(f"Cpen_dayah 合计 {report.cpen_dayah.sum():,.1f} 元, Cpen_long 合计 {report.cpen_long.sum():,.1f} 元")
    logger.info(f"ΔCost>0 天数: {(report.delta_cost > 0).sum()}/{len(report)}")
    logger.info(f"耗时 {elapsed:.1f}s（{elapsed / len(calendar):.2f}s/天，预算 ≤20s/天）")

    out_dir = RESULTS_TRADING / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"backtest_report_mode{mode}.csv"
    report.to_csv(out)
    logger.info(f"回测报告已落盘: {out}")


if __name__ == "__main__":
    main()
