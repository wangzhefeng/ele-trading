# domain — 市场无关领域契约层

全项目最底层契约包：只允许依赖标准库/pandas，不得 import 任何上层包
（`tests/test_structure_layers.py` 结构守卫强制）。

## 模块

| 模块 | 职责 |
|------|------|
| `contracts.py` | 交易链路共享契约：`PositionState`、`MarketForecastBundle`、`OperationalPlan`、`IntradayPlan`、`IntradayAdjustment`、`DRCommitment`、`DecisionTrace` |
| `events.py` | `Forecast→Bid→Award→Dispatch→Metering→Settlement` 事件契约骨架（`TradingEvent` 基座：issue_time/valid_time/version/source） |

事件骨架对应路线文档 Phase 0 交付物；完整市场日历、交割时段与单位系统
在 Phase 0 正式启动时补全。
