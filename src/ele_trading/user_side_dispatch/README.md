# 用户侧风光储调度模块(user_side_dispatch)

## 定位

本模块是 ele-trading 电力市场交易算法库中的**归档暂存区**,封存了工商业
用户侧风光储联合调度的完整算法链路。它与活动 v2 交易主线是**平级包、零代码
依赖**的关系:

| 维度 | `optimization/`(活动主线) | `user_side_dispatch/`(本模块) |
|------|---------------------------|-------------------------------|
| 业务场景 | 蒙西电力市场交易(发电侧/聚合商) | 工商业用户侧节能(用电侧) |
| 电价机制 | 现货市场出清价(预测 + 场景) | 固定分时目录电价(峰/平/谷/尖峰) |
| 盈利逻辑 | 市场套利 + CVaR 风险对冲 | 电度费 + 需量费 − 售电收入 − 弃电成本 |
| 核心契约 | `ScenarioSet` / `MarketForecastBundle` | `UserSide*DispatchInput` |
| 算法栈 | 套利 oracle / MPC / Two-stage-CVaR | 规则法 / MILP / CVXPY / 分布式多节点 |
| 状态 | 活动代码,被 `trading` 引用 | 归档,活动代码零引用 |

两者历史上曾混在同一个 `optimization/` 包中,v2 重构时按业务边界拆分:
市场侧交易优化留在 `optimization/`,用户侧调度整体迁入本归档包。

### 与 `investment_estimation` 的关系

本模块不含投资测算代码。原 `data_provider/todo/` 的投资侧文件
(`case_dataset.py`/`load_profile.py`/`schemas.py`/`loader.py`)已随合并
迁往 `investment_estimation/todo/`。`investment_estimation` 有自己的
`DistributedBESSDispatcher` 副本,不依赖本模块。

## 架构

```
user_side_dispatch/
├── interfaces.py          # 全部 UserSide* / Distributed* / Cvxp* 契约
│                          #   PV/Wind 类型已收敛为 Renewable* 别名
├── adapters/              # 场景适配层(薄包装,无求解逻辑)
│   ├── dispatch_adapters.py           # PV/Wind/±BESS 五类单节点场景
│   └── distributed_dispatch_adapters.py  # 三类分布式场景(依赖 CVXPY, lazy)
├── algorithms/            # 算法内核
│   ├── user_side_renewable_dispatch_class.py              # 可再生无储能(规则法)
│   ├── user_side_renewable_bess_dispatch_class.py         # 可再生+BESS(PuLP MILP)
│   ├── user_side_bess_dispatch_pulp.py                    # 纯储能(PuLP)
│   ├── user_side_bess_dispatch_cvxpy.py                   # 纯储能(CVXPY)
│   ├── user_side_bess_distributed_dispatch_class.py       # 分布式纯储能
│   └── user_side_renewable_bess_distributed_dispatch_class.py  # 分布式可再生+BESS
├── *_sample.py            # 合成样例构建器(YAML → *Input)
└── __init__.py            # 导出 + CVXPY lazy 加载
```

**adapter 设计**:adapter 是"多场景 → 单内核"的扇入翻译层。PV/Wind/WindPV
共用同一组可再生内核,adapter 负责把场景专属字段映射为内核统一的
`renewable_forecast`。纯储能内核(无可再生)只有单一场景,不需 adapter。

## 依赖与验证

- PuLP-backed modules import without CVXPY。
- CVXPY 和分布式入口经 `__getattr__` lazy 加载;使用前需
  `uv sync --extra archived-user-side`。
- 归档回归:`tests/user_side_dispatch/` 60 个测试,覆盖全部 adapter 和内核。

## 使用边界

- 消费方必须显式 `import ele_trading.user_side_dispatch`。
- 活动 `data_provider` / `optimization` 不得 re-export 本包。
- 生产入口和活动测试不得引用本包。

## 后续计划

本模块当前无活动入口、无 v2 owner、未被活动代码引用,处于稳定归档状态。
后续走向取决于业务需求:

1. **保持归档**(默认):如蒙西交易主线不涉及用户侧调度,维持现状。
2. **解档恢复**:若 v2 需要用户侧能力(如虚拟电厂聚合工商业储能参与市场),
   需同时满足三个条件——指定 v2 owner、建立活动 app/config 入口、补齐活动测试。
   恢复时应在 `trading/` 内新建桥接层(如 `trading/user_side_bridge.py`),
   把交易侧的 `MarketForecastBundle` 翻译为本模块的 `UserSide*DispatchInput`,
   保持本模块内核不变。
3. **整体删除**:若确认用户侧调度不在项目范围内,可清理本模块及其对应的
   `app/user_side_dispatch/`、`configs/user_side_dispatch/`
   和 `tests/user_side_dispatch/` 归档测试。
