# 新能源出力仿真算法说明 (`capacity_planning.resource_simulation`)

## 1. 概述

本目录包含四套独立的新能源出力仿真算法，用于生成典型年份下光伏（PV）和风电（Wind）的**逐时出力曲线**（kW）。每类资源各有两个版本：

| 模块 | 文件 | 定位 | 核心方法 |
|------|------|------|----------|
| 光伏 v1 | `pv_simulation_v1.py` | 配置驱动，支持多模式 | 清晰天空 / 气象驱动 / 回放 |
| 光伏 v2 | `pv_simulation_v2.py` | 气象驱动仿真器类 | GHI→DNI/DHI + 斜面辐照 + PVWatts |
| 风电 v1 | `wind_simulation_v1.py` | 配置驱动，气象数据自动获取 | 功率曲线 + 幂律外推 + FLH 标定 |
| 风电 v2 | `wind_simulation_v2.py` | 仿真器类，自动匹配机型 | windpowerlib ModelChain + 等效小时数校准 |

所有模块的输出统一为 `SimulationResult`（定义在 `models.py`），功率单位统一为 **kW**。

---

## 2. 目录结构

```
capacity_planning/resource_simulation/
├── models.py                 # 共用数据模型 SimulationResult
├── pv_simulation_v1.py       # 光伏 v1: 配置驱动，多模式入口
├── pv_simulation_v2.py       # 光伏 v2: PVSimulator 类
├── wind_simulation_v1.py     # 风电 v1: 配置驱动，自动获取气象
├── wind_simulation_v2.py     # 风电 v2: WindSimulator 类
├── __init__.py               # 对外导出接口
└── README.md
```

---

## 3. 共用模型：SimulationResult

```python
@dataclass(slots=True)
class SimulationResult:
    power_series: pd.Series              # 出力时序（kW）
    total_generation_mwh: float          # 年发电量（MWh）
    scale_factor: float                  # 校准系数 K
    selected_turbine: str | None = None  # 风电专属：机型名称
    turbine_count: int | None = None     # 风电专属：风机台数
    metadata: dict[str, float | str] | None = None  # 兼容层
```

---

## 4. 光伏仿真算法

### 4.1 光伏 v1：`PVProfileConfig` + 多模式入口

**配置类：`PVProfileConfig`**

| 字段 | 说明 |
|------|------|
| `latitude / longitude` | 场址经纬度 |
| `timezone` | 时区，如 `"Asia/Shanghai"` |
| `capacity_kwp` | 装机容量（kWp） |
| `tilt` | 组件倾角，`None` 则取纬度绝对值 |
| `azimuth` | 组件方位角（°） |
| `system_loss` | 系统综合损耗率 |
| `temp_coeff` | 功率温度系数（1/°C） |
| `cloud_factor` | 云量折减系数（仅清晰天空模式） |
| `mode` | `"clear_sky"` / `"weather_driven"` / `"replay"` |

**三种仿真模式：**

1. **`clear_sky`**：基于 pvlib Ineichen 清晰天空模型，计算斜面辐照度 → 组件温度 → PVWatts DC/AC 出力。适用于无实测气象数据的典型年估算。
2. **`weather_driven`**：委托 `PVSimulator`（v2），使用实测 GHI 气象数据进行物理仿真。
3. **`replay`**：直接回放历史 `pv_kw` 数据，用于对照或基准测试。

**主入口：`load_or_build_pv_profile()`**

```
输入: config + time_index/weather_df + 可选 cache_path
  ↓
检查缓存 → 命中则直接读取
  ↓ (未命中)
根据 mode 分派:
  clear_sky      → simulate_pv_clear_sky()
  weather_driven → simulate_pv_from_weather() → PVSimulator.simulate()
  replay         → 直接取 weather_df["pv_kw"]
  ↓
计算等效小时数 + 总发电量
  ↓ (首次运行且指定 cache_path)
写入 CSV 缓存
  ↓
输出: SimulationResult
```

### 4.2 光伏 v2：`PVSimulator` 类

面向实测气象数据的光伏仿真器，封装为类便于复用和参数化。

**仿真流程：**

