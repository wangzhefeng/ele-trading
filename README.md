# 电力交易算法架构

`ele-trading` 是一个面向虚拟电厂、电力现货交易、储能调度与风光储测算研究的研究型原型项目。当前主线聚焦 `src/ele_trading/` 核心包，把“数据 / 气象 → 预测 / 资源模拟 → 场景 → 优化 → 控制 → 结算 → 回测 / 容量测算”拆成可持续扩展的工程模块。

## 项目目标

1. 把储能单市场套利、MPC 滚动优化、Two-stage + CVaR 沉淀成模块化实现。
2. 给风光功率预测、偏差考核优化、风光储联合调度与容量规划测算预留稳定接口。
3. 为中国电力现货市场场景（广东、山东等）提供可扩展的工程骨架。

## 系统闭环架构

```text
原始数据采集 / 气象数据构造 → 数据清洗与特征工程 → 预测 / 资源模拟
→ 场景生成模块 → 优化求解模块 → 策略生成与下发
→ 实时执行 / MPC 修正 → 结算与收益分析 → 回测评估 / 容量测算
```

当前版本重点落地了两条链路：

1. **电力交易最小闭环**：样例数据、价格预测、场景生成、储能套利优化、MPC 滚动优化、Two-stage + CVaR、结算指标与回测入口。
2. **风光储测算链路**：合成气象数据、光伏 / 风电物理模拟、风光功率预测、容量规划优化与一体化运行演示入口。

## 仓库结构

```text
ele-trading/
├── src/ele_trading/          # 核心包（当前 README 聚焦这条主线）
│   ├── data/                 # CSV/YAML 数据加载、样例路径、dataclass schema
│   ├── forecasting/          # 价格、光伏、风电预测接口与实现
│   ├── scenario/             # 价格场景采样与缩减
│   ├── optimization/         # 储能套利、MPC、Two-stage + CVaR
│   ├── control/              # 滚动调度封装
│   ├── evaluation/           # 收益结算、偏差考核、回测指标
│   ├── capacity_planning/    # 风光模拟、容量规划、运行测算
│   └── utils/                # IO 与日志工具
├── app/                      # 可直接运行的 demo 入口
├── configs/                  # 市场、储能、场景、容量规划配置
├── data/                     # 人工构造的最小样例数据与研究数据
├── docs/                     # 架构说明、Two-stage 笔记、研究文档
├── tests/                    # 当前核心包测试与入口脚本冒烟测试
├── LOG.md                    # append-only 待办、限制和演进记录
├── pyproject.toml            # 项目配置与依赖
├── .agents/AGENTS.md         # Agent 协作规范
└── .claude/CLAUDE.md         # Claude 协作规范
```

仓库里还保留了若干历史研究目录，例如 `src/es_calc/`、`src/demand_response/`、`src/wind_pv_es_calc/` 等。它们不是当前根 README 的主线范围；接手项目时应优先阅读和修改 `src/ele_trading/` 及其关联的 `app/`、`configs/`、`data/`、`tests/`。

## 七大核心模块

### 1. 数据模块 `src/ele_trading/data`

- `schemas.py`：定义 `PriceSeries`、`StorageConfig`、`ScenarioRecord` 等 dataclass 数据结构。
- `loader.py`：从 CSV/YAML 读取价格序列、储能参数和场景样本。
- `sample_data.py`：提供内置样例数据路径和快捷加载函数。

### 2. 预测模块 `src/ele_trading/forecasting`

- `base.py`：定义统一预测输出 `ForecastOutput`，包含点预测与可选上下分位数。
- `price_forecast.py`：`SimplePriceForecaster`，基于历史均值和波动生成占位式价格预测。
- `solar_forecast.py`：`SolarPowerForecaster`，支持 `harmonic` 傅里叶谐波回归和 `physics` pvlib 物理模拟。
- `wind_forecast.py`：`WindPowerForecaster`，支持 `statistical` AR(p) + 气候学混合和 `physics` windpowerlib 物理模拟。
- `renewable_forecast.py`：保留风光预测占位接口，用于向后兼容。

### 3. 场景模块 `src/ele_trading/scenario`

- `sampler.py`：默认使用 Latin Hypercube Sampling（`scipy.stats.qmc`），可通过 Cholesky 分解引入时序相关性；保留 `method='mc'` 蒙特卡洛路径。
- `reduction.py`：Kantorovich/Wasserstein L1 后向缩减，迭代剔除并转移权重，直到剩余目标场景数量。

