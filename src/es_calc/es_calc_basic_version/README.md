# es_calc_basic_version 算法说明

## 算法定位

`es_calc_basic_version` 是单节点储能收益测算的保守版本。它按天切分负荷和电价数据，调用 `EsArbitraryRangeScheduler_withMaxDemand_basic` 生成单套储能的充放电策略，再由 `simulation.py` 回放策略并计算电度收益和需量电费变化。

保守性主要来自两个设计：

- 优化目标没有显式最小化全月最大需量，而是使用 `max_demand_price * min(e_c_in)` 近似控制充电导致的需量抬升。
- 目标函数加入 `lamda_amortize * norm(e_c_in_agg_vec)`，倾向于缓充，避免集中大功率充电。

该版本适合做偏保守的初步容量敏感性测算，不适合解释为全局最优的需量管理策略。

## 输入数据

核心入口是 `optimization.py` 和 `simulation.py`。

| 输入 | 位置或字段 | 含义 |
| --- | --- | --- |
| 负荷 | `data/{exp_name}/route_{route_num}/demand_load.csv` | 节点原始用电功率，字段通常为 `time`, `value` |
| 电价 | `data/{exp_name}/route_{route_num}/ele_price.csv` | 分时电价与时段类型，字段通常为 `time`, `value`, `type` |
| 储能功率 | `es_scale` | 最大充放电功率，代码中同时作为 `es_charge_max` 和 `-es_charge_min` |
| 储能容量 | `es_capacity_max = es_scale * 2` | 默认按 2 小时系统配置 |
| 可用深度 | `usable_depth = 0.90` | 实际可用容量上限为 `es_capacity_max * usable_depth` |
| 效率 | `charge_loss = 0.92`, `discharge_loss = 0.95` | 充电和放电效率 |
| 需量价格 | `max_demand_price` | 用于优化近似项和仿真阶段需量电费计算 |

## 决策变量与符号

调度器为每个储能设备和每个时间步建立三类变量：

- `e_c_in_matrix[i, t]`：储能 `i` 在时刻 `t` 的充电功率，取值小于等于 0。
- `e_c_out_matrix[i, t]`：储能 `i` 在时刻 `t` 的放电功率，取值大于等于 0。
- `soc_matrix[i, t]`：储能 `i` 在时刻 `t` 的电量状态。

聚合变量为：

- `e_c_in_agg_vec = sum_i e_c_in_matrix[i, :]`
- `e_c_out_agg_vec = sum_i e_c_out_matrix[i, :]`
- `soc_agg_vec = sum_i soc_matrix[i, :]`

策略输出中，正值表示放电，负值表示充电。当前版本的 `schedule_generate()` 输出列名为 `power_opt`。

## 目标函数

源码中的目标函数为：

```text
maximize:
    31 * dt * (e_c_in_agg + e_c_out_agg) @ price
    + max_demand_price * min(e_c_in_agg)
    - lamda_amortize * norm(e_c_in_agg)
```

其中 `dt = freq_minutes / 60`。

解释如下：

- `dt * (charge + discharge) @ price` 表示分时电价下的套利收益。由于充电功率为负、放电功率为正，低价充电会形成成本，高价放电形成收益。
- 系数 `31` 是历史日策略放大到月度收益口径的近似因子。
- `max_demand_price * min(e_c_in_agg)` 会惩罚充电峰值。因为 `e_c_in_agg` 为负，充电越集中，`min(e_c_in_agg)` 越小，目标值越低。
- `norm(e_c_in_agg)` 是缓充惩罚，降低尖锐充电行为。

该目标不是完整电费账单最小化模型。完整收益以 `simulation.py` 的回放结果为准。

## 约束条件

核心约束如下：

```text
soc[i,t] = current_soc[i]
           - sum_{k<=t}(e_c_in[i,k]) * dt * charge_eff[i]
           - sum_{k<=t}(e_c_out[i,k]) * dt / discharge_eff[i]

0 <= e_c_out[i,t] <= es_charge_max[i]
es_charge_min[i] <= e_c_in[i,t] <= 0
es_capacity_min[i] <= soc[i,t] <= es_capacity_max[i] * usable_depth[i]
sum_i e_c_out[i,t] <= max(demand_load[t], 0)
```

时段类型约束在源码中保留为注释，没有实际启用。因此该版本不会硬性规定“谷段只能充电、峰段只能放电”。

该版本也不保留跨天 SOC。`optimization.py` 每天构造调度器时使用 `[0]` 作为初始 SOC。

## 输出与收益口径

`optimization.py` 为每个 `es_scale` 生成策略文件，设计路径为：

```text
data/{exp_name}/{node_name}/opt_result/es_scale_experiment_basic/schedule_result_scale_{es_scale}.csv
```

`simulation.py` 读取策略后调用 `EssSimulationModel` 回放实际充放电，输出容量测算汇总：

| 字段 | 含义 |
| --- | --- |
| `revenue` | 总收益，等于电度成本节省减去需量电费增量 |
| `max_demand_rise_cost` | 优化后最大需量电费与原始最大需量电费的差 |
| `ori_energy` | 原始负荷电量 |
| `ori_cost` | 原始电度电费加需量电费 |
| `opt_cost` | 优化后电度电费加需量电费 |
| `charge_energy` | 储能充电电量 |
| `discharge_energy` | 储能放电电量 |
| `charge_balance` | 充电电费 |
| `discharge_balance` | 放电收益 |

仿真收益公式为：

```text
revenue = origin_energy_cost - opt_energy_cost - max_demand_rise_cost
max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost
```

## 适用边界

- 适合：单节点储能容量敏感性、偏保守收益测算、快速比较不同 `es_scale`。
- 不适合：严格需量最优、跨日 SOC 连续调度、多节点分布式储能、带光伏的能量流拆分。
- 不保证：每天两充两放、尽量充满、尽量放完、跨天或跨月 SOC 连续。
