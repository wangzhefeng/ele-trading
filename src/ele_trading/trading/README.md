# trading — 参与方交易编排层

本包是单结算交易链的编排落点：`TradingOrchestrator` 串联 持仓 → 预测 →
联合场景 → 日前运行 → 日内滚动 → 结算 的完整链路；`demo_fixtures` 提供
demo 样例数据与 fixture 生成器。日前价格只可作为显式解释信号，不进入
财务结算。

## 活动模块

| 模块 | 职责 |
|------|------|
| `orchestrator.py` | 注入数据、预测、场景、配置和求解器并运行完整链 |
| `demo_fixtures.py` | `SampleTradingDataProvider`（30 天 96 点样例）、`WalkForwardSeasonalNaiveProvider`（按 issue-time vintage 的无前瞻预测）、fixture 生成器 |

分层后的契约归属：领域契约在 `domain/`（`PositionState`/`OperationalPlan`/
`IntradayPlan`/`DecisionTrace` 等），市场规则在 `markets/single_settlement/`
（`MarketConfig`/`SettlementReport`/结算引擎/配置加载），头寸决策在
`positions/`，日前与日内运行在 `operations/`，回测与指标在 `backtest/`。

## 归档

v1 双结算归档（原 `todo/dual_settlement_v1/`）已删除：结算引擎（C/C2/
Cpen_dayah/Cpen_long）已激活为 `markets/dual_settlement/` 插件（唯一权威
实现）；v1 契约、报量报价日前、回测与 app 由 git 历史保留，需要溯源时
查 `git log`。活动代码不得 import 已删除的 `ele_trading.trading.todo`。

## 活动流向

```text
data provider → PositionState
forecast provider/registry → MarketForecastBundle
scenario builder → ScenarioSet
OperationalPlan → IntradayPlan → SettlementReport
```

活动入口（v2 §8.2）：`app/trading/run_{mid_long,monthly,day_ahead,intraday,dr,backtest}.py`
以及统一编排入口 `app/trading/run_pipeline.py`。`run_backtest.py` 把 30 天
walk-forward 回归基线写入 `results/trading/backtest/v2_baseline/`。样例数据只
用于接口和回归验证；生产数据必须经 `data_provider` 注入。
