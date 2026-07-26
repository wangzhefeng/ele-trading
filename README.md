# 电力交易算法架构

`ele-trading` 是面向虚拟电厂、电力现货交易、风光储容量规划和气象特征工程的研究型原型项目。当前主线集中在 `src/ele_trading/` 核心包，并通过 `app/` 入口脚本和 `configs/` 配置样例串起可运行链路；用户侧、分布式和 CVXPY 调度已归档至显式 `todo` 路径。

## 项目目标

1. 沉淀储能套利、MPC 和 Two-stage + CVaR 等活动交易/调度算法。
2. 打通风电、光伏、BESS、Wind+BESS、Wind+PV+BESS 容量规划与运行测算链路。
3. 提供数据构造、气象处理、预测、场景、优化、交易结算与回测的可扩展工程骨架。

## 系统闭环

```text
数据 / 配置 / 气象 → 数据清洗与特征工程 → 预测 / 资源模拟
→ 场景生成与缩减 → 优化调度 / 容量规划 → 滚动控制
→ 结算与指标 → 回测 / 收益测算 / 可视化
```

当前主要链路：

- **蒙西交易链路（主线）**：中长期仓位 → 月度分解 → 日前运行计划 → 日内滚动 → 实时电能 + 中长期差价合约单结算 → walk-forward 回测，外加需求响应。实现在 `src/ele_trading/trading/`，当前设计依据为 `docs/策略算法框架详细设计_v2.md`；v1 双结算设计仅作历史溯源。
- **市场储能链路**：价格读取、储能套利、MPC、Two-stage + CVaR、结算、回测。
- **归档用户侧链路**：用户侧、分布式和 CVXPY 调度保留于 `src/ele_trading/{data_provider,optimization}/todo/`，不属于活动 API 或常规入口。
- **风光储链路**：负荷构造、PV/风电出力 profile、BESS/Wind+BESS/Wind+PV+BESS 容量规划。
- **天气特征链路**：气象数据生成、读取、空间插值、相关性分析、聚类选点和权重计算。

## 设计原则

- 先打通端到端闭环，再逐步增加市场复杂度。
- 先支持单资产储能，再扩展风光储联合调度。
- 用统一接口与契约（如 `ForecastRequest`/`ForecastResult`、`MarketDataSnapshot`）串联各阶段，避免 `app/` 入口脚本直接依赖底层求解细节。
- CVXPY 通过最小延迟导入（`__getattr__`）访问；活动主链路和非-CVXPY 归档模块均不要求安装它。

## 仓库结构

```text
ele-trading/
├── src/ele_trading/              # 核心 Python 包（电力市场交易与调度）
│   ├── trading/                  # 蒙西交易主线（中长期/月度/日前/日内/结算/回测/DR）
│   ├── data_provider/            # 配置、样例数据、负荷、气象、时间序列处理
│   ├── forecasting/              # 价格、负荷、风光功率、ForecastProvider 接口和天气特征工程
│   ├── scenario/                 # 价格场景采样与缩减
│   ├── optimization/             # 活动储能套利、MPC、Two-stage 优化（用户侧/CVXPY 在 todo/）
│   └── utils/                    # IO、日志、时间、数据对齐工具
├── src/investment_estimation/    # 投资收益测算包（独立自包含，不依赖 ele_trading）
│   ├── data_provider/            # 投资测算数据接入
│   ├── dispatch/                 # 投资测算专用调度
│   ├── settlement/               # 月度结算
│   ├── finance/                  # IRR/NPV/回收期等财务指标
│   ├── capacity_search/          # 容量搜索
│   ├── resource_simulation/      # 风光资源物理出力仿真
│   ├── utils/                    # 自包含工具子集（迁移自 ele_trading.utils）
│   └── todo/                     # 待整合的迁移暂存区（老版 capacity_planning 整体并入）
├── app/                          # 可直接运行的入口（按 trading/optimization/capacity_planning/resource_simulation/legacy 分目录）
├── configs/                      # YAML 配置（按 market/optimization/capacity_planning/resource_simulation/legacy 分目录）
├── data/                         # 最小样例数据、legacy 兼容数据
├── docs/                         # 策略算法框架详细设计与算法笔记（需量预测、v2 迁移清单）
├── tests/                        # 单元测试和入口脚本冒烟测试
├── AGENTS.md                     # 项目 agent 规则唯一权威（含项目硬约束）
├── CLAUDE.md                     # 指向 AGENTS.md 的短指针
├── MEMORY.md                     # 项目记忆系统（状态、限制、约束、决策）
├── pyproject.toml                # 项目依赖与测试配置
└── uv.lock                       # uv 锁文件
```

