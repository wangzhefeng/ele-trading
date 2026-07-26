# forecasting — 预测与天气特征模块

本模块负责把价格、风光出力和气象测点数据转换为优化链路可消费的预测或特征结果。当前实现以工程接口和样例算法为主，不是生产级预测模型库。

## 当前文件

| 文件 | 职责 |
|------|------|
| `base.py` | 定义统一 `ForecastOutput`，包含点预测和可选上下界 |
| `contracts.py` | `ForecastRequest` / `ForecastResult` 唯一权威，校验范围、分位、索引、有限值、版本和无前瞻 |
| `registry.py` | 按 `target/model_name/model_version` 注册和解析预测模型；缺模型或未知目标显式失败 |
| `provider.py` | `ForecastProvider.forecast(request)` 统一入口；五类 typed convenience API 仅校验目标后委托通用路径 |
| `weather_forecast.py` | 外部天气适配器边界、按 `issue_time` 选择的归档 vintage、persistence/climatology 基线和 bias correction |
| `price_forecast.py` | 15 分钟/月度 seasonal-naive、回归、兼容 ARIMA 及其 request-oriented registry adapter |
| `load_forecast.py` | 递归 AR+climatology、短历史降级、五级 scope 与 bottom-up/最小二乘/约束层级协调 |
| `renewable_forecast.py` | 风/光共同接口，支持 physical/statistical/external 路径、MW 边界和 site/portfolio/region 聚合 |
| `pv_forecast.py` | `PVPowerForecaster` 兼容 API；物理路径在本包实现并强制夜间零出力 |
| `wind_forecast.py` | `WindPowerForecaster` 兼容 API；物理路径在本包实现切入/额定/切出曲线 |
| `metrics.py` | MAE、RMSE、pinball loss、区间覆盖率、方向准确率及单位/粒度元数据 |
| `weather_feature.py` | 天气特征工程：相关性、滞后相关性、聚类、插值、空间权重 |

## 当前能力

- 通用服务：`ForecastRequest` 明确目标、scope、频率、时域、模型名/版本和目标特有 `data`；`ForecastProvider` 不按 horizon 猜测业务语义。
- 天气预测：归档回测只选 `vintage.issue_time <= request.issue_time` 的最新版本；无归档时可使用确定性 persistence/climatology。
- 价格预测：`data.market_scope` 显式区分日前参考、实时参考和中长期，不从字符串或 horizon 推断；`ARIMAForecastModel` 可像其他模型一样注册并消费完整请求。
- 负荷预测：支持 system/region/node/portfolio/site，短历史使用可见降级标记，层级结果可强制聚合一致。
- 风光预测：共同接口输出 MW；物理、统计和外部适配器路径统一执行容量、可用率、PV 夜间零和风机功率曲线约束。物理路径只接收具体 `forecasting.contracts.ForecastResult`，或 valid-time 完全对齐且带显式 `feature_as_of` 的天气序列。
- 天气特征：支持 Pearson/Spearman/Kendall 相关性、多滞后相关性、KMeans/DBSCAN 聚类、RBF/Kriging 插值、测点到城市或中心点匹配。

## 上下游关系

- 上游：`data_provider` 提供可追溯市场快照、历史价格、负荷和气象数据。
- 下游：`scenario` 消费价格预测结果；`optimization` 消费风光预测或资源出力；`trading/` 蒙西链路经 `ForecastProvider` 消费价格/负荷预测（walk-forward 回测由 `trading/sample_data.py::WalkForwardSeasonalNaiveProvider` 按 issue-time vintage 生成无前瞻预测）。

## 使用边界

- 透明基线用于接口验证、回测基准和链路演示，不代表生产预测精度；外部生产预测必须通过 adapter 接入。
- `WeatherSimulator` 和样例天气生成器只用于测试 fixture/demo，不得标记为生产天气预测源。
- 模型内部可继续使用 `ForecastOutput`；跨包边界统一使用 `ForecastRequest` / `ForecastResult`。
- `issue_time`、`feature_as_of` 和结果 valid-time 索引必须带时区；`frequency` 必须是严格向前的偏移量，首个 valid-time 为 `issue_time + frequency`，后续逐次累加同一偏移量。
- weather/price/load/renewable statistical 历史序列必须按时间递增、无重复、规则采样并与请求频率一致；结果的 `feature_as_of` 取实际参与计算的最新历史时刻。
- `forecasting` 不得通过绝对或相对导入访问 `trading`，`feature_as_of` 必须不晚于请求的 `issue_time`。
- `SimpleForecastProvider` 不提供隐藏的价格默认值；兼容价格路径必须显式传入非空、带时区索引的 `default_history_prices`。
- PV 的日夜判断必须显式提供场站 `site_timezone`，不能借用结果展示时区；兼容 PV API 使用 `timezone` 和 `equiv_hours`，对尚未建模的经纬度、倾角、方位角和海拔参数显式拒绝。兼容风电 API 使用轮毂高度、参考高度、风切变指数和等效小时参数。
- renewable physical 输入使用稳定天气契约：

  | renewable target | `request.data.weather_variable` | 规范单位 | 显式序列键 |
  |---|---|---|---|
  | `wind_power` | `wind_speed` | `m/s` | `wind_speed` |
  | `pv_power` | `irradiance` | `W/m2` | `irradiance` |

  `weather_forecast` 必须是具体 `ForecastResult`，其 target 必须为
  `weather`，scope、`request.data.weather_variable` 和 `unit` 必须与 renewable
  请求一致；具有相似 `values` / `issue_time` 属性的 duck-typed 对象不被接受。
  直接传入显式序列时，还必须声明匹配的 `request.data.weather_unit`。
- 风光功率统一使用 MW；按 15 分钟换算能量由下游使用 `dt=0.25` 完成。
- 天气特征工程可能依赖 `weather` optional dependencies；新增调用前先确认 `pyproject.toml` 中依赖声明。
