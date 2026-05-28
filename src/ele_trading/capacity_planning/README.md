# capacity_planning

风光储容量规划模块，提供从出力仿真到容量优化的完整工具链。

## 模块总览

```
capacity_planning/
├── solar_simulation.py      # PV 物理仿真引擎
├── wind_simulation.py       # 风电物理仿真引擎
├── pv_profile.py            # PV 出力曲线生成（多模式 + 缓存）
├── wind_profile.py          # 风电出力曲线生成（高度外推 + 缓存）
├── capacity_optimizer.py    # 风光储联合网格搜索优化
├── bess_capacity_planner.py # 离网 BESS 容量规划
├── wind_bess_planner.py     # Wind+BESS 容量规划
└── wind_pv_bess_planner.py  # Wind+PV+BESS 容量规划
```

## 架构设计

模块分为两层：**仿真引擎层**负责将气象数据转化为出力时序，**规划器层**消费出力时序做容量优化。

```
┌─────────────────────────────────────────────────────────────┐
│  规划器层（消费出力曲线，搜索最优容量）                           │
│                                                             │
│  capacity_optimizer   bess_capacity_planner                 │
│  wind_bess_planner    wind_pv_bess_planner                  │
└────────┬──────────────────────────────────────┬─────────────┘
         │ 接收 load / wind / pv 出力时序        │
┌────────┴──────────────────────────────────────┴─────────────┐
│  仿真引擎层（生产出力曲线）                                    │
│                                                             │
│  ┌─ pv_profile.py ────────┐  ┌─ wind_profile.py ─────────┐ │
│  │ clear_sky    ─┐        │  │ 功率曲线构建 ─┐            │ │
│  │ weather_driven├→ 缓存  │  │ 幂律高度外推  ├→ 缓存      │ │
│  │ replay      ─┘        │  │ FLH 校准     ─┘            │ │
│  └──────────┬────────────┘  └──────────┬──────────────────┘ │
│             │ SolarSimulator           │ WindSimulator       │
│             ▼                          ▼                     │
│  solar_simulation.py          wind_simulation.py             │
│  (pvlib 全链路物理仿真)        (windpowerlib 机型仿真)        │
└─────────────────────────────────────────────────────────────┘
```

## 仿真引擎层

### solar_simulation.py — PV 物理仿真引擎

**原理：** 基于 pvlib 的全链路光伏仿真，输入气象 DataFrame（含 GHI、温度、风速），经过以下物理模型链：

1. **DISC 模型** — GHI 分解为 DNI + DHI
2. **Hay-Davies 模型** — 转换为斜面辐照度（POA）
3. **SAPM 模型** — 计算组件温度
4. **PVWatts DC/AC** — 计算直流/交流出力
5. **等效小时数校准** — 按目标年利用小时数缩放出力曲线

**核心类：**

```python
from ele_trading.capacity_planning import SolarSimulator, SolarSimResult

sim = SolarSimulator(
    latitude=28.42,
    longitude=117.88,
    timezone="Asia/Shanghai",
    altitude=50.0,
)
result: SolarSimResult = sim.simulate(
    weather_df,           # DataFrame，含 ghi/temp_air/wind_speed 列
    equiv_hours=1200.0,   # 目标等效利用小时数
    target_capacity_mw=1.0,
)
# result.output_mw           → pd.Series（MW）
# result.total_generation_mwh → float
# result.scale_factor         → float（校准系数 K）
```

**外部依赖：** pvlib, numpy, pandas

### wind_simulation.py — 风电物理仿真引擎

**原理：** 基于 windpowerlib 的风电仿真，自动从内置机型库选型：

1. **机型选择** — 根据轮毂高度和单机容量匹配 windpowerlib 内置机型（带 lru_cache）
2. **高度外推** — 幂律公式将参考高度风速外推至轮毂高度
3. **ModelChain** — 功率曲线仿真
4. **系统折减** — 0.92 综合效率（尾流 + 可用率 + 集电线损）
5. **等效小时数校准** — 按目标年利用小时数缩放

**核心类：**

