# Capacity Planning 容量规划模块

## 模块概述

本模块提供新能源+储能系统的容量规划与优化算法，支持风电、光伏、储能（BESS）的多种组合场景。主要功能包括：

- **可行性评估**：前置筛选，评估项目是否值得投资
- **容量搜索**：寻找满足约束的最优容量组合
- **调度优化**：基于 MILP/CVXPY 的最优充放电策略
- **经济评估**：IRR、全寿命收益等财务指标计算

## 算法架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          容量规划模块架构                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │ 可行性分析    │    │  容量搜索     │    │  经济评估     │              │
│  │              │    │              │    │              │              │
│  │ • 电价分析   │    │ • 网格搜索   │    │ • IRR 计算   │              │
│  │ • 负荷分析   │    │ • 二分搜索   │    │ • 全寿命收益  │              │
│  │ • 匹配评分   │    │ • 粗扫+细扫  │    │ • CAPEX/OPEX │              │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘              │
│         │                   │                   │                      │
│         └───────────────────┼───────────────────┘                      │
│                             │                                          │
│                    ┌────────▼────────┐                                 │
│                    │   调度仿真引擎   │                                 │
│                    │                 │                                 │
│                    │ • 贪心调度      │                                 │
│                    │ • Numba JIT     │                                 │
│                    │ • MILP 优化     │                                 │
│                    │ • CVXPY 求解    │                                 │
│                    └─────────────────┘                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

## 子模块详解

### 1. feasibility_analyzer.py - 储能可行性评估

**功能**：在 MILP 优化之前运行，作为前置筛选步骤。

**算法特点**：
- 电价-负荷-变压器匹配性评分（0~1）
- 基于统计特征的策略推荐
- 支持电价、负荷、变压器容量敏感性分析

**核心指标**：
- `PriceAnalysis`：电价均值、标准差、峰谷价差、偏度、峰度
- `LoadAnalysis`：负荷峰值、峰谷比、偏度、峰度
- `TransformerAnalysis`：剩余容量、充电窗口时长
- `MatchingAnalysis`：高/低电价负荷相关性、策略可执行性

**匹配性评分公式**：
```
score = max(0, corr_high) * 0.4
      + max(0, -corr_low) * 0.2
      + charge_feasibility * 0.2
      + strategy_executability * 0.2
```

**策略推荐逻辑**：
| 评分区间 | 推荐策略 |
|---------|---------|
| ≥ 0.75 | 中大容量（削峰+套利） |
| 0.55-0.75 | 中等容量，需优化策略 |
| 0.35-0.55 | 小容量高功率（削峰型） |
| < 0.35 | 不建议建设储能 |

**实现状态**：✅ 完整实现

---

### 2. multi_node_scanner.py - 多节点容量扫描

**功能**：对多个电价节点进行容量轮巡扫描，计算 IRR 和全寿命经济指标。

**算法特点**：
- 基于 PuLP MILP 求解最优套利调度
- 容量衰减模型（线性衰减至 `capacity_end_ratio`）
- 支持最大循环次数约束
- 支持负电价处理

**配置参数**：
```python
BESSSizingConfig:
    life_years: int = 10           # 项目寿命
    life_cycles: int = 4000        # 设计循环次数
    dod: float = 0.9               # 放电深度
    capex_per_kwh: float = 1500    # 单位容量投资（元/kWh）
    cap_min_mwh: float = 50        # 最小扫描容量
    cap_max_mwh: float = 200       # 最大扫描容量
    cap_step_mwh: float = 10       # 扫描步长
```

**MILP 目标函数**：
```
max Σ(price[t] * discharge[t] * η_dis - (price[t] + grid_fee) * charge[t] / η_ch) * dt
```

**约束条件**：
- SOC 动态约束
- 充放电功率约束
- 放电价格阈值约束
- 循环次数约束
- 负电价禁止充电约束

**实现状态**：✅ 完整实现

---

### 3. pv_bess_irr_planner.py - 光储 IRR 扫描

**功能**：三段式收益模型，轮巡储能容量×购电电价，计算光储整体 IRR。

**三段式收益模型**：
1. **PV 自用**：`min(PV, Load) * buy_price`
2. **储能平移弃电**：`min(BESS, Curtail, load_after_PV) * buy_price`
3. **余电上网**：`min(PV_left, PV * max_export_ratio) * export_price`

**配置参数**：
```python
PVBESSIRRConfig:
    pv_capex_yuan: float = 2.3e9      # PV 总投资
    bess_capex_per_kwh: float = 800   # 储能单位投资（元/kWh）
    export_price_per_kwh: float = 0.285  # 上网电价
    max_export_ratio: float = 0.20    # 最大上网比例
    life_years: int = 20              # 项目寿命
```

**输出指标**：
- `PVBESSIRRRow`：储能容量、购电价、年收益、年电量、O&M、现金流、IRR
- `DeltaIRRRow`：相邻容量的 IRR 变化（边际效益分析）

**实现状态**：✅ 完整实现

---

### 4. wind_pv_bess_capacity_optimizer.py - 风光储容量优化

**功能**：网格搜索+细扫两阶段优化，寻找最低成本的风光储容量组合。

**算法流程**：
1. **粗扫**：大步长网格搜索，快速定位可行区域
2. **细扫**：在粗扫最优解附近小步长精细搜索
3. **快速剪枝**：检查绿电比例约束的必要条件

**约束条件**：
- `green_ratio >= green_ratio_min`：绿电比例
- `self_use_ratio >= self_use_ratio_min`：自用率

