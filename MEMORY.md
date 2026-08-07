# MEMORY.md

> **项目记忆系统（供 AI Agent 读取）**。本文件由原 append-only `LOG.md` 重构而来，
> 记录项目的当前状态、架构归属、硬约束、已知缺口与关键决策。完整历史变更记录见
> `git log`（文件 rename 历史连续保留）。
>
> 更新原则：**保持当前状态准确**，不再 append-only；事实变化即就地更新，删除已过期条目。

## 1. 项目概览

`ele-trading` 是面向虚拟电厂、电力现货交易、风光储投资测算的**研究型原型**项目。技术栈：Python 3.11、`uv` 管理依赖、src layout（`src/ele_trading/` + `src/investment_estimation/` 两个平级包）。仓库内两个并存包：

- `src/ele_trading/` —— 电力市场交易与调度（核心包）。
- `src/investment_estimation/` —— 投资收益测算，**平级、完全自包含**（`grep ele_trading` 在包内 0 命中），不反向依赖交易主包。

## 2. 包结构与算法归属

### `src/ele_trading/`（交易/调度核心包）

模式接口化分层（2026-08-01 结构重构落地，依赖方向由 `tests/test_structure_layers.py` 强制：`domain` ← `markets` ← `positions`/`operations` ← `trading` ← `backtest`）：

| 子包 | 职责 |
|------|------|
| `domain/` | 市场无关领域契约 + `Forecast→…→Settlement` 事件契约骨架（最底层） |
| `markets/single_settlement/` | 单结算模式市场规则插件（`MarketConfig`/配置加载校验/结算引擎；规则研究参考蒙西市场规则） |
| `positions/` | 中长期/月度头寸决策（覆盖结构、阶梯申报、缺口再平衡） |
| `operations/` | 日前运行、日内滚动、DR 联合优化、多资源 LP 与执行偏差估计 |
| `trading/` | 参与方交易编排（`TradingOrchestrator`）+ demo fixtures（`demo_fixtures.py`） |
| `markets/dual_settlement/` | 双结算（偏差带考核）规则插件：C/C2/Cpen_dayah/Cpen_long 唯一权威实现（2026-08-01 自 v1 归档激活，未接主链） |
| `backtest/` | walk-forward 回测、交易/BESS 指标、反例与统一经济验收框架 |
| `demand_response/` | DR 参与决策（品种层机会成本评估，独立事后工具） |
| `data_provider/` | 配置、样例数据、负荷、气象、时序质量处理 |
| `forecasting/` | 价格角色、价格/负荷/风光功率预测、`ForecastProvider`、市场状态与天气特征工程 |
| `scenario/` | 联合场景、状态条件 Student-t Copula、极端模板、缩减与诊断 |
| `market_simulation/` | 独立市场仿真核：DC 网架、SCED/SCUC、报价/ABM、MARL；不依赖交易主链 |
| `optimization/` | 通用优化内核：活动储能套利、MPC、Two-stage+CVaR、共享 BESS 物理核、退化与风险度量 |
| `user_side_dispatch/` | 用户侧/分布式/CVXPY 调度（v3 D-001/M6 恢复为独立领域能力，只依赖 utils，与市场主链互不依赖） |
| `utils/` | IO、日志、时间、数据对齐工具 |

### `src/investment_estimation/`（投资测算自包含包）

- 新版闭环：`data_provider/` → `dispatch/` → `settlement/` → `finance/`（IRR/NPV/回收期/PPA 反推）→ `capacity_search/`，含 `resource_simulation/`（风光物理出力仿真）、`config_loader/`、`app/`、`configs/`。
- `todo/` —— **迁移暂存区**：老版 `ele_trading/capacity_planning` 整体并入，待与新版模块合并去重（非最终形态）。含多类 planner、资源仿真副本、容量扫描、`models/` 共享调度引擎。
- `utils/` —— 自包含工具子集（迁移自 `ele_trading.utils`，主包原文件保留）。

### `app/` 与 `configs/`

