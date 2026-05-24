# pv_es_calc 光储测算算法说明

## 算法定位

`pv_es_calc` 是带光伏发电、用户负荷和储能的收益测算模块。它不仅优化储能充放电，还显式拆分 PV 和电网的能量流：

- `pv_to_load`：光伏直接供本地负荷。
- `pv_to_battery`：光伏给储能充电。
- `pv_to_grid`：光伏上网。
- `grid_to_load`：电网供本地负荷。
- `grid_to_battery`：电网给储能充电。
- `battery_discharge`：储能放电供负荷。

当前保留 `v1` 到 `v5` 多个版本。`v1` 到 `v4` 以 cvxpy 线性规划为主，`v5` 为规则调度。

## 版本语义

| 版本 | 入口 | 主要差异 |
| --- | --- | --- |
| `v1` | `optimization_optim_pv_v1.py` | 基础光储 LP，考虑电度成本、需量成本、PV 上网收益、平滑惩罚和 SOC 软目标 |
| `v2` | `optimization_optim_pv_v2.py` | 中午窗口奖励 `pv_to_battery`，鼓励午间光伏优先充储 |
| `v3` | `optimization_optim_pv_v3.py` | 设计语义是中午窗口奖励 `pv_to_load`；但当前入口导入的是 v4 scheduler，实际运行会偏向 v4 的 `pv_to_grid` 惩罚语义 |
| `v4` | `optimization_optim_pv_v4.py` | 中午窗口惩罚 `pv_to_grid`，不强行区分 `pv_to_load` 与 `pv_to_battery` 的先后 |
| `v5` | `optimization_optim_pv_v5.py` | 规则调度：PV 先供负荷，余电上网；固定窗口电网充电和储能放电 |

历史语义可以简化为：

```text
v2 -> prioritize pv_to_battery
v3 -> prioritize pv_to_load
v4 -> penalize pv_to_grid
```

## 输入数据

| 输入 | 位置或字段 | 含义 |
| --- | --- | --- |
| 用户负荷 | `data/{exp_name}/{node_name}/demand_load.csv` | 本地负荷功率 |
| 分时电价 | `data/{exp_name}/{node_name}/ele_price.csv` | 电价和类型 |
| 光伏出力 | `data/{exp_name}/{node_name}/pv_load.csv` | PV 发电功率 |
| 储能参数 | `devices_info` | 功率、容量、效率、可用深度、变压器容量 |
| 光伏上网电价 | `pv_sell_price` | `pv_to_grid` 的收益单价 |
| 需量电价 | `max_demand_price` | 计算最大需量成本 |
| 时间分辨率 | `freq_minutes` | 电量换算系数 |

默认储能配置仍采用：

```text
es_charge_max = es_scale
es_charge_min = -es_scale
es_capacity_max = es_scale * 2
usable_depth = 0.90
charge_loss = 0.92
discharge_loss = 0.95
```

## 决策变量与能量守恒

LP 版本的核心变量包括：

- 储能变量：`e_c_in_matrix`、`e_c_out_matrix`、`soc_matrix`。
- PV 分流变量：`pv_to_load`、`pv_to_battery`、`pv_to_grid`。
- 电网供电变量：`grid_to_load`、`grid_to_battery`。

关键能量守恒约束：

```text
pv_to_load[t] + pv_to_battery[t] + pv_to_grid[t] = pv_load[t]

pv_to_load[t] + battery_discharge[t] + grid_to_load[t] = demand_load[t]

battery_charge[t] = pv_to_battery[t] + grid_to_battery[t]
grid_import[t] = grid_to_load[t] + grid_to_battery[t]
```

这几条约束决定了光伏、负荷、电网和储能之间的能量流闭合关系。

## 目标函数

LP 版本最小化净成本：

```text
minimize:
    net_cost
    + smooth_penalty
    - discharge_priority_reward
    + soc_target_penalty
    + version_specific_pv_term
```

基础成本项为：

```text
energy_cost = dt * sum(grid_import[t] * ele_price[t])
max_demand_cost = max_demand_price * max(grid_import)
pv_sell_revenue = dt * pv_sell_price * sum(pv_to_grid[t])
net_cost = energy_cost + max_demand_cost - pv_sell_revenue
```

