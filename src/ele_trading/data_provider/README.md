# data_provider — 交易数据接入与质量边界

本模块负责将市场、气象和资产输入转换为带 `as_of` 与版本信息的活动交易数据。投资测算 case 和用户侧样例不属于活动 API。

## 当前文件

| 文件 | 职责 |
|------|------|
| `contracts.py` | `MarketDataSnapshot`：校验时区、时间顺序、唯一性、观测截止时刻和版本 |
| `market_data.py` | 市场快照直接构造、市场 CSV/价格读取 |
| `asset_data.py` | 活动 BESS 资产配置与 YAML 读取 |
| `quality.py` | 时间戳清洗、重采样、对齐、质量分和异常修复 |
| `schemas.py` | 价格和通用 `ObservedPowerSeries` 活动类型 |
| `sample_data.py` | 内置最小样例路径和快捷加载函数 |
| `weather_data.py` | 气象数据聚合入口（facade）：自身无实现，re-export 下两个模块 |
| `resource_weather.py` | 气象实现：Open-Meteo ERA5 抓取 + 气象 CSV IO（被 resource_simulation 等 app 使用） |
| `weather_io.py` | 气象实现：Mongo/NetCDF/气象模拟器/实测文件夹（目前仅 `tests/forecasting/test_weather.py` 消费） |

> 原 `todo/` 归档已拆分迁出：用户侧/CVXPY 样例归 `ele_trading.user_side_dispatch/`，投资 case 与目标年份/profile 归 `investment_estimation/todo/`。

## 数据来源

- `data/trading/prices/`：交易线价格最小样例（日前/日内）。
- `data/trading/daily_sample_*.csv`：蒙西 96 点日清分样例（`ele_trading.trading.sample_data` 生成）。
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
