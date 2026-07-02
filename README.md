# 电力交易算法架构

`ele-trading` 是面向虚拟电厂、电力现货交易、用户侧/分布式储能调度、风光储容量规划和气象特征工程的研究型原型项目。当前主线集中在 `src/ele_trading/` 核心包，并通过 `app/` 入口脚本和 `configs/` 配置样例串起可运行链路。

## 项目目标

1. 沉淀储能套利、MPC、Two-stage + CVaR、用户侧储能、用户侧光伏、光伏+储能和分布式储能调度算法。
2. 打通风电、光伏、BESS、Wind+BESS、Wind+PV+BESS 容量规划与运行测算链路。
3. 提供数据构造、气象处理、预测、场景、优化、控制、结算、回测的可扩展工程骨架。

## 系统闭环

```text
数据 / 配置 / 气象 → 数据清洗与特征工程 → 预测 / 资源模拟
→ 场景生成与缩减 → 优化调度 / 容量规划 → 滚动控制
→ 结算与指标 → 回测 / 收益测算 / 可视化
```

当前主要链路：

- **市场储能链路**：价格读取、储能套利、MPC、Two-stage + CVaR、结算、回测。
- **用户侧链路**：负荷/电价/PV 样例构造，储能、PV-only、PV+storage、Wind-only、Wind+BESS、Wind+PV+BESS 调度与需量电费核算。
- **分布式储能链路**：多变压器、多柜组合、容量搜索、调度模拟和收益汇总。
- **风光储链路**：负荷构造、PV/风电出力 profile、BESS/Wind+BESS/Wind+PV+BESS 容量规划。
- **天气特征链路**：气象数据生成、读取、空间插值、相关性分析、聚类选点和权重计算。

## 仓库结构

```text
ele-trading/
├── src/ele_trading/              # 核心 Python 包
│   ├── data_provider/            # 配置、样例数据、负荷、气象、时间序列处理
│   ├── forecasting/              # 价格、风光功率和天气特征工程
│   ├── scenario/                 # 价格场景采样与缩减
│   ├── optimization/             # 储能、用户侧、CVXPY、Two-stage 优化
│   ├── control/                  # 滚动调度封装
│   ├── evaluation/               # 结算、偏差考核、指标、回测、仿真
│   ├── capacity_planning/        # BESS/PV/Wind/风光储容量规划（含资源仿真与 models/ 共享引擎）
│   ├── demand/                   # 最大需量计算
│   └── utils/                    # IO、日志、时间、数据对齐、绘图工具
├── app/                          # 可直接运行的入口（按 optimization/evaluation/capacity_planning/resource_simulation/legacy 分目录）
├── configs/                      # YAML 配置（按 market/optimization/capacity_planning/resource_simulation/legacy 分目录）
├── data/                         # 最小样例数据、legacy 兼容数据
├── docs/                         # 架构说明与算法笔记
├── tests/                        # 单元测试和入口脚本冒烟测试
├── AGENTS.md                     # 项目 agent 规则唯一权威（含项目硬约束）
├── CLAUDE.md                     # 指向 AGENTS.md 的短指针
├── LOG.md                        # append-only 状态、限制和后续工作记录
├── pyproject.toml                # 项目依赖与测试配置
└── uv.lock                       # uv 锁文件
```

## 核心模块

### `data_provider`

负责把 CSV/YAML、合成样例、负荷曲线、气象数据和 legacy 数据桥接成统一输入。重点文件包括：

- `loader.py`、`sample_data.py`、`schemas.py`：基础价格、储能、场景和 profile 数据结构。
- `load_profile.py`：从历史负荷 Excel 构造目标年份负荷曲线。
- `resource_weather.py`、`weather_io.py`：Open-Meteo、NetCDF、Mongo、样例气象和测点读取。
- `time_series_ops.py`：时间戳清洗、重采样、对齐和异常修复。
- `*_sample.py`：用户侧储能、PV、PV+storage、CVXPY 调度的确定性 demo 输入构造。

### `forecasting`

包含统一 `ForecastOutput`、简单价格预测、PV/风电功率预测、可兼容的 renewable stub，以及天气特征工程：

- PV 支持 `harmonic` 谐波回归和 `physics` 物理仿真。
- 风电支持 `statistical` 统计模型和 `physics` 物理仿真。
- `weather_feature.py` 支持相关性、滞后相关性、聚类选点、RBF/Kriging 插值和空间权重。

### `scenario`

价格场景模块已从简单扰动升级为：

- Latin Hypercube Sampling（LHS）或 Monte Carlo（`method="mc"`）采样。
- 可选 Cholesky 相关性注入。
- Kantorovich/Wasserstein L1 后向缩减，并重新分配被删场景权重。

### `optimization`

当前优化主线包括：

- `bess_arbitrage.py`：单市场储能套利和容量 sizing。
- `mpc_bess.py`：单窗口 MPC 与滚动 MPC。
- `two_stage_cvar.py`：Two-stage + CVaR 可求解模型。
- `user_side_bess_dispatch.py`：用户侧储能成本优化。
- 分布式 BESS 调度共享内核：用户侧多节点储能调度。
- `user_side_renewable_dispatch_class.py`：用户侧通用可再生能源无储能调度共享内核。
- `user_side_renewable_bess_dispatch_class.py`：用户侧通用可再生能源+BESS 调度共享内核。
- `user_side_pv_dispatch.py`、`user_side_pv_bess_dispatch.py`：用户侧 PV 场景适配入口。
- `user_side_wind_dispatch.py`、`user_side_wind_bess_dispatch.py`、`user_side_wind_pv_bess_dispatch.py`：用户侧 Wind / Wind+BESS / Wind+PV+BESS 场景适配入口。
- CVXPY BESS 调度 profile：用户侧 BESS 调度的凸优化版本。

