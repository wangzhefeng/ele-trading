# es_calc_without_demand_version 算法说明

## 算法定位

`es_calc_without_demand_version` 是单节点储能收益测算的“无需量优化”版本。它的优化目标只考虑分时电价套利，不把需量电费直接放入目标函数；但在仿真阶段仍会计算优化策略导致的需量电费变化。

这个版本适合回答：“如果策略只按峰谷价差套利，不主动优化需量，那么收益如何？” 它不是完全忽略需量，而是把需量影响作为事后评价项。

## 输入数据

| 输入 | 位置或字段 | 含义 |
| --- | --- | --- |
| 负荷 | `data/{exp_name}/{node_name}/demand_load.csv` | 节点原始用电功率 |
| 电价 | `data/{exp_name}/{node_name}/ele_price.csv` | 分时电价和时段类型 |
| 储能功率 | `es_scale` | 最大充放电功率 |
| 储能容量 | `es_capacity_max = es_scale * 2` | 默认 2 小时储能 |
| 可用深度 | `usable_depth = 0.90` | 可用容量比例 |
| 效率 | `charge_loss = 0.92`, `discharge_loss = 0.95` | 充放电效率 |
| 时间分辨率 | `freq_minutes` | 电量换算用 |

`max_demand_price` 仍作为参数传入入口函数，但调度器 `EsArbitraryRangeScheduler_withoutDemand` 不在目标函数中使用它。

## 决策变量与符号

变量与单节点 optim 版本一致：

- `e_c_in_matrix[i, t]`：充电功率，非正。
- `e_c_out_matrix[i, t]`：放电功率，非负。
- `soc_matrix[i, t]`：储能电量。
- `e_c_in_agg_vec`、`e_c_out_agg_vec`：聚合充放电功率。

输出策略列名为 `value`。正值放电，负值充电。

## 目标函数

源码中的目标函数为：

```text
maximize:
    dt * (e_c_in_agg + e_c_out_agg) @ price
```

它只优化电度套利收益：

- 低价时充电产生负收益项，也就是购电成本。
- 高价时放电产生正收益项，也就是减少购电或等价售电收益。
- 不包含 `max(demand_after_storage)`、`min(charge_power)` 或其他需量相关项。

因此该版本可能得到电度收益较高但需量电费变差的策略，最终收益需要看 `simulation.py` 的回放结果。

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
e_c_in_agg[t] >= max(demand_load[t], 0) - max(demand_load)
```

最后一条约束避免充电后并网负荷超过原始最大负荷：

```text
demand_load[t] - e_c_in_agg[t] <= max(demand_load)
```

它不是需量成本优化，只是把充电引起的需量突破压在原始峰值以内。

## 输出与收益口径

优化输出路径为：

```text
data/{exp_name}/{node_name}/opt_result/es_scale_experiment_optim_withoutDemand/schedule_result_scale_10_{es_scale}.csv
```

仿真阶段仍计算：

```text
revenue = origin_balance - opt_balance - max_demand_rise_cost
max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost
```

这意味着：

- 若策略只做套利且没有抬高月最大需量，`max_demand_rise_cost` 可能接近 0 或为负。
- 若策略在高负荷时段充电导致月最大需量上升，`max_demand_rise_cost` 会抵消部分电度收益。

## 适用边界

- 适合：只看峰谷套利能力、验证需量项对收益的影响、和 optim 版本做对照。
- 不适合：需量管理策略设计、变压器容量紧约束场景、多节点分布式储能。
- 不保证：两充两放、跨月 SOC 连续、严格最小化总电费。
