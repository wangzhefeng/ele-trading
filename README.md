# 电力交易算法架构

`ele-trading` 是一个面向虚拟电厂、电力现货交易与储能调度研究的研究型原型项目。它把调研文档中的"预测 -> 场景 -> 优化 -> 控制 -> 结算 -> 回测"闭环拆成可以持续扩展的工程模块，而不是停留在一次性脚本。

## 项目目标

本项目优先解决三个问题：

1. 把文档第 23 章中的工程架构变成一个真实可浏览、可运行、可扩展的代码仓库。
2. 把储能单市场套利、MPC 滚动优化、Two-stage + CVaR 沉淀成模块化实现。
3. 给后续风光预测、偏差考核优化、风光储联合调度预留稳定接口。

## 系统闭环架构

项目遵循以下工程闭环：

```text
原始数据采集 -> 数据清洗与特征工程 -> 预测模块 -> 场景生成模块
-> 优化求解模块 -> 策略生成与下发 -> 实时执行 / MPC 修正
-> 结算与收益分析 -> 回测评估与模型迭代
```

当前版本重点落地了闭环中的最小可用链路：样例数据、价格预测占位器、场景生成、储能套利优化、MPC 滚动优化、结算指标与回测入口。

## 六大核心模块职责

### 1. 数据模块 `src/ele_trading/data`

负责读取价格序列、储能参数、场景样本等基础输入，并统一样例数据格式。

### 2. 预测模块 `src/ele_trading/forecasting`

负责提供统一预测接口。当前落地的是一个基于历史均值与波动构造的价格预测器，以及风光预测骨架接口。

### 3. 场景模块 `src/ele_trading/scenario`

负责把预测结果扩展成多场景输入：

- **场景采样**：Latin Hypercube Sampling（`scipy.stats.qmc`）+ Cholesky 分解引入时序相关性，相同样本数下比纯蒙特卡洛降低 30–50% 方差；保留 `method='mc'` 向后兼容。
- **场景缩减**：Kantorovich/Wasserstein L1 后向缩减（Heitsch & Römisch 2003），迭代剔除再分配代价最小的场景直至目标数量 K，替代原 Top-K 占位逻辑。

### 4. 优化模块 `src/ele_trading/optimization`

负责核心优化决策：

- `storage_arbitrage.py`：单市场储能套利（PuLP/CBC）
- `mpc_storage.py`：单窗口 MPC 与滚动仿真，支持 `terminal_soc_fraction` 终端 SOC 下界约束
- `two_stage_cvar.py`：完整 Two-stage + CVaR 模型（Pyomo/glpk），含收益等式约束、SOC 动态递推、物理界约束、CVaR 线性化（Rockafellar & Uryasev 2000）

### 5. 控制模块 `src/ele_trading/control`

负责滚动调度与实时修正。当前通过 `rolling_dispatch.py` 封装了储能滚动控制流程。

### 6. 评估模块 `src/ele_trading/evaluation`

负责结算、指标计算与最小回测闭环：

- **基础指标**：总收益、套利收益、退化成本、平均 SOC
- **扩展指标**（`compute_extended_metrics`）：年化 Sharpe 比率、最大回撤（MDD）、等效完整循环次数（EFC）及单 EFC 收益、往返效率（RTE）、利用率
- **偏差考核**（`compute_deviation_penalty`）：广东式三段分层罚款模型（死区 ≤2% 免罚、2–5% 按 0.25 系数、>5% 按 0.50 系数）

## Two-stage 工程演进路线

项目按照文档建议，采用由浅入深的实现顺序：

### 阶段 A：确定性版本 ✅

- 单场景
- 单市场
- 不考虑风险
- 先把储能套利跑通

### 阶段 B：多场景期望收益版本 ✅

- 电价或出力引入多场景
- 用场景权重表示不确定性
- 目标函数转为期望收益

### 阶段 C：加入 CVaR 风险版本 ✅

- 增加 `eta` 与 `z_w`
- 显式建模尾部风险
- 为风险厌恶型策略预留参数接口

### 阶段 D：加入滚动优化 / MPC ✅

- 每个滚动窗口重预测、重求解
- 只执行当前一步
- 用实时状态驱动下一轮优化

## 当前已落地能力

- 单市场储能套利求解
- 储能 MPC 单窗口优化（含终端 SOC 下界约束）
- 储能滚动调度仿真
- Two-stage + CVaR 完整可求解模型（Pyomo + glpk）
- LHS + Cholesky 场景采样
- Kantorovich 后向场景缩减
- 扩展回测指标：Sharpe / MDD / EFC / RTE / 利用率
- 偏差考核分层罚款模型（广东标准）
- 广东市场参数配置文件（`configs/market_guangdong.yaml`）
- 样例价格数据与储能参数

## 当前未落地能力

- 真实市场规则接入（实时数据对接）
- 多市场联合收益建模
- 风光概率预测模型
- Benders / Progressive Hedging 等大规模分解求解
- 数据层 15 分钟颗粒度（96 时段/日）适配

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Two-stage CVaR 求解需要系统安装 glpk：

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

### 6. 运行测试

```bash
./.venv/bin/pytest -q
```

## 示例数据说明

`data/` 下的数据是根据文档第 23.5-23.7 节中的变量语义人工构造的最小样例数据，只用于：

- 演示接口形状
- 打通最小闭环
- 验证代码可运行

它们不代表真实电力现货市场、辅助服务市场或虚拟电厂运营数据，不能直接用于生产策略评估。

## 中国现货市场优先落地方向

结合文档建议，适合优先工程化的方向是：

1. 储能电价套利 + 滚动优化
2. 新能源偏差考核优化
3. 风光储联合收益优化

当前项目已经实现了第一、二个方向的核心算法，并为第三个方向预留了预测、场景和联合优化扩展位。