```python
from ele_trading.capacity_planning import WindSimulator, WindSimResult

sim = WindSimulator(
    hub_height=100.0,
    wind_shear_exp=0.143,
    wind_speed_ref_height=10.0,
)
result: WindSimResult = sim.simulate(
    weather_df,           # DataFrame，含 wind_speed/temperature/pressure 列
    equiv_hours=2000.0,
    target_capacity_mw=1.0,
)
# result.output_mw           → pd.Series（MW）
# result.selected_turbine    → str（选中的机型名）
# result.turbine_count       → int
```

**外部依赖：** windpowerlib, numpy, pandas

## Profile 生成层

Profile 层在仿真引擎之上增加了**多模式选择**和 **CSV 缓存**，是面向业务的统一入口。

### pv_profile.py — PV 出力曲线生成

支持三种模式：

| 模式 | 说明 | 依赖 |
|------|------|------|
| `clear_sky` | 基于 pvlib 晴空模型，无需实测气象 | time_index |
| `weather_driven` | 基于实测气象，调用 SolarSimulator | weather_df |
| `replay` | 直接回放历史 PV 出力数据 | weather_df（含 pv_kw 列） |

**配置：**

```python
from ele_trading.capacity_planning import PVProfileConfig, load_or_build_pv_profile

config = PVProfileConfig(
    latitude=28.42,
    longitude=117.88,
    timezone="Asia/Shanghai",
    capacity_kwp=28250.0,
    tilt=None,              # None → 自动取 abs(latitude)
    azimuth=180.0,
    system_loss=0.20,
    temp_coeff=-0.004,
    cloud_factor=0.75,      # 晴空模式下的云量折减
    mode="clear_sky",       # clear_sky / weather_driven / replay
)
```

**使用：**

```python
result = load_or_build_pv_profile(
    config=config,
    time_index=pd.DatetimeIndex(...),  # clear_sky 模式需要
    weather_df=None,                    # weather_driven/replay 模式需要
    cache_path="data/pv_cache.csv",    # 可选，首次生成后写入，后续直接读取
)
# result.power_series → pd.Series（kW）
# result.metadata     → dict（含 equivalent_hours 等）
```

**内部依赖：** solar_simulation.SolarSimulator

### wind_profile.py — 风电出力曲线生成

**原理：** 用户自定义功率曲线（cut_in / rated_speed / cut_out），结合幂律高度外推和等效小时数迭代校准。

| 参数 | 说明 |
|------|------|
| `cut_in` / `rated_speed` / `cut_out` | 风机切入/额定/切出风速（m/s） |
| `rated_power_kw` | 单机额定功率（kW） |
| `meteo_height_m` → `met_mast_height_m` → `hub_height_m` | 三级高度外推链 |
| `target_full_load_hours` | 目标等效小时数，用于校准 |
| `mean_wind_speed_target` | 可选，均值校准风速 |

**配置：**

```python
from ele_trading.capacity_planning import WindProfileConfig, load_or_build_wind_profile

config = WindProfileConfig(
    year=2025,
    freq="1h",
    farm_capacity_mw=110.0,
    target_full_load_hours=1920.7,
    mean_wind_speed_target=5.5,
    meteo_height_m=100.0,
    met_mast_height_m=140.0,
    hub_height_m=140.0,
    shear_alpha=0.2,
    rated_power_kw=5000.0,
    cut_in=3.0,
    rated_speed=11.0,
    cut_out=25.0,
    max_power_ratio=1.2,
    mode="resource_simulation",
)
```

**使用：**

```python
result = load_or_build_wind_profile(
    config=config,
    weather_df=weather_df,              # 含 wind_speed_100m/temperature_2m
    cache_path="data/wind_cache.csv",   # 可选
)
# result.power_series → pd.Series（MW）
# result.metadata     → dict（含 equivalent_hours 等）
```

**内部依赖：** pv_profile.RenewableProfileResult, data_provider.resource_weather

### 两种仿真方式的区别

solar_simulation 和 pv_profile（以及 wind_simulation 和 wind_profile）存在功能重叠，但面向不同场景：

| | solar/wind_simulation | pv/wind_profile |
|---|---|---|
| **面向** | forecasting、app 脚本 | 遗留工作流、数据准备 |
| **输出单位** | MW | kW（PV）/ MW（Wind） |
| **输入要求** | weather_df + equiv_hours | Config 对象（含模式选择） |
| **缓存** | 无 | CSV 读写 |
| **模式** | 单一物理仿真 | 多模式（clear_sky/replay 等） |

