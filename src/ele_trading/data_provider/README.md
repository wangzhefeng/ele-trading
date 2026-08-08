# data_provider — 数据接入与质量边界

本模块把市场、资产、气象和实测功率输入转换为当前活动算法可消费的数据结构。投资测算 case 和独立用户侧样例不属于本模块活动 API。

## 当前文件

| 文件 | 当前职责 |
|---|---|
| `contracts.py` | `MarketDataSnapshot`：校验时区、顺序、唯一性、观测截止时刻和版本 |
| `market_data.py` | 市场快照构造、市场 CSV、价格和实测功率读取 |
| `asset_data.py` | BESS 资产配置与 YAML 读取 |
| `quality.py` | 时间戳、重采样、对齐、质量分和异常修复 |
| `schemas.py` | `PriceSeries` 和 `ObservedPowerSeries` |
| `sample_data.py` | `data/trading/prices/` 最小样例加载 |
| `weather_data.py` | 气象 facade，转出 `resource_weather` 和 `weather_io` 能力 |
| `resource_weather.py` | Open-Meteo ERA5 与气象 CSV IO |
| `weather_io.py` | Mongo、NetCDF、模拟器和实测文件夹兼容能力 |

## 当前数据来源

- `data/trading/prices/`：活动 optimization 入口的最小价格样例；
- `data/trading/daily_sample_*.csv`：由 `ele_trading.trading.demo_fixtures` 生成的 96 点回测 fixture；
- `configs/optimization/`、`configs/markets/`：活动入口配置；
- Open-Meteo、NetCDF、Mongo 或本地测点文件：气象兼容来源。

## 当前契约行为

- 市场快照携带 `market`、`scope_type`、`scope_id`、`as_of`、`version` 和 `quality_flags`。
- `is_observation` 必须是无缺失的布尔列；只有显式 `False` 的预测行可以晚于 `as_of`。
- `build_trading_case_dataset()` 直接构造 `MarketDataSnapshot`，不经过投资测算 builder。
- 通用实测负荷和新能源功率使用 `ObservedPowerSeries`，不复用投资 profile 类型。

这些是当前代码和测试保护的行为。真实数据目录、发布/可用时刻、责任、许可和版本策略是 [v6 V5-10](../../../docs/策略算法框架详细设计-v6.md) 的未完成工作。
