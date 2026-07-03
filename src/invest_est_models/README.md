# invest_est_models

`invest_est_models` 是一个风光储投资测算算法原型包，用于把负荷、电价、风光资源、储能调度、月度结算、投资现金流和容量搜索串成一个可运行闭环。

当前实现不是单一 IRR 计算器，而是围绕两个核心商业指标构建的测算体系：

1. 投资方指标：项目税前 IRR、NPV、回收期、IRR 相对基准提升。
2. 业主指标：业主节费额、业主节费比例。

两个指标可以互相作为目标和约束：

```text
投资方视角：
    maximize project_irr
    subject to owner_saving_pct >= min_owner_saving_pct

业主视角：
    maximize owner_saving_pct
    subject to project_irr >= min_project_irr

投资方提升视角：
    maximize irr_uplift = candidate_project_irr - baseline_project_irr
    subject to project_irr >= min_project_irr
               owner_saving_pct >= min_owner_saving_pct
```

## 当前算法目标

本目录内算法要实现的目标是：

1. 接入一年或典型年的负荷、电价、风光资源时序数据。
2. 在小时级或 15 分钟级时间尺度上模拟风、光、储、负荷、电网之间的能量平衡。
3. 对储能进行规则型调度，当前支持风光余电优先充电、低价电网充电、高价放电供负荷。
4. 将逐时或 15 分钟调度结果汇总为月度结算口径。
5. 计算业主无项目成本、有项目综合用能成本、业主节费额和节费比例。
6. 计算投资方 PPA 收入、余电上网收入、CAPEX、年度现金流、项目税前 IRR、NPV 和回收期。
7. 在风、光、储容量和 PPA 价格候选组合中搜索可行方案。
8. 支持不同目标模式下的最优方案排序：
   - 投资方 IRR 优先。
   - 业主节费比例优先。
   - 投资方 IRR 相对基准提升优先。
9. 输出候选方案表、最优方案摘要、不可行原因表和年度现金流表。

## 目录结构

```text
invest_est_models/
  app/              运行入口脚本
  capacity_search/  风光储容量和 PPA 价格粗网格搜索
  config_loader/    YAML 配置加载和强类型配置对象
  configs/          MVP、V1-V5 示例场景配置
  data_provider/    CSV 读取、样例数据生成和时序校验
  dataset/          样例输入数据
  dispatch/         规则型储能调度和能量平衡
  finance/          CAPEX、现金流、IRR、NPV、回收期和 PPA 反求
  results/          示例运行输出
  settlement/       月度结算和业主/投资方收益汇总
  PLAN.md           需求拆解和版本规划
```

## 数据输入口径

当前实现要求每个场景通过 YAML 配置指定输入路径。YAML 位于 `configs/` 下时，相对路径按 `src/invest_est_models/` 解析。

### 负荷 CSV

字段：

```text
time,value
```

含义：

1. `time`：时间戳。
2. `value`：负荷平均功率，单位 kW。读取后会重命名为 `load_kw`。

### 电价 CSV

字段：

```text
time,price,price_type
```

含义：

1. `price`：电网购电价，单位元/kWh。
2. `price_type`：电价类型，用于储能规则调度，例如 `valley`、`flat`、`peak`、`尖峰`、`高峰`。

### 风光资源 CSV

字段：

```text
time,pv_kw,wind_kw
```

含义：

1. `pv_kw`：光伏平均出力，单位 kW。
2. `wind_kw`：风电平均出力，单位 kW。

当前模块不重新实现风光资源物理仿真。外部风光仿真脚本只要能输出上述字段，就可以接入本测算闭环。容量搜索时会按候选容量相对于配置中的基准容量比例缩放 `pv_kw` 和 `wind_kw`。

### 时间步长

`data_provider.build_timeseries()` 会按相邻时间戳自动推断 `dt_hours`。后续所有能量计算均使用：

```text
energy_kwh = power_kw * dt_hours
```

因此小时级和 15 分钟级数据可以共用同一套计算逻辑。

## 核心算法链路

### 1. 数据接入与校验

实现位置：

```text
data_provider/data_loader.py
```

运行逻辑：