**成本计算**：
```python
cost = wind_mw * 1000 * wind_yuan_per_kw / 10000  # 万元
     + pv_mw * 1000 * pv_yuan_per_kw / 10000
     + ess_mwh * 1000 * ess_yuan_per_kwh / 10000
```

**辅助函数**：
- `simple_energy_sanity_check()`：用固定年利用小时估算 PV 需求下界
- `curve_based_energy_check()`：用实际 PV 曲线估算 PV 需求

**实现状态**：✅ 完整实现

---

### 5. wind_pv_bess_capacity_planner.py - BESS 容量规划

**功能**：离网风光储容量规划，搜索满足约束的最小储能容量。

**算法特点**：
- 线性搜索（可配置搜索点数）
- 支持 Numba JIT 加速
- 自动检测时间步长

**约束条件**：
- `self_use_ratio >= self_use_ratio_min`：新能源自用率
- `load_cover_ratio >= load_cover_ratio_min`：负荷覆盖率

**调度逻辑**：
```
surplus > 0 且 SOC < SOC_max → 充电
deficit > 0 且 SOC > SOC_min → 放电
```

**实现状态**：✅ 完整实现

---

### 6. wind_bess_planner.py - Wind+BESS 容量规划

**功能**：支持两种调度模式的风电+储能容量规划。

**调度模式**：
1. **纯弃电搬运模式** (`enable_shift=False`)：
   - surplus (W > L) → 充电
   - deficit (L > W) → 放电

2. **平移充电模式** (`enable_shift=True`)：
   - 允许 Wind < Load 时抽取部分风电充电
   - 通过 lookahead 预判未来缺口
   - `shift_max_frac_of_wind`：最大平移比例

**搜索算法**：
- **二分搜索**：比线性搜索更快
- **可达性检查**：用极大容量测试物理上是否可达
- **可行性判断**：检查绿电自用率和负荷覆盖率

**配置参数**：
```python
WindBESSPlanConfig:
    eta_charge: float = 0.92       # 充电效率
    eta_discharge: float = 0.92    # 放电效率
    c_rate: float = 1.0            # 倍率
    min_green_self_consumption: float = 0.60
    min_load_coverage: float = 0.30
    tol_mwh: float = 0.1           # 二分搜索精度
    shift_policy: ShiftPolicy      # 平移策略
```

**辅助功能**：
- `quick_feasibility_diagnose()`：快速诊断能量比、富余能量比例
- `check_feasibility_upper_bound()`：极大容量测试
- `calc_monthly_wind_metrics()`：月度风电消纳统计
- `plot_capacity_curve()`：容量响应曲线可视化

**实现状态**：✅ 完整实现

---

### 7. wind_pv_bess_planner.py - Wind+PV+BESS 容量规划

**功能**：风光储三要素联合容量规划，PV 粗扫+细扫两阶段搜索 + BESS 二分搜索。

**算法流程**：
```
1. 能量门槛检查（gate check）
   └── 风+光年发电量/用电量 >= target_ratio

2. PV 粗扫
   └── 遍历 pv_min_kwp 到 pv_max_kwp，步长 pv_step_coarse_kwp

3. 对每个 PV 候选值
   └── BESS 二分搜索找最小容量

4. PV 细扫（可选）
   └── 在粗扫最优解附近精细搜索

5. 输出最优组合
```

**能量门槛检查**：
```python
gen_ratio = (wind_kwh + pv_kwh + other_kwh) / load_kwh
if gen_ratio < gate_target_ratio:
    return "gate_failed"
```

**Numba JIT 加速**：
- `_dispatch_annual_numba()`：贪心调度核心函数
- 支持充放切换间隔（`switch_gap_steps`）
- 自动回退到 Python 实现

**配置参数**：
```python
WindPVBESSPlanConfig:
    # 成本
    pv_capex_yuan_per_kwp: float = 2000
    bess_capex_yuan_per_kwh: float = 1000
    # 储能参数
    eta_roundtrip: float = 0.92
    c_rate: float = 0.5
    # 约束
    self_use_ratio_min: float = 0.60
    load_cover_ratio_min: float = 0.20
    # PV 搜索
    pv_step_coarse_kwp: float = 2000
    pv_step_fine_kwp: float = 250
    pv_refine_window_kwp: float = 8000
    # BESS 搜索
    batt_bisect_iter: int = 26
    batt_tol_kwh: float = 1.0
    # 能量门槛
    enable_gate_check: bool = True
    gate_target_ratio: float = 0.30
```

**对外 API**：
- `plan_wind_pv_bess()`：主规划函数（PV 参与搜索）
- `evaluate_wind_pv_bess()`：评估固定 PV 方案
- `evaluate_fixed_wind_pv_bess_capacity()`：评估固定容量组合
- `energy_gate_check()`：能量门槛检查

**实现状态**：✅ 完整实现

---

### 8. wind_pv_bess_irr_planner.py - IRR 目标型规划

#### 8.1 算法定位与适用场景

**一句话定位**：在业主综合电价上限给定的前提下，反推「风电 + 光伏 + 储能」三要素的最优容量配比，使项目财务 IRR 达到目标值。

**与其他规划器的区别**：

| 规划器 | 优化目标 | 决策方向 |
|---|---|---|
| #4 `wind_pv_bess_capacity_optimizer` | 满足消纳/覆盖约束的最低成本 | 风/光/储组合成本最小 |
| #7 `wind_pv_bess_planner` | 满足约束的最小 BESS 容量 | 给定 PV 反求 BESS |
| **本模块 IRR 目标型** | 满足 IRR 目标 + 约束的最低投资 | 三维联合 + 电价反推 |

**适用场景**：业主自建/合建新能源项目，电价已锁定，需要在给定回报率下校核可建规模；或开发商需要回答「业主综合电价多少时项目可做」的反问题。

