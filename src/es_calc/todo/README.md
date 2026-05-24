# es_calc/todo 历史算法盘点

## 目录定位

`src/es_calc/todo/` 不是正式算法入口，而是储能测算在多个项目阶段留下的历史脚本、客户场景、实验 notebook 和旧版模型拷贝。它的价值主要在于追溯算法思路，而不是直接复用代码。

正式版本已经沉淀在以下目录：

- `es_calc_basic_version`：保守单节点收益测算。
- `es_calc_optim_version`：激进单节点月度优化。
- `es_calc_without_demand_version`：无需量目标的电度套利测算。
- `es_calc_distribution_version`：分布式储能和多变压器测算。
- `es_simulation`：通用单节点仿真回放。

## 内容归类

| 类别 | 代表内容 | 与正式版本的关系 |
| --- | --- | --- |
| `backup/` | `optimization_basic.py`, `optimization_optim.py`, `simulation.py` | 正式 basic/optim/simulation 的早期备份或前身 |
| `es_schedule_for_MaxDemand_*` | `iflytek`, `FuDing`, `test`, `tiktok_example`, `830` | 客户或场景项目脚本，包含大量重复模型和硬编码路径 |
| MaxDemand 系列的 `models/` 子目录 | `EsArbitraryRangeScheduler_withMaxDemand_basic.py`, `optim.py`, `online.py` | 正式 scheduler 的早期模块化尝试 |
| `optimization_traversalOptLineForMonth_*` | 固定需量线遍历 | 可提取为“外层需量线搜索”算法思想 |
| `optimization_2stageIdealForMonth_*` | 两阶段理想需量线实验 | 可提取为“两阶段先定需量线再调度”的候选方案 |
| `optimization_evenchargeForMonth_*` | 均匀充电或慢充实验 | 与 basic 版本缓充惩罚、保守策略有关 |
| `optimization_optForMonth_*_online_True/False` | online/offline 月度优化对比 | 可提取为仿真假设和上线策略边界 |
| 统计 notebook | `full_discharge_amount_by_month.ipynb`, `total_discharge_times.ipynb`, `hour_result_625.csv` | 可提取运行强度指标 |
| `online_main.py`, `online_main_local.py` | 外部模型包调用入口 | 不适合直接纳入当前源码，适合保留接口口径参考 |

## 与正式版本的主要区别

### 代码组织

正式版本按“算法版本目录 + scheduler + simulation”组织，入口和输出目录相对稳定。`todo/` 中很多脚本把模型、数据读取、参数、仿真和结果保存写在同一个文件里，并且依赖客户名、月份、路径和特定实验编号。

因此 `todo/` 代码不应直接搬入正式目录。更稳的方式是提取算法思想，再用正式版本的接口重新实现。

### 时间尺度

正式版本主要是：

- basic：按天优化，仿真汇总。
- optim 和 without_demand：按月优化。
- distribution：按组合和月份或规则窗口生成策略。

`todo/` 里还存在：

- 按 5 分钟、15 分钟、60 分钟混用的实验。
- 从上月末延伸到本月末的窗口构造。
- 按小时对一个月滚动生成策略的实验。

这些时间尺度设计有参考价值，但必须先统一为明确的输入 contract，不能混入正式版本。

### 需量口径

正式 optim 版本直接在目标函数中惩罚优化后最大需量：

```text
max_demand_price * max(demand_after_storage)
```

`todo/` 中还出现了另一类思路：先给定最大需量控制线 `max_demand_line`，约束：

```text
demand_load[t] - e_c_in_agg[t] <= max_demand_line
```

然后遍历不同突破比例或控制线，回放仿真并选择收益最高的比例。这是值得提取的独立算法思想。

### 充放电窗口

正式部分版本把峰谷平硬约束保留为注释或使用固定窗口规则。`todo/` 中一些脚本启用了更强的时段约束：

```text
谷段禁止放电
峰段和尖峰禁止充电
平段禁止放电
```

这类规则适合作为策略模板，但需要和当前正式版本的“软目标/硬窗口”语义区分清楚。

## 可提取算法信息

### 固定需量控制线遍历

来源：

- `optimization_traversalOptLineForMonth_solveByDay_calrabel.py`
- `optimization_2stageIdealForMonth_solveByDay_calrabel_multiprocess.py`
- `dev/model_A.py`

核心思想：

