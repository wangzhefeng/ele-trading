# user_side_dispatch — 归档用户侧风光储调度

## 当前状态

本包封存工商业用户侧风光储调度能力，与活动市场交易主链平级、零活动代码依赖。它不属于当前活动 API、常规入口或常规 pytest 收集范围。

| 维度 | 活动市场交易 | 本归档包 |
|---|---|---|
| 业务场景 | 市场交易与聚合运行 | 工商业用户侧节能 |
| 价格机制 | 预测价格、场景和市场结算 | 固定分时目录电价 |
| 主要经济项 | 现货、合同、风险与履约 | 电度费、需量费、售电和弃电 |
| 核心契约 | `ScenarioSet`、`OperationalPlan` | `UserSide*DispatchInput` |
| 算法 | 套利、MPC、Two-stage、日前/日内 | 规则、PuLP、CVXPY、分布式 |
| 状态 | 活动 | 归档 |

## 当前结构

```text
user_side_dispatch/
├── interfaces.py
├── adapters/
│   ├── dispatch_adapters.py
│   ├── distributed_dispatch_adapters.py
│   └── distributed_dispatch_adapters_shared.py
├── algorithms/
│   ├── user_side_renewable_dispatch_class.py
│   ├── user_side_renewable_bess_dispatch_class.py
│   ├── user_side_bess_dispatch_pulp.py
│   ├── user_side_bess_dispatch_cvxpy.py
│   ├── user_side_bess_distributed_dispatch_class.py
│   └── user_side_renewable_bess_distributed_dispatch_class.py
├── *_sample.py
└── __init__.py
```

PV、Wind 和组合可再生场景通过 adapter 映射到统一 renewable 内核。CVXPY 和分布式导出使用延迟加载；运行相关入口需要安装 `archived-user-side` extra。

## 活动隔离

- 活动 `data_provider` 和 `optimization` 不转出本包 API；
- `app/user_side_dispatch/` 与 `configs/user_side_dispatch/` 只服务归档入口；
- `tests/user_side_dispatch/` 只能显式运行；
- 本包不包含投资收益测算代码。

## 恢复或删除条件

本轮不决定恢复或删除。若未来恢复为活动能力，必须同时具备：

1. 明确的业务 owner 和范围；
2. 与活动领域契约衔接的正式接口；
3. 活动 app/config 入口；
4. 纳入活动测试与回测的验收标准。

若未来删除，必须同时处理源码、入口、配置、测试和文档。最终方向记录在 [v3 决策表](../../../docs/策略算法框架详细设计-v3.md#6-待重新决策的现有实现)。