1. 读取负荷、电价、资源三个 CSV。
2. 按 `time` 做内连接，得到统一时序主表。
3. 按时间戳排序。
4. 推断 `dt_hours`。
5. 校验关键数据质量：
   - 必需字段是否存在。
   - 时间戳是否重复。
   - 是否存在缺失值。
   - `dt_hours` 是否为正。
   - 负荷、电价、风光出力是否非负。

该层的输出是调度模型的统一输入表：

```text
time, load_kw, price, price_type, pv_kw, wind_kw, dt_hours
```

### 2. 规则型储能调度

实现位置：

```text
dispatch/rule_based.py
```

当前调度是可解释的规则策略，不是优化调度。每个时间步按以下顺序分配能量：

1. 风光优先直接供负荷。

   ```text
   renewable_to_load_kwh = min(load_kwh, renewable_kwh)
   ```

2. 风光余电优先给储能充电。

   ```text
   charge_from_renewable_kwh <= bess_power_kw * dt_hours
   charge_from_renewable_kwh <= (soc_max - soc) / charge_efficiency
   ```

3. 在放电电价类型内，储能对剩余负荷放电。

   ```text
   discharge_to_load_kwh <= bess_power_kw * dt_hours
   discharge_to_load_kwh <= (soc - soc_min) * discharge_efficiency
   ```

4. 如果允许电网充电，并且当前 `price_type` 属于充电电价类型，则用电网补充储能。

5. 剩余负荷由电网购电。

6. 剩余风光电量作为余电上网。

储能 SOC 更新关系：

```text
soc_t = soc_{t-1}
        + charge_kwh * charge_efficiency
        - discharge_kwh / discharge_efficiency
```

SOC 边界：

```text
soc_min = energy_kwh * soc_min_pct
soc_max = energy_kwh * soc_max_pct
```

无储能或储能容量为 0 时，算法退化为：

```text
风光先供负荷
负荷缺口由电网购电
风光超出负荷部分余电上网
```

### 3. 月度结算

实现位置：

```text
settlement/monthly.py
```

结算模型把时序调度结果按月聚合，形成业主侧和投资方侧指标。

无项目基准成本：

```text
baseline_grid_cost_t = load_kw_t * dt_hours_t * price_t
```

有项目后电网购电成本：

```text
grid_purchase_cost_t = grid_buy_kwh_t * price_t
```

PPA 结算电量当前口径：

```text
ppa_energy_kwh_t = renewable_to_load_kwh_t + charge_from_renewable_kwh_t
```

PPA 收入或业主 PPA 成本：

```text
ppa_revenue_t = ppa_energy_kwh_t * ppa_price
```

余电上网收入：

```text
export_revenue_t = grid_sell_kwh_t * export_price
```

业主有项目综合成本：

```text
with_project_owner_cost =
    grid_purchase_cost
  + transmission_adder_cost
  + deviation_penalty_cost
  + basic_charge
  + demand_charge
  + ppa_cost_to_owner
```

业主节费：

```text
owner_saving = baseline_grid_cost - with_project_owner_cost
owner_saving_pct = owner_saving / baseline_grid_cost
```

投资方收入：

```text
investor_revenue = ppa_cost_to_owner + export_revenue
```

当前已经支持的结算扩展参数：

1. `basic_charge_per_month`：每月固定基本电费。
2. `demand_charge_per_kw_month`：需量电费，按月最大电网购电功率估算。
3. `transmission_price_adder`：输配电价附加。
4. `deviation_penalty_per_kwh`：偏差考核费用率。

这些参数目前是占位口径，用于在业务规则未完全确认时保持算法接口稳定。

### 4. 财务测算

实现位置：

```text
finance/irr.py
```

CAPEX 当前口径：

```text
capex =
    wind_capacity_kw * capex_wind_per_kw
  + pv_capacity_kw * capex_pv_per_kw
  + bess_power_kw * capex_bess_power_per_kw
  + bess_energy_kwh * capex_bess_energy_per_kwh
```

年度现金流：

