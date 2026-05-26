# 优化模块说明

优化模块承接价格、场景和储能参数，输出申报或调度结果。

## 当前文件

- `interfaces.py`：统一优化输入输出结构。
- `storage_arbitrage.py`：单市场储能套利。
- `mpc_storage.py`：MPC 单窗口与滚动优化。
- `two_stage_cvar.py`：Two-stage + CVaR 可求解模型。
- `user_side_storage_dispatch.py`：用户侧 / 园区侧储能调度模型。
- `user_side_pv_dispatch.py`：用户侧 / 园区侧光伏调度模型。
- `user_side_pv_storage_dispatch.py`：用户侧 / 园区侧光伏+储能调度模型。

## 市场储能纯价格套利

`storage_arbitrage.py` 实现的是“单市场储能纯价格套利”模型。它把储能视为可以直接参与电能量市场结算的独立资产，在已知价格序列下决定每个时段充电、放电或不动作，目标是最大化价格套利收益。

### 适用场景

该模型适合以下问题：

- 独立储能电站、共享储能或聚合商资源在单一电能量市场中按价格买低卖高。
- 用历史价格曲线评估某地区、某容量配置下的储能套利潜力。
- 作为复杂模型的 benchmark，判断更复杂的园区模型、MPC 模型或多市场模型是否有合理收益来源。
- 快速比较不同功率、容量、效率、退化成本下的理论收益上限。

它不描述用户侧或园区侧综合用能优化，因此不需要未来负荷预测。负荷预测属于电表后模型的输入，通常用于削峰、需量电费优化、光伏自用、反送电限制或园区综合成本优化。

### 模型输入

当前函数 `solve_storage_arbitrage()` 的核心输入是：

- `prices[t]`：每个时段的市场电价。
- `soc0`、`soc_min`、`soc_max`：初始 SOC 和容量上下界。
- `p_ch_max`、`p_dis_max`：最大充电 / 放电功率。
- `eta_ch`、`eta_dis`：充放电效率。
- `deg_cost`：按充放电吞吐量近似的线性退化成本。
- `dt`：时间步长。
- `enforce_terminal_soc`：是否强制末端 SOC 回到初始值。

### 决策变量与约束

每个时段包含：

- `p_ch[t]`：充电功率。
- `p_dis[t]`：放电功率。
- `soc[t]`：时段末 SOC。
- `u_ch[t]`、`u_dis[t]`：充放电互斥的二进制状态。

核心约束包括：

```text
u_ch[t] + u_dis[t] <= 1
p_ch[t]  <= p_ch_max  * u_ch[t]
p_dis[t] <= p_dis_max * u_dis[t]
```

以及 SOC 动态：

```text
soc[t] = soc[t-1] + eta_ch * p_ch[t] * dt - p_dis[t] * dt / eta_dis
```

首时段用 `soc0` 作为上一时段 SOC。若 `enforce_terminal_soc=True`，则额外约束 `soc[last] == soc0`，避免模型通过透支末端 SOC 获得不可重复收益。

### 目标函数

目标函数是：

```text
max sum_t price[t] * (p_dis[t] - p_ch[t]) * dt
        - deg_cost * (p_ch[t] + p_dis[t]) * dt
```

也就是“放电卖电收入 - 充电买电成本 - 线性退化成本”。在当前样例里 `deg_cost` 很小，因此调度结果主要由电价价差驱动。

### 结果解释

返回结果包含：

- `objective`：最优目标函数值，可理解为该价格曲线和储能参数下的理论套利收益。
- `p_ch`：各时段充电功率；低价时段通常为正。
- `p_dis`：各时段放电功率；高价时段通常为正。
- `soc`：各时段末 SOC，用于检查充放电是否符合容量和效率约束。

解释结果时不要只看最高价和最低价。模型会在功率限制、容量限制、效率损耗、SOC 下限和可选末端 SOC 约束下做全局分配。因此同一条价格曲线可能出现多次“低价充电 -> 高价放电”循环，也可能因为容量已满、SOC 已到下限或未来价差更大而选择暂时不动作。

### 与用户侧 / 园区侧调度的区别

市场储能纯价格套利只关心市场价格和储能物理约束，适合做套利基准和收益上限评估。用户侧 / 园区侧调度还需要负荷预测、光伏 / 风电预测、购售电规则、需量电费、并网限制和反送电约束，目标通常是最小化综合用能成本或最大化园区综合收益。

如果后续需要加入负荷预测，不应直接把 `load_forecast` 塞进当前模型，而应新增或拆出用户侧储能调度模型，显式建模 `grid_import`、`grid_export`、负荷平衡、需量电费和新能源消纳等约束。

## 用户侧 / 园区侧储能调度

