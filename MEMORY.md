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

- `app/`：入口脚本按 `trading/`（7+1：run_pipeline + run_{mid_long,monthly,day_ahead,intraday,dr,backtest}）、`optimization/`（3）、`capacity_planning/`（6，指向 `investment_estimation.todo`）、`resource_simulation/`（4）分目录。
- `configs/`：YAML 按算法链路组织；蒙西交易线配置为根目录 `configs/market_mengxi.yaml`。

### 归属硬约束

交易/调度通用内核 → `optimization/`；蒙西交易主线 → `trading/`；投资收益测算（IRR/容量/结算/资源仿真）→ `investment_estimation/`。`src/ele_trading/capacity_planning/` **已删除**，不在 `ele_trading` 下重建容量规划/收益测算模块。

## 3. 当前主线状态（蒙西交易线 v2）

依据 `docs/策略算法框架详细设计_v2.md`（v2 为当前权威设计；`_v1.md`/无后缀版本为历史溯源）。算法内核与命令行入口均已落地于 `src/ele_trading/trading/`：

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
- **配置**：`configs/market_mengxi.yaml`（经 `trading/config_loader.load_market_config()` 加载，字段与 `MarketConfig` 对应）。
- **回测基线产物**：`results/trading/backtest/v2_baseline/`。

> **提交状态（2026-07-26）**：v1.3→v2 重构当前在 working tree（**未提交**），HEAD（dev, `42a57cb`）仍为 v1.3。v1.3 双结算（`compute_settlement_C/C2/cpen_*`）已归档至 `trading/todo/dual_settlement_v1/`，活动 `trading/` 下为 v2 单结算实现（`build_settlement_report`）。接手前先 `git status` 确认，不要把未提交的 v2 迁移误判为已落地或回滚。

## 4. 测试基线（2026-07-26 实测，working tree，含进行中的 v2 迁移）

```bash
uv run python -m pytest -q
# → 432 passed, 6 failed, 7 skipped, 3 deselected（448 collected），~87s
```

3 个失败均为 **pre-existing**（非回归），分两类：

| 类别 | 失败测试 | 根因 |
|------|----------|------|
| 容量规划（2） | `test_capacity_planning_v4_phase1::test_wind_pv_bess_irr_runner_requires_explicit_data_dir_or_demo`、`test_entry_scripts::test_run_wind_bess_capacity_planning` | `_resolve_data_dir` 符号缺失 / capacity_planning 清理残留 |
| 配置加载（1） | `test_yaml_config_loading::test_yaml_config_loading_goes_through_read_yaml` | 「全部 yaml 经 `ele_trading.utils.io.read_yaml`」规则与 `investment_estimation` 自包含性的架构冲突 |

> 注：legacy 数据桥接 3 个失败（`test_legacy_data_bridge`、`test_run_wind_pv_legacy_profit_eval`）随 legacy 链路整链删除（2026-08-01）。

## 5. 硬约束

硬边界（求解器 / 场景 / 指标 / 结算 / 市场参数 / 配置一致性 / 数据边界）的**唯一权威是 `AGENTS.md`**。本文件不复制约束，避免双份漂移；需要时直接读 `AGENTS.md`。

## 6. 已知缺口与待办

- **resource_simulation 重复**：`investment_estimation/resource_simulation/`（新版）与 `todo/resource_simulation/`（老版 cp 迁入）并存，待去重。
- **forecasting 物理预测暂挂起**：`forecasting/pv_forecast.py`、`wind_forecast.py` 的 physics 分支延迟 import `todo.resource_simulation`（仅 physics 分支触发 ImportError），待 resource_simulation 正本归属确定后一并处理。
- **todo/ 与新版模块合并去重**：`todo/` 为迁移暂存区，非最终形态。
- **3 个 pre-existing 测试失败**：见 §4，属 capacity_planning 清理范畴，可另起任务。
- **`app/trading/` 入口已就位**（早期 LOG 记为待办，现已由 `run_*.py` ×7 + `run_pipeline.py` 补齐，**该条待办已关闭**）。

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
| `docs/策略算法框架详细设计_v2.md` | 蒙西交易线当前权威设计（`_v1.md`/无后缀版为历史溯源，另会话维护中） |
| `app/README.md` | 入口脚本清单（21 个）与运行约定 |
| `configs/README.md` | YAML 配置清单与对应入口 |
| `tests/README.md` | 测试清单与冒烟边界 |
| `src/ele_trading/{trading,data_provider,forecasting,scenario,optimization}/README.md` | 各子包说明 |
| `src/investment_estimation/README.md` + `todo/README.md` | 投资测算包与迁移暂存区说明 |
