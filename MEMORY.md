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

| 子包 | 职责 |
|------|------|
| `trading/` | 蒙西交易主线：中长期/月度/日前/日内/结算/回测/DR（当前开发主线） |
| `data_provider/` | 配置、样例数据、负荷、气象、时序质量处理 |
| `forecasting/` | 价格/负荷/风光功率预测、`ForecastProvider` 接口、天气特征工程 |
| `scenario/` | 价格场景采样（LHS/MC）与缩减（Kantorovich/Wasserstein） |
| `optimization/` | 活动储能套利、MPC、Two-stage+CVaR（用户侧/CVXPY 在 `todo/`） |
| `utils/` | IO、日志、时间、数据对齐工具 |

### `src/investment_estimation/`（投资测算自包含包）

- 新版闭环：`data_provider/` → `dispatch/` → `settlement/` → `finance/`（IRR/NPV/回收期/PPA 反推）→ `capacity_search/`，含 `resource_simulation/`（风光物理出力仿真）、`config_loader/`、`app/`、`configs/`。
- `todo/` —— **迁移暂存区**：老版 `ele_trading/capacity_planning` 整体并入，待与新版模块合并去重（非最终形态）。含多类 planner、资源仿真副本、容量扫描、`models/` 共享调度引擎。
- `utils/` —— 自包含工具子集（迁移自 `ele_trading.utils`，主包原文件保留）。

### `app/` 与 `configs/`

- `app/`：入口脚本按 `trading/`（7）、`optimization/`（3）、`user_side_dispatch/`（4，归档）分目录。容量规划入口（6 个）和 PV/Wind 资源仿真入口位于 `src/investment_estimation/app/`。
- `configs/`：YAML 按算法链路组织；蒙西交易线配置为根目录 `configs/trading/market_mengxi.yaml`。容量规划配置位于 `src/investment_estimation/configs/capacity_planning/`。

### 归属硬约束

交易/调度通用内核 → `optimization/`；蒙西交易主线 → `trading/`；投资收益测算（IRR/容量/结算/资源仿真）→ `investment_estimation/`。`src/ele_trading/capacity_planning/` **已删除**，不在 `ele_trading` 下重建容量规划/收益测算模块。

## 3. 当前主线状态（蒙西交易线 v2）

依据 `docs/策略算法框架详细设计-v2.md`（v2 为当前权威设计；`-v1.md`/`-v0.md` 为历史溯源）。算法内核与命令行入口均已落地于 `src/ele_trading/trading/`：

- **数据契约** `contracts.py`：`MarketConfig`/`OperationalPlan`/`IntradayPlan`/`SettlementReport`/`PositionState`/`MarketForecastBundle`/`DecisionTrace`（无日前金融申报量）。
- **单结算** `settlement_mengxi.py::build_settlement_report`：实时电能 `Q_real*p_real` + 中长期差价 `Q_long*(p_long-p_ref)` + 长协回收 + DR/退化/执行分项（不重复计费）。
- **日前** `day_ahead_coupled.py`（共享 BESS 物理核 + 可选场景 CVaR；日前价仅作解释性信号，不进结算）。
- **日内** `intraday_rolling.py`（冻结已执行前缀 + 剩余窗口重优化 + 求解失败回退物理裁剪）。
- **orchestrator.py**：持仓 → 预测 → 联合场景 → 日前 → 日内 → 单结算。
- **中长期/月度** `mid_long_planner.py` / `monthly_trader.py`（仓位结构、集中竞价阶梯申报、缺口再平衡；无订单簿时输出透明量价走廊）。
- **需求响应** `day_ahead_coupled.py`（`dr_enabled=True` 时两阶段联合优化：Pass A 基线 → Pass B 增量放电补偿），履约结算 `settlement_mengxi.compute_dr_settlement`。`demand_response/allocator.py` 为独立事后评估工具，不参与主链路。
- **样例数据** `sample_data.py`：30 天 96 点样例 + `WalkForwardSeasonalNaiveProvider`（按 issue-time vintage 的无前瞻 walk-forward 预测）。
- **回测/指标** `backtest.py` / `metrics.py`：walk-forward 回测（无储能/确定性/风险/oracle 四基准，仅 oracle 可用未来）+ BESS/风险/退化指标。
- **入口**：`app/trading/run_{mid_long,monthly,day_ahead,intraday,dr,backtest}.py` + 统一 `run_pipeline.py`。
- **配置**：`configs/trading/market_mengxi.yaml`（经 `trading/config_loader.load_market_config()` 加载，字段与 `MarketConfig` 对应）。
- **回测基线产物**：`results/trading/backtest/v2_baseline/`。