### 4. 优化模块 `src/ele_trading/optimization`

- `storage_arbitrage.py`：单市场储能套利（PuLP/CBC），含充放电互斥、SOC 动态递推、可选终端 SOC 约束。
- `mpc_storage.py`：单窗口 MPC 求解与滚动仿真，支持 `terminal_soc_fraction` 终端 SOC 下界约束。
- `two_stage_cvar.py`：Two-stage + CVaR 模型（Pyomo），包含收益等式、SOC 动态、偏差约束与 CVaR 线性化。
- `interfaces.py`：统一 `StorageArbitrageResult`、`MPCStepResult` 等优化结果结构。

### 5. 容量规划模块 `src/ele_trading/capacity_planning`

- `solar_simulation.py`：基于 `pvlib` 的光伏物理模拟与等效小时数校准。
- `wind_simulation.py`：基于 `windpowerlib` 的风电物理模拟与等效小时数校准。
- `capacity_optimizer.py`：粗-精两阶段网格搜索容量优化，并提供 `simulate_operation()` 做给定装机后的全年运行测算。

### 6. 控制模块 `src/ele_trading/control`

- `rolling_dispatch.py`：滚动调度包装器，复用 MPC 模块，让控制层不直接依赖底层求解细节。

### 7. 评估模块 `src/ele_trading/evaluation`

- `backtest.py`：`run_simple_backtest()` 最小回测闭环。
- `metrics.py`：基础收益指标与扩展绩效指标，包括 Sharpe、最大回撤、EFC、单 EFC 收益、RTE、利用率。
- `settlement.py`：调度收益计算与广东式分层偏差考核罚款模型。

## 快速开始

### 1. 安装项目依赖

本项目按项目根目录 `.venv` 和 `uv` 管理 Python 环境。

```bash
uv sync
```

常用依赖包括 `pulp`、`pyomo`、`scipy`、`rainflow`、`pvlib`、`windpowerlib`、`numba`、`scikit-learn`、`cvxpy` 等。

求解器是运行优化链路的前置条件：

- PuLP 储能套利和 MPC 路径需要本机可执行 `cbc`。
- Two-stage + CVaR Pyomo 演示需要 `glpk` 或兼容求解器。
- macOS 可参考 `brew install glpk` 安装 GLPK；是否安装 CBC 取决于本机求解器管理方式。

### 2. 运行储能套利样例

```bash
uv run python app/run_storage_arbitrage.py
```

### 3. 运行 MPC 样例

```bash
uv run python app/run_mpc_demo.py
```

### 4. 运行 Two-stage + CVaR 演示

```bash
uv run python app/run_two_stage_skeleton.py
```

### 5. 运行最小回测

```bash
uv run python app/run_backtest.py
```

### 6. 运行风光储测算演示

```bash
uv run python app/run_wind_solar_storage.py
```

该脚本串起：合成全年 8760 小时气象数据 → 光伏 / 风电物理模拟 → 工业负荷合成 → 容量规划优化 → 全年时序运行测算 → 48 小时风电 / 光伏功率预测演示。它比前几个最小 demo 更重，适合用于链路演示，不适合作为每次改动后的快速冒烟命令。

## 测试与当前状态

标准验证命令：

```bash
uv run python -m pytest -q
```

当前工作区实测结果：56 项测试全部通过。PuLP 储能套利和 MPC 路径使用 `PULP_CBC_CMD`，优先走项目 `.venv` 中 PuLP 自带的 CBC 求解器，避免依赖系统 `PATH` 中的 `cbc` 命令。

## 配置文件

| 文件 | 用途 |
|------|------|
| `configs/storage.yaml` | 储能物理参数（SOC、功率、效率、退化成本） |
| `configs/market.yaml` | 基础市场参数 |
| `configs/market_guangdong.yaml` | 广东现货市场：15 分钟颗粒度、偏差考核分层阈值、价格限幅 |
| `configs/scenario.yaml` | 场景生成参数（数量、噪声、权重） |
| `configs/capacity_planning.yaml` | 容量规划搜索策略、储能 / 成本参数 |

## 样例数据说明

`data/` 下的数据是人工构造的最小样例和研究数据，仅用于：

- 演示接口形状
- 打通最小闭环
- 验证代码可运行

不代表真实电力现货市场数据，不能用于生产策略评估。

## 研究文档

`docs/` 整理了架构说明、Two-stage + CVaR 工程笔记和多份调研 / 方案文档，用于解释工程设计来源与算法背景。