## 核心模块

### `trading`（蒙西交易主线）

实现《策略算法框架详细设计 v2》的蒙西交易策略链，是当前开发主线。算法内核与命令行入口均已就位：

- `contracts.py`：单结算数据契约 dataclass（`MarketConfig`/`OperationalPlan`/`IntradayPlan`/`SettlementReport`/`PositionState`/`MarketForecastBundle`/`DecisionTrace` 等，无日前金融申报量）。
- `settlement_mengxi.py`：蒙西单结算 `build_settlement_report`（实时电能 `Q_real*p_real` + 中长期差价 `Q_long*(p_long-p_ref)` + 长协回收 + DR/退化/执行分项，不重复计费）+ 结算时段折算 `aggregate_to_settle_periods`。
- `day_ahead_coupled.py`：日前运行计划 `solve_day_ahead_operational`（共享 BESS 物理核 + 可选场景 CVaR；日前价仅作解释性信号，不进结算）。
- `intraday_rolling.py`：日内滚动 `solve_intraday_rolling`（冻结已执行前缀 + 剩余窗口重优化 + 求解失败回退物理裁剪）。
- `orchestrator.py`：`TradingOrchestrator` 串联 持仓 → 预测 → 联合场景 → 日前运行 → 日内 → 单结算。
- `mid_long_planner.py` / `monthly_trader.py`：中长期仓位结构与分月分解、集中竞价阶梯申报与缺口再平衡（无订单簿时输出透明量价走廊）。
- `dr_allocator.py`：需求响应参与决策（经济参数全部来自市场配置）。
- `sample_data.py`：`SampleTradingDataProvider`（30 天 96 点样例）+ `WalkForwardSeasonalNaiveProvider`（按 issue-time vintage 的无前瞻 walk-forward 预测）。
- `backtest.py` / `metrics.py`：walk-forward 回测（无储能/确定性/风险/oracle 四基准，仅 oracle 可用未来）与 BESS/风险/退化指标。
- 入口：`app/trading/run_{mid_long,monthly,day_ahead,intraday,dr,backtest}.py` + 统一 `run_pipeline.py`；30 天 walk-forward 回归基线产物见 `results/trading/backtest/v2_baseline/`。

### `data_provider`

负责把市场、气象和资产输入转换为带 `as_of` 与版本的交易快照。重点文件包括：

- `contracts.py` / `market_data.py`：`MarketDataSnapshot`、市场 CSV 读取与活动交易数据集直接构造。
- `weather_data.py` / `asset_data.py` / `quality.py`：气象、资产和时间质量权威入口。
- `schemas.py`：通用 `ObservedPowerSeries`、价格和场景活动类型。
- `loader.py`：仅保留无投资语义的 market/asset 兼容转发。
- `resource_weather.py`、`weather_io.py`、`time_series_ops.py`：已弃用兼容入口。
- `todo/`：归档目标年份/profile、投资 case、用户侧和 CVXPY 样例构造，不是活动 data-provider API。

### `forecasting`

包含跨包唯一的 `ForecastRequest` / `ForecastResult`、`ForecastProvider` 接口、价格/负荷预测、PV/风电功率预测、可兼容的 renewable stub，以及天气特征工程：

- PV 支持 `harmonic` 谐波回归和 `physics` 物理仿真。
- 风电支持 `statistical` 统计模型和 `physics` 物理仿真。
- `weather_feature.py` 支持相关性、滞后相关性、聚类选点、RBF/Kriging 插值和空间权重。

### `scenario`

价格场景模块已从简单扰动升级为：

- Latin Hypercube Sampling（LHS）或 Monte Carlo（`method="mc"`）采样。
- 可选 Cholesky 相关性注入。
- Kantorovich/Wasserstein L1 后向缩减，并重新分配被删场景权重。

### `optimization`

