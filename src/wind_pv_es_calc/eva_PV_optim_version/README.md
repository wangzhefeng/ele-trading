# eva_PV_optim_version

该目录保留为 legacy reference。

用途边界：

- 作为历史负荷重建、PV 仿真、风电资源回标和样例数据拼装的参考实现。
- 作为主线重构后的回归对照来源。

不再承担的职责：

- 不再作为 `ele_trading` 主线的数据入口。
- 不再继续扩展新的主业务功能。
- 不再作为收益测算和电力市场交易的数据层标准实现。

当前主线对应能力已迁入或正在迁入：

- `ele_trading.data_provider.load_profile`
- `ele_trading.data_provider.resource_weather`
- `ele_trading.data_provider.case_dataset`
- `ele_trading.capacity_planning.pv_profile`
- `ele_trading.capacity_planning.wind_profile`
- `ele_trading.capacity_planning.capacity_optimizer`（已整合 `storage_optim_PV_BESS` 的调度、剪枝、能量估算能力）
- `ele_trading.capacity_planning.wind_bess_planner`（已整合 `storage_optim_Wind_BESS_combine` 的 Wind+BESS 容量规划能力）
- `ele_trading.capacity_planning.wind_pv_bess_planner`（已整合 `storage_optim_Wind_PV_BESS_combine` 的 Wind+PV+BESS 容量规划能力）

若需要新增主线功能，应优先修改 `src/ele_trading/` 下对应模块，而不是在本目录追加新脚本。

---

## 开发记录

### 2026-05-27 Session 1：主线数据层重构与 legacy 桥接

#### 已完成：主线数据层重构

在 `src/ele_trading/` 下新增并接通以下模块：

- `data_provider/time_series_ops.py`
- `data_provider/load_profile.py`
- `data_provider/resource_weather.py`
- `data_provider/case_dataset.py`
- `capacity_planning/pv_profile.py`
- `capacity_planning/wind_profile.py`

并扩展：

- `data_provider/schemas.py`、`data_provider/loader.py`、`data_provider/__init__.py`
- `capacity_planning/__init__.py`
- `forecasting/renewable_forecast.py`、`forecasting/__init__.py`

作用：将本目录的负荷、PV、风电预处理逻辑抽成主线可复用数据层，支持 `load_profile`、`pv_profile`、`wind_profile`、`case_dataset` 四类标准能力。

#### 已完成：legacy 数据桥接

新增 `prepare_legacy_temp_data.py`：

- 读取 `configs/wind_pv_es_calc_data_bridge.yaml`
- 用主线 `ele_trading` 框架生成或复用 legacy 兼容数据
- 输出到 `data/wind_pv_es_calc/temp/` 下四份 CSV

兼容口径：

- `df_2025.csv`：`Time`, `P_kw`（15 分钟频率）
- `df_pv_2025.csv`：`Time`, `pv_kw`（15 分钟频率）
- `df_wind_2025.csv`：`Time`, `WindPower_MW`（1 小时频率）
- `df_total.csv`：合并后含 `Wind_kw`、`NetLoad_kw`

#### 已完成：新增运行入口

- `app/run_wind_pv_legacy_profit_eval.py` — 年度收益测算
- `app/run_wind_pv_legacy_market_trading.py` — 风光储交易调度 demo

对应配置：

- `configs/wind_pv_es_calc_data_bridge.yaml`
- `configs/wind_pv_legacy_profit_eval.yaml`
- `configs/wind_pv_legacy_market_trading.yaml`

#### 已完成：测试与文档

- `tests/test_data_layer_generalization.py`
- `tests/test_legacy_data_bridge.py`
- `tests/test_entry_scripts.py`
- `app/README.md`、`configs/README.md`

验证状态：124 passed（全量测试通过）。

### 2026-05-27 Session 2：P3 数据入口统一

#### 已完成：算法文件数据路径迁移

将本目录下 7 个活跃算法文件的数据读取路径从已不存在的 `src/ba_eva/dataset/temp/` 统一迁移到 `data/wind_pv_es_calc/temp/`：

