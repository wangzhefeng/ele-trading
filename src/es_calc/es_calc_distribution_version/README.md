# es_calc_distribution_version 算法说明

## 算法定位

`es_calc_distribution_version` 用于分布式储能测算。它把一个系统内多台变压器、多个储能柜组合放在同一测算框架里，比较不同柜数组合的收益。

当前目录保留了 `v1` 到 `v5` 多个版本：

| 版本 | 核心语义 |
| --- | --- |
| `v1` | 多变压器公共母线线性规划，允许储能放电跨变压器分配给同系统负荷 |
| `v2` | 在 `v1` 基础上加入平滑惩罚、爬坡约束和可选 SOC 软目标 |
| `v3` | 增加 `allocation_group_labels`，可限制只在同一分组内互济 |
| `v4` | 园区级收益和需量核算，保留跨变压器分配、平滑和 SOC 软目标 |
| `v5` | 固定两充两放时间窗的规则调度，不再调用优化求解器 |

正式文档分析应优先看最新 `v5`，同时在版本对比中保留 `v1` 到 `v4` 的优化语义。

## 输入数据

分布式版本比单节点版本多了 local transformer load 和柜数组合。

| 输入 | 含义 |
| --- | --- |
| `system_demand_load` | 园区或系统总负荷，用于收益和最大需量核算 |
| `local_demand_load_matrix` | 每台变压器后的本地负荷矩阵 |
| `ele_prices`, `ele_types` | 分时电价和电价类型 |
| `devices_info` | 每个储能设备的功率、容量、效率、所属变压器容量 |
| `current_soc_list` | 每个储能设备初始电量 |
| `max_demand_price` | 需量电费单价 |
| `allocation_group_labels` | `v3` 使用的互济分组标签 |
| `freq_minutes` | 时间分辨率 |

脚本侧通常会遍历不同柜数组合，生成 `combo_key`，例如：

```text
338_1-1__338_2-1__338_3-1__342_1-1__342_2-1
```

## 决策变量与符号

`v1` 到 `v4` 是线性规划模型，核心变量包括：

- `e_c_in_matrix[i, t]`：储能 `i` 的充电功率，非正。
- `e_c_out_matrix[i, t]`：储能 `i` 的放电功率，非负。
- `soc_matrix[i, t]`：储能 `i` 的电量。
- `grid_to_load_matrix[j, t]`：电网供给变压器 `j` 后负荷的功率。
- `allocation_by_source[i][j, t]`：储能 `i` 在时刻 `t` 分配给变压器 `j` 后负荷的放电功率。
- `system_grid_import[t]`：系统总并网功率。

`v5` 是规则调度模型，不建立 cvxpy 变量，而是按时间步递推 `charge_array`、`discharge_array`、`soc_matrix` 和 allocation 结果。

## 目标函数

`v1` 到 `v4` 的优化方向是最小化总成本：

```text
minimize:
    energy_cost
    + max_demand_cost
    + cross_flow_penalty
    + smooth_penalty
    + soc_target_penalty
```

其中：

- `energy_cost = sum(system_grid_import[t] * price[t] * dt)`
- `max_demand_cost = max_demand_price * max(system_grid_import)`
- `cross_flow_penalty` 轻微惩罚跨变压器放电分配，优先本地消纳。
- `smooth_penalty` 惩罚相邻时刻充放电功率跳变。
- `soc_target_penalty` 可软性鼓励在充电窗口结束时接近满电、放电窗口结束时接近空电。

`v5` 不解优化问题，收益由仿真阶段计算。规则为：

- 充电窗口：`00:00-06:00` 和 `12:00-14:00`。
- 放电窗口：`06:00-12:00` 和 `16:00-24:00`。
- 充电时按变压器剩余容量、储能功率上限和 SOC 空间充电。
- 放电时先供本地负荷，再在同系统内按剩余负荷跨变压器分配。

## 约束条件

线性规划版本的核心约束：

```text
soc[i,t] = current_soc[i]
           - sum_{k<=t}(e_c_in[i,k]) * dt * charge_eff[i]
           - sum_{k<=t}(e_c_out[i,k]) * dt / discharge_eff[i]

sum_j allocation_by_source[i][j,t] = e_c_out[i,t]
grid_to_load[j,t] + sum_i allocation_by_source[i][j,t] = local_load[j,t]
grid_to_load[j,t] + cross_in[j,t] + charge_power[j,t] <= transform_capacity[j]
cross_out[i,t] <= transform_capacity[i]

0 <= e_c_out[i,t] <= es_charge_max[i]
es_charge_min[i] <= e_c_in[i,t] <= 0
es_capacity_min[i] <= soc[i,t] <= es_capacity_max[i] * usable_depth[i]
```

时段规则通过 `_charge_allowed()` 和 `_build_discharge_allowed_mask()` 施加：

- 非充电窗口内，`e_c_in == 0`。
- 非放电窗口内，`e_c_out == 0`。
- 在部分版本中，充电窗口内禁止放电，放电窗口内禁止充电。

`v3` 额外支持互济分组约束：

```text
if allocation_group_labels[source] != allocation_group_labels[target]:
    allocation_by_source[source][target, :] = 0
```

## 输出与收益口径

优化阶段通常输出：

- `capacity_search_summary.csv`：每个柜数组合一行，记录组合、收益估计、是否选中等。
- `schedule_result_combo_{combo_key}.csv`：某个柜数组合对应的全时段策略。
- allocation 相关列：如 `allocation_*`、`transformer_import_*`、`grid_import_total`，用于解释跨变压器供能。

仿真阶段由 `simulation_dist_v*.py` 重新计算收益：

```text
revenue = ori_cost - opt_cost
ori_cost = origin_energy_cost + ori_max_demand_cost
opt_cost = opt_energy_cost + opt_max_demand_cost
```

典型 summary 字段包括：

| 字段 | 含义 |
| --- | --- |
| `revenue` 或 `revenue_收益` | 储能策略带来的运行收益 |
| `max_demand_rise_cost` | 需量电费变化 |
| `ori_cost` | 无储能原始总成本 |
| `opt_cost` | 有储能优化后总成本 |
| `total_cabinets` | 总储能柜数 |
| `total_capacity_kwh` | 总电容量 |
| `selected_combo_key` | 选中组合对应的调度文件 key |

单位容量收益可按以下方式计算：

```text
unit_revenue = revenue / total_capacity_kwh
```

它用于比较不同容量组合的边际经济性。总收益随容量增加不代表单位容量收益也增加。

## 适用边界

- 适合：多变压器、分布式储能柜组合、跨变压器互济、园区级收益和需量分析。
- 不适合：带光伏的 PV 分流收益，需使用 `pv_es_calc`。
- `v1` 到 `v4` 不保留跨月 SOC，通常月度独立求解。
- `v5` 硬编码固定两充两放窗口，但它是规则递推，不保证经济最优。
- “尽量充满、尽量放完”在优化版本中是软目标，不是所有版本都启用的硬约束。
