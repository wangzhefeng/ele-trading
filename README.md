# 电力交易算法架构

`ele-trading` 是面向虚拟电厂和电力市场交易的研究型算法原型。当前活动代码位于 `src/ele_trading/`，通过 `app/` 入口与 `configs/` 配置形成数据、预测、场景、头寸、运行、结算和回测闭环。

平级包 `src/investment_estimation/` 是独立、自包含的投资收益测算模块，不属于本文和 v5 电力市场交易设计的范围；其能力与用法见该包自身 README。

## 文档导航

| 文档 | 用途 | 权威性 |
|---|---|---|
| [策略算法框架详细设计 v5](docs/策略算法框架详细设计-v5.md) | 当前代码事实、未完成闭环、目标架构和下一开发路线 | 唯一在研设计 |
| [策略算法框架详细设计 v3](docs/策略算法框架详细设计-v3.md) | 已批准的领域边界、配置和优化内核决策 | 架构决策记录 |
| [策略算法框架详细设计 v4](docs/策略算法框架详细设计-v4.md) | P0 算法增强与 P1/P2 的历史溯源 | 历史设计 |
| [应用入口说明](app/README.md) | 活动与归档入口 | 使用说明 |
| [配置目录说明](configs/README.md) | 当前配置与加载边界 | 使用说明 |
| [测试目录说明](tests/README.md) | 测试范围、命令和验证口径 | 验证说明 |
| v0/v1/v2 与需量预测 v0 | 历史方案 | 仅供追溯，非当前规范 |

协作准则见 [AGENTS.md](AGENTS.md)；`CLAUDE.md` 仅提供指针，不另立规则副本。

## 项目目标

1. 形成可验证的电力市场交易算法工程闭环。
2. 统一数据时间、预测来源、场景概率、储能物理状态、结算分项和决策追踪。
3. 为储能套利、MPC、风险优化、日前/日内运行和 walk-forward 回测提供可复用基线。
4. 在 v5 中先完成报价—成交—履约、运行主链接线、真实数据校准和经济验收，再决定是否设计 v6。

## 当前活动链路

```text
市场 / 资产 / 气象数据
  → 数据质量与可追溯快照
  → 价格 / 负荷 / 风电 / 光伏预测
  → 联合场景生成与缩减
  → 中长期与月度头寸
  → 日前运行计划
  → 日内滚动调整
  → 单结算
  → walk-forward 回测与指标
```