| 文件 | 路径替换 | import 修复 |
|---|---|---|
| `data_processing.py` | 4 处 | 无需（已正确） |
| `storage_optim_PV_BESS.py` | 3 处 | 3 处 |
| `storage_optim_Wind_BESS_1.py` | 3 处 | 3 处 |
| `storage_optim_Wind_BESS_2.py` | 4 处 + 2 处注释 | 4 处 |
| `storage_optim_Wind_BESS_3.py` | 2 处 | 2 处 |
| `storage_optim_Wind_PV_BESS_1.py` | 3 处 | 4 处 |
| `storage_optim_Wind_PV_BESS_3.py` | 2 处注释 | 3 处 |

额外修正：

- 3 个文件中 `df_wind_2026.csv` 笔误改为 `df_wind_2025.csv`（与 bridge 输出一致）
- `plot_ts` import 统一为 `from utils.plot_ts import ...`（实际位置）
- `ba_eva.storage_optim_common` import 统一为 `from wind_pv_es_calc.storage_optim_common import ...`
- `ba_eva.eva_PV_optim_version.*` import 统一为 `from wind_pv_es_calc.eva_PV_optim_version.* import ...`

未改动：`backup/` 目录、`ba_eva_optim_version/` 目录。

验证状态：124 passed，`grep ba_eva` 在本目录 `*.py` 下已无残留。

### 2026-05-27 Session 3：storage_optim_PV_BESS 算法整合

#### 已完成：算法能力融合

将 `storage_optim_PV_BESS.py` 的核心能力整合进 `ele_trading.capacity_planning.capacity_optimizer`，不新建模块，作为 `CapacityOptimizer` 的运行模式之一。

整合内容：

| legacy 能力 | 主线实现 | 状态 |
|---|---|---|
| `dispatch_numba` 贪心调度（C-rate、eta_roundtrip sqrt 分配） | `_simulate_op` 增强：支持 `eta_roundtrip`、`c_rate`、`soc_max_frac`、`soc_init_frac` | 已完成 |
| 快速剪枝（年发电量 < 覆盖率目标 × 年负荷 → 跳过） | `_grid_search` 在 ess 循环前按年度能量剪枝 | 已完成 |
| `simple_energy_sanity_check`（固定年利用小时估算 PV MWp 下界） | 新增同名函数，输出所需 PV MWp 表 | 已完成 |
| `curve_based_energy_check`（实际曲线年发电量估算） | 新增同名函数，输出所需 PV MWp | 已完成 |
| `infer_dt_hours`、`monthly_kwh` 工具函数 | 移入 `ele_trading.utils.time_index` | 已完成 |
| PV-only 搜索模式（fixed_wind_mw=0） | `CapacityOptimizer.optimize` 原有 `fixed_wind_mw` 参数支持 | 已完成 |
| `pv_monthly_kwh` 输出 | `CapacityPlanResult` 新增 `pv_monthly_kwh` 字段 | 已完成 |

未引入的能力（有意不引入）：

- **Numba JIT 加速**：ele_trading 无 Numba 依赖，当前规模下纯 Python 可接受
- **`PlanConfigFast` 配置类**：沿用 dict + 默认值模式，不引入额外配置类
- **`ShiftPolicy`（前瞻充电）**：当前场景不需要，后续按需扩展
- **月度约束模式（`constraint_mode`）**：当前仅年度约束，后续按需扩展

#### 已完成：配置与运行脚本完善

`configs/capacity_planning.yaml` 补全：

- 新增 `scenario` section：经纬度、时区、等效小时、负荷均值
- 新增 `constraints` section：绿电率、自用率约束
- 补全 `search` section：`max_wind_mw`、`max_pv_mw`、`max_ess_mwh` 搜索上界

`app/run_wind_solar_storage.py` 重构：

- 从 YAML 加载参数，去掉硬编码常量
- 封装 `run_scenario(name, config)` 函数
- 新增 3 个应用场景示例：
  - A：风光储联合优化（北京工业用户）
  - B：PV-only 最小投资（南方园区）
  - C：高绿电率碳中和方案（出口型企业）

#### 已完成：测试

新增测试（`tests/test_capacity_optimizer.py`）：

