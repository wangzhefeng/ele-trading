# pv_es_calc 光伏储能测算

`pv_es_calc` 是面向“光伏 + 用户负荷 + 储能”的收益测算模块，核心目标是同时评估：

- 光伏自发自用与上网收益；
- 储能峰谷套利；
- 储能削减最大需量带来的需量电费变化；
- 不同 v1-v5 策略口径下的收益差异。

## 合并后的主线结构

当前新增的统一主线保留三类核心文件：

| 文件 | 职责 |
| --- | --- |
| `optimization/EsArbitraryRangeScheduler_withMaxDemand.py` | 统一调度器，按 `method_version` 执行 v1-v4 LP 或 v5 规则调度 |
| `optimization.py` | 读取 YAML、加载数据、构造设备参数、批量容量搜索并输出策略 CSV |
| `simulation.py` | 读取策略 CSV，计算 baseline、优化后成本、收益拆分和汇总 CSV |

旧的 `optimization_optim_pv_v1.py` 到 `optimization_optim_pv_v5.py`、旧 scheduler 和 `simulation_pv.py` 暂未删除，避免破坏历史入口。后续若要满足严格“只保留三类主文件”的目录状态，需要单独确认删除。

## 配置文件

默认配置位于：

```text
src/pv_es_calc/config/pv_es_calc.yaml
```

主要配置分组：

| 分组 | 含义 |
| --- | --- |
| `data` | 默认测试数据目录、CSV 文件名、列名、编码 |
| `run` | 起止时间、时间分辨率、默认版本、容量列表、输出目录 |
| `storage` | 储能可用深度、效率、变压器容量、容量倍率、初始 SOC |
| `market` | 需量电价、光伏上网电价 |
| `objective` | 平滑、放电优先、SOC 软目标等通用目标权重 |
| `version_methods` | v1-v5 的版本差异权重和调度类型 |
| `plot` | 绘图开关、默认绘图时间范围和保存目录 |

默认测试数据使用：

```text
data/profit_calc/pv_es/demand_load.csv
data/profit_calc/pv_es/pv_load.csv
data/profit_calc/pv_es/ele_price.csv
```

`ele_price.csv` 带 BOM，配置中默认使用 `utf-8-sig` 读取。

## v1-v5 版本语义

| 版本 | 调度类型 | 版本差异 |
| --- | --- | --- |
| `v1` | LP | 基础光储线性规划，午间 PV 偏好权重为 0 |
| `v2` | LP | 午间奖励 `pv_to_battery`，鼓励光伏优先充储 |
| `v3` | LP | 午间奖励 `pv_to_load`，鼓励光伏优先供本地负荷 |
| `v4` | LP | 午间惩罚 `pv_to_grid`，减少午间光伏上网 |
| `v5` | rule-based | PV 先供负荷，固定窗口电网充电，放电窗口补负荷 |

v1-v4 走统一 cvxpy LP 路径，版本差异只通过目标函数权重控制。v5 不求解 LP，按规则递推。

## 运行命令

容量搜索并生成策略结果：

```bash
uv run python src/pv_es_calc/optimization.py
```

指定版本：

```bash
uv run python src/pv_es_calc/optimization.py --method-version v4
```

指定配置：

```bash
uv run python src/pv_es_calc/optimization.py --config src/pv_es_calc/config/pv_es_calc.yaml --method-version v5
```

根据策略结果生成收益汇总：

```bash
uv run python src/pv_es_calc/simulation.py
```

默认输出路径按版本区分：

```text
data/profit_calc/pv_es/opt_result-v4/es_scale_experiment_optim/
data/profit_calc/pv_es/opt_result-v4/estimate_result_scale_all_optim.csv
```

## 统一调度输出字段

策略 CSV 保持与历史口径兼容：

| 字段 | 含义 |
| --- | --- |
| `value` | 储能净功率，正值放电、负值充电 |
| `pv_to_load` | 光伏直接供本地负荷 |
| `pv_to_battery` | 光伏给储能充电 |
| `pv_to_grid` | 光伏上网 |
| `grid_to_load` | 电网供本地负荷 |
| `grid_to_battery` | 电网给储能充电 |
| `battery_charge` | 储能充电功率 |
| `battery_discharge` | 储能放电功率 |
| `grid_import` | 电表侧购电功率 |
| `soc` | 储能电量 |
| `net_load_after_dispatch` | 调度后净购电功率 |

核心能量守恒：

```text
pv_to_load[t] + pv_to_battery[t] + pv_to_grid[t] = pv_load[t]
pv_to_load[t] + battery_discharge[t] + grid_to_load[t] = demand_load[t]
grid_import[t] = grid_to_load[t] + grid_to_battery[t]
value[t] = battery_discharge[t] - battery_charge[t]
```

## 收益口径

`simulation.py` 内部保持英文 key，最终 CSV 导出层才追加中文说明列名。

核心口径：

```text
revenue = baseline_cost - opt_cost
baseline_cost = baseline_energy_cost + baseline_max_demand_cost - baseline_pv_sell_revenue
opt_cost = energy_cost + max_demand_cost - pv_sell_revenue
```

其中 baseline 是“有光伏、无储能”的光伏基准，而不是“无光伏无储能”的纯负荷基准。`load_only_max_demand_cost` 仅作为对照字段。

## 绘图工具

旧 `simulation_pv.py` 中的策略明细绘图已迁移到项目级工具：

```python
from utils.pv_es_plot import plot_strategy_power_detail
```

新函数直接接收 `demand_load_df`、`pv_load_df`、`ele_price_df`、`strategy_df` 或显式保存路径，不再硬编码 `data/{exp_name}/{node_name}`。

## 验证命令

```bash
uv run python -m pytest -q tests/test_pv_es_calc_scheduler.py
uv run python -m pytest -q tests/test_pv_es_calc_optimization.py tests/test_pv_es_calc_simulation.py tests/test_pv_es_plot.py
uv run python src/pv_es_calc/optimization.py
uv run python src/pv_es_calc/simulation.py
uv run python -m pytest -q
git diff --check
```