```text
cashflow_0 = -capex

cashflow_year =
    base_investor_revenue * (1 - renewable_degradation_pct)^(year - 1)
  - capex * fixed_om_pct_of_capex
  - bess_replacement_cost_if_any
```

储能更换成本：

```text
bess_replacement_cost =
    (bess_power_kw * capex_bess_power_per_kw
   + bess_energy_kwh * capex_bess_energy_per_kwh)
  * bess_replacement_cost_pct
```

项目税前 IRR：

```text
NPV(r) = sum(cashflow_y / (1 + r)^y)
IRR = r where NPV(r) = 0
```

当前用 `scipy.optimize.brentq` 在 `[-0.95, 1.0]` 区间求根。若现金流没有同时出现正负值，或区间内无根，则返回 `None`。

NPV：

```text
NPV(discount_rate) = sum(cashflow_y / (1 + discount_rate)^y)
```

回收期：

```text
payback_year = 上一年度 + abs(上一年度累计现金流) / 当年现金流
```

固定 PPA 单价反求：

```text
find ppa_price
subject to compute_project_irr(ppa_price) = target_irr
```

反求同样使用二分/Brent 求根思想。若最低价已满足目标 IRR，返回下界；若最高价仍不满足，返回 `None`。

### 5. 容量搜索

实现位置：

```text
capacity_search/grid_search.py
```

容量搜索采用粗网格枚举，优点是可解释、容易回溯不可行原因，适合当前阶段和业务人员讨论方案。

搜索变量：

```text
wind_capacity_kw
pv_capacity_kw
bess_power_kw
bess_energy_kwh
ppa_price
```

算法流程：

1. 读取 YAML 中的候选数组。
2. 对候选数组做笛卡尔积枚举。
3. 对每个候选方案生成新的 `ProjectConfig`。
4. 按候选容量比例缩放风光资源曲线：

   ```text
   scaled_pv_kw = base_pv_kw * candidate_pv_capacity / base_pv_capacity
   scaled_wind_kw = base_wind_kw * candidate_wind_capacity / base_wind_capacity
   ```

5. 对候选方案运行规则调度。
6. 对调度结果做月度结算。
7. 计算候选方案财务指标：
   - `project_irr`
   - `npv_at_target_irr`
   - `payback_years`
8. 计算候选方案业主侧和政策侧指标：
   - `owner_saving`
   - `owner_saving_pct`
   - `self_use_ratio`
   - `export_ratio`
9. 判断可行性。
10. 对可行方案按 `objective_mode` 排序，选出最优方案。
11. 输出候选结果、不可行结果、最优方案和最优方案年度现金流。

当前可行性约束：

```text
project_irr >= min_project_irr
owner_saving_pct >= min_owner_saving_pct
self_use_ratio >= min_self_use_ratio, if configured
export_ratio <= max_export_ratio, if configured
```

不可行原因字段：

```text
project_irr_unavailable
project_irr_below_min
owner_saving_pct_below_min
self_use_ratio_below_min
export_ratio_above_max
```

## 已实现的 6 个版本

当前已经实现 MVP、V1、V2、V3、V4、V5 共 6 个版本。它们复用同一套输入、调度、结算和财务模块，区别主要在是否做容量搜索，以及容量搜索的最优排序目标。

### 统一数学符号和运筹优化视角

为了理解 MVP 到 V5 的差异，先定义统一符号。

时间集合：

```text
T = {1, 2, ..., n}
```

月份集合：

```text
M = {1, 2, ..., 12}
```

容量和价格决策变量：

```text
x = (W, P, Bp, Be, q)

W  = wind_capacity_kw
P  = pv_capacity_kw
Bp = bess_power_kw
Be = bess_energy_kwh
q  = ppa_price
```

时序输入：

```text
L_t       = load_kw at time t
C_t       = grid price at time t
R_t(W,P)  = pv_kw(P) + wind_kw(W)
Delta_t   = dt_hours
```

调度输出：

```text
G_t       = grid_buy_kwh
E_t       = grid_sell_kwh
U_t       = renewable_to_load_kwh
S_t       = state of charge
Ch_t      = total charge_kwh
Dis_t     = discharge_to_load_kwh
```

投资方收益函数：