- `app/`：入口脚本按 `trading/`（7）、`optimization/`（3）、`user_side_dispatch/`（4，独立领域）分目录。容量规划入口（6 个）和 PV/Wind 资源仿真入口位于 `src/investment_estimation/app/`。
- `configs/`：YAML 按算法链路组织；单结算配置为 `configs/markets/single_settlement.yaml`（v3 六子对象组合式 typed config + `schema_version`）；双结算为 `configs/markets/dual_settlement.yaml`。容量规划配置位于 `src/investment_estimation/configs/capacity_planning/`。

### 归属硬约束

交易/调度通用内核 → `optimization/`；领域契约 → `domain/`；市场规则插件 → `markets/<模式>/`；头寸 → `positions/`；运行 → `operations/`；回测 → `backtest/`；编排 → `trading/`；投资收益测算（IRR/容量/结算/资源仿真）→ `investment_estimation/`。`src/ele_trading/capacity_planning/` **已删除**，不在 `ele_trading` 下重建容量规划/收益测算模块。活动代码不得出现地区名命名（如 mengxi）。

## 3. 当前主线状态（v5 实施中）

[策略算法框架详细设计 v5](docs/策略算法框架详细设计-v5.md) 是当前唯一在研设计、事实快照和下一开发路线；v3 的 D-001～D-007 是仍生效的已批准架构决策，v0～v2 与 v4 仅作历史溯源。v3 迁移 M0–M6 已落地，活动链路按分层包 + 市场模式协议组织：

- **领域契约** `domain/`：共享交易契约（`PositionState`/`MarketForecastBundle`/`OperationalPlan`/`IntradayPlan`/`DRCommitment`/`DecisionTrace`）+ 事件链（`TradingEvent` 全链 + `MarketCalendar` + `derive_input_versions`，编排与回测均输出事件链）。
- **市场模式协议** `markets/protocol.py`：`MarketMode` 提供配置、结算与价格角色 capability，`SettlementEngine` 注入结算语义；`markets/sections.py` 为六子对象组合式 typed config（market/scenario/bess/dr/monthly/solver）+ `schema_version`。主链不 import 具体模式（结构守卫强制），但头寸、运行和报价策略尚未成为完整可替换 capability，Bid→Award 闭环属于 v5 V5-8。
- **优化内核** `optimization/`：四层拆分（D-004/D-005）——共享 BESS 物理核（`bess_model`）、目标组件层（`objectives.py`）、统一结果提取（`extraction.py`）、typed 求解出口（`solver.py` `solve_pulp_model`）。MPC 已迁移共享核（D-004），`dt` 统一 0.25。
- **头寸/运行/编排/回测**：`positions/`、`operations/`（DR 联合优化在 `day_ahead_coupled.py`，履约结算经 `SettlementEngine` 注入）、`trading/orchestrator.py`（固定四类预测 + 场景 + 日前 + 日内 + 结算，构建并输出事件链）、`backtest/`（四基准回测 + 指标 + 事件链完整性断言）。
- **场景** `scenario/`：canonical 唯一化（D-007）——`Scenario`/`ScenarioSet`/`build_joint_scenarios`/`reduce_scenarios`；legacy `PriceScenario`/`generate_price_scenarios`/`normalize_weights` 已删除。
- **用户侧** `user_side_dispatch/`：v3 D-001/M6 恢复为独立领域能力，只依赖 `utils`，结构守卫 `test_user_side_dispatch_stays_independent` 强制与市场主链互不依赖。
- **入口**：`app/trading/run_{mid_long,monthly,day_ahead,intraday,dr,backtest}.py` + `run_pipeline.py`；`app/optimization/`（3）；`app/user_side_dispatch/`（4）。
- **配置**：`configs/markets/single_settlement.yaml`（schema v1 六区段）；旧扁平格式经 `scripts/migrate_market_config_v3.py` 一次性迁移，loader 不再接受。
- **回测基线产物**：`results/trading/backtest/v2_baseline/`（与 v2 基线逐项一致）。

### v5 全量扩展（V5-0~V5-7，D-014~D-016）

v5 在 v3/v4 之上建立预测—场景—仿真—决策—结算闭环。V5-0～V5-7 均已有算法核和专项测试，但未满足真实数据、主链接线、校准、影子运行与生产默认晋级条件；下一步以 v5 V5-8～V5-11 为准：