### `capacity_planning`

容量规划模块覆盖 BESS、PV+BESS、Wind+BESS、Wind+PV+BESS 四类场景，共 10 个 planner + 资源仿真子包 + 5 个规划 helper：

- **第一组 BESS sizing**：`bess_capacity_distributed_planner.py`（多节点 LP）、`bess_capacity_economic_planner.py`（MILP 联合优化）、`bess_capacity_operating_planner.py`（CVXPY 运营优化）。
- **第二组 PV+BESS**：`pv_bess_planner.py`（二分搜索最小容量）、`pv_bess_irr_planner.py`（三段式 IRR 扫描）。
- **第三组 Wind+BESS**：`wind_bess_planner.py`（二分搜索）、`wind_bess_irr_planner.py`（两段式 IRR 扫描）。
- **第四组 Wind+PV+BESS 联合**：`wind_pv_bess_capacity_optimizer.py`（最低成本）、`wind_pv_bess_capacity_planner.py`（最小储能）、`wind_pv_bess_irr_planner.py`（IRR 目标型 + 电价反推）。
- **资源仿真**（`resource_simulation/`）：PV/Wind 物理出力仿真，供容量规划和物理预测模式复用。
- **容量规划公共合同**：`interfaces.py` 合并容量规划实际使用的分布式容量搜索配置、单节点 BESS CVXPY 调度合同和分布式 BESS 调度合同，用于避免 capacity planning 反向依赖 `optimization/`。
- **共享引擎**（`models/`）：`cvxp_bess_dispatch.py`（单节点 BESS CVXPY 调度）、`distributed_bess_dispatch.py`（多节点分布式 BESS 调度）、`dispatch_algo.py`（Numba 贪心年度调度）、`resource_bess_planner_core.py`（单源 PV/Wind 贪心 + 二分搜索）、`simulation_model.py`（策略回放仿真）。
- **辅助**：`feasibility_analyzer.py`（电价-负荷匹配评分）、`multi_node_scanner.py`（多节点 MILP 扫描）。

长期建设计划维护在 `src/ele_trading/capacity_planning/PLAN.md`，当前权威基线为 V2：先建一条 canonical 物理+结算链（时序调度→月度结算→财务）并用 golden/oracle 验证，再迁源码；V1 保留为历史。

详见 `src/ele_trading/capacity_planning/README.md`。

### `capacity_planning.resource_simulation`

风光资源物理出力仿真现归属 `capacity_planning` 子包，输出统一 `SimulationResult`（kW 时序），PV/风电各两版：

- 光伏：`pv_simulation_v1.py`（配置驱动，清晰天空/气象/回放，含缓存）、`pv_simulation_v2.py`（PVSimulator 类）。
- 风电：`wind_simulation_v1.py`（配置驱动，自动获取气象 + FLH 校准）、`wind_simulation_v2.py`（WindSimulator 类）。
- 共用模型：`models.py`（`SimulationResult`）。

### `evaluation`、`control`、`demand`、`utils`

- `evaluation`：收益结算、偏差考核、IRR、扩展储能指标、简单回测、仿真模型。
- `control`：基于 MPC 的滚动调度包装。
- `demand`：固定窗口/滑动窗口最大需量和需量电费计算。
- `src/ele_trading/utils`：包内 YAML、日志、时间切分、时间索引、数据对齐工具。

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
uv run python app/evaluation/run_backtest.py
uv run python app/optimization/run_user_side_bess_dispatch.py
uv run python app/optimization/run_user_side_pv_dispatch.py
uv run python app/optimization/run_user_side_pv_bess_dispatch.py
uv run python app/capacity_planning/run_wind_pv_bess_capacity_planning_1.py
```

更完整的入口清单见 `app/README.md`。

## 配置文件

`configs/` 下配置按算法链路组织，包括市场、储能、场景、用户侧调度、legacy 数据桥接、分布式储能、BESS/Wind+BESS/Wind+PV+BESS 容量规划。字段说明见 `configs/README.md`。

## 验证

标准验证命令：

```bash
uv run python -m pytest -q
```

当前测试包含核心算法、样例数据构造、入口脚本、气象特征等切片。

## 数据边界

`data/` 中既有人工构造的最小样例，也有 legacy 兼容链路和研究数据。样例数据用于接口验证、demo 和回归测试，不代表真实市场数据，不能直接用于生产策略评估。

## 协作边界

- Agent 规则的唯一权威是根目录 `AGENTS.md`（含通用准则 + 第 5 节项目硬约束），根目录 `CLAUDE.md` 是指向它的短指针。不要在 `.agents/`、`.claude/`、`.hermes/` 等子目录重建指令副本。
- 新算法实现放入 `src/ele_trading/`，入口脚本只负责组装配置、数据和日志输出。
- 交易/调度侧通用内核放在 `optimization/`；容量规划专用内核、副本、容量扫描、场景编排、收益测算和文件导出放在 `capacity_planning/`。
- 通用工具函数放入 `src/ele_trading/utils/`，包括 IO、日志、时间处理、绘图等。
- `LOG.md` 为 append-only 状态记录；过期历史不回写，追加新状态说明。