```text
Revenue_inv(x) =
    sum_t ppa_energy_t(x) * q
  + sum_t grid_sell_t(x) * export_price
```

业主节费函数：

```text
Saving_owner(x) =
    BaselineCost - WithProjectOwnerCost(x)

SavingRatio_owner(x) =
    Saving_owner(x) / BaselineCost
```

投资方 IRR 函数：

```text
IRR(x) = r

where:
    sum_y Cashflow_y(x) / (1 + r)^y = 0
```

当前实现可以从运筹优化角度理解为一个“外层离散搜索 + 内层确定性仿真 + 财务评价”的模型：

```mermaid
flowchart LR
    A["候选集合 S: 风/光/储/PPA"] --> B["逐候选枚举 x in S"]
    B --> C["资源曲线容量缩放 R_t(x)"]
    C --> D["规则调度仿真"]
    D --> E["月度结算"]
    E --> F["现金流与 IRR/NPV"]
    F --> G["约束过滤"]
    G --> H["按 objective_mode 字典序排序"]
```

运筹建模形式可以写成：

```text
choose x in S

subject to:
    IRR(x) >= IRR_min
    SavingRatio_owner(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured

maximize:
    objective_mode dependent objective
```

其中 `S` 是 YAML 中候选容量和 PPA 单价的笛卡尔积：

```text
S =
    W_candidates
  x P_candidates
  x Bp_candidates
  x Be_candidates
  x q_candidates
```

当前没有把储能调度写成 LP/MILP 求解器，而是用规则策略生成调度结果。因此当前运筹结构是：

```text
外层：离散组合优化
内层：规则仿真，不求解连续调度优化
```

后续如果将储能调度升级为优化调度，可将内层替换为线性规划或混合整数规划，例如：

```text
minimize or maximize dispatch objective

subject to:
    load balance
    renewable allocation balance
    SOC transition
    charge/discharge power limits
    SOC lower/upper bounds
    optional no-simultaneous-charge-discharge constraints
```

### MVP：固定方案测算与 PPA 反求

配置文件：

```text
configs/mvp_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_mvp_demo \
  --config src/invest_est_models/configs/mvp_demo.yaml
```

算法目标：

```text
在给定固定风光储容量、固定 PPA 价格、负荷、电价和风光资源后，
计算项目税前 IRR，并反求达到目标 IRR 所需的最低固定 PPA 单价。
```

详细运行逻辑：

1. 从 YAML 读取输入路径、项目参数、储能参数和财务参数。
2. 如 `sample_data.enabled=true`，生成模拟负荷、电价和风光资源 CSV。
3. 读取三类 CSV 并构建统一时序主表。
4. 运行规则型储能调度。
5. 汇总月度结算。
6. 基于月度投资方收入构造年度现金流。
7. 计算 `project_irr`。
8. 在 `[0, 2]` 元/kWh 区间内反求达到 `target_irr` 的 PPA 单价。
9. 输出：
   - `results/mvp_dispatch_timeseries.csv`
   - `results/mvp_monthly_settlement.csv`
   - 命令行摘要 `project_irr` 和 `target_ppa_price`

算法原理：

MVP 是单方案评价模型。它不搜索容量，只回答当前配置是否有经济性，以及如果投资方要求目标 IRR，固定 PPA 单价至少需要多少。

数学模型：

MVP 中容量和 PPA 初始价格都是给定参数，不把 `x` 作为搜索变量：

```text
x_fixed = (W0, P0, Bp0, Be0, q0)
```

第一步是固定方案评价：

```text
Evaluate:
    IRR(x_fixed)
    SavingRatio_owner(x_fixed)
    NPV(x_fixed)
```

第二步是单变量反求 PPA 价格。此时容量固定，只把 `q` 作为未知数：

```text
find q*

subject to:
    IRR(W0, P0, Bp0, Be0, q*) = target_irr
    q_low <= q* <= q_high
```

等价求根问题：

```text
f(q) = IRR(W0, P0, Bp0, Be0, q) - target_irr
find q* where f(q*) = 0
```

当前实现使用 Brent 求根。该方法依赖 `q` 与投资方收入近似单调：

