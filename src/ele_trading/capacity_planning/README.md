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

### 4. capacity_optimizer.py - 风光储容量优化

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

### 5. bess_capacity_planner.py - BESS 容量规划

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

**功能**：扫描风光储容量组合，寻找满足 IRR 目标的最低投资方案。

**算法特点**：
- 三维网格搜索（Wind × PV × BESS）
- PPA 电价反推
- IRR 容差约束

**电价模型**：
```python
green_price = (target_owner_price * load - grid_buy_price * grid_buy_kwh) / used
ppa_price = green_price - green_price_adder
owner_avg_price = (green_price * used + grid_buy_price * grid_buy_kwh) / load
```

**IRR 计算**：
```python
total_capex = wind_capex + pv_capex + bess_capex
annual_revenue = green_price * annual_green_used_kwh
annual_opex = total_capex * annual_opex_ratio
annual_cashflow = annual_revenue - annual_opex
irr = compute_irr([-total_capex] + [annual_cashflow] * life_years)
```

**筛选逻辑**：
1. 物理可行性：自用率、覆盖率约束
2. 经济可行性：PPA 电价 > 0
3. IRR 约束：|irr - target_irr| <= irr_tolerance

**实现状态**：✅ 完整实现

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

### 10. dist_bess_dispatch.py - 分布式储能测算

**功能**：多变压器公共母线下的分布式储能调度优化。

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
| capacity_optimizer | ✅ | 100% | 完整实现，两阶段搜索 |
| bess_capacity_planner | ✅ | 100% | 完整实现，含 Numba 加速 |
| wind_bess_planner | ✅ | 100% | 完整实现，含平移充电模式 |
| wind_pv_bess_planner | ✅ | 100% | 完整实现，含能量门槛检查 |
| wind_pv_bess_irr_planner | ✅ | 100% | 完整实现，IRR 目标型 |
| bess_capacity_sizer | ✅ | 100% | 完整实现，MILP 联合优化 |
| dist_bess_dispatch | ✅ | 100% | 完整实现，含 v1-v5 预设 |

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
├── evaluation/
│   └── metrics.py             # IRR 计算等指标
└── optimization/
    └── interfaces.py          # 分布式储能配置接口
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