当前活动优化主线包括：

- `bess_arbitrage.py`：单市场储能套利和容量 sizing。
- `mpc_bess.py`：单窗口 MPC 与滚动 MPC。
- `two_stage_cvar.py`：Two-stage + CVaR 可求解模型。
- `todo/`：归档的用户侧、分布式和 CVXPY 调度；需要时通过显式归档路径导入，并安装 `uv sync --extra archived-user-side`。

### `investment_estimation`

投资收益测算包，与 `ele_trading` 主包**平级、完全自包含**（`grep ele_trading` 在包内 0 命中），覆盖风光储项目的投资测算闭环：负荷/电价/资源 → 调度 → 月度结算 → 财务（IRR/NPV/回收期）→ 容量搜索。

- **新版闭环**：`data_provider/`（数据接入）、`dispatch/`（规则调度）、`settlement/`（月度结算）、`finance/`（IRR/NPV/回收期/PPA 反推）、`capacity_search/`（容量搜索）、`resource_simulation/`（风光物理出力仿真）、`config_loader/`（YAML 配置）、`app/`（包内入口）。
- **`todo/`（迁移暂存区）**：老版 `ele_trading/capacity_planning` 的全部投资收益测算内容已整体并入此处，等待与上述新版模块合并去重（非最终形态）。含 BESS/PV+BESS/Wind+BESS/Wind+PV+BESS 多类 planner、资源仿真副本、容量扫描、场景编排与 `models/` 共享调度引擎。
- **两包关系**：`capacity_planning` 是 `investment_estimation` 的上一版。新版 `investment_estimation` 为纯投资测算、自带 utils 子集与 `finance.compute_irr`，不反向依赖 `ele_trading`。

详见 `src/investment_estimation/README.md` 与 `src/investment_estimation/todo/README.md`（迁移暂存区说明）。

### `utils`

- `src/ele_trading/utils`：包内 YAML、日志、数值、时间索引与数据对齐工具。

## Two-stage + CVaR 模型

> 实现：`src/ele_trading/optimization/two_stage_cvar.py`；入口：`app/optimization/run_two_stage_skeleton.py`
> 参考：Conejo et al. (2010) *Decision Making Under Uncertainty in Electricity Markets*；Rockafellar & Uryasev (2000) CVaR 线性化

把日前承诺与实时修正拆成两层随机规划：第一阶段（here-and-now）在不确定性暴露前确定日前申报量 `q[t]`；第二阶段（wait-and-see）在每个场景 ω 下确定充放电 `p_ch`/`p_dis[t,ω]`、SOC `soc[t,ω]`、偏差 `dev_pos`/`dev_neg[t,ω]` 与场景收益 `R[ω]`，并由 CVaR 辅助变量 `η`、`z[ω]` 度量尾部风险。

| 阶段 | 变量 | 含义 |
|------|------|------|
| 第一阶段 | `q[t]` | 日前市场申报量 |
| 第二阶段 | `p_ch[t,ω]`, `p_dis[t,ω]` | 每场景充/放电功率 |
| 第二阶段 | `soc[t,ω]` | 每场景荷电状态 |
| 第二阶段 | `dev_pos[t,ω]`, `dev_neg[t,ω]` | 每场景正/负偏差 |
| 第二阶段 | `R[ω]` | 每场景总收益 |
| CVaR | `η`, `z[ω]` | CVaR 辅助变量 |

主要约束：

1. **收益等式**：`R[ω] = Σ_t [π_DA[t]·q[t] + π_RT[t,ω]·(p_dis − p_ch) − κ_pos·dev_pos − κ_neg·dev_neg − c_deg·(p_ch + p_dis)]·Δt`
2. **SOC 动态递推**：`soc[t,ω] = soc[t−1,ω] + η_ch·p_ch[t,ω]·Δt − p_dis[t,ω]/η_dis·Δt`，受 `soc_min ≤ soc ≤ soc_max` 约束
3. **偏差约束**：`dev_pos + dev_neg ≤ dead_band·q[t]`（死区 2%），超出部分按分层罚款系数 κ_pos/κ_neg 进入收益
4. **CVaR 线性化**：`z[ω] ≥ −R[ω] − η`，目标为 `max E[R] − λ·[η + 1/(1−α)·Σ_ω p_ω·z_ω]`
5. **物理界**：`0 ≤ p_ch ≤ p_ch_max`，`0 ≤ p_dis ≤ p_dis_max`