`user_side_storage_dispatch.py` 实现的是电表后用户侧储能调度模型。它与市场储能纯价格套利不同：模型输入包含未来负荷预测，目标是最小化园区购电成本和需量电费，而不是让储能作为独立市场资产按价格买低卖高。

当前版本只考虑储能设备、负荷预测、购电价格、分时电价类型和需量电费；暂不考虑光伏 / 风电出力、售电价格、上网电量和站点策略后处理。

### 模型定位

市场储能纯价格套利是表前或独立储能视角，储能按市场价格低买高卖。用户侧 / 园区侧储能调度是电表后成本优化视角，储能的动作会改变园区电表购电曲线，因此必须同时考虑负荷预测、购电价格、需量电费和储能状态。

当前模型是用户侧调度的核心优化层，不包含平台数据接入、预测模型生成、站点策略后处理或多设备滚动执行编排。

### 当前数学模型

核心输入包括：

- `load_forecast[t]`：未来各时段负荷预测。
- `buy_price[t]`：未来各时段购电价格。
- `price_type[t]`：分时电价类型，当前作为结果上下文保留，暂不施加硬规则。
- `storage`：储能容量、SOC 上下限、充放电功率上限和效率。
- `initial_soc`：调度窗口初始 SOC。
- `demand_charge_rate`：最大需量电价。
- `step_hours`：时间步长。
- `terminal_soc_target`：可选末端 SOC 目标。
- `cycle_cost_rate`：可选储能吞吐成本。

决策变量包括：

- `charge_power[t]`：充电功率。
- `discharge_power[t]`：放电功率。
- `soc[t]`：时段末 SOC。
- `grid_import[t]`：电表购电功率。
- `max_grid_import`：调度窗口最大电表购电功率。
- `is_charging[t]`、`is_discharging[t]`：充放电互斥状态。

目标函数是最小化：

```text
energy_cost + demand_cost + cycle_cost
```

其中：

```text
energy_cost = sum_t buy_price[t] * grid_import[t] * step_hours
demand_cost = demand_charge_rate * max_grid_import
cycle_cost = sum_t cycle_cost_rate
                  * (charge_power[t] + discharge_power[t])
                  * step_hours
```

核心电表侧平衡为：

```text
grid_import[t] = load_forecast[t] + charge_power[t] - discharge_power[t]
```

其中 `grid_import[t] >= 0`，并且 `discharge_power[t] <= load_forecast[t]`，因此当前模型默认禁止反送电。需量电费通过最大购电功率变量建模：

```text
max_grid_import >= grid_import[t]
demand_cost = demand_charge_rate * max_grid_import
```

SOC 动态为：

```text
soc[t] = soc[t-1]
         + eta_ch * charge_power[t] * step_hours
         - discharge_power[t] * step_hours / eta_dis
```

首时段使用 `initial_soc` 作为上一时段 SOC。若设置 `terminal_soc_target`，模型会约束窗口末端 SOC 等于该目标。

充放电互斥为：

```text
is_charging[t] + is_discharging[t] <= 1
charge_power[t] <= p_ch_max * is_charging[t]
discharge_power[t] <= p_dis_max * is_discharging[t]
```

结果包括充电功率、放电功率、净储能功率、SOC、电表购电功率、最大需量、能量电费、需量电费、总成本和约束违约检查。解释结果时应同时看 `grid_import` 和 `max_grid_import`：前者反映逐时购电曲线，后者决定需量电费。

### 当前实现程度

| 能力 | 当前状态 |
| --- | --- |
| 负荷预测输入 | 已实现 |
| 储能 SOC 递推 | 已实现 |
| 充放电互斥 | 已实现 |
| 电表侧购电功率 | 已实现 |
| 最大需量建模 | 已实现 |
| 禁止反送电 | 已实现 |
| 放电不超过负荷 | 已实现 |
| 结果约束检查 | 已实现 |
| 多设备联合调度 | 未实现 |
| 滚动控制封装 | 未实现 |
| 光伏 / 风电出力 | 暂不考虑 |
| 售电 / 上网电量 | 暂不考虑 |
| legacy 策略后处理 | 暂不接入 |
| `price_type` 峰谷平尖硬规则 | 暂未使用 |
| 平台数据预处理适配层 | 未实现 |

### 相对 `es_rolling_schedule` 未迁入的内容

当前 `ele_trading` 新模型没有原样搬迁 `src/es_rolling_schedule/`，以下能力仍保留在 legacy 目录或尚未进入主线：

