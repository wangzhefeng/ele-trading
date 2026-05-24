# es_simulation 仿真算法说明

## 算法定位

`es_simulation` 是 `es_calc` 多个版本共用的储能策略回放模块。优化器输出的是期望充放电功率，仿真器负责按储能设备物理边界逐步修正策略，并计算电度电费、需量电费和收益。

目录中有两个模型：

| 文件 | 需量口径 |
| --- | --- |
| `EssSimulation.py` | `revenue_calculation()` 中计入最大需量电费 |
| `EssSimulation_withoutMaxDemand.py` | `revenue_calculation()` 中不计入最大需量电费 |

注意：`es_calc_basic_version/simulation.py`、`es_calc_optim_version/simulation.py` 和 `es_calc_without_demand_version/simulation.py` 会在脚本层重新按月计算需量电费，因此是否使用 `withoutMaxDemand` 需要结合调用脚本一起判断。

## 输入数据

`EssSimulationModel` 初始化需要一套储能配置：

| 配置项 | 含义 |
| --- | --- |
| `transform_capacity` | 变压器容量上限 |
| `invertband` | 放电后保留的最小负荷带，避免放电超过负荷 |
| `es_capacity_max` | 储能额定容量 |
| `usable_depth` | 可用容量比例 |
| `soc_redundant_ratio` | 最小保留 SOC 比例 |
| `es_charge_min` | 最大充电功率，负值 |
| `es_charge_max` | 最大放电功率，正值 |
| `charge_loss` | 充电效率 |
| `discharge_loss` | 放电效率 |

`simulation_process()` 需要：

- `demand_load`：以时间为索引、含 `value` 列的负荷 DataFrame。
- `es_strategy`：以时间为索引、含 `value` 或等价策略列的调度 DataFrame。
- `last_soc`：初始储能电量。

## 单步仿真逻辑

`one_step(time_lag, demand_load, command, soc)` 按命令符号区分充放电。

放电时：

```text
charge = min(command, max_discharge_power)
charge = min(charge, demand_load - invert_band)
inner_energy_vari = charge / discharge_efficiency * time_lag
soc_new = soc - inner_energy_vari
```

若放电后 SOC 会低于 `battery_capacity * soc_redundant_ratio`，则自动削减放电功率。

充电时：

```text
charge = max(command, max_charge_power)
if demand_load - charge > transform_capacity:
    charge = -(transform_capacity - demand_load)
inner_energy_vari = charge * charge_efficiency * time_lag
soc_new = soc - inner_energy_vari
```

因为充电命令为负，`soc - inner_energy_vari` 实际是 SOC 增加。若超过 `battery_capacity * usable_depth`，则自动削减充电功率。

空闲时命令为 0，SOC 不变。

## 输出数据

`simulation_process()` 输出三张表：

| 输出 | 含义 |
| --- | --- |
| `es_charge_df` | 实际执行的储能功率，正值放电、负值充电 |
| `es_soc_df` | 每个时间步仿真后的 SOC |
| `total_load_df` | 优化后总负荷，含 `total_load` 和 `es_load` |

总负荷计算为：

```text
total_load = demand_load - es_charge
```

因此：

- 放电时 `es_charge > 0`，总负荷下降。
- 充电时 `es_charge < 0`，总负荷上升。

## 收益计算

`revenue_calculation()` 逐时间步计算原始电度电费和优化后电度电费：

```text
origin_balance += demand_load[t] * dt * price[t]
opt_balance += (demand_load[t] - es_load[t]) * dt * price[t]
```

`EssSimulation.py` 额外计入最大需量电费：

```text
origin_balance += max_demand_price * max(original_load)
opt_balance += max_demand_price * max(optimized_load)
```

调用脚本通常会进一步按月重算：

```text
ori_max_demand_cost = max_demand_price * sum(monthly_max(original_load))
opt_max_demand_cost = max_demand_price * sum(monthly_max(optimized_load))
revenue = origin_energy_cost - opt_energy_cost - (opt_max_demand_cost - ori_max_demand_cost)
```

## 适用边界

- 仿真器只回放已有策略，不产生优化策略。
- 单步逻辑会修正越界功率，因此仿真结果可能与优化器原始策略有差异。
- 当前仿真模型是单节点口径，不处理分布式 allocation 和 PV 分流。
- 跨月或跨日 SOC 是否连续取决于调用方传入的 `last_soc`，多数现有脚本使用 0 作为初始 SOC。