```
输入: weather_df (ghi, temp_air, wind_speed) + equiv_hours + target_capacity_mw
  ↓
① 太阳位置计算 → pvlib.location.get_solarposition()
  ↓
② GHI → DNI + DHI → pvlib.irradiance.disc()（DISC 模型）
  ↓
③ 斜面辐照度 → pvlib.irradiance.get_total_irradiance()（Hay-Davies 模型）
  ↓
④ 组件温度 → pvlib.temperature.sapm_cell()（SAPM 开放架构参数）
  ↓
⑤ DC 功率 → pvlib.pvsystem.pvwatts_dc()（以 1 MW 为基准）
  ↓
⑥ AC 出力 = DC × 系统综合效率(0.96) → MW
  ↓
⑦ 校准: K = equiv_hours / (原始等效小时数)
  ↓
⑧ 缩放: output_kw = pac_mw × K × target_capacity_mw × 1000
  ↓
输出: SimulationResult
```

**关键参数：**

| 参数 | 值 | 说明 |
|------|-----|------|
| `_GAMMA_PDC` | -0.004 /°C | PVWatts 功率温度系数 |
| `_SYSTEM_EFF` | 0.96 | 逆变器 × 线损 × 其他损耗 |
| SAPM 组件参数 | a=-3.56, b=-0.075, deltaT=3 | 开放架构玻璃-玻璃组件 |

---

## 5. 风电仿真算法

### 5.1 风电 v1：`WindProfileConfig` + 自动气象获取

**配置类：`WindProfileConfig`**

| 字段 | 说明 |
|------|------|
| `year` | 仿真目标年份 |
| `freq` | 时间分辨率（如 `"1h"`） |
| `farm_capacity_mw` | 风场总装机容量（MW） |
| `mean_wind_speed_target` | 测风塔高度处年均风速校准目标（m/s） |
| `target_full_load_hours` | 年等效满发小时数目标（h） |
| `meteo_height_m` | 气象数据风速测量高度（m） |
| `met_mast_height_m` | 测风塔高度（m） |
| `hub_height_m` | 风机轮毂高度（m） |
| `shear_alpha` | 风切变指数 |
| `rated_power_kw` | 单机额定功率（kW） |
| `cut_in / rated_speed / cut_out` | 切入/额定/切出风速（m/s） |
| `max_power_ratio` | 最大出力与装机容量的比值上限 |
| `mode` | `"resource_simulation"` 自动获取气象数据 |

**仿真流程：**

```
输入: config + 可选 weather_df + 可选 cache_path
  ↓
检查缓存 → 命中则直接读取
  ↓ (未命中)
自动获取气象数据 → fetch_weather_open_meteo() (wind_speed_100m, temperature_2m)
  ↓
① 风速高度外推: 100m → 测风塔高度(140m) → 轮毂高度
   使用幂律公式: v₂ = v₁ × (h₂/h₁)^α
  ↓
② 年均风速校准（可选）: v₁₄₀ ×= target / mean(v₁₄₀)
  ↓
③ 构建自定义功率曲线:
   - 切入~额定: P = ((v-cut_in)/(rated_speed-cut_in))³ × rated_power
   - 额定~切出: P = rated_power
  ↓
④ 单机功率 → windpowerlib ModelChain
  ↓
⑤ 风场聚合: 单机功率 × 台数(装机容量/单机容量)
  ↓
⑥ 二次标定 rescale_wind_output_to_target_flh():
   迭代 8 次:
     a) 全局缩放至目标能量
     b) 削峰至 max_power_ratio × 装机
     c) 将被削能量回补至未饱和时段
   目标: 峰值 ≤ 1.2×装机 且 年 FLH ≈ target
  ↓
输出: SimulationResult (含缓存)
```

**风场级聚合控制（核心算法）：**

`rescale_wind_output_to_target_flh()` 实现"削峰回补"策略，确保：
- 峰值出力不超过 `max_power_ratio × farm_capacity_mw`（默认 1.2 倍）
- 年等效满发小时数精确对标目标值
- 能量守恒（削峰损失的电量在低出力时段回补）

### 5.2 风电 v2：`WindSimulator` 类

面向实测气象数据的风电仿真器，自动从 windpowerlib 内置库匹配风机机型。

**仿真流程：**

```
输入: weather_df (wind_speed, temperature, pressure) + equiv_hours + target_capacity_mw
  ↓
① 机型选择 → _select_turbine():
   遍历 windpowerlib 内置库，逐一实例化获取额定功率
   - 指定单机容量: 选最接近的机型
   - 未指定: 选中位数机型
   (结果由 lru_cache 缓存)
  ↓
② 风速外推: ref_height → hub_height（幂律）
  ↓
③ 构建 MultiIndex 列格式 → windpowerlib ModelChain
  ↓
④ 单机功率 → 归一化为容量因子 [0, 1]
  ↓
⑤ 应用系统综合效率(0.92): 尾流损耗 + 可用率 + 集电线损
  ↓
⑥ 校准: K = equiv_hours / (原始等效小时数)
  ↓
⑦ 缩放: output_kw = cf × eff × K × target_capacity_mw × 1000
  ↓
输出: SimulationResult (含 selected_turbine, turbine_count)
```

