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
│   ├── optimization/             # 储能、用户侧、CVXPY、分布式储能、Two-stage 优化
│   ├── control/                  # 滚动调度封装
│   ├── evaluation/               # 结算、偏差考核、指标、回测、仿真
│   ├── capacity_planning/        # PV/风电/BESS/风光储容量规划
│   ├── demand/                   # 最大需量计算
│   └── utils/                    # IO、日志、时间、数据对齐、绘图工具
├── app/                          # 可直接运行的 demo/流程入口
├── configs/                      # YAML 配置样例
├── data/                         # 最小样例数据、legacy 兼容数据、研究数据
├── docs/                         # 架构说明与算法笔记
├── tests/                        # 单元测试和入口脚本冒烟测试
├── LOG.md                        # append-only 状态、限制和待办记录
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
- `mpc_storage.py`：单窗口 MPC 与滚动 MPC。
- `two_stage_cvar.py`：Two-stage + CVaR 可求解模型。
- `user_side_bess_dispatch.py`：用户侧储能成本优化。
- `user_side_renewable_dispatch.py`：用户侧通用可再生能源无储能调度内核。
- `user_side_renewable_bess_dispatch.py`：用户侧通用可再生能源+BESS 调度内核。
- `user_side_pv_dispatch.py`、`user_side_pv_bess_dispatch.py`：用户侧 PV 场景适配入口。
- `user_side_wind_dispatch.py`、`user_side_wind_bess_dispatch.py`、`user_side_wind_pv_bess_dispatch.py`：用户侧 Wind / Wind+BESS / Wind+PV+BESS 场景适配入口。
- `user_side_bess_dispatch_cvxpy.py`：用户侧 BESS 调度的 CVXPY 版本 profile。
- `dist_ess_dispatch.py`：分布式储能多柜容量搜索和调度模拟。

### `capacity_planning`

容量规划模块覆盖 PV、风电、BESS 和组合系统：

- 联合容量优化：`capacity_optimizer.py`。
- BESS / Wind+BESS / Wind+PV+BESS：`bess_capacity_planner.py`、`wind_bess_planner.py`、`wind_pv_bess_planner.py`。
- 可行性、IRR 和多节点扫描：`feasibility_analyzer.py`、`pv_bess_irr_planner.py`、`multi_node_scanner.py`。

### `resource_simulation`

风光资源仿真模块覆盖物理出力模拟和 profile 构造：

- PV/风电物理仿真：`pv_simulation.py`、`wind_simulation.py`。
- profile 构造与缓存：`pv_profile.py`、`wind_profile.py`。

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
uv run python app/run_bess_arbitrage.py
uv run python app/run_mpc_demo.py
uv run python app/run_two_stage_skeleton.py
uv run python app/run_backtest.py
uv run python app/run_user_side_bess_dispatch.py
uv run python app/run_user_side_pv_dispatch.py
uv run python app/run_user_side_pv_bess_dispatch.py
uv run python app/run_wind_pv_bess_capacity_planning_1.py
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

- 新算法实现放入 `src/ele_trading/`，入口脚本只负责组装配置、数据和日志输出。
- 通用工具函数放入 `src/ele_trading/utils/`，包括 IO、日志、时间处理、绘图等。
- `LOG.md` 为 append-only 状态记录；过期历史不回写，追加新状态说明。