```text
q increases
  -> PPA revenue increases
  -> annual cashflow increases
  -> IRR increases
```

运筹视角：

MVP 不是组合优化模型，而是“固定方案仿真评价 + 单变量非线性方程求解”。它适合回答单个项目配置是否达标，以及目标 IRR 对应的 PPA 价格边界。

### V1：基础 capacity_search

配置文件：

```text
configs/v1_capacity_search_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v1_capacity_search_demo.yaml
```

算法目标：

```text
在风、光、储和 PPA 候选组合中做粗网格搜索，
筛选同时满足投资方 IRR 和业主节费比例约束的方案，
并按投资方 IRR 优先规则选出最优方案。
```

V1 显式配置：

```yaml
search:
  objective_mode: investor_irr_first
```

详细运行逻辑：

1. 读取 v1 YAML 搜索配置。
2. 枚举：
   - 风电容量候选值。
   - 光伏容量候选值。
   - 储能功率候选值。
   - 储能容量候选值。
   - 固定 PPA 单价候选值。
3. 对每个候选方案缩放风光资源曲线。
4. 运行调度、月度结算和财务测算。
5. 计算 `project_irr`、`owner_saving_pct`、`self_use_ratio`、`export_ratio`。
6. 按约束判断可行性。
7. 对可行候选按以下规则排序：

   ```text
   先 project_irr
   再 owner_saving_pct
   再 npv_at_target_irr
   ```

8. 输出候选表、不可行表、最优摘要和年度现金流。

算法原理：

V1 是“约束过滤 + 投资方收益优先排序”的离散搜索模型。它不是连续优化器，而是在有限候选集合中选出最优可行方案。

数学模型：

V1 的候选集合为：

```text
S_v1 =
    W_candidates
  x P_candidates
  x Bp_candidates
  x Be_candidates
  x q_candidates
```

对每个候选 `x in S_v1`，先通过调度和结算得到：

```text
IRR(x)
SavingRatio_owner(x)
SelfUseRatio(x)
ExportRatio(x)
NPV(x)
Payback(x)
```

可行域：

```text
F_v1 = {
    x in S_v1 |
        IRR(x) >= IRR_min
        SavingRatio_owner(x) >= Saving_min
        SelfUseRatio(x) >= SelfUse_min, if configured
        ExportRatio(x) <= Export_max, if configured
}
```

目标函数采用字典序最大化，而不是单一加权求和：

```text
maximize lexicographic key:
    K_v1(x) = (IRR(x), SavingRatio_owner(x), NPV(x))

subject to:
    x in F_v1
```

字典序含义是：先比较第一项；第一项相同或非常接近时，再比较第二项；之后再比较第三项。

运筹视角：

V1 是有限集合上的离散可行性筛选与字典序优化：

```text
argmax_{x in F_v1} K_v1(x)
```

这种方法的优点是可解释、易调试、能直接输出不可行原因；缺点是精度受候选网格影响，不能保证连续变量意义下的全局最优。

### V2：目标模式切换 capacity_search

配置文件：

```text
configs/v2_owner_saving_first_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v2_owner_saving_first_demo.yaml
```

算法目标：

```text
保留 V1 的 capacity_search 流程，
通过 objective_mode 改变最优方案排序规则，
使同一套搜索算法支持业主节费比例优先。
```

V2 显式配置：

```yaml
search:
  objective_mode: owner_saving_first
```

详细运行逻辑：

1. 复用 V1 的候选枚举、调度、结算、财务测算和可行性约束。
2. 可行性仍要求：

   ```text
   project_irr >= min_project_irr
   owner_saving_pct >= min_owner_saving_pct
   ```

3. 最优排序切换为：

   ```text
   先 owner_saving_pct
   再 project_irr
   再 npv_at_target_irr
   ```

4. 输出中写入：
   - `objective_mode`
   - `objective_value`
   - `ranking_primary_metric`
   - `ranking_secondary_metric`

算法原理：

V2 体现“双目标 + 约束切换”的第一步：两个核心指标都计算，但通过配置决定哪一个作为主排序目标。它没有复制新的搜索模块，而是把排序规则参数化。

