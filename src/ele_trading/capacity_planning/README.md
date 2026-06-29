# Capacity Planning 容量规划模块

## 目录

- [模块定位](#模块定位)
- [算法架构总览](#算法架构总览)
- [调度引擎层（共享）](#调度引擎层共享)
- [第一组：BESS 容量规划（用户侧 / 工商业储能）](#第一组bess-容量规划用户侧--工商业储能)
- [第二组：PV+BESS 容量规划](#第二组pvbess-容量规划)
- [第三组：Wind+BESS 容量规划](#第三组windbess-容量规划)
- [第四组：Wind+PV+BESS 三要素联合规划](#第四组windpvbess-三要素联合规划)
- [辅助模块](#辅助模块)
- [跨组对比与选型指南](#跨组对比与选型指南)

---

## 模块定位

本模块提供新能源 + 储能系统的容量规划与优化算法，覆盖三大决策场景：

| 决策类型 | 回答的问题 | 涉及模块组 |
|----------|-----------|-----------|
| **纯储能 sizing** | 给定负荷/电价，建多大储能最赚？ | 第一组 |
| **源-储联合 sizing** | 给定新能源装机，配多少储能满足消纳约束？ | 第二、三组 |
| **三要素联合优化** | 风、光、储各建多少？ | 第四组 |

模块遵循 **AGENTS.md 第 5 节** 的硬边界：入口脚本在 `app/capacity_planning/run_*.py`，可复用调度内核在 `models/` 下，禁止在 notebook 中直接调用求解器。

---

## 算法架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        容量规划模块架构                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              业务规划层（10 个 planner 脚本）                  │   │
│  │                                                             │   │
│  │  第一组 BESS sizing     第二组 PV+BESS    第三组 Wind+BESS   │   │
│  │  · distributed          · pv_bess         · wind_bess       │   │
│  │  · economic (MILP)      · pv_bess_irr     · wind_bess_irr   │   │
│  │  · operating (CVXPY)                                       │   │
│  │                                                             │   │
│  │           第四组 Wind+PV+BESS 联合规划                       │   │
│  │           · capacity_optimizer  · capacity_planner          │   │
│  │           · irr_planner                                     │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                             │                                       │
│             ┌───────────────┼───────────────┐                      │
│             ▼               ▼               ▼                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ 贪心调度引擎  │  │ MILP/LP 引擎 │  │ 仿真回放引擎  │              │
│  │ (Numba JIT)  │  │ (PuLP/CVXPY) │  │ ( EssSim )   │              │
│  │              │  │              │  │              │              │
│  │ dispatch_   │  │ distributed  │  │ EssSimulation│              │
│  │ annual()    │  │ _planner     │  │ Model        │              │
│  │ resource_   │  │              │  │              │              │
│  │ bess_core() │  │              │  │              │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│         models/dispatch_algo.py   models/simulation_model.py        │
│         models/resource_bess_planner_core.py                       │
└─────────────────────────────────────────────────────────────────────┘
```

**核心设计原则**：调度引擎与业务规划解耦。业务层负责"搜索什么容量组合"，引擎层负责"给定容量算出物理产出/经济收益"。三类引擎的区别：

| 引擎 | 决策方式 | 典型消费者 | 加速 |
|------|---------|-----------|------|
| `dispatch_annual`（贪心） | 规则：直供→充电→放电→弃电 | 第四组 `_planner` / `irr_planner` | Numba JIT |
| `resource_bess_planner_core`（单源贪心） | 规则：surplus→充电，deficit→放电 | 第二、三组 `pv_bess_planner` / `wind_bess_planner` | numpy 向量化 |
| MILP/LP（PuLP/CVXPY） | 数学规划求最优解 | 第一组 economic/distributed | CBC 求解器 |
| `EssSimulationModel`（仿真） | 回放外部给定的策略 | 第一组 operating | 纯 Python |

---

## 调度引擎层（共享）

### `models/dispatch_algo.py` — 贪心年度调度引擎

**定位**：给定容量配置，用规则生成充放电动作并累计全年电量。是第二/三/四组所有 planner 的公共物理层。

**核心函数**：`dispatch_annual()` → 内部调用 `_dispatch_annual_numba()`（Numba JIT 版）或 numpy 简化回退。

**每时步决策顺序**：

```
1. 直供：direct = min(L, G)         # 新能源直接供负荷
2. 充电：p_ch = min(surplus, Pmax, SOC上限反推功率)   # 盈余优先充电
3. 放电：p_dis = min(deficit, Pmax, SOC下限反推功率)  # 缺口由电池补
4. 弃电：curtail = surplus - p_ch   # 仍剩余则丢弃
```

**效率模型**：对称开方 `η_c = η_d = √η_roundtrip`（默认 0.92 → 0.959）。

**特色**：`switch_gap_steps` 参数限制充放电频繁切换，保护电池。

**返回**：6 个年度电量指标 `dict`（`ren_gen_kwh`, `ren_used_kwh`, `load_kwh`, `direct_used_kwh`, `bess_discharge_kwh`, `curtail_kwh`）。

详见 [§8.3.2 物理层](#832-物理层bess-贪心调度逐时步仿真)（现有 README 下方完整数学推导）。

### `models/simulation_model.py` — 策略回放仿真器

**定位**：被动仿真器。不决定充多少，只把外部指令按真实物理约束落地，输出逐时步曲线和收益。

**与 `dispatch_annual` 的区别**：

| 维度 | `EssSimulationModel` | `dispatch_annual` |
|------|---------------------|-------------------|
| 角色 | 策略执行器 | 策略生成器 |
| 决策来源 | 外部 `es_strategy` 输入 | 内部贪心规则 |
| 输入 | 单负荷 + 策略 + 电价 | 多源新能源 + 负荷 |
| 输出 | 逐时步曲线 + 原始/优化收益对比 | 年度电量聚合 |
| 效率 | 充放分离（可不对称） | 对称开方 |
| 约束 | 变压器容量、SOC floor/ceiling | SOC 上下限、C-rate、切换间隔 |

被第一组 `operating_planner` 消费：CVXPY 求解最优策略 → `EssSimulationModel` 回放验证。

---

## 第一组：BESS 容量规划（用户侧 / 工商业储能）

**共同场景**：已有负荷曲线 + 分时电价，需要确定储能的容量和充放电策略。**不含新能源发电**，纯靠电价差套利 + 需量管理盈利。

### `bess_capacity_distributed_planner.py` — 分布式多节点储能

**功能**：工业园区多变压器场景下，搜索各节点储能机柜数的最优组合。

**算法特点**：
- **拓扑建模**：`SYSTEMS` 字典定义多变压器分组（如 338/342/park 三种系统配置），每个变压器挂载若干标准化储能机柜（`DIST_BESS_CABINET_CAPACITY_KWH` / `DIST_BESS_CABINET_POWER_KW`）
- **组合枚举**：`full_grid_candidates()` 按 `CabinetEqualityMode`（NONE/GLOBAL/GROUP）生成机柜数候选组合
- **调度引擎**：调用 `optimization/user_side_bess_distributed_dispatch_class.py` 的 `DistributedBESSDispatcher`，按月分段求解 LP
- **V1-V5 预设**：5 种调度策略预设（LP/rule-based、不同 `grid_import_formula`、`smooth_penalty`、`ramp_rate` 组合）

**核心输出**：每个机柜组合的 `revenue`、`transformer_violation_count`、各节点 power/soc 时序。

**与同组的区别**：

| 维度 | distributed | economic | operating |
|------|------------|----------|-----------|
| 拓扑 | 多变压器多节点 | 单节点 | 单节点 |
| 调度引擎 | LP（按月分段） | MILP（全年） | CVXPY（按月/日分段） |
| 决策变量 | 各节点机柜数 | 容量 + 充放电策略 | 充放电策略（容量给定搜索） |
| 变压器约束 | 显式建模（5 台） | 单台 `transformer_rating` | 单台 |

### `bess_capacity_economic_planner.py` — MILP 联合优化

**功能**：单节点场景下，把额定容量 `Cap_rated` 作为决策变量，与充放电策略联合优化，最大化净套利收益（扣除年化 CAPEX 和循环 OPEX）。

**MILP 模型**：

```
决策变量：P_ch[t], P_dis[t]（功率）, E[t]（SOC）, u_ch[t]/u_dis[t]（0-1 状态）, Cap_rated（容量）

目标：max Σ(price[t]·P_dis[t]·η_dis - price[t]·P_ch[t]/η_ch)·dt
        - annualized_capex(Cap_rated) - opex(ΣP_dis[t]·dt)

关键约束：
  · P_ch[t] ≤ c_rate · Cap_rated              # 功率受容量限制
  · u_ch[t] + u_dis[t] ≤ 1                    # 充放电互斥
  · E[t] = E[t-1] + P_ch[t]·η_c·dt - P_dis[t]·dt/η_d   # SOC 动态
  · Cap_rated·min_dod ≤ E[t] ≤ Cap_rated      # SOC 边界
  · ΣP_dis[t]·dt ≥ max_cycles·Cap_rated·min_utilization  # 利用率下限
  · P_dis[t] ≤ load_curve[t]                   # 禁止向电网倒送
  · load[t] + P_ch[t] ≤ transformer_rating     # 变压器容量
```

**特色**：`min_power_ratio > 0` 时启用 McCormick 包络松弛，建模"P_ch ≥ mpr·c_rate·Cap_rated·u_ch"的非线性关系。

**输出**：`CapacitySizingResult`（最优容量、功率、充放电/SOC 时序、往返效率、实际利用率）。

### `bess_capacity_operating_planner.py` — CVXPY 运营优化

**功能**：给定容量候选，用 CVXPY 求解最优充放电策略，再用 `EssSimulationModel` 回放验证，线性搜索收益最大的容量。

**算法流程**：

```
for bess_kwh in linspace(0, batt_hi_max, search_points):
    1. CVXPY 分段求解充放电策略（按 day/month 切分，profile 由 version 决定）
    2. EssSimulationModel.simulation_process() 回放策略 → 实际 SOC/充放电曲线
    3. revenue_calculation() 算原始 vs 优化收益（含需量电费）
    4. 选 revenue 最大的容量
```

**与 economic 的关键区别**：
- economic 把容量和策略**联合**放进一个 MILP 同时优化；operating 把容量搜索放在外层，内层对每个固定容量分别求最优策略
- economic 返回全局最优（但 MILP 规模大、求解慢）；operating 线性扫描 + 分段求解，更快但容量点是离散的
- operating 多了 `EssSimulationModel` 回放环节，能输出物理可行的逐时步曲线（CVXPY 解可能因效率建模差异与物理回放不闭合）

---

## 第二组：PV+BESS 容量规划

**共同场景**：给定光伏装机（或光伏出力曲线），搜索满足消纳约束的储能容量。**无电价套利**，目标是在离网/自发自用场景下最大化绿电消纳。

### `pv_bess_planner.py`

**功能**：给定 PV 装机，二分搜索最小储能容量，使自用率和负荷覆盖率达到阈值。

**调度模式**（两种）：

1. **纯弃电搬运**（`enable_shift=False`）：
   - `surplus > 0`（PV > Load）→ 充电
   - `deficit > 0`（Load > PV）→ 放电
   - 禁止电网充电

2. **平移充电**（`enable_shift=True`）：
   - 允许 PV < Load 时主动抽取部分 PV 充电
   - 通过 `lookahead_steps` 预判未来缺口（`future_deficit > 0.5·L·window` 且 SOC < 0.7 时触发）
   - `shift_max_frac_of_pv` 限制平移比例（默认 0.30）

**搜索算法**：二分搜索。先 `check_feasibility_upper_bound()`（极大容量测物理可达性），再倍增找可行上界，最后二分到 `tol_mwh` 精度。

**效率模型**：充放分离 `eta_charge` / `eta_discharge`（默认均 0.92），与第四组 `dispatch_annual` 的对称开方不同。

### `pv_bess_irr_planner.py`

**功能**：**不模拟时序调度**，用三段式收益模型 + 轮巡扫描储能容量 × 购电电价，计算光储整体 IRR。

**三段式收益模型**（每月一行 MWh 数据）：

```
1. PV 自用：    Gain1 = min(PV, Load) × buy_price
2. BESS 平移弃电：Gain3 = min(BESS, Curtail, load_after_PV) × buy_price
3. 余电上网：    Gain2 = min(PV_left, PV × max_export_ratio) × export_price
```

**与 pv_bess_planner 的本质区别**：

| 维度 | `pv_bess_planner` | `pv_bess_irr_planner` |
|------|-------------------|----------------------|
| 时间粒度 | 逐时步（8760h+） | 月度聚合（12 行） |
| 调度引擎 | 逐时步仿真（Python/numpy） | 无调度，三段式公式 |
| 决策目标 | 满足消纳约束的最小容量 | 最大化 IRR |
| 输入数据 | 高分辨率负荷/PV 曲线 | 月度 PV/Load/Curtail 汇总 |
| 扫描维度 | 单变量（BESS 容量）二分 | 双变量（BESS × 购电价）网格 |
| 适用阶段 | 详细设计 | 前期可研/敏感性分析 |

---

## 第三组：Wind+BESS 容量规划

**共同场景**：给定风电装机，搜索满足消纳约束的储能容量。结构与第二组高度对称。

### `wind_bess_planner.py`

**功能/算法**：与 `pv_bess_planner.py` **几乎完全对称**，仅把 `pv_kw` 换成 `wind_kw`。同样支持纯弃电搬运 / 平移充电两种模式，二分搜索，充放分离效率。差异仅在数据对齐时风电单位为 MW（需 ×1000 转 kW）。

### `wind_bess_irr_planner.py`

**功能**：与 `pv_bess_irr_planner.py` 对称，轮巡储能容量 × 购电电价，计算风储整体 IRR。采用**两段式收益模型**（风电直供 → 储能平移弃风补缺，余电上网极少触发）。

**与 pv_bess_irr 的差异**：光储三段式（自用→平移→上网），风储退化为两段（风电通常 < 负荷，无大量余电上网）。保留第三段（余电上网）作为与光储对称的出口，默认 `max_export_ratio=0.20` 下几乎不生效。

**核心入口**：`scan_wind_bess_irr()`，返回 `WindBESSIRRResult`（scan_df + delta_df + best）。

---

## 第四组：Wind+PV+BESS 三要素联合规划

**共同场景**：风、光、储三个决策变量联合优化。三个脚本代表三种不同的决策目标。

### `wind_pv_bess_capacity_optimizer.py` — 最低成本优化器

**功能**：网格搜索 + 细扫两阶段，寻找满足绿电比例和自用率约束的**最低投资成本**组合。

**算法流程**：

```
1. 粗扫：wind × pv × ess 三维网格（步长 10MW / 10MW / 20MWh）
   └── 快速剪枝：wind_unit_sum·w + pv_unit_sum·p < green_threshold 则跳过
2. 细扫：在粗扫最优解 ±30% 范围内，步长缩至 1MW / 1MW / 2MWh
3. 返回最低 cost 的可行解
```

**调度引擎**：自带 `_simulate_op()`（内联贪心，与 `dispatch_annual` 逻辑一致但独立实现），未调用 `models/dispatch_algo.py`。

**约束**：`green_ratio ≥ green_ratio_min`（绿电占负荷比）、`self_use_ratio ≥ self_use_ratio_min`（绿电自用率）。

**成本函数**：`cost = wind·元/kW + pv·元/kWp + ess·元/kWh`（万元）。

### `wind_pv_bess_capacity_planner.py` — 最小储能规划器

**功能**：给定风/光装机，搜索满足自用率和负荷覆盖率的**最小储能容量**。本质是 optimizer 的退化版（风/光固定，只搜储能）。

**调度引擎**：自带 `_dispatch_numba()`（Numba JIT，独立实现，逻辑同 `dispatch_annual`）。

**搜索**：线性扫描 `linspace(0, batt_hi_max, search_points)`，取第一个满足约束的容量（非二分，因为目标是最小容量而非最小成本）。

**与 optimizer 的区别**：

| 维度 | capacity_optimizer | capacity_planner |
|------|-------------------|-----------------|
| 决策变量 | wind + pv + ess 三维 | 仅 ess（风/光给定） |
| 搜索方式 | 粗扫 + 细扫两阶段 | 线性扫描 |
| 目标 | min cost（满足约束） | min bess_kwh（满足约束） |
| 约束 | green_ratio + self_use_ratio | self_use_ratio + load_cover_ratio |
| 调度引擎 | 内联 `_simulate_op` | 内联 `_dispatch_numba` |

### `wind_pv_bess_irr_planner.py` — IRR 目标型规划

**功能**：在业主综合电价上限给定的前提下，反推风/光/储的最优容量配比，使项目 IRR 达到目标值。

**算法定位**（与同组两个的区别）：

| 规划器 | 优化目标 | 决策方向 |
|--------|---------|---------|
| capacity_optimizer | 满足消纳约束的最低成本 | 三维 min cost |
| capacity_planner | 满足约束的最小储能 | 给定风/光反求 BESS |
| **irr_planner** | 满足 IRR 目标的可行配比 | 三维联合 + 电价反推 |

**核心数学模型**：

1. **物理层**：调用 `models/dispatch_algo.py::dispatch_annual()`（**唯一使用共享引擎的脚本**），对每个候选 (w, pv, b) 做 105,120 步逐时步仿真，得到 `ren_gen_kwh` / `ren_used_kwh` / `load_kwh` / `curtail_kwh`。

2. **经济层 — 绿电结算价反推**：

$$
P_{green} = \frac{P_{owner} \cdot L - P_{grid} \cdot (L - L_{green})}{L_{green}}
$$

含义：业主全年总电费固定为 $P_{owner} \cdot L$，绿电部分按 $P_{green}$ 结算，缺口按电网价 $P_{grid}$ 买。$P_{green} \le 0$ 则项目无收入。

3. **IRR 解算**：现金流 `[-CAPEX, annual_cf × life_years]`，`annual_cf = revenue - OPEX`，用 `compute_irr()` 求解。

**搜索空间**：wind × pv × bess 三维网格（默认 28 × 14 × 100 = 39,200 候选），逐个评估，筛出 IRR ∈ [target - tol, target + tol] 的可行解，取投资规模最大者。

**详细数学推导**见下方 [§8.3 算法原理](#83-算法原理数学建模)（现有 README 完整保留）。

---

## 辅助模块

### `feasibility_analyzer.py` — 储能可行性评估

**定位**：MILP 优化前的**前置筛选**。不建储能模型，仅用统计特征评分。

**匹配性评分公式**：

```
score = max(0, corr_high_price_load) × 0.4      # 高电价与高负荷正相关
      + max(0, -corr_low_price_load) × 0.2      # 低电价与低负荷负相关
      + charge_feasibility × 0.2                 # 低价时段变压器有余量
      + strategy_executability × 0.2             # 可充窗口占比
```

**策略推荐**：score ≥ 0.75 → 中大容量；0.55-0.75 → 中等；0.35-0.55 → 小容量高功率；< 0.35 → 不建议。

### `multi_node_scanner.py` — 多节点容量扫描

**功能**：对多个电价节点用 PuLP MILP 求解最优套利调度，扫描容量区间计算 IRR 和全寿命经济指标。支持容量线性衰减、循环次数约束、负电价处理。

### `wind_pv_bess_irr_tuning.py` — 资源场景敏感性

**功能**：遍历不同风/光资源场景（不同年份的气象数据），对每个场景运行 `plan_wind_pv_bess_for_target_irr`，评估最优解对资源波动的鲁棒性。

---

## 跨组对比与选型指南

### 按场景选型

| 你的场景 | 推荐模块 | 理由 |
|---------|---------|------|
| 工业园区多变压器，各节点配多少储能 | `bess_capacity_distributed_planner` | 唯一支持多节点拓扑 |
| 单节点，要最优容量 + 策略联合解 | `bess_capacity_economic_planner` | MILP 全局最优 |
| 单节点，给定容量想看运营收益曲线 | `bess_capacity_operating_planner` | CVXPY + 仿真回放 |
| 只有月度数据，快速看光储 IRR | `pv_bess_irr_planner` | 三段式公式，无需时序 |
| 有 PV 曲线，求最小储能满足自用率 | `pv_bess_planner` | 二分搜索，支持平移充电 |
| 有风电曲线，求最小储能 | `wind_bess_planner` | 与 pv_bess 对称 |
| 风+光+储三维，求最低投资 | `wind_pv_bess_capacity_optimizer` | 两阶段网格搜索 |
| 风+光固定，只求最小储能 | `wind_pv_bess_capacity_planner` | 线性扫描 |
| 业主要求 IRR 达标，反推配比 | `wind_pv_bess_irr_planner` | 唯一支持电价反推 |

### 按调度引擎分组

| 引擎 | 使用者 | 效率模型 |
|------|-------|---------|
| `dispatch_annual`（共享贪心） | `wind_pv_bess_planner`, `wind_pv_bess_irr_planner` | 对称开方 √η_rt |
| `resource_bess_planner_core`（单源贪心） | `pv_bess_planner`, `wind_bess_planner`（薄包装） | 充放分离 η_c/η_d |
| 内联贪心（各 planner 自带） | `wind_pv_bess_capacity_optimizer`, `wind_pv_bess_capacity_planner` | 对称开方或充放分离 |
| LP/MILP（PuLP） | `bess_capacity_economic_planner`, `multi_node_scanner`, `bess_capacity_distributed_planner` | 充放分离 |
| CVXPY | `bess_capacity_operating_planner` | 充放分离 |
| 三段式/两段式公式（无调度） | `pv_bess_irr_planner`, `wind_bess_irr_planner` | 不涉及 |
| `EssSimulationModel`（回放） | `bess_capacity_operating_planner`（验证环节） | 充放分离 |

> **⚠️ 已知一致性问题**：不同脚本对充放效率的建模不统一（对称开方 vs 充放分离）。若用 `dispatch_annual` 生成策略后再用 `EssSimulationModel` 回放，结果会因效率建模差异不闭合。跨脚本串联时需注意。

---

## 子模块索引

| 文件 | 组 | 核心入口 | 状态 |
|------|---|---------|------|
| `bess_capacity_distributed_planner.py` | 1 | `run_capacity_search()`, `optimize_combo()` | ✅ |
| `bess_capacity_economic_planner.py` | 1 | `solve_capacity_sizing()` | ✅ |
| `bess_capacity_operating_planner.py` | 1 | `plan_energy_system()` | ✅ |
| `pv_bess_planner.py` | 2 | `plan_pv_bess_system()` | ✅ |
| `pv_bess_irr_planner.py` | 2 | `scan_pv_bess_irr()` | ✅ |
| `wind_bess_planner.py` | 3 | `plan_wind_bess_system()` | ✅ |
| `wind_bess_irr_planner.py` | 3 | `scan_wind_bess_irr()` | ✅ |
| `wind_pv_bess_capacity_optimizer.py` | 4 | `CapacityOptimizer.optimize()` | ✅ |
| `wind_pv_bess_capacity_planner.py` | 4 | `plan_energy_system()` | ✅ |
| `wind_pv_bess_irr_planner.py` | 4 | `plan_wind_pv_bess_for_target_irr()` | ✅ |
| `feasibility_analyzer.py` | 辅助 | `BESSFeasibilityAnalyzer.analyze()` | ✅ |
| `multi_node_scanner.py` | 辅助 | `scan_multiple_nodes()` | ✅ |
| `wind_pv_bess_irr_tuning.py` | 辅助 | `run_wind_pv_bess_irr_resource_tuning()` | ✅ |
| `models/dispatch_algo.py` | 引擎 | `dispatch_annual()` | ✅ |
| `models/resource_bess_planner_core.py` | 引擎 | `simulate_dispatch()`, `find_min_capacity_bisect()` | ✅ |
| `models/simulation_model.py` | 引擎 | `EssSimulationModel` | ✅ |

---

## 8.3 算法原理（数学建模）

> 以下为 `wind_pv_bess_irr_planner.py` 的完整数学推导，从物理层调度到经济层 IRR 解算。

### 8.3.1 决策变量与搜索空间

决策变量为风电装机 $w$、光伏装机 $pv$、储能容量 $b$（单位 MW、MWh）：

$$
(w,\ pv,\ b) \in [w_{\min}, w_{\max}] \times [pv_{\min}, pv_{\max}] \times [b_{\min}, b_{\max}]
$$

按配置默认步长（`wind_step_mw=10`、`pv_step_mw=10`、`bess_step_mwh=10`），搜索空间为：

$$
N_{cand} = 28 \times 14 \times 100 = 39{,}200 \text{ 候选}
$$

### 8.3.2 物理层：BESS 贪心调度（逐时步仿真）

对每个候选 $(w, pv, b)$，先把单位出力曲线按装机缩放，再做 105,120 步逐时步仿真。设时步长 $\Delta t$（小时），$L_t$ 为负荷功率（kW），新能源发电为：

$$
G_t = w \cdot 1000 \cdot \tilde{W}_t + pv \cdot 1000 \cdot \tilde{P}_t \quad \text{(kW)}
$$

其中 $\tilde{W}_t, \tilde{P}_t \in [0, 1]$ 为单位出力曲线。

定义直供、盈余、缺口：

$$
D_t = \min(L_t, G_t),\quad S_t = \max(G_t - L_t, 0),\quad D'_t = \max(L_t - G_t, 0)
$$

**充放电决策**（Numba JIT，`_dispatch_annual_numba`）：

$$
p_t^{ch} = \min\!\left(S_t,\ P_{\max},\ \frac{soc_{\max} - soc_{t-1}}{\eta_c \cdot \Delta t}\right)
$$

$$
p_t^{dis} = \min\!\left(D'_t,\ P_{\max},\ \frac{(soc_{t-1} - soc_{\min}) \cdot \eta_d}{\Delta t}\right)
$$

**充电 $p_t^{ch}$ 三约束详解**：

- $S_t$：**原料约束**，当前时刻新能源盈余功率（kW），即「可以充的电」的上限。$S_t = 0$ 时无电可充。
- $P_{\max}$：**功率约束**，储能系统的额定充放电功率（kW），由 C-Rate 决定：$P_{\max} = c\_rate \times E$。$c\_rate = 0.5$ 表示 2 小时电池（0.5C），例如 $E = 100$ MWh 时 $P_{\max} = 50$ MW。
- $\frac{soc_{\max} - soc_{t-1}}{\eta_c \cdot \Delta t}$：**SOC 上限约束**反推的充电功率上限。物理意义：要在 $\Delta t$ 时段内把 SOC 从 $soc_{t-1}$ 充到 $soc_{\max}$，需要**净存入** $(soc_{\max} - soc_{t-1})$ kWh 能量；但因充电效率 $\eta_c < 1$，从电源侧实际需抽取 $\frac{soc_{\max} - soc_{t-1}}{\eta_c}$ kWh（多抽以补偿损耗），换算成功率即为此项。
- $\min(\cdot)$：三约束取最小者，保证「原料不超、功率不越、SOC 不破」三者**同时成立**。

**放电 $p_t^{dis}$ 三约束详解**：

- $D'_t$：**需求约束**，当前时刻新能源不足以覆盖负荷的缺口（kW），即「需要放出的电」的需求上限。
- $P_{\max}$：同充电侧的功率约束，充/放电共用同一额定功率。
- $\frac{(soc_{t-1} - soc_{\min}) \cdot \eta_d}{\Delta t}$：**SOC 下限约束**反推的放电功率上限。物理意义：电池内可释放能量为 $(soc_{t-1} - soc_{\min})$ kWh；但因放电效率 $\eta_d < 1$，这部分能量经过电池内部转换后实际能送到负荷端的只有 $(soc_{t-1} - soc_{\min}) \cdot \eta_d$ kWh；按 $\Delta t$ 折算为功率即为此项。
- $\min(\cdot)$：同充电，取三者最小。

**约束触发条件**（代码中的隐式开关，公式外另加的布尔判断）：

```python
# 充电执行条件：(S_t > 0) ∧ (soc < soc_max) ∧ (can_charge)
# 放电执行条件：(D'_t > 0) ∧ (soc > soc_min) ∧ (can_discharge)
# can_charge / can_discharge 由 switch_gap_steps 决定
#   (上次是放电则需等 switch_gap_steps 步才能转充电，反之亦然)
```

**SOC 演化**：

$$
soc_t = soc_{t-1} + \eta_c \cdot p_t^{ch} \cdot \Delta t - \frac{p_t^{dis} \cdot \Delta t}{\eta_d}
$$

**逐项物理意义**：

- **充电项** $\eta_c \cdot p_t^{ch} \cdot \Delta t$：从电源侧以功率 $p_t^{ch}$ 持续 $\Delta t$ 小时，进入电池端的总能量为 $p_t^{ch} \cdot \Delta t$，扣去充电损耗 $(1 - \eta_c) \cdot p_t^{ch} \cdot \Delta t$（转化为热），**净存入电池** $\eta_c \cdot p_t^{ch} \cdot \Delta t$。所以 $\eta_c$ **乘在「入口侧」**。
- **放电项** $\frac{p_t^{dis} \cdot \Delta t}{\eta_d}$：要向负荷端交付 $p_t^{dis} \cdot \Delta t$（出口侧）的能量，由于放电效率 $\eta_d < 1$，电池必须**实际释放** $\frac{p_t^{dis} \cdot \Delta t}{\eta_d}$ 才能在出口端得到目标值。所以 $\eta_d$ **除在「出口侧」**。
- **位置不对称性**：充电的 $\eta_c$ 乘在加项、放电的 $\eta_d$ 除在减项——**这种不对称正反映物理过程的不对称**：充电时入口端多抽、出口端少存；放电时入口端多放、出口端少送。损耗在能量传递路径上单向发生，因此「入口」和「出口」使用了不同的 $\eta$ 位置。
- **时间步长** $\Delta t$：本仿真采用真实数据时间步长，5min 步长对应 $\Delta t = 1/12 \approx 0.0833$ 小时；小时级仿真对应 $\Delta t = 1$ 小时。

**约束不变量**（每步自动满足）：

$$
soc_{\min} \le soc_t \le soc_{\max}, \quad \forall t
$$

由充电/放电决策中的 $soc$ 边界项保证——一旦 $soc_{t-1} = soc_{\max}$，充电项自动归零；一旦 $soc_{t-1} = soc_{\min}$，放电项自动归零。

**弃电**：

$$
curtail = \sum_{t=1}^{T} (S_t - p_t^{ch}) \cdot \Delta t
$$

**弃电发生的三个原因**（任一即触发）：

| 触发条件 | 物理现象 | 后果 |
|---|---|---|
| $soc_{t-1} = soc_{\max}$ | 电池已满 | $p_t^{ch} = 0$，$S_t$ 全部弃掉 |
| $P_{\max}$ 不够大 | 功率到顶 | $p_t^{ch} = P_{\max}$，$S_t - P_{\max}$ 部分弃掉 |
| $(soc_{\max} - soc_{t-1})$ 太小 | 剩余空间不足 | SOC 上限约束功率 < $S_t$，差额弃掉 |

**关键参数化**：
- 效率对称化：$\eta_c = \eta_d = \sqrt{\eta_{roundtrip}}$，默认 0.92 → 0.959
- 功率上限：$P_{\max} = c\_rate \cdot E$，默认 $c\_rate = 0.5$（2 小时电池）
- SOC 边界：$soc \in [0.1E,\ 1.0E]$
- 初始 SOC：$soc_0 = 0.5E$

### 8.3.3 经济层：电价反推与 IRR 解算

#### 绿电结算价反推

**核心假设**：业主综合用电价恒等于目标值 $P_{owner}$：

$$
P_{owner} \cdot L = P_{green} \cdot L_{green} + P_{grid} \cdot (L - L_{green})
$$

**反推公式**（由上式解 $P_{green}$）：

$$
P_{green} = \frac{P_{owner} \cdot L - P_{grid} \cdot (L - L_{green})}{L_{green}}
$$

**几何直觉**（设覆盖率 $\rho = L_{green}/L$）：

$$
P_{green} = P_{grid} - \frac{P_{grid} - P_{owner}}{\rho}
$$

**关键推论**：

1. $\rho$ 越小（覆盖率越低），$P_{green}$ 越低 → 项目收入越少
2. 当 $\rho \to 1$（全自用），$P_{green} \to P_{owner}$，与电网电价无关
3. 当 $P_{green} \le 0$：项目无收入 → **PPA 约束失败**
4. 给定 $P_{grid} = 0.36$、$P_{owner} = 0.32$，覆盖率每提升 10%，$P_{green}$ 提高约 0.011 元/kWh

#### IRR 解算

现金流序列：

$$
\text{cashflows} = [-\text{CAPEX},\ \underbrace{\text{annual\_cf},\ \ldots,\ \text{annual\_cf}}_{\text{life\_years 年}}]
$$

其中：

$$
\text{annual\_cf} = P_{green} \cdot L_{green} - \text{OPEX}
$$

IRR 为使 NPV = 0 的折现率 $r^*$：

$$
\sum_{t=0}^{T} \frac{\text{cashflows}_t}{(1+r^*)^t} = 0
$$

候选解需满足 $|r^* - \text{target\_irr}| \le \text{irr\_tolerance}$。
