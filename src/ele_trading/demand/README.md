# demand — 最大需量计算模块

本模块用于计算用户侧负荷曲线的最大需量和需量电费，服务用户侧储能、削峰填谷和容量评估。

## 背景

按需量计费时，基本电费通常由统计周期内最大需量决定：

```text
基本电费 = 最大需量(kW) × 需量电价(元/kW/月)
```

最大需量是对负荷功率按规定窗口取平均后得到的最大值。

## 当前文件

| 文件 | 职责 |
|------|------|
| `config.py` | `DemandConfig`、`DemandResult` |
| `calc.py` | 固定窗口、滑动窗口、最大需量和需量电费计算 |
| `data.py` | CSV 负荷曲线读取和模拟负荷生成 |
| `plot.py` | 负荷曲线和最大需量可视化 |
| `sample.py` | 完整示例 |

## 窗口方式

| 方式 | 函数 | 说明 |
|------|------|------|
| 固定窗口 | `calc_fixed_window()` | 将时间序列切成不重叠窗口，每段取均值 |
| 滑动窗口 | `calc_sliding_window()` | 按窗口长度逐点滑动取均值 |

同一数据和窗口长度下，滑动窗口最大需量通常大于或等于固定窗口最大需量。

## 快速使用

```python
import pandas as pd
from ele_trading.demand import DemandConfig, calc_demand, calc_demand_charge

df = pd.read_csv("load_curve.csv", parse_dates=["timestamp"])
power = df.set_index("timestamp")["power_kw"]

cfg = DemandConfig(window_minutes=15, window_type="sliding", demand_price=40.0)
result = calc_demand(power, cfg)
charge = calc_demand_charge(result)
```

运行示例：

```bash
uv run python -c "from ele_trading.demand.sample import main; main()"
```

## 使用边界

- 输入功率需要有时间索引，并明确单位为 kW 或 MW。
- 需量窗口长度应与电网计量规则一致，常见为 15 分钟或 30 分钟。
- 本模块只做需量计算，不负责储能调度；调度模型在 `optimization` 中实现。