数学模型：

V2 仍使用与 V1 相同的候选集合和可行域：

```text
S_v2 = S_v1
F_v2 = F_v1
```

变化只发生在目标排序函数。V2 的 `owner_saving_first` 模式为：

```text
maximize lexicographic key:
    K_v2(x) = (SavingRatio_owner(x), IRR(x), NPV(x))

subject to:
    x in F_v2
```

与 V1 的差异：

```text
V1: K(x) = (IRR, SavingRatio, NPV)
V2: K(x) = (SavingRatio, IRR, NPV)
```

运筹视角：

V2 将双目标问题转换为可配置的字典序多目标优化。它没有使用加权目标：

```text
alpha * IRR + beta * SavingRatio
```

原因是当前阶段 `alpha` 和 `beta` 很难用业务口径校准。字典序排序更符合谈判逻辑：先明确主目标，再把另一个目标作为约束或次级偏好。

可解释为：

```text
argmax_{x in F_v2} K_v2(x)
```

该模式适合业主优先初筛：先找节费比例最高的方案，再检查投资方收益表现。

### V3：以投资方 IRR 为目标，业主节费比例为约束

配置文件：

```text
configs/v3_investor_irr_target_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v3_investor_irr_target_demo.yaml
```

算法目标：

```text
在业主节费比例不低于底线的前提下，
选择投资方项目税前 IRR 最高的风光储和 PPA 方案。
```

数学形式：

```text
maximize project_irr(x)

subject to:
    project_irr(x) >= min_project_irr
    owner_saving_pct(x) >= min_owner_saving_pct
    optional policy constraints
```

其中 `x` 表示：

```text
wind_capacity_kw
pv_capacity_kw
bess_power_kw
bess_energy_kwh
ppa_price
```

详细运行逻辑：

1. 读取 V3 YAML。
2. 使用 `objective_mode=investor_irr_first`。
3. 枚举候选方案并计算所有指标。
4. 用业主节费比例作为硬约束之一。
5. 在可行方案中选择 `project_irr` 最大的方案。

算法原理：

V3 是投资方视角的绝对收益优化场景。它看的是候选方案自身 IRR 是否最高，不计算相对某个基准方案的提升幅度。

数学模型：

V3 是对 V1 投资方视角的场景化表达：

```text
maximize IRR(x)

subject to:
    x in S_v3
    IRR(x) >= IRR_min
    SavingRatio_owner(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured
```

当前实现中，为了保持排序稳定，实际采用字典序：

```text
K_v3(x) = (IRR(x), SavingRatio_owner(x), NPV(x))

argmax_{x in F_v3} K_v3(x)
```

其中：

```text
F_v3 = feasible candidate set under V3 constraints
```

经济含义：

```text
PPA price q higher
  -> investor revenue tends to increase
  -> IRR tends to increase
  -> owner saving tends to decrease
```

因此 V3 必须保留业主节费比例约束，否则模型可能倾向选择高 PPA 价格，使投资方收益最大但业主不可接受。

运筹视角：

V3 是投资方收益最大化的离散约束优化问题。它适合投资方测算或谈判底价分析：先确保业主仍有最低节费，再看投资收益能做到多高。

### V4：以业主节费比例为目标，投资方 IRR 为约束

配置文件：

```text
configs/v4_owner_saving_target_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v4_owner_saving_target_demo.yaml
```

算法目标：

```text
在投资方 IRR 不低于最低收益要求的前提下，
选择业主节费比例最高的风光储和 PPA 方案。
```

数学形式：

```text
maximize owner_saving_pct(x)

subject to:
    project_irr(x) >= min_project_irr
    owner_saving_pct(x) >= min_owner_saving_pct
    optional policy constraints
```

详细运行逻辑：

1. 读取 V4 YAML。
2. 使用 `objective_mode=owner_saving_first`。
3. 枚举候选方案并计算所有指标。
4. 用投资方最低 IRR 作为硬约束之一。
5. 在可行方案中选择 `owner_saving_pct` 最大的方案。

算法原理：

V4 是业主视角的节费优化场景。它利用 PPA 价格、容量组合和储能调度结果，在保证投资方最低收益的前提下，寻找业主成本下降最多的候选方案。