1. 外层枚举需量线比例，例如 `ratio in range(10, 240, 10)`。
2. 对每个比例构造控制线：

   ```text
   max_demand_control_line = base_load_stat * (1 + ratio / 1000)
   ```

3. 内层调度时加入约束：

   ```text
   demand_load[t] - e_c_in_agg[t] <= max_demand_control_line
   ```

4. 对每个比例生成策略并仿真收益。
5. 选择收益最高的比例作为推荐控制线。

提取原因：

- 它比直接把最大需量写进目标函数更容易解释给业务方。
- 可以显式展示“允许多少需量突破”和“收益”的权衡曲线。
- 适合上线前做控制线标定。

建议提取方式：

- 新建独立设计，不复用历史脚本。
- 抽象参数为 `control_line_base`, `ratio_grid`, `simulation_metric`。
- 输出 `ratio`, `control_line`, `revenue`, `max_demand_delta`, `schedule_path`。

### online/offline 调度口径

来源：

- `optimization_optForMonth_solveByMonth_calrabel_online_True.py`
- `optimization_optForMonth_solveByMonth_calrabel_online_False.py`
- `simulation_totalYear_optForMonth_online_True.py`
- `simulation_totalYear_optForMonth_online_False.py`

核心思想：

- offline：用完整月份数据优化，代表理论上限。
- online：按滚动或受限信息生成策略，更接近实际控制。

提取原因：

- 当前正式 optim 版本偏离线最优，收益可能高估。
- 文档和后续实现需要明确“预测完美假设”和“在线控制假设”的差异。

建议提取方式：

- 先形成文档级口径，不急于抽代码。
- 在正式算法说明中增加收益解释边界：offline 收益上限、online 收益可执行性更强。
- 若后续实现，统一以 `inference_window`、`control_window`、`soc_carryover` 三个参数描述。

### 等效充放电统计指标

来源：

- `full_discharge_amount_by_month.ipynb`
- `total_discharge_times.ipynb`
- `simulation_totalYear_optForMonth_moreInfo.ipynb`
- `hour_result_625.csv`

可提取指标：

```text
actual_charge_hours = count(power < 0) * dt
actual_discharge_hours = count(power > 0) * dt
equivalent_charge_cycles = sum(-power[power < 0]) * dt / usable_capacity
equivalent_discharge_cycles = sum(power[power > 0]) * dt / usable_capacity
```

提取原因：

- 收益指标不能反映储能使用强度。
- 等效循环次数可辅助评估电池寿命、运维压力和收益质量。

建议提取方式：

- 后续可在 simulation summary 中增加运行强度字段。
- 字段命名建议保持英文内部 key，例如 `equivalent_charge_cycles`，导出层再补中文说明。

### 慢充和平滑倾向项

来源：

- `is_slow_charge`
- `lamda_amortize`
- `cp.norm(e_c_in_agg_vec)`
- 按峰谷平给 `soc_agg_vec` 加微小奖励或惩罚

核心思想：

- 惩罚集中充电，降低需量抬升风险。
- 通过 SOC 微小权重引导在峰前保持电量、在高价时段释放电量。

提取原因：

- 这是 basic 版本保守性的历史来源。
- 对实际 EMS 更友好，能减少策略抖动和尖峰充电。

建议提取方式：

- 保留为软约束设计项，不作为默认硬规则。
- 参数化为 `smooth_charge_weight`、`soc_price_type_weight`。
- 在文档中说明它影响策略形态，不应被解释为严格收益项。

## 不建议提取的内容

以下内容不应直接进入正式算法：

- 客户目录级 `main.py`、`main1.py`、`main2.py`。
- `.zip` 文件和中间 CSV。
- 硬编码的 `exp_name`、`node_name`、月份、容量、路径。
- 多份重复的 `EssSimulation.py` 和 `EssSimulation_withoutMaxDemand.py`。
- 只用于探索的 notebook 单元输出。
- 依赖外部 `model.model_packages.*` 的入口脚本。

保留这些文件作为历史证据即可。若要正式化，先写接口和测试，再从算法思想重建实现。

## 建议后续路线

1. 先完成当前正式版本 README，统一算法口径。
2. 再单独设计“需量控制线遍历”扩展，不直接修改现有 optim 版本。
3. 将等效充放电指标作为仿真 summary 的独立增强项。
4. 若要做 online 策略，先定义预测窗口、控制窗口和 SOC 续接规则。
5. 最后再评估是否清理 `todo/`，清理前必须先确认历史材料是否还需要保留。