- **价格角色** `domain/price_roles.py` + `markets/price_roles.py`（兼容导出）：日前/实时/中长期/回收价格角色词汇固化。
- **概率预测** `forecasting/market_state.py`（市场状态 logistic provider）+ `price_history.py`（按角色解析训练序列）。
- **状态条件场景** `scenario/state_conditioned.py`：t-Copula 联合场景 + 有证据的极端模板。
- **多资源优化** `operations/multi_resource.py`（BESS 群+可调负荷+新能源限电+电网购电）+ `execution_bias.py`（执行偏差学习）。
- **退化与风险** `optimization/degradation.py` Level 2 温度退化 + `risk.py` 风险度量菜单。
- **结算对账** `markets/single_settlement/reconciliation.py`。
- **回测经济验收** `backtest/counterexamples.py`（HARD 反例框架）+ `acceptance.py`（统一经济验收与影子评估）。
- **市场数字孪生** `market_simulation/`（v5 D-014 新增顶层包）：`grid/`（DC 网络+SCED+SCUC+后定价+N-1）、`behavior`（报价推断+ABM）、`learning`（MARL 环境+policy guard）。只依赖 domain + optimization.solver，结构守卫强制不依赖 trading/backtest/具体市场插件。

### v4 P0（Phase A 算法增强，可选启用，默认行为不变）

v4 设计（`docs/策略算法框架详细设计-v4.md`）在 v3 架构边界内增强各层算法能力，D-008~D-013 默认值保持不变。P0 已落地六项（全部可选，不改变默认链路）：

- **预测** `forecasting/lightgbm_provider.py`：LightGBM 点预测 + 分位回归（price/load，日历+滞后+滚动统计特征，无前瞻约束）；`forecasting/metrics.quantile_calibration_error`。
- **场景** `scenario/diagnostics.py`：五项诊断（权重守恒/边际一致/相关保持/极端覆盖/复现性）。
- **优化** `optimization/degradation.py`：Level 1 日历+循环退化分离（LP 可线性化），`bess_arbitrage` 增 `degradation="level1"` 可选。
- **头寸** `positions/mid_long_optimizer.py`：CVaR 约束优化头寸（覆盖/预算/换手惩罚/年度总量），默认仍走启发式。
- **回测** `backtest/data_protocol.py`：真实数据切分契约（train/validation/test + 无前瞻 vintage 校验）；`backtest/metrics` 增价格捕获率/偏差占比/分位校准误差。

## 4. 测试快照（2026-08-08）

```bash
env -u PYTHONPATH UV_CACHE_DIR=.uv_cache uv run pytest -q -W error::RuntimeWarning
# → 711 passed, 4 skipped, 5 deselected, 11 warnings
```

该结果仅证明代码、接口和标准算例回归；生产晋级仍受 v5 §15 的真实数据、经济、影子与回滚门约束。

v5 新增测试：`tests/market_simulation/`（SCED/SCUC + ABM + MARL）、`tests/operations/`（多资源+执行偏差）、`tests/backtest/test_counterexamples.py` + `test_v5_acceptance.py`、`tests/forecasting/test_v5_{market_state,price_roles}.py`、`tests/scenario/test_v5_state_conditioned.py`、`tests/optimization/test_v5_{level2_degradation,risk_measures}.py`、`tests/markets/test_v5_reconciliation.py`、`tests/data_provider/test_v5_foundation_contracts.py`、`tests/trading/test_v5_forecast_vintages.py`。

## 5. 硬约束

硬边界（求解器 / 场景 / 指标 / 结算 / 市场参数 / 配置一致性 / 数据边界）的**唯一权威是 `AGENTS.md`**。本文件不复制约束，避免双份漂移；需要时直接读 `AGENTS.md`。

## 6. 已知缺口与待办

- **todo/ 与新版模块合并去重**：`todo/` 为迁移暂存区，非最终形态。
- **`app/trading/` 入口已就位**（早期 LOG 记为待办，现已由 `run_*.py` ×7 + `run_pipeline.py` 补齐，**该条待办已关闭**）。
- **resource_simulation 重复已解决**（2026-08-01）：删除根 `app/resource_simulation/`、`configs/resource_simulation/` 和 `todo/resource_simulation/` 副本，只保留 `investment_estimation.resource_simulation`（正本）和 `investment_estimation/app/` 入口。capacity_planning 入口的 `todo.resource_simulation` 引用已统一改指新版。

