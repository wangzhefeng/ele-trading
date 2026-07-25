# data_provider — 数据、配置与样例输入模块

本模块负责把 CSV/YAML、负荷曲线、气象数据、legacy 兼容数据和合成样例转换为核心算法可消费的数据结构。

## 当前文件

| 文件 | 职责 |
|------|------|
| `schemas.py` | 价格、储能、场景、负荷、PV/风电 profile、case dataset 的 dataclass |
| `loader.py` | CSV/YAML 读取函数，返回统一结构 |
| `sample_data.py` | 内置最小样例路径和快捷加载函数 |
| `case_dataset.py` | 构造投资测算和交易测算 case dataset |
| `load_profile.py` | 从历史负荷 Excel 构造目标年份负荷 profile |
| `resource_weather.py` | Open-Meteo 获取、天气 CSV 读写 |
| `weather_io.py` | NetCDF、Mongo、样例气象、测点读取和天气模拟 |
| `time_series_ops.py` | 时间戳清洗、重采样、对齐、质量分和异常修复 |
| `user_side_storage_sample.py` | 用户侧储能 demo 配置读取和合成输入 |
| `user_side_pv_dispatch_sample.py` | 用户侧 PV-only demo 配置读取和合成输入 |
| `user_side_pv_bess_dispatch_sample.py` | 用户侧 PV+storage demo 配置读取和合成输入 |
| `user_side_pv_sample.py` | 早期用户侧 PV/PV+storage 兼容样例构造 |
| `cvxp_storage_sample.py` | CVXPY 储能调度 demo 配置读取和合成输入 |

## 数据来源

- `data/raw/`：价格和储能最小样例。
- `data/scenarios/`：价格场景样例。
- `data/wind_pv_es_calc/`：legacy 风光储测算兼容数据。
- `configs/*.yaml`：入口脚本和样例构造参数。
- 外部天气源：Open-Meteo、NetCDF、Mongo 或本地测点文件。

## 典型流向

```text
configs/*.yaml / data/* / weather source
→ data_provider loader / builder
→ forecasting / optimization / evaluation（投资测算侧为平级包 `investment_estimation`）
→ app demo 或 tests
```

## 使用边界

- 本模块负责数据形状、路径解析、样例生成和轻量质量处理，不负责优化目标函数。
- 真实项目接入时，应在这里建立稳定的数据 contract，再让算法模块消费 contract。
- legacy 桥接代码用于兼容历史 CSV 字段，不应成为新主线字段命名的来源。
