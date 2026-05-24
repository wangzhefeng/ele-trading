# 电力交易算法架构

`ele-trading` 是一个面向虚拟电厂、电力现货交易、储能调度与风光储测算研究的研究型原型项目。它把"数据/气象 → 预测/资源模拟 → 场景 → 优化 → 控制 → 结算 → 回测/测算"闭环拆成可持续扩展的工程模块。

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
2. **风光储测算链路**：合成气象数据、光伏/风电物理模拟、风光功率预测、容量规划优化与一体化运行演示入口。

## 仓库结构

```text
ele-trading/
├── src/ele_trading/          # 核心包（7 个模块）
│   ├── data/                 # 数据加载、样例数据、schema 定义
│   ├── forecasting/          # 预测接口（价格、风电、光伏）
│   ├── scenario/             # 场景采样（LHS）与缩减（Kantorovich）
│   ├── optimization/         # 储能套利、MPC、Two-stage + CVaR
│   ├── control/              # 滚动调度封装
│   ├── evaluation/           # 结算、回测指标、偏差考核
│   ├── capacity_planning/    # 风光模拟、容量规划优化
│   └── utils/                # IO 工具、日志
├── app/                      # 应用入口脚本（5 个 demo）
├── configs/                  # 市场、储能、场景、容量规划配置
├── data/                     # 样例数据
│   ├── raw/                  # 日前/日内价格 CSV、储能参数 YAML
│   └── scenarios/            # 场景样本 CSV
├── docs/                     # 研究文档与方案
├── LOG.md                    # 项目待办与演进记录
├── pyproject.toml            # 项目配置与依赖
└── .claude/CLAUDE.md         # Agent 协作规范
```

## 七大核心模块

### 1. 数据模块 `src/ele_trading/data`

- `loader.py`：从 CSV/YAML 读取价格序列、储能参数、场景样本
- `sample_data.py`：内置样例数据路径与快捷加载函数
- `schemas.py`：`PriceSeries`、`StorageConfig`、`ScenarioRecord` 数据结构

### 2. 预测模块 `src/ele_trading/forecasting`

- `ForecastOutput`：统一预测输出（点预测 + 可选分位区间）
- `SimplePriceForecaster`：基于历史均值与波动的价格预测占位实现
- `SolarPowerForecaster`：光伏功率预测，支持 `harmonic`（傅里叶谐波回归）与 `physics`（pvlib 物理模拟）两种模式
- `WindPowerForecaster`：风电功率预测，支持 `statistical`（AR(p) + 气候学混合）与 `physics`（windpowerlib 物理模拟）两种模式
- `RenewableForecastStub`：占位接口（向后兼容）

### 3. 场景模块 `src/ele_trading/scenario`

- `sampler.py`：Latin Hypercube Sampling（`scipy.stats.qmc`）+ Cholesky 分解引入时序相关性；保留 `method='mc'` 向后兼容
- `reduction.py`：Kantorovich/Wasserstein L1 后向缩减（Heitsch & Römisch 2003），迭代剔除再分配代价最小的场景直至目标数量 K

### 4. 优化模块 `src/ele_trading/optimization`

- `storage_arbitrage.py`：单市场储能套利（PuLP/CBC），含充放电互斥约束、SOC 动态递推、可选终端 SOC 约束
- `mpc_storage.py`：单窗口 MPC 求解 + 滚动仿真，支持 `terminal_soc_fraction` 终端 SOC 下界约束
- `two_stage_cvar.py`：完整 Two-stage + CVaR 模型（Pyomo），含收益等式约束、SOC 动态递推、偏差约束、CVaR 线性化（Rockafellar & Uryasev 2000）
- `interfaces.py`：`StorageArbitrageResult`、`MPCStepResult` 数据结构

### 5. 容量规划模块 `src/ele_trading/capacity_planning`

- `SolarSimulator`：基于 `pvlib` 的光伏物理模拟与等效小时数校准
- `WindSimulator`：基于 `windpowerlib` 的风电物理模拟与等效小时数校准
- `CapacityOptimizer`：粗-精两阶段网格搜索容量优化
- `simulate_operation`：给定装机规模后的全年时序运行测算

### 6. 控制模块 `src/ele_trading/control`

- `rolling_dispatch.py`：滚动调度包装器，复用 MPC 模块，控制层与求解细节解耦

### 7. 评估模块 `src/ele_trading/evaluation`

- `backtest.py`：`run_simple_backtest()` 最小回测闭环
- `metrics.py`：
  - `summarize_storage_metrics()`：基础指标（总收益、套利收益、退化成本、平均 SOC）
  - `compute_extended_metrics()`：年化 Sharpe 比率、最大回撤（MDD）、等效完整循环次数（EFC）及单 EFC 收益、往返效率（RTE）、利用率
- `settlement.py`：
  - `compute_dispatch_revenue()`：调度收益计算
  - `compute_deviation_penalty()`：广东式三段分层罚款模型（死区 ≤2% 免罚、2–5% 按 0.25 系数、>5% 按 0.50 系数）

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

主要依赖：`pulp`、`pyomo`、`scipy`、`rainflow`、`pvlib`、`windpowerlib`、`numba`、`scikit-learn`。
Two-stage CVaR 求解需要系统安装 `glpk`：

```bash
# macOS
brew install glpk
```

### 2. 运行储能套利样例

```bash
./.venv/bin/python app/run_storage_arbitrage.py
```

### 3. 运行 MPC 样例

```bash
./.venv/bin/python app/run_mpc_demo.py
```

### 4. 运行 Two-stage + CVaR 演示

```bash
./.venv/bin/python app/run_two_stage_skeleton.py
```

### 5. 运行最小回测

```bash
./.venv/bin/python app/run_backtest.py
```

### 6. 运行风光储测算演示

```bash
./.venv/bin/python app/run_wind_solar_storage.py
```

该脚本串起：合成全年气象数据 → 光伏/风电物理模拟 → 工业负荷合成 → 容量规划优化 → 全年时序运行测算 → 48 小时风电/光伏功率预测演示。

### 7. 运行测试

```bash
./.venv/bin/pytest -q
```

> **注意**：当前 `tests/` 目录缺失，需要重建测试套件后才能运行。

## 配置文件

| 文件 | 用途 |
|------|------|
| `configs/storage.yaml` | 储能物理参数（SOC、功率、效率、退化成本） |
| `configs/market.yaml` | 基础市场参数 |
| `configs/market_guangdong.yaml` | 广东现货市场：15 分钟颗粒度、偏差考核分层阈值、价格限幅 |
| `configs/scenario.yaml` | 场景生成参数（数量、噪声、权重） |
| `configs/capacity_planning.yaml` | 容量规划搜索策略、储能/成本参数 |

## 样例数据说明

`data/` 下的数据是人工构造的最小样例，仅用于：

- 演示接口形状
- 打通最小闭环
- 验证代码可运行

不代表真实电力现货市场数据，不能用于生产策略评估。

## 研究文档

`docs/research/` 整理了多份调研与方案文档，包括电力市场交易调研文档、风光储测算算法方案等，用于解释工程设计来源与算法背景。
