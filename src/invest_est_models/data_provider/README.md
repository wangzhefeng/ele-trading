# data_provider

`data_provider` 统一负责测算场景的数据接入和模拟数据生成，不再使用 `io/` 或 `sample_data/` 作为模块目录名。

主要文件：

1. `data_loader.py`：读取并对齐负荷、电价、风光资源 CSV。
2. `price_type.py`：统一中文或英文电价类型为标准英文编码。
3. `sample_generator.py`：生成可复现的模拟输入 CSV。

输入文件：

1. 负荷 CSV：`time,value`，`value` 当前按平均功率 `kW` 解释。
2. 电价 CSV：`time,price,price_type`，`price` 当前按 `元/kWh` 解释，`price_type` 读取后统一为英文编码。
3. 风光资源 CSV：`time,pv_kw,wind_kw`，表示已仿真的光伏和风电平均功率。

`price_type` 标准值：

1. `deep_valley`：深谷。
2. `valley`：谷、低谷。
3. `flat`：平。
4. `peak`：峰、高峰。
5. `sharp_peak`：尖峰。

中文别名会在读取阶段标准化。例如 CSV 中的 `高峰` 会转换为 `peak`，`尖峰` 会转换为 `sharp_peak`。

核心逻辑：

1. 按 `time` 精确对齐负荷、电价和资源数据。
2. 按相邻时间戳自动推断 `dt_hours`。
3. 在真实输入尚不确定时生成可复现的模拟负荷、电价、风光资源 CSV。

使用方式：

```python
from invest_est_models.data_provider import (
    build_timeseries,
    generate_sample_csvs,
    read_load_csv,
    read_price_csv,
    read_resource_csv,
)

paths = generate_sample_csvs("src/invest_est_models/dataset", year=2026, freq="1h")
df = build_timeseries(
    read_load_csv(paths["load"]),
    read_price_csv(paths["price"]),
    read_resource_csv(paths["resource"]),
)
```

## 实现进度

MVP 版本已实现：

1. 负荷、电价、风光资源 CSV 读取。
2. `price_type` 中英文输入标准化。
3. 必需字段检查。
4. 时间戳解析、精确合并和 `dt_hours` 自动识别。
5. 小时级和 15 分钟级输入的基础兼容。
6. 一整年模拟负荷、电价、风光资源 CSV 生成。
7. 通过 YAML 配置年份和时间频率。

v1 版本已实现：

1. 对齐后时序主表校验。
2. 重复时间戳、缺失值、非正时间步、负负荷、负电价和负风光出力检查。

后续待扩展：

1. 全年完整性校验。
2. 异常值分级报告。
3. 多场景批量输入。
4. 单位元数据和时区处理。
5. 与后续真实风光资源仿真脚本对接。
