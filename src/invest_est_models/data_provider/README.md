# data_provider

`data_provider` 统一负责测算场景的数据接入和模拟数据生成，不再使用 `io/` 或 `sample_data/` 作为模块目录名。

主要文件：

1. `data_loader.py`：读取并对齐负荷、电价、风光资源 CSV。
2. `sample_generator.py`：生成可复现的模拟输入 CSV。

输入文件：

1. 负荷 CSV：`time,value`，`value` 当前按平均功率 `kW` 解释。
2. 电价 CSV：`time,price,price_type`，`price` 当前按 `元/kWh` 解释。
3. 风光资源 CSV：`time,pv_kw,wind_kw`，表示已仿真的光伏和风电平均功率。

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
2. 必需字段检查。
3. 时间戳解析、精确合并和 `dt_hours` 自动识别。
4. 小时级和 15 分钟级输入的基础兼容。
5. 一整年模拟负荷、电价、风光资源 CSV 生成。
6. 通过 YAML 配置年份和时间频率。

v1 版本已实现：

1. 对齐后时序主表校验。
2. 重复时间戳、缺失值、非正时间步、负负荷、负电价和负风光出力检查。

后续待扩展：

1. 全年完整性校验。
2. 异常值分级报告。
3. 多场景批量输入。
4. 单位元数据和时区处理。
5. 与后续真实风光资源仿真脚本对接。