- `test_pv_only_mode`：fixed_wind_mw=0 退化为 PV-only 搜索
- `test_eta_roundtrip_dispatch`：eta_roundtrip sqrt 分配等价性
- `test_pv_monthly_kwh_in_result`：月度 PV 发电量输出
- `test_infer_dt_hours`：小时级 / 15 分钟步长推断
- `test_monthly_kwh`：月度电量汇总
- `test_simple_energy_sanity_check`：固定利用小时估算
- `test_curve_based_energy_check`：实际曲线估算

验证状态：131 passed（全量测试通过）。

#### 整合结论

`storage_optim_PV_BESS.py` 的核心算法能力（调度模拟、快速剪枝、能量估算、工具函数）已全部整合进 `ele_trading.capacity_planning.capacity_optimizer`。本目录的 `storage_optim_PV_BESS.py` 保留为 legacy 参考实现，不再需要为主线功能提供算法逻辑。

### 2026-05-28 Session 4：Wind+BESS 算法整合

#### 已完成：算法文件合并

对比 `storage_optim_Wind_BESS_2.py`（高级版，shift 策略 + lookahead + terminal SOC）和 `storage_optim_Wind_BESS_3.py`（简化版，纯弃电搬运），合并为 `storage_optim_Wind_BESS_combine.py`：

| 功能 | BESS_2 来源 | BESS_3 来源 | 合并方式 |
|---|---|---|---|
| 纯弃电搬运模式 | — | `simulate_surplus_shift()` | 作为 `enable_shift=False` 模式 |
| 平移充电模式 | `simulate_dispatch_offgrid_shiftable()` | — | 作为 `enable_shift=True` 模式 |
| 二分搜索 | — | `find_min_capacity()` | 统一 `find_min_capacity_bisect()` |
| 可达性检查 | — | `find_min_capacity()` 中 `cap_mwh=1e6` | 合并后补充可达性检查 |
| 月度统计 | `calc_monthly_wind_metrics()` | — | 保留 |
| 容量曲线 | `plot_capacity_curve()` | — | 保留 |
| 快速诊断 | `quick_feasibility_diagnose()` | — | 保留 |

#### 已完成：主线迁移

将 `storage_optim_Wind_BESS_combine.py` 作为独立模块迁入 `ele_trading` 框架：

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/ele_trading/capacity_planning/wind_bess_planner.py` | 新建 | Wind+BESS 容量规划算法（二分搜索 + shift 策略） |
| `configs/wind_bess_capacity_planning.yaml` | 新建 | 配置文件 |
| `app/run_wind_bess_capacity_planning.py` | 新建 | 运行脚本 |
| `src/ele_trading/capacity_planning/__init__.py` | 修改 | 新增导出 |

数据类：`ShiftPolicy`、`WindBESSPlanConfig`、`WindBESSResult`。
主入口：`plan_wind_bess_system()`。

### 2026-05-28 Session 5：Wind+PV+BESS 算法整合

#### 已完成：算法文件合并

对比 `storage_optim_Wind_PV_BESS_3.py`（PV 搜索 + Numba 加速）和 `storage_optim_Wind_PV_BESS_1.py`（能量门槛检查 + 充放切换间隔），合并为 `storage_optim_Wind_PV_BESS_combine.py`：

| 功能 | BESS_3 来源 | BESS_1 来源 | 合并方式 |
|---|---|---|---|
| PV 粗扫 + 细扫 | `plan_pv_bess_fast()` | — | 作为主搜索框架 |
| Numba 调度引擎 | `dispatch_annual_numba()` | — | 集成为默认引擎 |
| 充放切换间隔 | — | `dispatch_annual_offgrid()` 中 `switch_gap` | 集成到 Numba 引擎 |
| 能量门槛检查 | — | `energy_gate_check()` | 作为可选前置检查 |
| BESS 二分搜索 | `find_min_bess_kwh()` | `_estimate_min_bess_capacity()` | 统一为 `_find_min_bess_kwh()` |
| 月度发电量 | `monthly_kwh()` | — | 保留 |
| 固定 PV 评估 | — | `evaluate_wind_pv_bess()` | 保留为 Mode 2 |

#### 已完成：主线迁移

将 `storage_optim_Wind_PV_BESS_combine.py` 作为独立模块迁入 `ele_trading` 框架：

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/ele_trading/capacity_planning/wind_pv_bess_planner.py` | 新建 | Wind+PV+BESS 容量规划算法（PV 搜索 + BESS 二分 + Numba 加速） |
| `configs/wind_pv_bess_capacity_planning.yaml` | 新建 | 配置文件 |
| `app/run_wind_pv_bess_capacity_planning.py` | 新建 | 运行脚本 |
| `src/ele_trading/capacity_planning/__init__.py` | 修改 | 新增导出 |