## 7. 关键决策（勿重新争论）

- **单结算为 canonical + 市场模式协议化（v3 D-002）**：单结算是主链默认实现；主链只依赖 `MarketMode`/`SettlementEngine` 协议，模式选择在入口（组合根）完成，五包不得 import 具体模式（结构守卫强制）。双结算为完整插件，经同一协议可注入，尚无头寸/运行/编排主链用例。
- **配置六子对象 + schema_version（v3 D-003）**：`MarketConfig` 由六 typed 子对象组合（market/scenario/bess/dr/monthly/solver）+ `schema_version`；旧扁平格式经 `scripts/migrate_market_config_v3.py` 迁移，loader 拒绝旧格式。
- **BESS 物理核统一（v3 D-004）+ 模型四层拆分（D-005）**：MPC 迁移共享 `add_bess_constraints` 核，删私有约束，`dt` 统一 0.25；目标组件层（`objectives.py`）、结果提取（`extraction.py`）、求解出口（`solve_pulp_model`）统一。
- **场景 canonical 唯一化（v3 D-007）**：`Scenario`/`ScenarioSet`/`build_joint_scenarios` 唯一；legacy `PriceScenario`/`generate_price_scenarios`/`normalize_weights` 已删除。
- **用户侧恢复为独立领域（v3 D-001/M6）**：`user_side_dispatch` 不再归档，恢复为活动独立领域，只依赖 `utils`，与市场主链互不依赖（结构守卫强制）；pyproject 删除 `norecursedirs` 排除。
- **事件契约落地（v3 D-006）**：`TradingEvent` 全链（Forecast/Bid/Award/Dispatch/Metering/Settlement）+ `MarketCalendar`；编排与回测均构建并输出事件链，回测断言事件链完整性。
- **v1.3 双结算结算引擎保留为插件**：C/C2/Cpen_dayah/Cpen_long 在 `markets/dual_settlement/`；v1 归档其余部分已删除，git 历史保留；广东分层偏差考核已移除不得加回。
- **wind_pv_bess_irr_planner 以 `equity_irr` 为正典**：`settlement`/`maximize_irr`/`ppa_locked`/`replacement` 已废弃，勿加回（main `f64be80`）。
- **capacity_planning 已整体并入 `investment_estimation/todo/`**：`src/ele_trading/capacity_planning/` 已删除，不在 `ele_trading` 下重建。
- **双包边界**：`investment_estimation` 不反向依赖 `ele_trading`（0 处 import）；投资测算类算法归 `investment_estimation`，交易/调度归 `ele_trading`。

## 8. 文档地图

| 文档 | 内容 |
|------|------|
| `AGENTS.md` | 项目 agent 规则唯一权威（仅项目特有硬约束；通用编码准则在各 agent 全局配置） |
| `README.md` | 项目总览、系统闭环、设计原则、仓库结构、核心模块、Two-stage+CVaR 模型、v2 重构进度、快速开始 |
| `docs/策略算法框架详细设计-v5.md` | 当前代码事实、未完成闭环、验收门和下一开发路线 |
| `docs/策略算法框架详细设计-v3.md` | 已批准架构决策（D-001～D-007） |
| `docs/策略算法框架详细设计-v0.md`～`-v4.md` | 历史需求、迁移和算法增强溯源；v4 P1/P2 的未完成项已转入 v5 |
| `app/README.md` | 入口脚本清单与运行约定（活动 14 个：optimization 3 + trading 7 + user_side_dispatch 4） |
| `configs/README.md` | YAML 配置清单与对应入口 |
| `tests/README.md` | 测试清单与冒烟边界 |
| `src/ele_trading/{domain,markets,positions,operations,trading,backtest,demand_response,data_provider,forecasting,scenario,optimization}/README.md` | 各子包说明 |
| `src/investment_estimation/README.md` + `todo/README.md` | 投资测算包与迁移暂存区说明 |