- 5 分钟平台数据预处理，包括 `df_load`、`df_price`、`df_soc`、`df_date`、`df_weather` 的时间索引对齐、缺失插值和负荷负值处理。
- 按 `es_cycle_division_hour` 拆分第一优化周期和第二优化周期的滚动执行逻辑。
- 多设备逐设备策略输出和 `e_c_opt_node_id` 映射。
- 实时 SOC 数据滞后检查。
- 上海特化谷 / 深谷电价拉平工具。
- 峰、谷、平、尖等电价类型硬规则。
- 月度重复收益近似。
- cvxpy 版本中的平滑项、SOC 偏置项和启发式惩罚。
- `post_handler_lingang` 中的峰尖连续块识别、最大功率放电、冷静期等策略后处理。
- `shortTerm_maxDemand_match` 短期需量匹配逻辑。

这些内容没有迁入不是遗漏，而是当前版本刻意把主线收敛为可测试的优化核心。后续若要补齐，应优先增加数据适配层、滚动控制封装和多设备建模，再考虑站点策略后处理。

### 新增能力

相对 legacy 实现，当前 `ele_trading` 主线新增了以下工程化能力：

- 使用 `UserSideStorageDispatchInput`、`UserSideStorageParams`、`UserSideStorageDispatchResult` 明确输入输出边界。
- 使用 PuLP 建立 MILP 模型，与现有 `storage_arbitrage.py`、`mpc_storage.py` 的求解器路径一致。
- 使用标准 `max_grid_import` 变量表达需量电费，而不是用充电功率近似需量。
- 显式建模 `grid_import`，并通过变量下界禁止反送电。
- 输出 `constraint_violations`，便于解释结果和发现模型约束异常。
- 已有单元测试覆盖接口、需量削峰、峰谷价差响应、负荷敏感性和输入校验。

## 用户侧 / 园区侧光伏调度

`user_side_pv_dispatch.py` 处理只有光伏、没有储能的电表后调度场景。模型输入未来负荷预测、光伏预测、购电价格、分时电价类型、需量电费和光伏余电处理规则，输出光伏自用、上网、弃光、电网购电和成本。

该模型没有可控储能设备，因此不引入求解器，而是做确定性能量分配：

```text
pv_to_load[t] = min(load_forecast[t], pv_forecast[t])
grid_import[t] = max(load_forecast[t] - pv_to_load[t], 0)
```

当光伏大于负荷时，余电由 `UserSidePVExportParams` 控制：

- `allow_export=True`：余电可上网，受 `export_limit` 限制。
- `allow_export=False`：余电不可上网，进入 `pv_curtailment`。
- `curtailment_cost_rate` 可用于给弃光加惩罚成本。

成本口径为：

```text
total_cost = energy_cost + demand_cost + curtailment_cost - sell_revenue
```

该模型适合作为“光伏基准”或“无储能 baseline”，用于解释 PV-only 与 PV+storage 的增量收益。

## 用户侧 / 园区侧光伏+储能调度

`user_side_pv_storage_dispatch.py` 在光伏调度基础上加入储能设备，使用 PuLP 建立 MILP。它参考 `pv_es_calc` 的能量流字段，但不复刻 v1-v5 版本体系，也不默认套用固定充放电窗口。

核心能量流约束：

```text
pv_to_load[t] + pv_to_storage[t] + pv_to_grid[t] + pv_curtailment[t] = pv_forecast[t]
pv_to_load[t] + discharge_power[t] + grid_to_load[t] = load_forecast[t]
charge_power[t] = pv_to_storage[t] + grid_to_storage[t]
grid_import[t] = grid_to_load[t] + grid_to_storage[t]
max_grid_import >= grid_import[t]
```

SOC 动态、充放电互斥和储能上下限沿用用户侧储能模型：

```text
soc[t] = soc[t-1]
         + eta_ch * charge_power[t] * step_hours
         - discharge_power[t] * step_hours / eta_dis

is_charging[t] + is_discharging[t] <= 1
charge_power[t] <= p_ch_max * is_charging[t]
discharge_power[t] <= p_dis_max * is_discharging[t]
```

目标函数最小化：

```text
energy_cost + demand_cost + cycle_cost + curtailment_cost - sell_revenue
```

可选 `UserSideDispatchPolicy` 提供轻量策略规则：

- `charge_allowed_hours`：非允许时段禁止 `pv_to_storage` 和 `grid_to_storage`。
- `discharge_allowed_hours`：非允许时段禁止放电。
- `pv_to_storage_reward_rate` / `pv_to_load_reward_rate` / `pv_export_penalty_rate`：作为目标函数中的软偏好项。

默认不启用策略规则，此时模型是纯经济优化：由负荷预测、光伏预测、价格、需量成本、售电规则和储能约束共同决定调度。

## 上下游关系

- 上游依赖数据模块、预测模块和场景模块。
- 下游结果被控制模块、评估模块和 app 脚本使用。

## 扩展建议

后续可增加风光储联合调度、多市场联合收益、偏差考核约束和鲁棒优化。