## 4. 测试基线（2026-08-01 实测，目录重构后）

```bash
uv run python -m pytest -q
# → 450 passed, 4 skipped, 3 deselected，~89s（无失败）
```

此前 6 个 pre-existing 失败已全部清除：legacy 数据桥接 3 个随 legacy 链路整链删除；容量规划 2 个与配置加载 1 个随本次目录重构（capacity_planning 并入 `investment_estimation/app/`、配置归位）解决。

## 5. 硬约束

硬边界（求解器 / 场景 / 指标 / 结算 / 市场参数 / 配置一致性 / 数据边界）的**唯一权威是 `AGENTS.md`**。本文件不复制约束，避免双份漂移；需要时直接读 `AGENTS.md`。

## 6. 已知缺口与待办

- **todo/ 与新版模块合并去重**：`todo/` 为迁移暂存区，非最终形态。
- **`app/trading/` 入口已就位**（早期 LOG 记为待办，现已由 `run_*.py` ×7 + `run_pipeline.py` 补齐，**该条待办已关闭**）。
- **resource_simulation 重复已解决**（2026-08-01）：删除根 `app/resource_simulation/`、`configs/resource_simulation/` 和 `todo/resource_simulation/` 副本，只保留 `investment_estimation.resource_simulation`（正本）和 `investment_estimation/app/` 入口。capacity_planning 入口的 `todo.resource_simulation` 引用已统一改指新版。

## 7. 关键决策（勿重新争论）

- **单结算为 canonical**：蒙西单结算是唯一活动结算实现；v1.3 双结算与广东分层偏差考核已弃用/移除，活动代码不得加回（2026-07-25）。
- **wind_pv_bess_irr_planner 以 `equity_irr` 为正典**：`settlement`/`maximize_irr`/`ppa_locked`/`replacement` 已废弃，勿加回（main `f64be80`）。
- **capacity_planning 已整体并入 `investment_estimation/todo/`**：`src/ele_trading/capacity_planning/` 已删除，不在 `ele_trading` 下重建。
- **双包边界**：`investment_estimation` 不反向依赖 `ele_trading`（0 处 import）；投资测算类算法归 `investment_estimation`，交易/调度归 `ele_trading`。

## 8. 文档地图

| 文档 | 内容 |
|------|------|
| `AGENTS.md` | 项目 agent 规则唯一权威（仅项目特有硬约束；通用编码准则在各 agent 全局配置） |
| `README.md` | 项目总览、系统闭环、设计原则、仓库结构、核心模块、Two-stage+CVaR 模型、v2 重构进度、快速开始 |
| `docs/策略算法框架详细设计-v2.md` | 蒙西交易线当前权威设计（`-v1.md`/`-v0.md` 为历史溯源，另会话维护中） |
| `app/README.md` | 入口脚本清单与运行约定（活动 10 个：optimization 3 + trading 7；归档 user_side_dispatch 4 个） |
| `configs/README.md` | YAML 配置清单与对应入口 |
| `tests/README.md` | 测试清单与冒烟边界 |
| `src/ele_trading/{trading,data_provider,forecasting,scenario,optimization}/README.md` | 各子包说明 |
| `src/investment_estimation/README.md` + `todo/README.md` | 投资测算包与迁移暂存区说明 |