当前默认链路使用单结算。市场模式经 `MarketMode` 协议在入口选择注入；双结算具备规则插件但尚无完整主链用例。价格角色、日内新 forecast vintage、可选执行偏差收紧与结算后自动对账已接线；多资源可选产出独立资源级计划，但资源级日内、计量与结算未接线；统一验收要求证据对象；市场仿真仍是待接线算法核。完整事实与下一批次见 [v5 §3、§14](docs/策略算法框架详细设计-v5.md#3-当前实现基线与缺口)。

## 仓库结构

```text
ele-trading/
├── src/ele_trading/
│   ├── domain/                # 领域与决策追踪契约
│   ├── data_provider/         # 市场、资产、气象数据与质量处理
│   ├── forecasting/           # 统一预测接口与基线模型
│   ├── scenario/              # 联合场景生成与缩减
│   ├── market_simulation/     # SCED/SCUC、ABM、MARL 的独立市场仿真核
│   ├── optimization/          # 共享 BESS 物理核、目标组件、统一求解出口、套利/MPC/Two-stage + CVaR
│   ├── positions/             # 中长期和月度头寸
│   ├── operations/            # 日前运行与日内滚动
│   ├── markets/               # MarketMode 协议、共享配置子对象、单/双结算插件
│   ├── demand_response/       # 独立 DR 经济性评估
│   ├── trading/               # 活动交易编排、事件链与 demo fixtures
│   ├── backtest/              # walk-forward 回测与指标
│   ├── user_side_dispatch/    # 用户侧/分布式/CVXPY 调度（独立领域能力）
│   └── utils/                 # 时间、数据对齐、IO、数值和日志工具
├── src/investment_estimation/ # 平级、自包含的投资收益测算包
├── app/                       # 项目级入口
├── configs/                   # 活动与归档入口配置
├── data/                      # 样例、fixture 和兼容数据
├── docs/                      # v5 当前路线、v3 架构决策和历史资料
├── tests/                     # 单元、集成、结构和入口测试
├── AGENTS.md                  # 通用协作准则
└── CLAUDE.md                  # 文档指针
```

## 当前能力摘要

### 数据与预测

- `MarketDataSnapshot` 记录市场、scope、`as_of`、版本、质量标记和观测来源。
- `ForecastRequest` / `ForecastResult` 统一价格、负荷、天气和风光预测边界。
- 现有预测实现以工程基线、兼容模型和外部 adapter 边界为主，不代表生产预测精度。

### 场景与优化

- 联合场景支持 LHS、Monte Carlo、相关性注入和 Wasserstein L1 后向缩减。
- 活动优化包含 BESS 套利、MPC 和 Two-stage + CVaR，全部构建在统一共享 BESS 物理核之上，共用目标组件层、结果提取与 typed 求解出口。
- 日前运行支持可选 CVaR 和 DR 联合优化；日内支持冻结已执行前缀、滚动重优化与受控回退。

### 市场与回测

- 单结算链覆盖中长期差价、实时电能、回收、DR、退化和执行调整等分项。
- 中长期、月度和独立 DR 决策当前主要是透明启发式基线。
- 回测提供无储能、确定性、风险和 oracle 对照；仅 oracle 可以使用未来实际值。

## 活动入口

从项目根目录运行：

```bash
uv sync --extra dev
uv run python app/optimization/run_bess_arbitrage.py
uv run python app/optimization/run_mpc_demo.py
uv run python app/optimization/run_two_stage_skeleton.py
uv run python app/trading/run_pipeline.py
uv run python app/trading/run_backtest.py
```

完整入口、配置和归档边界分别见 [app/README.md](app/README.md) 与 [configs/README.md](configs/README.md)。

## 验证

完整仓库测试：

```bash
uv run python -m pytest -q
```

完整仓库验证快照与生产边界见 [v5 §3、§15](docs/策略算法框架详细设计-v5.md#15-验证与性能预算)。2026-08-08 的全仓命令 `env -u PYTHONPATH UV_CACHE_DIR=.uv_cache uv run pytest -q -W error::RuntimeWarning` 结果为 `711 passed, 4 skipped, 5 deselected`；这不是永久测试数量，也不证明生产效果。

## 当前实现状态

- 单结算样例主链已形成可运行闭环，决策全程经事件链可追踪。
- 预测时间契约、场景概率、BESS 运行计划、结算分项和事件链完整性已有活动测试保护。
- 市场配置、结算和价格角色已插件化；头寸、运行和报价策略尚未成为完整的 MarketMode 可替换策略，Bid→Award 事件闭环是 v5 下一批次。
- 配置为六子对象组合式 typed config + `schema_version`；BESS 物理核、目标组件、求解出口与结果提取已四层拆分。
- 用户侧调度已恢复为独立领域能力；需量控制的经济规则待业务侧确认（见需量预测 v0 方案待确认问题）。

## 数据边界

`data/` 中的人工样例、fixture、模拟数据和兼容数据只用于接口验证、demo 和回归测试，不代表真实市场数据，不能直接用于生产策略评估。

## 文档维护

- 当前代码事实、实施状态、验证快照或下一批次变化时更新 [v5](docs/策略算法框架详细设计-v5.md)。
- 已批准的领域边界、配置或优化内核决策变化时更新 [v3 设计](docs/策略算法框架详细设计-v3.md)。
- 入口、配置或测试变化时同步更新对应 README。
- 历史文档不得重新作为当前需求、实现约束或验收依据。