数学模型：

V4 是 V3 的角色切换：

```text
maximize SavingRatio_owner(x)

subject to:
    x in S_v4
    IRR(x) >= IRR_min
    SavingRatio_owner(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured
```

当前实现排序键：

```text
K_v4(x) = (SavingRatio_owner(x), IRR(x), NPV(x))

argmax_{x in F_v4} K_v4(x)
```

经济含义：

```text
PPA price q lower
  -> owner cost tends to decrease
  -> owner saving tends to increase
  -> investor IRR tends to decrease
```

因此 V4 必须保留投资方最低 IRR 约束，否则模型可能倾向选择低 PPA 价格，使业主节费最大但投资方不可接受。

运筹视角：

V4 是业主收益最大化的离散约束优化问题。它适合业主侧报价方案评估：在投资方收益底线之上，选择业主综合用能成本最低的方案。

### V5：投资方 IRR uplift 模式

配置文件：

```text
configs/v5_investor_irr_uplift_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v5_investor_irr_uplift_demo.yaml
```

算法目标：

```text
相对于一个明确的基准方案，
选择投资方 IRR 提升幅度最大的候选方案，
同时保证候选方案满足投资方 IRR 和业主节费比例约束。
```

数学形式：

```text
baseline_project_irr = IRR(baseline_project)
candidate_project_irr(x) = IRR(candidate_project_x)
irr_uplift(x) = candidate_project_irr(x) - baseline_project_irr

maximize irr_uplift(x)

subject to:
    candidate_project_irr(x) >= min_project_irr
    candidate_owner_saving_pct(x) >= min_owner_saving_pct
    optional policy constraints
```

详细运行逻辑：

1. 读取 V5 YAML。
2. 使用 `objective_mode=investor_irr_uplift`。
3. 读取 `baseline_project`。
4. 用 `baseline_project` 覆盖基准风光储容量和 PPA 价格。
5. 对基准方案运行资源缩放、调度、结算和财务测算。
6. 得到：
   - `baseline_project_irr`
   - `baseline_owner_saving_pct`
7. 枚举候选方案。
8. 对每个候选方案计算：
   - `candidate_project_irr`
   - `candidate_owner_saving_pct`
   - `irr_uplift`
9. 按约束筛选可行方案。
10. 对可行方案按以下规则排序：

    ```text
    先 irr_uplift
    再 candidate_project_irr
    再 candidate_owner_saving_pct
    ```

11. 输出基准指标、候选指标和 uplift 指标。

算法原理：

V5 与 V3 的差异是：

```text
V3 看绝对 IRR：哪个候选方案自身 project_irr 最高。
V5 看相对提升：哪个候选方案相对于 baseline_project 的 IRR 增量最大。
```

当前 V5 第一版采用百分点差值：

```text
irr_uplift = candidate_project_irr - baseline_project_irr
```

如果基准方案 IRR 不可求，当前实现会直接报错，避免在缺少基准收益率时输出没有业务含义的提升值。

数学模型：

V5 引入基准方案：

```text
x_base = (W_base, P_base, Bp_base, Be_base, q_base)
```

先计算基准指标：

```text
IRR_base = IRR(x_base)
SavingRatio_base = SavingRatio_owner(x_base)
```

再对每个候选方案计算：

```text
IRR_candidate(x) = IRR(x)
SavingRatio_candidate(x) = SavingRatio_owner(x)
IRR_uplift(x) = IRR_candidate(x) - IRR_base
```

V5 优化模型：

```text
maximize IRR_uplift(x)

subject to:
    x in S_v5
    IRR_candidate(x) >= IRR_min
    SavingRatio_candidate(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured
    IRR_base is available
```

当前实现排序键：

```text
K_v5(x) = (IRR_uplift(x), IRR_candidate(x), SavingRatio_candidate(x))

argmax_{x in F_v5} K_v5(x)
```

V5 与 V3 的结果可能不同。示例：

```text
方案 A: IRR_base = 8%,  IRR_candidate = 12%, IRR_uplift = 4pct
方案 B: IRR_base = 11%, IRR_candidate = 13%, IRR_uplift = 2pct
```