**关键参数：**

| 参数 | 值 | 说明 |
|------|-----|------|
| `_SYSTEM_EFF` | 0.92 | 尾流损耗 + 可用率 + 集电线损 |
| `hub_height` | 100.0 m | 默认轮毂高度 |
| `wind_shear_exp` | 0.143 | 默认风切变指数 |
| `wind_speed_ref_height` | 10.0 m | 气象数据参考高度 |

---

## 6. 版本对比

### 光伏 v1 vs v2

| 维度 | v1 (PVProfileConfig) | v2 (PVSimulator) |
|------|---------------------|------------------|
| 入口方式 | 配置类 + 函数式 | 类封装 |
| 气象输入 | GHI 或 time_index | 必须 GHI |
| 辐照分解 | Ineichen 清晰天空 或 DISC | DISC |
| 斜面模型 | 默认 Perez | Hay-Davies |
| 组件温度 | pvsyst_cell | SAPM (open-rack) |
| 缓存支持 | ✅ | ❌ |
| 多模式 | clear_sky / weather_driven / replay | 单一模式 |

### 风电 v1 vs v2

| 维度 | v1 (WindProfileConfig) | v2 (WindSimulator) |
|------|----------------------|-------------------|
| 入口方式 | 配置类 + 函数式 | 类封装 |
| 机型定义 | 手动定义功率曲线 | 自动匹配 windpowerlib 内置库 |
| 风速校准 | 年均风速 + FLH 双重校准 | 仅 FLH 校准 |
| 峰值约束 | 削峰回补（max_power_ratio） | 无显式峰值约束 |
| 系统效率 | 无独立系数（含在校准中） | 0.92 综合折减 |
| 缓存支持 | ✅ | ❌ |
| 气象获取 | 自动 (Open-Meteo) | 外部传入 |

---

## 7. 外部依赖

| 库 | 用途 |
|----|------|
| `pvlib` | 太阳位置、辐照分解、斜面辐照、组件温度、PVWatts DC/AC |
| `windpowerlib` | 风机功率曲线、ModelChain、机型库 |
| `pandas` | 时序数据处理 |
| `numpy` | 数值计算 |
| `ele_trading.data_provider` | 气象数据获取（Open-Meteo，仅 v1） |

---

## 8. 使用示例

### 光伏 v1（配置驱动）

```python
from ele_trading.capacity_planning.resource_simulation import PVProfileConfig, load_or_build_pv_profile

config = PVProfileConfig(
    latitude=30.5928,
    longitude=114.3055,
    timezone="Asia/Shanghai",
    capacity_kwp=100000,
    tilt=None,         # 自动取纬度
    azimuth=180.0,
    system_loss=0.15,
    temp_coeff=-0.004,
    cloud_factor=0.8,  # 仅清晰天空模式
    mode="clear_sky",
)
result = load_or_build_pv_profile(
    config=config,
    time_index=pd.date_range("2024-01-01", "2024-12-31 23:00", freq="h"),
)
```

### 光伏 v2（仿真器类）

```python
from ele_trading.capacity_planning.resource_simulation import PVSimulator

sim = PVSimulator(latitude=30.5928, longitude=114.3055)
result = sim.simulate(
    weather_df=weather_df,       # 含 ghi, temp_air, wind_speed
    equiv_hours=1200.0,
    target_capacity_mw=100.0,
)
```

### 风电 v1（配置驱动）

```python
from ele_trading.capacity_planning.resource_simulation import WindProfileConfig, load_or_build_wind_profile

config = WindProfileConfig(
    year=2024,
    freq="1h",
    farm_capacity_mw=200,
    mean_wind_speed_target=7.5,
    target_full_load_hours=2200,
    meteo_height_m=100.0,
    met_mast_height_m=140.0,
    hub_height_m=140.0,
    shear_alpha=0.14,
    rated_power_kw=5000,
    cut_in=3.0,
    rated_speed=11.0,
    cut_out=25.0,
    max_power_ratio=1.2,
    mode="resource_simulation",
)
result = load_or_build_wind_profile(config=config)
```

### 风电 v2（仿真器类）

```python
from ele_trading.capacity_planning.resource_simulation import WindSimulator

sim = WindSimulator(hub_height=100.0)
result = sim.simulate(
    weather_df=weather_df,       # 含 wind_speed, temperature, pressure
    equiv_hours=2000.0,
    target_capacity_mw=200.0,
)
```
