# backtest — walk-forward 回测与指标

## 当前模块

| 模块 | 当前职责 | 成熟度 |
|---|---|---|
| `backtest.py` | 驱动 `TradingOrchestrator`，比较无储能、确定性、风险和 oracle 四组结果 | 可运行基线 |
| `metrics.py` | BESS、Sharpe、最大回撤、EFC、RTE、利用率、雨流退化指标，价格捕获率/偏差占比/分位校准误差（v4 P0） | 活动指标 |
| `data_protocol.py` | 真实数据切分契约（train/validation/test + 无前瞻 vintage 校验，v4 P0） | 可选增强 |

回测要求未来实际值只进入事后结算和显式标记的 oracle。确定性与风险策略必须使用决策时刻可获得的 forecast vintage。

`results/trading/backtest/v2_baseline/` 是既有历史产物目录名，其中的 `v2` 不代表当前设计版本或永久文件契约。

当前回测主要证明样例链路、无前瞻边界和回归一致性，不证明生产策略收益。真实数据切分、基准组、业务反例和性能预算由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#78-回测与评估)定义。