数据类：`WindPVBEssPlanConfig`、`WindPVBEssResult`。
主入口：`plan_wind_pv_bess()`（PV+BESS 联合搜索）、`evaluate_wind_pv_bess()`（固定 PV 评估）。

#### 已完成功能复查

对比源文件发现并补充以下遗漏：

| 遗漏项 | 修复内容 |
|---|---|
| `other_kw` 第三类新能源 | `_dispatch_annual_numba`、`_dispatch_annual`、`_find_min_bess_kwh`、`plan_wind_pv_bess` 均新增 `other_kw`/`other_input` 参数 |
| 弃电量跟踪 | Numba 调度新增 `curtail_e` 返回值，结果 dict 新增 `curtail_kwh` |
| `WindPVBEssResult` 字段 | 新增 `switch_gap_hours`、`debug` 字段 |
| `evaluate_wind_pv_bess` | 补充固定 PV 评估入口（Mode 2） |

---

## Legacy 参考：`eva_Pv.ipynb` 代码功能分析

> 原始分析见 `eva_Pv.md`（已合并入本文件后删除）。

### 整体概览

`eva_Pv.ipynb` 不是单一的光伏分析 notebook，而是把多类试验性代码堆叠在一起的"风光储测算工作台"。从单元内容看，它至少包含以下 5 类逻辑：

1. 负荷原始数据读取、清洗、补点与 2025 年序列构造。
2. 光伏出力模拟与等效小时校验。
3. 风电出力模拟与风场年能量校准。
4. 风光固定条件下的储能容量寻优，以及风光储联合容量规划。
5. 月度统计、结果导出与绘图分析。

这个 notebook 的特点不是"结构化实现"，而是"围绕同一业务场景持续叠加试验代码"。因此它更适合作为分析素材和算法草稿来源，而不适合作为稳定模块直接复用。

### 分模块分析

#### 1. 负荷数据处理

核心函数：`read_power_folder_raw`、`build_daily_energy_2025`、`fill_2025_power_by_daily_energy`、`smooth_2024_shape`、`shift_2024_to_2025`、`fill_missing_days_by_nearest`。

- `read_power_folder_raw`：批量读取 Excel 文件，统一生成 `Time` 和 `P_kw` 字段。
- `build_daily_energy_2025`：根据手工给定的月电量字典，展开成 2025 年每日电量目标。
- `fill_2025_power_by_daily_energy`：用日总电量约束回填缺失点（按时间插值权重分配）。
- `smooth_2024_shape` / `shift_2024_to_2025`：保留历史形状特征，迁移到 2025 年序列。
- `fill_missing_days_by_nearest`：按邻近日期曲线形状补齐整天缺失。

产物：具备 `Time` 和 `P_kw` 的年度负荷数据表。

#### 2. 光伏出力模拟

核心函数：`simulate_pv_output`、`validate_equivalent_hours`、`plot_daily_pv_shape`、`plan_pv_bess_min_capex_fast`。

- `simulate_pv_output`：基于 pvlib 计算光伏 AC 输出功率序列。
- `validate_equivalent_hours`：校验年等效利用小时数。
- `plan_pv_bess_min_capex_fast`：搜索最小化投资的光伏+储能方案。

#### 3. 风电出力模拟

核心函数/类：`fetch_era5_land_open_meteo`、`resample_hourly_to_15min`、`calibrate_energy_with_cap`、`WindFarmConfig`、`WindFarmPowerModelERA5Land`。