#### 8.2 算法测试运行流程

入口脚本：`app/run_wind_pv_bess_irr_planning.py`。端到端流程分为 6 步：

| 步骤 | 动作 | 输入 | 输出 | 典型耗时 |
|---|---|---|---|---|
| 1 | 加载负荷数据 | `data/profit_calc/wind_pv_bess/v1/demand_load.csv` | `df_load`（105,120 行，5min 步长，2023 全年） | < 1s |
| 2 | 构建/读取风电单位出力曲线 | `wind_simulation_v1` + Open-Meteo 气象数据 | `wind_unit` Series（8760 行，kW/MW） | 首次 ~30s（带气象下载），缓存命中 < 0.1s |
| 3 | 构建/读取光伏单位出力曲线 | `pv_simulation_v1` | `pv_unit` Series（105,120 行，kW/kWp） | 首次 ~3s，缓存命中 < 0.1s |
| 4 | 三维网格扫描 + BESS 调度仿真 | 三项 + `WindPVBESSIRRPlanConfig` | 候选列表 / 诊断表 | ~18s（Numba 加速） |
| 5 | 保存结果 | 扫描结果 | `results/wind_pv_bess_irr/optimal_solution.csv`、`diagnostics.csv` | < 1s |
| 6 | 日志输出关键指标 | 扫描结果 | 结构化日志 | < 0.1s |

**缓存机制**：
- 风电单位曲线缓存于 `wind_unit_curve.csv`（小时级）
- 光伏单位曲线缓存于 `pv_unit_curve.csv`（5min 级）
- 气象数据缓存于 `weather_cache.csv`（避免重复调用 Open-Meteo）
- 任一缓存命中即跳过仿真，仅做读 CSV → 索引对齐

**配置加载**：YAML 路径 `configs/wind_pv_bess_irr_planning.yaml`，通过 `_to_config()` 装配为 `WindPVBESSIRRPlanConfig` dataclass。

#### 8.3 算法原理（数学建模）

##### 8.3.1 决策变量与搜索空间

决策变量为风电装机 $w$、光伏装机 $pv$、储能容量 $b$（单位 MW、MWh）：

$$
(w,\ pv,\ b) \in [w_{\min}, w_{\max}] \times [pv_{\min}, pv_{\max}] \times [b_{\min}, b_{\max}]
$$

按配置默认步长（`wind_step_mw=10`、`pv_step_mw=10`、`bess_step_mwh=10`），搜索空间为：

$$
N_{cand} = 28 \times 14 \times 100 = 39{,}200 \text{ 候选}
$$

##### 8.3.2 物理层：BESS 贪心调度（逐时步仿真）

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

**逐项物理意义**：

- $(S_t - p_t^{ch})$：当前时刻 $t$ 的新能源盈余中，**没能存入电池的部分**（kW）。当 $p_t^{ch} < S_t$ 时差值为正，即为损失；当 $p_t^{ch} = S_t$ 时盈余全部消纳，弃电贡献为 0。
- $\max(\cdot, 0)$（代码中隐含）：**数值保护**，防止浮点误差下 $p_t^{ch}$ 略大于 $S_t$ 出现负弃电。
- $\sum_{t=1}^{T}$：全年 $T$ 步（$T = 105{,}120$）累加，得到**年弃电量**（kWh）。
- $\Delta t$：把每步的功率（kW）转换为能量（kWh）的换算因子。

**弃电发生的三个原因**（任一即触发）：

| 触发条件 | 物理现象 | 后果 |
|---|---|---|
| $soc_{t-1} = soc_{\max}$ | 电池已满 | $p_t^{ch} = 0$，$S_t$ 全部弃掉 |
| $P_{\max}$ 不够大 | 功率到顶 | $p_t^{ch} = P_{\max}$，$S_t - P_{\max}$ 部分弃掉 |
| $(soc_{\max} - soc_{t-1})$ 太小 | 剩余空间不足 | SOC 上限约束功率 < $S_t$，差额弃掉 |

**弃电量的经济意义**：在 IRR 模型中**不计收入**（既不售电网也不供业主），是新能源项目的「免费损失」。本规划通过 `curtail_kwh` 字段输出至 `diagnostics.csv`，可作为**是否扩容 BESS** 的判断依据：弃电率高时增大 $b$ 往往能摊薄单位投资。

**关键参数化**：
- 效率对称化：$\eta_c = \eta_d = \sqrt{\eta_{roundtrip}}$，默认 0.92 → 0.959
- 功率上限：$P_{\max} = c\_rate \cdot E$，默认 $c\_rate = 0.5$（2 小时电池）
- SOC 边界：$soc \in [0.1E,\ 1.0E]$
- 初始 SOC：$soc_0 = 0.5E$

**年度统计量**：仿真返回 $\sum G_t \cdot \Delta t$（年发电）、$\sum (D_t + p_t^{dis} \cdot \Delta t)$（年绿电消纳）、$\sum L_t \cdot \Delta t$（年用电）、$curtail$（年弃电）。

##### 8.3.3 经济层：电价反推与 IRR 解算

###### 8.3.3.1 绿电结算价反推

**核心假设**：业主综合用电价恒等于目标值 $P_{owner}$（业主视角下的「不介意电源结构」）：

$$
P_{owner} \cdot L = P_{green} \cdot L_{green} + P_{grid} \cdot (L - L_{green})
$$

**公式的物理/业务含义**：

