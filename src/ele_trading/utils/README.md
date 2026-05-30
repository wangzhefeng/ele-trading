# Utils

通用工具库。

## 模块

- **`time_index.py`** — 时间索引生成与处理
- **`time_splitting.py`** — 时间范围拆分（月份、日粒度）
- **`time_process.py`** — 时间处理辅助（内部使用）
- **`data_alignment.py`** — 时间序列对齐与规范化
- **`energy_price.py`** — 电价相关工具
- **`io.py`** — 文件读写（YAML、文本）
- **`log_util.py`** — 日志工具
- **`day2month.py`** — 日期 → 月份映射
- **`pv_es_plot.py`** — 光储策略绘图
- **`charge_discharge_plot/`** — 充放电数据可视化（独立目录，自带数据与脚本）

---

## 用法示例

```python
from ele_trading.utils import (
    # time_index
    generate_days, generate_hours, generate_quarters, generate_5mins,
    generate_time_points, infer_dt_hours, monthly_kwh,
    end_of_that_day, process_time_index,
    start_of_this_bess_cycle, end_of_this_bess_cycle, bess_cycle_window,
    # time_splitting
    generate_month_ranges, generate_day_pairs, get_time_ranges,
    # data_alignment
    as_time_series, normalize_time_and_load, align_to_time, align_and_merge,
    # energy_price
    flatten_valley_price_diff,
    # io
    read_yaml, write_text,
    # log_util
    logger,
    # pv_es_plot
    plot_strategy_power_detail,
)
```

---

## API

### time_index.py

时间索引生成与处理。

| 函数 | 说明 |
|------|------|
| `infer_dt_hours(dt)` | 推断时间戳的小时粒度 |
| `monthly_kwh(power, ...)` | 按月统计电量（kWh） |
| `generate_time_points(start, end, freq, ...)` | 生成左闭右开时间点序列 |
| `generate_days(start, end)` | 生成日级时间索引 |
| `generate_hours(start, end)` | 生成小时级时间索引 |
| `generate_quarters(start, end)` | 生成 15 分钟级时间索引 |
| `generate_5mins(start, end)` | 生成 5 分钟级时间索引 |
| `end_of_that_day(dt)` | 返回当天最后一刻 |
| `process_time_index(df, col)` | 将时间戳列转为去重的 DatetimeIndex |
| `start_of_this_bess_cycle(dt)` | 储能周期开始时间 |
| `end_of_this_bess_cycle(dt)` | 储能周期结束时间 |
| `bess_cycle_window(dt)` | 返回储能周期的 (start, end) |

### time_splitting.py

时间范围拆分工具。

| 函数 | 说明 |
|------|------|
| `generate_month_ranges(start, end, ...)` | 将日期范围按月拆分 |
| `generate_day_pairs(start, end, ...)` | 生成日粒度 (begin, end) 对 |
| `get_time_ranges(start, end, ...)` | 通用时间范围拆分 |

### data_alignment.py

时间序列对齐与规范化。

| 函数 | 说明 |
|------|------|
| `as_time_series(s)` | 将 Series/DataFrame 规范为 Series(index=DatetimeIndex) |
| `normalize_time_and_load(df, ...)` | 规范化负荷 DataFrame：提取时间轴和负荷数组（kW），按时间排序 |
| `align_to_time(s, t)` | 将 Series 对齐到时间轴 t，线性插值，缺失填 0 |
| `align_and_merge(load_df, wind_df, ...)` | 将负荷（kW）和风电（MW）对齐到统一时间轴 |

### energy_price.py

电价相关工具。

#### `flatten_valley_price_diff(df, *, price_col='elePrice', type_col='eleType', valley_types=('谷', '深谷'), inplace=False)`

将谷/深谷电价展平为上一个谷值电价。

在电价 DataFrame 中，谷和深谷时段通常有不同电价。此函数将深谷电价替换为前一个谷电价，使所有谷时段电价一致。适用于需要统一谷时段电价进行收益计算的场景。

**参数：**

- `df` — 包含电价和电价类型的 DataFrame
- `price_col` — 电价列名
- `type_col` — 电价类型列名（如 '峰'、'谷'、'深谷'、'平'）
- `valley_types` — 需要展平的类型值
- `inplace` — 是否原地修改

**返回：** 处理后的 DataFrame

**示例：**

```python
from ele_trading.utils.energy_price import flatten_valley_price_diff
df = flatten_valley_price_diff(df)
```

### io.py

文件读写工具。

| 函数 | 说明 |
|------|------|
| `read_yaml(path)` | 读取 YAML 文件并返回字典 |
| `write_text(path, content)` | 写入文本文件 |

### log_util.py

日志工具。

| 名称 | 说明 |
|------|------|
| `logger` | 全局 logger 实例 |

### day2month.py

日期到月份的映射。

### pv_es_plot.py

光储策略绘图。

| 函数 | 说明 |
|------|------|
| `plot_strategy_power_detail(...)` | 绘制光储策略功率详情图 |

### charge_discharge_plot/

充放电数据可视化子目录，包含独立的绘图脚本和示例数据。

- `data_visual.py` — 充放电数据可视化
- `plot_data/` — 绘图数据（按项目组织）
- `plot_results/` — 绘图输出（按项目组织）