V3 会偏向方案 B，因为候选方案绝对 IRR 更高；V5 会偏向方案 A，因为相对基准提升更大。

运筹视角：

V5 是带基准方案的相对收益优化问题。它适合回答“优化策略相对原方案到底提升了多少”，而不只是“当前方案收益率是多少”。

当前实现采用百分点差值作为提升：

```text
IRR_uplift = IRR_candidate - IRR_base
```

暂未采用相对增长率：

```text
(IRR_candidate - IRR_base) / abs(IRR_base)
```

原因是百分点差值更符合项目 IRR 谈判表达，也避免基准 IRR 接近 0 时相对增长率失真。

## objective_mode 对照表

| 模式 | 目标含义 | 主排序指标 | 次排序指标 | 典型版本 |
|---|---|---|---|---|
| `investor_irr_first` | 投资方 IRR 优先 | `project_irr` | `owner_saving_pct` | V1、V3 |
| `owner_saving_first` | 业主节费比例优先 | `owner_saving_pct` | `project_irr` | V2、V4 |
| `investor_irr_uplift` | 投资方 IRR 相对基准提升优先 | `irr_uplift` | `candidate_project_irr` | V5 |

## 输出文件

MVP 输出：

```text
results/mvp_dispatch_timeseries.csv
results/mvp_monthly_settlement.csv
```

V1-V5 输出：

```text
results/v*_candidate_results.csv
results/v*_best_summary.csv
results/v*_infeasible_reasons.csv
results/v*_annual_cashflows.csv
```

候选结果关键字段：

1. 容量与价格：
   - `wind_capacity_kw`
   - `pv_capacity_kw`
   - `bess_power_kw`
   - `bess_energy_kwh`
   - `ppa_price`
2. 投资方指标：
   - `project_irr`
   - `candidate_project_irr`
   - `npv_at_target_irr`
   - `payback_years`
   - `baseline_project_irr`
   - `irr_uplift`
3. 业主指标：
   - `owner_saving`
   - `owner_saving_pct`
   - `candidate_owner_saving_pct`
   - `baseline_owner_saving_pct`
4. 电量和政策指标：
   - `renewable_generation_kwh`
   - `ppa_energy_kwh`
   - `export_energy_kwh`
   - `self_use_ratio`
   - `export_ratio`
5. 可行性和目标模式：
   - `is_feasible`
   - `infeasible_reasons`
   - `objective_mode`
   - `objective_value`
   - `ranking_primary_metric`
   - `ranking_secondary_metric`
   - `constraint_min_project_irr`
   - `constraint_min_owner_saving_pct`

## 已验证命令

```bash
PYTHONPATH=src ./.venv/bin/python -m compileall src/invest_est_models
PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_invest_est_models_v1.py -q

PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_mvp_demo \
  --config src/invest_est_models/configs/mvp_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v1_capacity_search_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v2_owner_saving_first_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v3_investor_irr_target_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v4_owner_saving_target_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search \
  --config src/invest_est_models/configs/v5_investor_irr_uplift_demo.yaml
```

## 当前边界和后续扩展

当前已经实现的是可运行、可解释的算法闭环，但仍有明确边界：

1. 风光资源物理仿真未在本目录内重写，当前通过资源 CSV 接入。
2. 负荷模型当前直接读取典型年 CSV，尚未从历史电费单反推负荷曲线。
3. 电价模型当前直接读取全年分时电价 CSV，尚未实现行政日历自动生成。
4. 储能调度当前是规则策略，尚未引入线性规划、混合整数规划或滚动优化。
5. 财务口径当前是项目税前 IRR，尚未实现税后 IRR、资本金 IRR、融资还款、折旧和税费。
6. 结算中的基本电费、需量电费、输配电价和偏差考核仍是占位口径，需要后续根据真实业务规则细化。
7. V5 的 `baseline_project` 需要业务侧明确基准方案定义，当前示例使用模拟基准。
8. 当前搜索是粗网格枚举，后续可扩展为分层粗细搜索、Pareto 前沿或优化模型。