| 项 | 含义 | 业务侧 |
|---|---|---|
| $P_{owner} \cdot L$ | 业主全年总电费 | 业主视角 |
| $P_{green} \cdot L_{green}$ | 业主向新能源项目支付的绿电费 | 业主 → 项目 |
| $P_{grid} \cdot (L - L_{green})$ | 业主向电网支付的电费 | 业主 → 电网 |

等式含义：业主**不关心**电从哪里来，只关心**全年总电费**等于目标值。绿色电力对业主等价于「省下的电网电费」，这部分由项目通过 $P_{green}$ 回收。

**反推公式推导**（由上式解 $P_{green}$）：

$$
P_{green} = \frac{P_{owner} \cdot L - P_{grid} \cdot (L - L_{green})}{L_{green}}
$$

**逐项物理意义**：

- **分子第一项** $P_{owner} \cdot L$：业主全年愿意支付的总电费（市场约束 / 业主预算上限）
- **分子第二项** $P_{grid} \cdot (L - L_{green})$：如果这部分电从电网买，业主应支付的电费
- **分子差值**：业主**因自建新能源而省下的电费** = 新能源项目的"收入总盘"
- **除以 $L_{green}$**：把"省下的总盘"分摊到每度新能源电上 = $P_{green}$

**几何直觉**（设覆盖率 $\rho = L_{green}/L$，化简为单变量）：

$$
P_{green} = P_{grid} - \frac{P_{grid} - P_{owner}}{\rho}
$$

**关键推论**：

1. $\rho$ 越小（覆盖率越低），$P_{green}$ 越低 → 项目收入越少
2. 当 $\rho \to 1$（全自用），$P_{green} \to P_{owner}$，与电网电价无关
3. 当 $P_{green} \le 0$：意味着"省下的电费"为 0 或负 → 项目无收入或倒贴 → **L3 PPA 约束失败**
4. 给定 $P_{grid} = 0.36$、$P_{owner} = 0.32$，覆盖率每提升 10%，$P_{green}$ 提高约 0.011 元/kWh

**剥离绿电附加价得到 PPA 价**：

$$
P_{ppa} = P_{green} - P_{adder}
$$

**业务含义**：
- 绿电附加价 $P_{adder}$（默认 0.074 元/kWh）是国家/地方对绿色电力的环境价值补贴
- 业主**实际支付** $P_{green}$，但其中 $P_{adder}$ 部分代表环境价值归属，回流到新能源项目
- 新能源项目**实际收入口径** = $P_{ppa} \cdot L_{green}$
- 当 $P_{ppa} > 0$ 时项目方可覆盖成本 → L3 约束

###### 8.3.3.2 总投资与年现金流

**总投资**（一次性，建设期不考虑）：

$$
CAPEX = 10^3 \left(w \cdot c_w + pv \cdot c_{pv} + b \cdot c_b\right)
$$

**逐项解释**：

| 项 | 单位 | 量纲换算 | 经济含义 |
|---|---|---|---|
| $w \cdot c_w$ | MW × 元/kW = 1000 × 元 | $10^3$ 把 MW 转为 kW | 风电设备 + 安装 |
| $pv \cdot c_{pv}$ | MWp × 元/kWp = 1000 × 元 | $10^3$ 把 MWp 转为 kWp | 光伏组件 + 支架 + 逆变器 |
| $b \cdot c_b$ | MWh × 元/kWh = 1000 × 元 | $10^3$ 把 MWh 转为 kWh | 电池 + PCS + BMS |

**简化假设**：建设期一次性投入，无分期付款；不考虑建设期利息（用 $r$ 反映时间价值）；期末残值为 0。

**年现金流**：

$$
CF_{annual} = \underbrace{P_{green} \cdot L_{green}}_{\text{年收入}} - \underbrace{\alpha \cdot CAPEX}_{\text{年运维}}
$$

| 项 | 符号 | 性质 | 备注 |
|---|---|---|---|
| $P_{green} \cdot L_{green}$ | $+$ | 流入 | 来自业主的绿电费 |
| $\alpha \cdot CAPEX$ | $-$ | 流出 | 运维、检修、保险、人工、税费 |

**关键参数 $\alpha$**：默认 0.02（年运维 = 总投资 2%）。这是**简化建模**——把不规则的运维支出均摊为与 CAPEX 成正比的年金。

**为什么不折旧**：
- 本模型用**现金流法**（cash flow method），不是会计利润法
- 不扣折旧，但通过折现率 $r$ 反映资本时间价值
- 期末残值：本模型设为 0（不回收残值）

**为什么不交所得税**：
- 简化场景：税前 IRR（pre-tax IRR）
- 真实项目可在 $CF_{annual}$ 中再扣 $T \cdot (CF_{annual} - 折旧)$

###### 8.3.3.3 IRR 求解

**基本方程**（NPV = 0）：

$$
NPV(r) = \sum_{t=0}^{N} \frac{CF_t}{(1+r)^t} = 0
$$

$$
CF_0 = -CAPEX, \quad CF_{t \ge 1} = CF_{annual}
$$

**展开形式**：

$$
0 = -CAPEX + \frac{CF_{annual}}{1+r} + \frac{CF_{annual}}{(1+r)^2} + \cdots + \frac{CF_{annual}}{(1+r)^N}
$$

**几何级数求和**（等额年金）：

利用 $\sum_{t=1}^{N} x^t = x \cdot \frac{1 - x^N}{1 - x}$，令 $x = 1/(1+r)$：

$$
CAPEX = CF_{annual} \cdot \frac{1 - (1+r)^{-N}}{r}
$$

**资本回收系数**（Capital Recovery Factor, CRF）：

设 $k = CF_{annual} / CAPEX$（资本回收率，量纲 1/年），移项得：

