# forecasting — 预测与天气特征模块

本模块负责把价格、风光出力和气象测点数据转换为优化链路可消费的预测或特征结果。当前实现以工程接口和样例算法为主，不是生产级预测模型库。

## 当前文件

| 文件 | 职责 |
|------|------|
| `base.py` | 定义统一 `ForecastOutput`，包含点预测和可选上下界 |
| `price_forecast.py` | `SimplePriceForecaster`，基于历史均值和波动生成价格预测 |
| `renewable_forecast.py` | renewable 预测抽象类和 stub，保留兼容接口 |
| `solar_forecast.py` | `SolarPowerForecaster`，支持 `harmonic` 和 `physics` 两种模式 |
| `wind_forecast.py` | `WindPowerForecaster`，支持 `statistical` 和 `physics` 两种模式 |
| `weather_feature.py` | 天气特征工程：相关性、滞后相关性、聚类、插值、空间权重 |

## 当前能力

- 价格预测：用简单统计规则生成点预测和上下界，服务场景生成 demo。
- 光伏预测：`harmonic` 模式用日内谐波拟合；`physics` 模式复用 PV 物理仿真。
- 风电预测：`statistical` 模式混合 AR(p) 与气候学均值；`physics` 模式复用风电物理仿真。
- 天气特征：支持 Pearson/Spearman/Kendall 相关性、多滞后相关性、KMeans/DBSCAN 聚类、RBF/Kriging 插值、测点到城市或中心点匹配。

## 上下游关系

- 上游：`data_provider` 提供历史价格、负荷、气象数据和样例 profile。
- 下游：`scenario` 消费价格预测结果；`optimization` 和 `capacity_planning` 消费风光预测或资源出力。

## 使用边界

- 当前预测器用于接口验证和链路演示，不代表生产预测精度。
- 需要接入真实预测模型时，应保持 `ForecastOutput` 输出形状稳定。
- 天气特征工程可能依赖 `weather` optional dependencies；新增调用前先确认 `pyproject.toml` 中依赖声明。
