# demand — 最大需量电功率计算模块

## 背景

中国电力市场中，大工业/商业用户的电费由两部分组成：

- **电度电费** = 实际用电量 (kWh) × 电度电价
- **基本电费** = 最大需量 (kW) × 需量电价，或 变压器容量 (kVA) × 容量电价

用户可选择"按需量"或"按容量"缴纳基本电费。选择需量方式时，每月电费与**当月最大需量**直接挂钩。本模块提供最大需量的计算工具。

## 算法说明

### 最大需量的定义

最大需量是指统计周期内（通常为一个月），以规定的时间窗口对负荷功率取平均值后得到的最大值。

### 两种窗口方式

| 方式 | 说明 | 适用场景 |
|------|------|----------|
| **固定窗口** (`fixed`) | 将时间序列切分为不重叠的 N 分钟段，每段取均值 | 电网计量（中国电网常见 15 分钟/30 分钟抄表周期） |
| **滑动窗口** (`sliding`) | 以 N 分钟为宽度逐点滑动取均值 | 精细化分析、储能需量削减策略评估 |

数学表达：

```
固定窗口:  P_demand = max{ mean(P[t..t+N]) | t = 0, N, 2N, ... }
滑动窗口:  P_demand = max{ mean(P[t..t+N]) | t = 0, 1, 2, ... }
```

滑动窗口的最大需量 >= 固定窗口的最大需量（同一数据、同一窗口时长）。

### 需量电费计算

```
基本电费 = 最大需量 (kW) × 需量电价 (元/kW/月)
```

## 快速开始

```python
import pandas as pd
from ele_trading.demand import DemandConfig, calc_demand, calc_demand_charge

# 准备负荷曲线（DatetimeIndex + kW 功率值）
df = pd.read_csv("load_curve.csv", parse_dates=["timestamp"])
power = df.set_index("timestamp")["power_kw"]

# 配置: 15 分钟滑动窗口，需量电价 40 元/kW/月
cfg = DemandConfig(window_minutes=15, window_type="sliding", demand_price=40.0)

# 计算
result = calc_demand(power, cfg)
print(f"最大需量: {result.max_demand:.1f} kW")
print(f"发生时刻: {result.peak_timestamp}")

# 电费
charge = calc_demand_charge(result)
print(f"月基本电费: {charge['demand_charge']:.1f} 元")
```

运行完整示例（含可视化）：

```bash
python -c "from ele_trading.demand.sample import main; main()"
```

## API 参考

### `DemandConfig`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `window_minutes` | `int` | `15` | 窗口时长（分钟），典型值: 15, 30 |
| `window_type` | `"fixed" \| "sliding"` | `"sliding"` | 窗口类型 |
| `demand_price` | `float` | `0.0` | 需量电价（元/kW/月） |
| `power_unit` | `"kW" \| "MW"` | `"kW"` | 输入功率单位，MW 自动换算为 kW |

### `DemandResult`

| 属性 | 类型 | 说明 |
|------|------|------|
| `max_demand` | `float` | 全局最大需量（kW） |
| `peak_timestamp` | `pd.Timestamp` | 最大需量发生时刻 |
| `monthly_max` | `pd.Series` | 每月最大需量（index=Period） |
| `daily_max` | `pd.Series` | 每日最大需量（index=date） |
| `window_series` | `pd.Series` | 完整窗口平均功率序列 |
| `config` | `DemandConfig` | 计算所用配置 |

### 核心函数

#### `calc_demand(power, config) -> DemandResult`

计算最大需量的主入口。`power` 为 DatetimeIndex 的功率 Series。

#### `calc_demand_charge(result, demand_price=None) -> dict`

计算需量电费。返回 `{"max_demand_kw", "demand_price", "demand_charge"}`。

#### `calc_fixed_window(power, window_minutes) -> pd.Series`

返回固定窗口平均功率序列（index 为窗口起始时间）。

#### `calc_sliding_window(power, window_minutes) -> pd.Series`

返回滑动窗口平均功率序列（与原序列等长）。

### 数据函数

#### `load_load_curve(path) -> pd.DataFrame`

从 CSV 加载负荷曲线，须含 `timestamp` 和 `power_kw` 列。

#### `generate_simulated_load(n_days=30, freq="15min", seed=42) -> pd.DataFrame`

生成模拟负荷曲线（基础负荷 + 双峰日周期 + 噪声）。

### 可视化函数

#### `plot_load_with_demand(power, result)`

绘制负荷曲线 + 最大需量水平线 + 峰值标注。

#### `plot_monthly_demand(result)`

绘制每月最大需量柱状图。

## 文件结构

```
demand/
    __init__.py    # 公共 API
    config.py      # DemandConfig / DemandResult 数据类
    calc.py        # 核心算法
    data.py        # 数据加载与模拟
    plot.py        # 可视化
    sample.py      # 完整使用示例
    README.md      # 本文件
```
