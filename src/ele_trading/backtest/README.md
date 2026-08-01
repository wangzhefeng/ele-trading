# backtest — walk-forward 回测与指标

## 模块

| 模块 | 职责 |
|------|------|
| `backtest.py` | `run_walk_forward_backtest`：无前瞻 walk-forward 回测，无储能/确定性/风险感知/oracle 四基准（仅 oracle 可见未来） |
| `metrics.py` | 交易/BESS 指标（`summarize_bess_metrics`、`compute_extended_metrics`）与雨流退化核算（`compute_rainflow_degradation`） |

回测驱动 `trading.TradingOrchestrator` 做全链回放；30 天回归基线产物见
`results/trading/backtest/v2_baseline/`。