$$
k = CRF(r, N) = \frac{r \cdot (1+r)^N}{(1+r)^N - 1}
$$

求解 $r$ 即为 IRR。**对一般 $k$ 值无解析解**，必须用数值方法。

**$N = 15$ 年线性年金下 $k$ 与 $r$ 的对应关系**（数值求解）：

| $k = CF_{annual}/CAPEX$ | IRR $r$ | 业务含义 |
|---|---|---|
| 0.0667（$= 1/15$） | $\to 0\%$ | 年金勉强覆盖投资（理论下界） |
| 0.070 | 0.62% | 微弱回收 |
| 0.080 | 2.37% | 较低回报 |
| 0.096 | 4.95% | 银行理财水平 |
| **0.117** | **8.02%** | **本项目目标 IRR** |
| 0.130 | 9.80% | 接近 10% |
| 0.150 | 12.40% | 较高回报 |
| 0.200 | 18.42% | 高回报 |
| 0.250 | 24.01% | 极高回报 |

> 验证：$k = 0.117$ 时 $r \approx 8\%$，与 $CRF(8\%, 15) = 11.68\%$ 一致（数值解与公式互证）。

**当前实跑验证**（125/140/5 候选）：
- $CAPEX = 11.225$ 亿、$CF_{annual} = 0.793$ 亿 → $k = 7.06\%$
- 查表内插：$r \approx 0.73\%$（与代码实测 IRR=0.0073 一致）
- **远低于**目标 8% 对应的 $k = 11.7\%$，故无解

###### 8.3.3.4 IRR 数值方法（二分法 Bisection）

**为什么用数值方法**：
- 等额年金下 $CRF(r, N) = k$ 涉及 $(1+r)^N$，无封闭解（Lambert W 函数可解但不实用）
- 不等额现金流序列：更无解析解
- 必须用迭代法

**本项目实现**（`ele_trading.evaluation.metrics.compute_irr`）：

**步骤 1：可行性检查**

```python
if not (has_negative and has_positive in cash_flows):
    return 0.0
```

- 现金流必须**既有正又有负**（典型：$CF_0 < 0$ + $CF_{t \ge 1} > 0$）
- 否则 NPV 永远不为 0 → 无 IRR → 返回 0.0

**步骤 2：动态区间搜索**

```python
low, high = -0.99, 1.0     # 初始 [-99%, 100%]
for _ in range(20):
    if npv(low) * npv(high) <= 0:
        break              # 找到异号区间，根存在
    high *= 10             # 否则 high *= 10
```

- 初始区间 $[-0.99, 1.0]$ 覆盖新能源项目典型 IRR（-99% ~ 100%）
- 若整个区间 $NPV$ 同号（说明根不在 $[-0.99, 1.0]$ 内），逐步扩大 $high$ 到 10、100、1000...
- 最多 20 次扩大 → $high$ 可达 $10^{20}$
- 仍无异号 → 无 IRR → 返回 0.0

**NPV 函数定义**：

$$
NPV(r) = \sum_{t=0}^{N} \frac{CF_t}{(1+r)^t}
$$

**步骤 3：二分法迭代**

```python
for _ in range(max_iter):  # max_iter=100
    mid = (low + high) / 2
    npv_mid = npv(mid)
    if abs(npv_mid) < tol:  # tol=1e-6
        return mid          # 收敛
    if npv_mid * npv_low < 0:
        high, npv_high = mid, npv_mid   # 根在 [low, mid]
    else:
        low, npv_low = mid, npv_mid     # 根在 [mid, high]
return (low + high) / 2  # 兜底：未收敛时取中点
```

**收敛原理**：
- $NPV(r)$ 在根附近单调（标准项目）
- 每次迭代区间折半：$[low, high]$ 长度 $\to 0$
- 100 次迭代后区间长度 $1.99 \times 2^{-100} \approx 1.5 \times 10^{-30}$，远低于 $10^{-6}$ 容差
- 实际收敛步数：约 21 步（$\log_2(1.99 / 10^{-6}) \approx 21$）

**单调性保证**：
- 对等额年金 + 一次性投资的「标准项目」，$NPV(r)$ 是 $r$ 的单调递减函数
- 对非典型项目（如末期大额残值），可能违反单调性，二分法可能失败
- 本项目 15 年线性年金 + 0 残值 = 标准场景，**保证收敛**

**数值方法的优劣对比**：

| 维度 | 二分法（采用） | 牛顿法 | 解析法 |
|---|---|---|---|
| 收敛速度 | 线性（~21 步） | 二次（~5 步） | — |
| 稳定性 | **高**（必收敛） | 中（需好初值） | — |
| 实现复杂度 | 低 | 中（需解析导数） | 高（无一般解） |
| 适用范围 | 任何 NPV 函数 | 需可导 | 等额年金 |
| 本项目选择 | ✅ | ❌ | ❌ |

**选择二分法的原因**：稳定性 > 速度。IRR 在规划算法中调用 ~39,200 次/次规划，21 步与 5 步的差异（~4×）远不如"必收敛"的鲁棒性重要。

**误差与精度**：
- 默认 $tol = 10^{-6}$，对应 IRR 精度 0.0001%（远超 0.2% 容差需求）
- $max\_iter = 100$ 远大于实际需求（~21 步），**无收敛失败风险**

**典型调用栈**：
1. `plan_wind_pv_bess_for_target_irr` 调用 39,200 次 `_evaluate_candidate`
2. 每次 `_evaluate_candidate` 调用 1 次 `compute_irr`
3. 每次 `compute_irr` 平均 ~21 步二分迭代
4. 每步 NPV 计算 N+1=16 次加法和除法
5. **总 NPV 评估** = 39,200 × 21 × 16 ≈ **13.2M 次浮点运算**