- `fetch_era5_land_open_meteo`：通过 Open-Meteo 获取 ERA5-Land 气象数据。
- `WindFarmPowerModelERA5Land`：风速高度换算 → 功率曲线 → 年能量回补，输出风电功率序列。

#### 4. 风光固定条件下的储能寻优

核心函数/类：`BESSRuleConfig`、`align_curves`、`energy_gate_check`、`simulate_bess_for_coverage`、`estimate_min_bess_capacity`、`plan_wind_fixed_pv_bess_fast`。

- `BESSRuleConfig`：储能调度规则和设备参数。
- `align_curves`：负荷/光伏/风电三类曲线对齐。
- `energy_gate_check`：快速能量可行性筛选。
- `estimate_min_bess_capacity`：满足目标的最小储能容量估算。

#### 5. 风光储联合规划与最小投资测算

核心函数/类：`UnitsConfig`、`plan_energy_system`、`run_planning_min_investment`、`calc_monthly_wind_metrics`、`run_wind_bess_planning_min_cap`。

- `plan_energy_system`：在给定约束下搜索满足自用率/覆盖率的系统配置。
- `run_planning_min_investment`：从输入到结果的封装入口。
- `calc_monthly_wind_metrics`：按月统计风电消纳指标。

### 数据与文件依赖

输入来源：

- 本地负荷 Excel 文件目录（原始路径：`D:\\测算工作\\负荷曲线`）
- 手工指定的月度电量参数
- 外部气象数据接口 Open-Meteo（ERA5-Land）

输出文件（`src/ba_eva/dataset/` 下）：

- `pv_monthly_kwh.csv`、`pv_gen_kwh_monthly.csv`、`wind_gen_kwh_monthly.csv`
- `df_total.csv`、`bess_schedule.csv`、`bess_monthly_metrics.csv`

残留 Windows 绝对路径：

- `D:\\228-售前测算\\乌兰察布\\df_wind_2026.csv`
- `D:\\228-售前测算\\乌兰察布\\df_2025.csv`
- `D:\\228-售前测算\\乌兰察布\\pv_kw_100.csv`

### 主要问题与风险

1. **重复定义**：`PlanConfigFast`、`BESSConfig`、`plan_energy_system` 等在不同单元中被重复实现或二次改写。
2. **试验叠加式组织**：边试验、边复制、边扩展的研究过程产物，单元之间存在强状态依赖。
3. **执行顺序敏感**：依赖前面单元产出的内存变量和本地中间文件，不适合作为模块直接导入。
4. **路径不一致**：notebook 代码仍引用旧位置或本地绝对路径，与当前仓库布局不一致。

### 结论

`eva_Pv.ipynb` 是围绕"负荷 + 光伏 + 风电 + 储能 + 投资测算"逐步扩展出来的综合测算 notebook。其核心算法能力已分批整合进 `ele_trading.capacity_planning` 下各模块，notebook 保留为 legacy 参考实现。

---

## 设计约束

- 本目录后续算法试验继续使用 legacy 兼容 CSV，不强迫立刻重写算法入口。
- 数据生成统一走 `ele_trading` 新数据框架，不再回到旧四个脚本。
- `app` 入口支持两种模式：只读已有 CSV；先刷新 bridge 数据再运行。
- 后续算法读取路径统一到 `data/wind_pv_es_calc/temp/` 下三份 CSV。
- `prepare_legacy_temp_data.py` 中 `refresh_mode: use_cached` 时读缓存，改为 `refresh` 时重新生成。

## 待继续的工作

### P1：增强收益口径（`run_wind_pv_legacy_profit_eval.py`）

- `buy_price_source` 是否需要更多模式
- `annual_arbitrage_revenue` 当前为 `0.0`，需接入正式逻辑
- 收益拆分是否需要更正式地接入 `evaluation/settlement` 层

### P2：增强交易入口（`run_wind_pv_legacy_market_trading.py`）

- 窗口切片策略是否要更细化
- 风/光是否继续聚合为单个 `pv_forecast`，还是拆开建模
- 是否需要扩展为更真实的交易窗口和价格输入
