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