###### 8.3.3.5 简化与精度边界

| 简化项 | 实际情形 | 影响 |
|---|---|---|
| 15 年线性 $CF_{annual}$ | 首年运维低、末期高 | IRR 偏低估 0.1~0.3% |
| 期末残值为 0 | 设备残值 5~10% CAPEX | IRR 偏低估 0.3~0.5% |
| 税前模型 | 实际所得税率 25% | 税后 IRR 低 2~3% |
| 无衰减 | PV 年衰减 0.5%、BESS 容量 80%/10y | 第 10 年起发电少，IRR 低 |
| 单业主分时电价 | 实际分时电价 | 反推 $P_{green}$ 略偏 |
| 无风险溢价 | 实际含 1~2% 风险溢价 | 目标 IRR 应更高 |

**结论**：本模型 IRR 是**项目经济性边界估算**，精度量级 ±0.5%。如需精确到 ±0.1%，需引入衰减模型、分时电价、税收。

##### 8.3.4 四道过滤

| 层级 | 条件 | 不通过后果 |
|---|---|---|
| L1 物理存在 | $gen, used, load > 0$ | 直接丢弃（不进 diagnostics） |
| L2 消纳约束 | 自用率 $\ge 60\%$ ∧ 覆盖率 $\ge 35\%$ | 直接丢弃（不进 diagnostics） |
| L3 PPA 价格 | $P_{green} > 0$ ∧ $P_{ppa} > 0$ | 保留至 diagnostics（reason=`non_positive_ppa`） |
| L4 IRR 约束 | $\lvert IRR - r_{target} \rvert \le 0.2\%$ | 保留至 diagnostics（reason=`irr_out_of_tolerance`） |

**最优解选择**（两级排序）：

$$
\text{best} = \arg\min_{(w, pv, b)\ \text{passed}} \left(CAPEX,\ \lvert IRR - r_{target} \rvert \right)
$$

##### 8.3.5 算法复杂度

- **时间复杂度**：$O(N_{cand} \times T)$，本次实跑 $39{,}200 \times 105{,}120 \approx 4.1 \times 10^9$ 步。Numba JIT 加速后实测约 18 秒；无 Numba 时 Python 解释器下耗时约 100× 上升。
- **空间复杂度**：$O(T)$ 每候选（已展开为连续 numpy 数组，无副本复制）。
- **结果规模**：物理/PPA/IRR 任一不通过时仅写 `diagnostics.csv`；本实跑产生 17,258 行诊断记录。

#### 8.4 算法运行结果解读

针对本次实跑（默认配置）：

```
status=no_solution
message=未找到满足 PPA/IRR 约束的风光储组合
```

**最近候选**（IRR 差距最小）：

| 指标 | 值 |
|---|---|
| 装机 | 风 125MW + 光 140MW + 储 5MWh |
| 总投资 CAPEX | **11.225 亿元** |
| 年绿电消纳 | 4.121 亿 kWh |
| 年电网购电 | 7.540 亿 kWh |
| 自用率 / 覆盖率 | 94.3% / 35.3% |
| 反推绿电价 | 0.247 元/kWh |
| 年收入 / 年运维 / 年净 CF | 1.017 / 0.225 / **0.793 亿元** |
| **IRR** | **0.73%**（目标 8%，差距 7.27%） |

**为何无可行解**（数学推导）：

1. **覆盖率锁定绿电价格**：覆盖率上限 35.3% 决定 $L_{green}/L = 0.353$，代入电价反推式：

$$
P_{green} = 0.36 - \frac{0.36 - 0.32}{0.353} \approx 0.247\ \text{元/kWh} \ll 0.32
$$

2. **投资回收能力不足**：年净 CF / CAPEX = 7.06%。对 15 年线性年金，达成 8% IRR 所需的最低回收率为：

$$
r_{req} = \frac{r(1+r)^N}{(1+r)^N - 1} = \frac{0.08 \times 1.08^{15}}{1.08^{15} - 1} \approx 11.7\%
$$

3. **根本性矛盾**：业主综合电价上限 0.32 元/kWh < 电网电价 0.36 元/kWh，绿电消纳越多反而需要把 $P_{green}$ 压得越低（对业主的"让利"），最终落在 0.247 元/kWh 附近，远不足以让 11.225 亿投资在 15 年内回收到 8% IRR。

**调参建议**（如需找到可行解）：

| 调整方向 | 参数 | 建议值 |
|---|---|---|
| 提高业主电价 | `target_owner_price_yuan_per_kwh` | 0.36 ~ 0.40 元/kWh |
| 降低回报要求 | `target_irr` | 0.05（5%） |
| 提高绿电占比 | `load_cover_ratio_min` | ≥ 0.50 |
| 降低单位投资 | `wind/pv/bess_capex` | 跟随市场下行趋势调整 |

**结果文件位置**：
- `results/wind_pv_bess_irr/optimal_solution.csv` — 无解时**不生成**
- `results/wind_pv_bess_irr/diagnostics.csv` — 17,258 行，按 `irr_gap` 升序排列

#### 8.5 关键工程取舍

| 维度 | 现状 | 边界 |
|---|---|---|
| 调度策略 | 贪心逐时步 | 非全局最优（无前瞻窗口、无分时电价套利） |
| 搜索方式 | 三维等步长网格 | 步长 10 偏粗，无局部精修阶段 |
| 加速手段 | Numba JIT | 依赖 numba，否则 Python 慢 ~100× |
| 物理粒度 | 5min 步长 | 不模拟尾流、逆变器限功率、机型差异 |
| 业务范围 | 单业主电价上限 | 不支持多业主分账、跨节点套利 |
| 数据来源 | 缓存 + 真实 Open-Meteo 气象 | 风电仿真为统计模型，非真实机组 SCADA |

