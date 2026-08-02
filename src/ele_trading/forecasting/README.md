# forecasting — 预测契约与工程基线

本模块把天气、价格、负荷和风光输入转换为优化链可消费的预测结果。当前优势是统一契约和无前瞻校验；现有模型主要用于工程基线、兼容回归和外部 adapter 边界，不代表生产预测精度。

## 能力分类

### 契约与注册

| 文件 | 当前职责 |
|---|---|
| `contracts.py` | `ForecastRequest` / `ForecastResult` 的索引、分位、单位、版本和无前瞻校验 |
| `registry.py` | 按 target、模型名和版本注册/解析模型 |
| `provider.py` | 通用 `ForecastProvider.forecast(request)` 和 typed convenience API |
| `metrics.py` | MAE、RMSE、pinball loss、区间覆盖率、方向准确率、分位校准误差（v4 P0） |

### 可运行基线

| 文件 | 当前职责 |
|---|---|
| `price_forecast.py` | seasonal-naive、回归和 ARIMA 兼容实现 |
| `load_forecast.py` | 递归 AR、climatology、短历史降级和层级协调 |
| `weather_forecast.py` | vintage 选择、persistence/climatology 和 bias correction |
| `renewable_forecast.py` | 风光 physical/statistical/external 路径和聚合 |
| `pv_forecast.py` | PV 兼容 API、容量边界和夜间零出力 |
| `wind_forecast.py` | 风电兼容 API 和切入/额定/切出曲线 |
| `weather_feature.py` | 相关性、滞后、聚类、插值、测点匹配和空间权重 |

`seasonal_naive_provider.py` 与 `trading.demo_fixtures.WalkForwardSeasonalNaiveProvider` 是显式 demo/回测 provider，不是隐藏生产默认值。

### 可选增强（v4 P0）

| 文件 | 当前职责 | 状态 |
|---|---|---|
| `lightgbm_provider.py` | LightGBM 点预测 + 分位回归（pinball loss），支持 price/load 目标，日历+滞后+滚动统计特征，无前瞻约束 | 可选增强（默认非默认模型） |

## 当前跨包行为

- 跨包统一使用 `ForecastRequest` / `ForecastResult`；`ForecastOutput` 只作为模型内部或兼容结果。
- `issue_time`、`feature_as_of` 和 valid-time 索引必须带时区。
- 首个 valid-time 位于 `issue_time + frequency`，历史序列必须有序、无重复且与频率一致。
- `feature_as_of` 不得晚于 `issue_time`，`forecasting` 不依赖 `trading`。
- 风光输出统一为 MW；能量换算由下游按明确 `dt` 完成。
- 物理风光输入必须使用明确天气变量、单位、scope 和来源时间，不能接受未校验的 duck-typed 对象。

## 生产未验证边界

- 样例天气、persistence、climatology、seasonal-naive 和 stub 只用于接口、demo 或对照。
- 外部生产预测需要通过 adapter 接入，并保留 issue-time vintage 与来源版本。
- 经纬度、设备参数、真实误差分布和模型供应商尚未形成统一生产验收。

需量预测的业务归属、生产 adapter 和未来契约由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#9-需量预测归属决策)决定。
