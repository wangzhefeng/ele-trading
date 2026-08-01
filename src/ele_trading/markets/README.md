# markets — 当前结算规则插件

本包当前按结算模式组织配置契约、加载校验和结算计算。它已经形成结算规则插件边界，但头寸、运行、编排和回测仍直接依赖默认单结算类型，因此还不是完整市场策略插件体系。

## `single_settlement/`

| 模块 | 当前职责 |
|---|---|
| `contracts.py` | 跨市场、运行、场景、BESS、DR、月度和求解参数的 `MarketConfig`，以及 `SettlementReport` |
| `config_loader.py` | YAML 映射、未知/缺失字段和取值校验 |
| `settlement.py` | 实时电能、合同差价、月度回收、DR、退化、执行调整和分项报告 |

单结算是当前活动主链使用的默认模式。`build_settlement_report()` 对各签名分项只计一次，并保留 baseline/delta cost。

## `dual_settlement/`

| 模块 | 当前职责 |
|---|---|
| `contracts.py` | 双结算配置与报告 |
| `config_loader.py` | 偏差带、带序和结算时段校验 |
| `settlement.py` | C/C2、日前偏差考核和中长期回收 |

双结算当前是带测试的独立规则库，没有接入 `positions`、`operations`、`TradingOrchestrator` 或 `backtest`。其存在不表示完整第二市场链已经实现。

## 共享能力

`shared.py` 提供两个模式共同使用的 `aggregate_to_settle_periods()`，负责结算时段聚合和能量守恒校验。

## 当前边界与待决策项

- 市场参数当前从 `configs/markets/<模式>.yaml` 加载；
- 两种模式的 `SettlementReport` 分项结构不同；
- 双结算配置只覆盖当前结算规则，不包含完整报价、风控和策略参数；
- 新市场是否按结算模式、参与者角色或更高层策略接口组织，由 v3 决定。

目标 `SettlementPolicy`、市场插件范围和配置拆分见 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#75-市场策略)。