**算法本质**：是「项目经济性边界扫描」工具，**不是**「调度最优」工具。它回答的是「在业主综合电价约束下，哪种配比能让项目 IRR 达到目标」，而非「给定配比后如何最优调度」。两者精度需求不同：经济性边界只需近似电量平衡，而调度最优需要分时电价、设备约束的全链路建模。

#### 8.6 实现状态

✅ **完整实现**：含三维网格扫描、BESS 贪心调度（Numba）、电价反推、IRR 数值解、四级过滤、诊断表导出。

---

### 9. bess_capacity_sizer.py - 储能容量+调度联合优化

**功能**：MILP 联合优化储能容量与调度策略，最大化净套利收益。

**决策变量**：
- `Cap_rated`：额定容量
- `P_ch[t]`、`P_dis[t]`：充放电功率
- `E[t]`：SOC
- `u_ch[t]`、`u_dis[t]`：充放电状态（二进制）

**目标函数**：
```
max Σ(price[t] * P_dis[t] * dt) - Σ(price[t] * P_ch[t] * dt)
    - annualized_capex - opex
```

**年化 CAPEX 计算**：
```python
crf = r * (1+r)^n / ((1+r)^n - 1)  # 资本回收系数
annualized_capex = capex_per_kwh * Cap_rated * crf
```

**约束条件**：
- SOC 动态约束
- 充放电互斥约束
- 变压器容量约束
- 周期性 SOC 回归约束
- 充放电切换间隔约束
- 最小连续充放电时段约束
- 循环次数约束
- McCormick 包络松弛（可选）

**实现状态**：✅ 完整实现

---

### 10. distributed_bess_planner.py - 分布式储能测算

**功能**：多变压器公共母线下的分布式储能调度优化。

**数据类型**：输入输出与调度配置 dataclass 定义于本目录 `interfaces.py`（`DistBESSDispatchInput`、`DistBESSDispatchResult`、`DistBESSSchedulerConfig` 等），不再反向依赖 `optimization/interfaces.py`。

**拓扑结构**：
```
        ┌─────────────────────────────────────┐
        │            公共母线 (Park)            │
        └───┬─────────┬─────────┬─────────┬───┘
            │         │         │         │
        ┌───┴───┐ ┌───┴───┐ ┌───┴───┐ ┌───┴───┐
        │ 338_1 │ │ 338_2 │ │ 338_3 │ │ 342_1 │ ...
        │ 2000kVA│ │1600kVA│ │1600kVA│ │1250kVA│
        └───────┘ └───────┘ └───────┘ └───────┘
```

**预设配置**：

| 预设 | 求解器 | 网格公式 | 非负约束 | 放电模式 | 平滑惩罚 | 斜坡限制 |
|-----|--------|---------|---------|---------|---------|---------|
| v1 | LP | SUM_LOAD | 否 | price_type | 无 | 无 |
| v2 | LP | SUM_LOAD | 否 | price_type | 有 | 有 |
| v3 | LP | SUM_LOAD | 否 | price_type | 有 | 有 |
| v4 | LP | PARK_BASELINE | 是 | price_type | 有 | 有 |
| v5 | RULE_BASED | PARK_BASELINE | 是 | fixed_window | 无 | 无 |

**约束条件**：
- SOC 动态约束
- 充放电时间窗口约束
- 变压器容量约束
- 跨变压器功率流约束
- 斜坡率约束（可选）
- SOC 目标惩罚（可选）

**目标函数**：
```
min energy_cost + max_demand_cost + cross_flow_penalty + smooth_penalty + soc_target_penalty
```

**容量搜索模式**：
- `full_grid`：全网格搜索（支持并行）
- `max_capacity`：仅评估最小和最大容量
- `coordinate`：坐标下降法（贪心邻居搜索）

**柜数约束模式**：
- `CabinetEqualityMode.NONE`：无约束
- `CabinetEqualityMode.GLOBAL`：全局等柜数
- `CabinetEqualityMode.GROUP`：分组等柜数

**实现状态**：✅ 完整实现

---

### 11. pv_bess_planner.py - PV+BESS 容量规划

**功能**：光伏+储能系统容量规划，支持两种调度模式。

**调度模式**：
1. **纯弃电搬运模式** (`enable_shift=False`)：
   - surplus (PV > L) → 充电
   - deficit (L > PV) → 放电

2. **平移充电模式** (`enable_shift=True`)：
   - 允许 PV < Load 时抽取部分 PV 充电
   - 通过 lookahead 预判未来缺口
   - `shift_max_frac_of_pv`：最大平移比例

**搜索算法**：
- **二分搜索**：快速定位最小可行容量
- **可达性检查**：用极大容量测试物理上是否可达
- **可行性判断**：检查自用率和负荷覆盖率

**配置参数**：
```python
PVBESSPlanConfig:
    eta_charge: float = 0.92       # 充电效率
    eta_discharge: float = 0.92    # 放电效率
    c_rate: float = 1.0            # 倍率
    min_self_consumption: float = 0.60
    min_load_coverage: float = 0.30
    pv_capex_yuan_per_kwp: float = 2000    # PV 单位投资
    bess_capex_yuan_per_kwh: float = 1000  # 储能单位投资
    tol_mwh: float = 0.1           # 二分搜索精度
    shift_policy: ShiftPolicy      # 平移策略
```