求解：pyomo + `glpk`（LP）或 `cbc`（含二元变量的 MILP）；最小可解示例 |T|=4、|Ω|=3、权重 0.2/0.5/0.3、α=0.95、λ=0.1。

## 快速开始

本项目使用项目根目录 `.venv` 和 `uv`：

```bash
uv sync --extra dev
```

常用入口：

```bash
uv run python app/optimization/run_bess_arbitrage.py
uv run python app/optimization/run_mpc_demo.py
uv run python app/optimization/run_two_stage_skeleton.py
uv run python app/trading/run_backtest.py
uv run python app/capacity_planning/run_wind_pv_bess_capacity_planning_1.py
```

更完整的入口清单见 `app/README.md`。

## 配置文件

`configs/` 下配置按算法链路组织，包括市场、储能、场景、legacy 数据桥接和容量规划；归档用户侧/CVXPY 配置位于 `configs/optimization/todo/`。字段说明见 `configs/README.md`。

## 验证

标准验证命令：

```bash
uv run python -m pytest -q
```

当前测试（448 收集，432 passed）包含蒙西交易主线（结算/日前/日内/回测/中长期/DR/预测 39 项）、核心算法、样例数据构造、入口脚本、投资测算与气象特征等切片。`tests/README.md` 有按模块的清单与冒烟边界；少量 pre-existing 失败（legacy 数据桥接、容量规划清理、配置加载冲突等 6 项）见 `MEMORY.md`。

## v2 重构进度

按 `docs/策略算法框架详细设计_v2.md` §9 的阶段划分，活动 `src/ele_trading/` 重构进度：

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 0 基线 | ✅ | 模块边界与归档隔离结构守卫测试 |
| Phase 1 用户侧隔离 + 包清理 | ✅ | user_side/CVXPY/分布式进 `todo/`；`control`/`demand`/`evaluation` 删除并并入 `trading/` |
| Phase 2 契约 + 数据层 | ✅ | `ForecastRequest/Result`、`MarketDataSnapshot`；消除下层 → `trading` 反向依赖（含 `data_provider` 守卫） |
| Phase 3 完整预测 | ✅ | 气象/价格/负荷/风/光五类预测、registry、层级协调、评估指标 |
| Phase 4 联合场景 + 优化 | ✅ | `Scenario/ScenarioSet`、后向 Wasserstein L1 缩减、共享 BESS 核、PuLP+CBC 两阶段 CVaR |
| Phase 5 单结算交易链 | ✅ | 单结算权威、`OperationalPlan`、`TradingOrchestrator`、walk-forward 回测；v1 双结算归档至 `trading/todo/dual_settlement_v1/` |
| Phase 6 回测/性能/发布 | ✅ | 30 天 walk-forward 回归基线、失败模式测试、§10.6 性能预算测试（`-m slow`）、文档同步 |

### 已知约束与遗留

- 标 `TODO(rule-confirm)` 的市场参数（中长期回收、two-stage 偏差成本等）为待业务规则确认的默认值，用于生产决策前需标定。
- 6 个 pre-existing 测试失败均位于 out-of-scope 的 `src/investment_estimation/` 与 legacy 桥接（见上文「验证」与 `MEMORY.md`），不属于 v2 活动交易链（§1.1）。
- 性能预算测试默认跳过，显式运行：`UV_CACHE_DIR=.uv_cache uv run pytest -m slow -q`。
- 所有 v2 改动目前在工作树（按约定未提交）。

## 数据边界

`data/` 中既有人工构造的最小样例，也有 legacy 兼容链路和研究数据。样例数据用于接口验证、demo 和回归测试，不代表真实市场数据，不能直接用于生产策略评估。

## 协作边界

- 协作与 agent 规则、代码归属的唯一权威是 `AGENTS.md`（项目特有硬约束）；`CLAUDE.md` 是指向它的指针，不在 `.agents/`、`.claude/`、`.hermes/` 等子目录另建副本。
- `MEMORY.md` 记录项目当前状态、缺口与决策；完整历史见 `git log`。