## 规划器层

### capacity_optimizer.py — 风光储联合优化

**原理：** 粗扫 + 细扫两阶段网格搜索，在风 MW × PV MW × ESS MWh 三维空间中寻找满足绿电率和自消纳率约束的最小成本方案。

```python
from ele_trading.capacity_planning import CapacityOptimizer

optimizer = CapacityOptimizer(storage_params, cost_params, search_params)
result = optimizer.optimize(
    load_series, wind_unit_output, solar_unit_output,
    green_ratio_min=0.3, self_use_ratio_min=0.6,
)
# result.wind_mw, result.pv_mw, result.ess_mwh, result.total_cost_wan
```

### bess_capacity_planner.py — 离网 BESS 容量规划

**原理：** 贪心调度 + 线性扫描，寻找满足自消纳率和负荷覆盖率的最小 BESS 容量。支持 Numba JIT 加速。

```python
from ele_trading.capacity_planning import plan_energy_system, BESSPlanConfig

cfg = BESSPlanConfig(bess_capex_yuan_per_kwh=1000.0, self_use_ratio_min=0.6)
result = plan_energy_system(df_load, pv_power, wind_input, cfg=cfg)
# result.bess_kwh, result.feasible, result.cost_yuan
```

### wind_bess_planner.py — Wind+BESS 容量规划

**原理：** 二分搜索最小可行 BESS 容量。支持两种调度模式：
- **纯弃电搬运** — 只用 surplus 充电，deficit 放电
- **平移充电** — 允许 Wind < Load 时提前充电，应对未来 deficit

```python
from ele_trading.capacity_planning import plan_wind_bess_system, WindBESSPlanConfig

cfg = WindBESSPlanConfig(min_green_self_consumption=0.6)
result = plan_wind_bess_system(df_load, wind_input, cfg=cfg)
# result.capacity_mwh, result.feasible, result.schedule
```

### wind_pv_bess_planner.py — Wind+PV+BESS 容量规划

**原理：** PV 粗扫 + 细扫两阶段搜索，每个 PV 候选点内用二分搜索找最小 BESS。Numba JIT 加速年度调度，支持能量门槛预筛（gate check）和充放切换间隔。

```python
from ele_trading.capacity_planning import plan_wind_pv_bess, WindPVBEssPlanConfig

cfg = WindPVBEssPlanConfig(
    self_use_ratio_min=0.6,
    load_cover_ratio_min=0.2,
)
result = plan_wind_pv_bess(df_load, pv_unit_kw, wind_input, cfg=cfg)
# result.pv_kwp, result.bess_kwh, result.status
```

## 数据流

### 路径 A：app 脚本（直接使用仿真引擎）

```
合成气象数据
  → SolarSimulator.simulate() → pv_unit_output (MW/MW)
  → WindSimulator.simulate()  → wind_unit_output (MW/MW)
  → 规划器（CapacityOptimizer / WindPVBEssPlanConfig / ...）
  → 最优容量方案
```

对应脚本：`app/run_wind_solar_storage.py`、`app/run_bess_capacity_planning.py`、`app/run_wind_bess_capacity_planning.py`、`app/run_wind_pv_bess_capacity_planning.py`

### 路径 B：遗留工作流（使用 Profile 层 + 缓存）

```
Open-Meteo 气象 API
  → load_or_build_pv_profile(PVProfileConfig) → pv_kw 时序（CSV 缓存）
  → load_or_build_wind_profile(WindProfileConfig) → wind_kw 时序（CSV 缓存）
  → build_legacy_total_frame() → 合并总表
  → 遗留优化算法
```

对应脚本：`app/run_legacy_data_preparation.py`（配置：`configs/wind_pv_es_calc_data_bridge.yaml`）

## 外部依赖

| 包 | 用途 |
|---|---|
| pvlib | 光伏物理仿真（辐照度、组件温度、DC/AC 出力） |
| windpowerlib | 风电物理仿真（功率曲线、ModelChain） |
| numba | 可选，BESS 调度引擎 JIT 加速 |
| numpy, pandas | 基础数值计算 |