**辅助功能**：
- `quick_feasibility_diagnose()`：快速诊断能量比、富余能量比例
- `check_feasibility_upper_bound()`：极大容量测试
- `calc_monthly_pv_metrics()`：月度光伏消纳统计
- `plot_capacity_curve()`：容量响应曲线可视化

**实现状态**：✅ 完整实现

---

## 算法实现程度评估

| 模块 | 实现状态 | 完整度 | 备注 |
|-----|---------|-------|------|
| feasibility_analyzer | ✅ | 100% | 完整实现，含敏感性分析 |
| multi_node_scanner | ✅ | 100% | 完整实现，含衰减模型 |
| pv_bess_irr_planner | ✅ | 100% | 完整实现，三段式收益模型 |
| pv_bess_planner | ✅ | 100% | 完整实现，含平移充电模式 |
| wind_pv_bess_capacity_optimizer | ✅ | 100% | 完整实现，两阶段搜索 |
| wind_pv_bess_capacity_planner | ✅ | 100% | 完整实现，含 Numba 加速 |
| wind_bess_planner | ✅ | 100% | 完整实现，含平移充电模式 |
| wind_pv_bess_planner | ✅ | 100% | 完整实现，含能量门槛检查 |
| wind_pv_bess_irr_planner | ✅ | 100% | 完整实现，IRR 目标型 |
| bess_capacity_sizer | ✅ | 100% | 完整实现，MILP 联合优化 |
| distributed_bess_planner | ✅ | 100% | 完整实现，含 v1-v5 预设 |

**总体评估**：模块实现完整度 **100%**（11个子模块全部实现）。

## 依赖关系

```
capacity_planning/
├── utils/
│   ├── time_index.py          # 时间索引处理
│   ├── data_alignment.py      # 数据对齐
│   ├── num_utils.py           # 数值工具
│   ├── demand_charge.py       # 需量电费计算
│   └── time_splitting.py      # 时间分割
└── evaluation/
    └── metrics.py             # IRR 计算等指标
```

## 外部依赖

- **numpy**：数值计算
- **pandas**：数据处理
- **pulp**：MILP 求解器
- **cvxpy**：凸优化求解器
- **numba**：JIT 编译加速（可选）
- **matplotlib**：可视化（可选）

## 使用示例

### 1. 可行性评估

```python
from ele_trading.capacity_planning import BESSFeasibilityAnalyzer, FeasibilityAnalyzerConfig

cfg = FeasibilityAnalyzerConfig(
    load_col="Load",
    price_col="Price",
    time_col="Time",
    transformer_kva=80000,
)
analyzer = BESSFeasibilityAnalyzer(cfg)
result = analyzer.analyze(df_price, df_load)

print(f"匹配性评分: {result.matching.score:.2f}")
print(f"推荐策略: {result.strategy.description}")
```

### 2. Wind+BESS 容量规划

```python
from ele_trading.capacity_planning import plan_wind_bess_system, WindBESSPlanConfig

cfg = WindBESSPlanConfig(
    min_green_self_consumption=0.60,
    min_load_coverage=0.30,
    capex_cny_per_kwh=1000,
)
result = plan_wind_bess_system(df_load, wind_input, cfg)

print(f"可行: {result.feasible}")
print(f"容量: {result.capacity_mwh:.1f} MWh")
print(f"成本: {result.cost_cny/1e4:.1f} 万元")
```

### 3. Wind+PV+BESS 容量规划

```python
from ele_trading.capacity_planning import plan_wind_pv_bess, WindPVBESSPlanConfig

cfg = WindPVBESSPlanConfig(
    self_use_ratio_min=0.60,
    load_cover_ratio_min=0.20,
    gate_target_ratio=0.30,
)
result = plan_wind_pv_bess(df_load, pv_unit_kw, wind_input, cfg)

print(f"状态: {result.status}")
print(f"PV: {result.pv_kwp/1e3:.1f} MWp")
print(f"BESS: {result.bess_kwh/1e3:.1f} MWh")
print(f"总投资: {result.total_capex_yuan/1e4:.1f} 万元")
```

### 4. 分布式储能测算

```python
from ele_trading.capacity_planning import run_dist_bess_dispatch, DistBESSDispatchInput

input_data = DistBESSDispatchInput(
    base_dir="/path/to/data",
    start_time=datetime(2024, 1, 1),
    end_time=datetime(2024, 12, 31),
    max_demand_price=40.0,
    freq_minutes=15,
    search_mode="coordinate",
    system_name="park",
    preset="v4",
)
result = run_dist_bess_dispatch(input_data)

print(f"最优收益: {result.best_revenue:.2f} 元")
print(f"储能柜数: {result.best_total_cabinets}")
```

## 注意事项

1. **数据格式要求**：
   - 时间列必须是 datetime 格式
   - 功率单位需要明确指定（kW/MW/GW）
   - 电价单位需要明确指定（元/kWh 或 元/MWh）

2. **性能优化**：
   - 安装 numba 可显著加速调度仿真
   - 分布式储能测算支持 `workers` 参数并行计算
   - 大规模搜索建议使用 `coordinate` 模式

3. **约束松弛**：
   - 如果找不到可行解，尝试放松约束阈值
   - 检查能量门槛是否通过
   - 增大搜索范围上限

4. **IRR 计算**：
   - 依赖 `evaluation.metrics.compute_irr` 函数
   - 需要确保现金流序列正确（首期为负的投资）

## 待改进项

1. **搜索效率**：部分模块使用线性搜索，可考虑改用二分或梯度方法
2. **不确定性**：当前算法为确定性优化，可考虑加入鲁棒优化或随机规划
3. **多目标**：当前主要优化成本/收益，可考虑加入碳排放等多目标
