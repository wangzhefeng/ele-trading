# user_side_dispatch — 独立用户侧风光储调度

## 当前状态

本包是活动的独立领域能力，与电力市场交易主链平级并保持零业务依赖；它只依赖 `utils`，由结构守卫验证。它拥有独立的入口、配置和测试，但不进入 `trading`、`markets`、`operations` 或 `backtest` 主链。

| 维度 | 市场交易 | 用户侧调度 |
|---|---|---|
| 业务场景 | 市场交易与聚合运行 | 工商业用户侧节能与风光储调度 |
| 价格机制 | 预测价格、场景和市场结算 | 目录电价或市场化落地价（`landed_price` 双模式合成） |
| 主要经济项 | 现货、合同、风险与履约 | 电度费、需量费、售电和弃电 |
| 核心契约 | `ScenarioSet`、`OperationalPlan` | `UserSide*DispatchInput` |
| 算法 | 套利、MPC、Two-stage、日前/日内 | 规则、PuLP、CVXPY、分布式 |
| 状态 | 活动主链 | 活动独立领域 |

## 当前结构

```text
user_side_dispatch/
├── interfaces.py
├── landed_price.py
├── adapters/
├── algorithms/
├── *_sample.py
└── __init__.py
```

PV、Wind 和组合可再生场景通过 adapter 映射到统一 renewable 内核。CVXPY 和分布式导出使用延迟加载；相关入口依赖 `archived-user-side` extra。

## 落地电价合成（landed_price）

`landed_price.py` 将 `buy_price` 从外生序列变为可审计合成结果，对应 1656 号文与 1077 号文两条政策时间线：

- `catalogue`：目录电价直接透传；
- `market`：中长期价 × 覆盖率 + 现货价 × (1 - 覆盖率) + 输配电价电量部分 + 政府性基金及附加；`price_type` 由交易电价秩次启发式打标。

输配电价与基金费率按生效日通过 `TariffSchedule` 版本化；示例见 `configs/user_side_dispatch/tariff_schedule_demo.yaml`。两部制容量部分不参与逐时段合成，由 `LandedPrice.demand_charge_rate` 提供给调度输入。

## 隔离边界与成熟度

- 市场交易主链不转出本包 API；本包也不依赖市场模式、市场结算或回测；
- `app/user_side_dispatch/`、`configs/user_side_dispatch/` 和 `tests/user_side_dispatch/` 是活动独立领域的正式入口、配置和回归范围；
- 本包不包含投资收益测算代码；
- 需量结算口径、合同需量等业务规则仍待确认；当前不是生产已验证能力。

领域隔离由 v3 D-001 规定；市场交易的当前开发路线见 [v6](../../../docs/策略算法框架详细设计-v6.md)，不将用户侧能力重新并入市场主链。
