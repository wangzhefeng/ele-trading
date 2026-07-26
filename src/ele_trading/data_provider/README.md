# data_provider — 交易数据接入与质量边界

本模块负责将市场、气象和资产输入转换为带 `as_of` 与版本信息的活动交易数据。投资测算 case 和用户侧样例不属于活动 API。

## 当前文件

| 文件 | 职责 |
|------|------|
| `contracts.py` | `MarketDataSnapshot`：校验时区、时间顺序、唯一性、观测截止时刻和版本 |
| `market_data.py` | 市场快照直接构造、市场 CSV/价格/场景读取 |
| `weather_data.py` | 外部与历史气象数据的活动公开入口 |
| `asset_data.py` | 活动 BESS 资产配置与 YAML 读取 |
| `quality.py` | 时间戳清洗、重采样、对齐、质量分和异常修复 |
| `schemas.py` | 价格、场景和通用 `ObservedPowerSeries` 活动类型 |
| `loader.py` | 已弃用的通用兼容入口，仅转发 market/asset API |
| `sample_data.py` | 内置最小样例路径和快捷加载函数 |
| `case_dataset.py` | 已弃用的交易数据集导入路径，仅转发到 `market_data.py` |
| `resource_weather.py` / `weather_io.py` | 已弃用的气象兼容入口；新代码使用 `weather_data.py` |
| `time_series_ops.py` | 已弃用的质量函数兼容入口；新代码使用 `quality.py` |
| `todo/` | 目标年份/profile、投资 case、资源容量仿真类型、用户侧及 CVXPY 样例归档 |

## 数据来源

- `data/trading/prices/`：交易线价格最小样例（日前/日内，含 96 点）。
- `data/trading/config/`：交易线储能最小样例配置。
- `data/trading/scenarios/`：价格场景样例（Two-stage 骨架演示输入）。
- `data/trading/daily_sample_*.csv`：蒙西 96 点日清分样例（`ele_trading.trading.sample_data` 生成）。
- `data/wind_pv_es_calc/`：legacy 风光储测算兼容数据。
- `configs/*.yaml`：入口脚本和样例构造参数。
- 外部天气源：Open-Meteo、NetCDF、Mongo 或本地测点文件。

## 典型流向

```text
CSV / YAML / weather source
→ market_data / weather_data / asset_data
→ MarketDataSnapshot + quality validation
→ forecasting / optimization / trading
→ app demo 或 tests
```

## 使用边界

- 活动交易数据必须携带 `market`、`scope_type`、`scope_id`、`as_of`、`version` 与 `quality_flags`。
- 每个快照必须包含无缺失的布尔列 `is_observation`；只有严格布尔值 `False` 的预测行可晚于 `as_of`。
- `build_trading_case_dataset()` 直接构造 `MarketDataSnapshot`，不得经由投资 case builder。
- 通用实测负荷/新能源功率使用 `ObservedPowerSeries`；不得复用投资 profile 类型。
- legacy 桥接代码用于兼容历史 CSV 字段，不应成为新主线字段命名的来源。