版本差异项：

- `v2`：在中午窗口对 `pv_to_battery` 加奖励。
- `v3`：在中午窗口对 `pv_to_load` 加奖励。
- `v4`：在中午窗口对 `pv_to_grid` 加惩罚。

`v5` 不构造优化目标，而是按规则递推：

1. PV 先满足负荷。
2. 多余 PV 直接上网。
3. 固定充电窗口从电网给储能充电。
4. 放电窗口内储能补充剩余负荷。

## 约束条件

储能 SOC 约束：

```text
soc[i,t] = current_soc[i]
           - sum_{k<=t}(e_c_in[i,k]) * dt * charge_eff[i]
           - sum_{k<=t}(e_c_out[i,k]) * dt / discharge_eff[i]

0 <= e_c_out[i,t] <= es_charge_max[i]
es_charge_min[i] <= e_c_in[i,t] <= 0
es_capacity_min[i] <= soc[i,t] <= es_capacity_max[i] * usable_depth[i]
```

系统约束：

```text
pv_to_grid[t] <= transform_capacity
grid_import[t] <= transform_capacity
```

时间窗口约束：

- `_charge_allowed()` 固定允许充电窗口，通常为夜间和午间。
- `_build_discharge_rule()` 根据电价类型构建允许放电窗口和放电优先级。
- 非充电窗口内禁止 `pv_to_battery` 和 `grid_to_battery`。
- 非放电窗口内禁止储能放电。

SOC 软目标用于鼓励：

- 充电窗口结束时接近满电。
- 放电窗口结束时接近最小 SOC。

它们是带松弛变量的惩罚项，不是绝对硬约束。

## 输出与收益口径

优化输出路径按版本区分，例如：

```text
data/{exp_name}/{node_name}/opt_result-v1.1/es_scale_experiment_optim/
data/{exp_name}/{node_name}/opt_result-v2.1/es_scale_experiment_optim/
data/{exp_name}/{node_name}/opt_result-v3.1/es_scale_experiment_optim/
data/{exp_name}/{node_name}/opt_result-v4/es_scale_experiment_optim/
data/{exp_name}/{node_name}/opt_result-v5/es_scale_experiment_optim/
```

策略文件中通常包含：

| 字段 | 含义 |
| --- | --- |
| `value` | 储能净功率，正值放电、负值充电 |
| `pv_to_load` | PV 直接供负荷 |
| `pv_to_battery` | PV 充储 |
| `pv_to_grid` | PV 上网 |
| `grid_to_load` | 电网供负荷 |
| `grid_to_battery` | 电网充储 |
| `battery_discharge` | 储能放电 |
| `soc` | 储能电量 |

`simulation_pv.py` 计算的 summary 字段包括：

- `baseline_energy_cost`：无储能但有 PV 的电度成本。
- `baseline_max_demand_cost`：无储能但有 PV 的需量成本。
- `energy_cost`：有储能优化后的电度成本。
- `max_demand_cost`：有储能优化后的需量成本。
- `pv_sell_revenue`：PV 上网收益。
- `grid_import_energy`：电网购电电量。
- `grid_to_battery_energy`：电网充储电量。
- `pv_to_battery_energy`：PV 充储电量。
- `battery_discharge_energy`：储能放电电量。
- `pv_to_grid_energy`：PV 上网电量。

收益通常解释为：

```text
revenue = baseline_total_cost - optimized_total_cost
optimized_total_cost = energy_cost + max_demand_cost - pv_sell_revenue
```

## 适用边界

- 适合：光伏自发自用、光伏充储、上网收益、储能峰谷套利和需量成本联合分析。
- 不适合：多变压器分布式互济，需要使用 `es_calc_distribution_version`。
- `v2`、`v3`、`v4` 的午间优先级是目标函数权重，不是严格的物理先后顺序。
- 当前 `optimization_optim_pv_v3.py` 入口与 `EsArbitraryRangeScheduler_withMaxDemand_optim_pv_v3.py` 文件语义不一致；解释现有 v3.1 输出前应先确认实际导入的 scheduler。
- `v5` 是规则策略，解释性强，但不代表最优收益。
