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
