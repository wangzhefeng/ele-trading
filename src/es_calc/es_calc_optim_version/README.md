# es_calc_optim_version 算法说明

## 算法定位

`es_calc_optim_version` 是单节点储能收益测算的激进版本。它按月切分数据，在一个月尺度上同时优化电度套利和需量成本，目标函数直接包含优化后最大需量项，因此比 `basic_version` 更接近电费账单最小化。

该版本适合评估“在已知全年或整月负荷与电价的情况下，储能容量能带来多少理论收益”。它的结果通常比保守版本更高，但依赖完美负荷预知，不等价于在线控制收益。

## 输入数据

核心入口是 `optimization.py` 和 `simulation.py`。

| 输入 | 位置或字段 | 含义 |
| --- | --- | --- |
| 负荷 | `data/{exp_name}/{node_name}/demand_load.csv` | 节点原始负荷功率，字段通常为 `time`, `value` |
| 电价 | `data/{exp_name}/{node_name}/ele_price.csv` | 分时电价与电价类型，字段通常为 `time`, `value`, `type` |
| 储能功率 | `es_scale` | 最大充放电功率 |
| 储能容量 | `es_capacity_max = es_scale * 2` | 默认 2 小时储能 |
| 变压器容量 | `transform_capacity = 63000` | 用于限制负荷叠加充电后的总功率 |
| 需量价格 | `max_demand_price` | 计入优化目标和仿真收益 |
| 时间分辨率 | `freq_minutes` | 电量换算系数 `dt = freq_minutes / 60` |

## 决策变量与符号

`EsArbitraryRangeScheduler_withMaxDemand_optim` 建立以下变量：

- `e_c_in_matrix[i, t]`：充电功率，非正。
- `e_c_out_matrix[i, t]`：放电功率，非负。
- `soc_matrix[i, t]`：SOC 对应的储能电量。
- `e_c_in_agg_vec`、`e_c_out_agg_vec`：节点级聚合充放电功率。

策略输出列名为 `value`。正值表示储能放电，负值表示储能充电。

## 目标函数

源码中的目标函数为：

```text
maximize:
    dt * (e_c_in_agg + e_c_out_agg) @ price
    - max_demand_price * max(demand_load - e_c_in_agg - e_c_out_agg)
```

其中：

- `demand_load - e_c_in_agg - e_c_out_agg` 是优化后的并网负荷。由于 `e_c_in_agg <= 0`，充电会抬高并网负荷；由于 `e_c_out_agg >= 0`，放电会降低并网负荷。
- 第一项是电度套利收益。
- 第二项直接惩罚优化后的月内最大需量。

与 `basic_version` 相比，optim 版本把“最大需量”放进目标函数，而不是只惩罚最大充电功率，因此收益口径更激进、更依赖未来负荷信息。

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
demand_load[t] - e_c_in_agg[t] <= transform_capacity
```

源码中保留了按峰谷平类型限制充放电的注释块，但当前没有启用。因此充放电方向主要由电价、需量成本和 SOC 约束共同决定。

`optimization.py` 按月优化，每个月初始 SOC 仍传入 `[0]`，没有跨月 SOC 续接。

## 输出与收益口径

优化输出路径为：

```text
data/{exp_name}/{node_name}/opt_result/es_scale_experiment_optim/schedule_result_scale_{es_scale}.csv
```

`simulation.py` 使用同一套 `EssSimulationModel` 回放策略并计算收益：

```text
origin_balance = 原始电度电费
opt_balance = 优化后电度电费
ori_max_demand_cost = max_demand_price * sum(monthly_max(original_load))
opt_max_demand_cost = max_demand_price * sum(monthly_max(optimized_load))
revenue = origin_balance - opt_balance - (opt_max_demand_cost - ori_max_demand_cost)
```

汇总字段包括 `revenue`、`max_demand_rise_cost`、`ori_cost`、`opt_cost`、`charge_energy`、`discharge_energy`、`charge_balance`、`discharge_balance`。

## 适用边界

- 适合：单节点、整月离线优化、容量收益上限估计、需量成本敏感性分析。
- 不适合：在线预测误差场景、跨月 SOC 连续控制、多节点互济、光伏自发自用和上网收益分析。
- 不保证：两充两放、日内充满放空、按电价类型硬切换。
