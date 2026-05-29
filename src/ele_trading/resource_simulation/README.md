# resource_simulation — 风光资源仿真与 profile 构造

本模块负责把气象、站点参数和缓存文件转换为容量规划可消费的风电/光伏出力曲线。

## 当前文件

| 文件 | 职责 |
|------|------|
| `pv_simulation.py` | 基于 `pvlib` 的 PV 物理仿真，支持等效小时数校准 |
| `wind_simulation.py` | 基于 `windpowerlib` 的风电物理仿真，支持机型选择和等效小时数校准 |
| `pv_profile.py` | PV profile 构造，支持 `clear_sky`、`weather_driven`、`replay` 和 CSV 缓存 |
| `wind_profile.py` | 风电 profile 构造，支持高度外推、功率曲线和满发小时校准 |

## 模块定位

本模块只负责风光资源侧的出力曲线生成，不负责容量规划、IRR 测算或储能调度。容量规划算法消费本模块生成的曲线后，再自行做容量缩放、消纳统计、经济性计算。

典型数据流：

```text
气象 / 站点参数 / 历史出力 / 缓存 CSV
→ resource_simulation
→ 风电出力曲线 / 光伏出力曲线
→ capacity_planning
→ 风光储容量规划、BESS 搜索、IRR 测算
```

## `pv_simulation.py` 原理

`pv_simulation.py` 提供 `SolarSimulator`，基于 `pvlib` 做光伏物理仿真。输入气象数据需要包含：

| 字段 | 含义 |
|------|------|
| `ghi` | 水平总辐照度，W/m2 |
| `temp_air` | 环境温度，degC |
| `wind_speed` | 风速，m/s |

核心计算流程：

1. 根据经纬度和时间计算太阳位置。
2. 使用 DISC 模型把 `GHI` 分解为 `DNI`。
3. 按下式计算 `DHI`：

   ```text
   DHI_t = GHI_t - DNI_t * cos(zenith_t)
   ```

4. 使用 Hay-Davies 模型计算组件斜面辐照度 `POA`。
5. 使用 SAPM 温度模型计算组件温度：

   ```text
   T_cell_t = f(POA_t, temp_air_t, wind_speed_t)
   ```

6. 使用 PVWatts 计算 1 MW 基准 DC 出力：

   ```text
   P_dc_t = PVWatts(POA_t, T_cell_t, pdc0=1MW)
   ```

7. 乘系统综合效率 `_SYSTEM_EFF = 0.96` 并转换为 MW：

   ```text
   P_ac_raw_t = P_dc_t * 0.96 / 1e6
   ```

8. 根据目标等效小时数 `equiv_hours` 做年发电量校准：

   ```text
   E_raw = sum(P_ac_raw_t * dt_hours)
   E_target = 1MW * equiv_hours
   K = E_target / E_raw
   ```

9. 缩放到目标装机容量：

   ```text
   output_mw_t = P_ac_raw_t * K * target_capacity_mw
   ```

输出 `SolarSimResult`：

| 字段 | 含义 |
|------|------|
| `output_mw` | 指定 `target_capacity_mw` 下的光伏 MW 出力曲线 |
| `total_generation_mwh` | 模拟期总发电量，MWh |
| `scale_factor` | 等效小时数校准系数 `K` |

如果 `target_capacity_mw = 1.0`，`output_mw` 是 1 MW 光伏装机下的 MW 出力曲线。

## `wind_simulation.py` 原理

`wind_simulation.py` 提供 `WindSimulator`，基于 `windpowerlib` 做风电物理仿真。输入气象数据需要包含：

| 字段 | 含义 |
|------|------|
| `wind_speed` | 参考高度风速，m/s |
| `temperature` | 环境温度，degC |
| `pressure` | 气压，Pa |

核心计算流程：

1. 从 `windpowerlib` 内置机型库中选择风机型号。若传入 `single_turbine_capacity_mw`，选择额定功率最接近的机型；否则选择接近中位额定功率的机型。
2. 使用幂律将参考高度风速外推到轮毂高度：

   ```text
   v_hub_t = v_ref_t * (hub_height / wind_speed_ref_height) ^ wind_shear_exp
   ```

3. 构造 `windpowerlib.ModelChain` 所需的气象 MultiIndex 表。
4. 用风机功率曲线计算单机出力，并归一化为容量因子：

   ```text
   CF_t = P_turbine_t / P_rated
   ```

5. 应用系统综合折减 `_SYSTEM_EFF = 0.92`：

   ```text
   CF_sys_t = CF_t * 0.92
   ```

6. 根据目标等效小时数 `equiv_hours` 做校准：

   ```text
   E_raw_per_mw = sum(CF_sys_t * dt_hours)
   K = equiv_hours / E_raw_per_mw
   ```

7. 缩放到目标风电装机：

   ```text
   output_mw_t = CF_sys_t * K * target_capacity_mw
   ```

输出 `WindSimResult`：

| 字段 | 含义 |
|------|------|
| `output_mw` | 指定 `target_capacity_mw` 下的风电 MW 出力曲线 |
| `total_generation_mwh` | 模拟期总发电量，MWh |
| `scale_factor` | 等效小时数校准系数 `K` |
| `selected_turbine` | 选用风机型号 |
| `turbine_count` | 按目标容量估算的风机台数 |

如果 `target_capacity_mw = 1.0`，`output_mw` 是 1 MW 风电装机下的 MW 出力曲线。

## `pv_profile.py` 原理

`pv_profile.py` 是光伏 profile 构造层，面向业务流程封装仿真、回放和缓存。核心入口是：

```python
load_or_build_pv_profile(config, time_index=None, weather_df=None, cache_path=None)
```

`PVProfileConfig` 主要参数：

| 字段 | 含义 |
|------|------|
| `latitude`, `longitude`, `timezone` | 站点位置和时区 |
| `capacity_kwp` | 光伏装机容量，kWp |
| `tilt`, `azimuth` | 倾角和方位角 |
| `system_loss` | 系统损耗比例 |
| `temp_coeff` | 温度功率系数 |
| `cloud_factor` | 晴空模式下的云量/资源折减系数 |
| `mode` | 构造模式 |

支持三种模式：

| mode | 逻辑 |
|------|------|
| `clear_sky` | 基于经纬度、时间和 Ineichen 晴空模型生成辐照，再计算光伏出力 |
| `weather_driven` | 调用 `SolarSimulator`，用外部气象驱动光伏仿真 |
| `replay` | 直接使用 `weather_df["pv_kw"]` 作为已有光伏出力 |

`clear_sky` 模式中，出力计算口径是：

```text
pv_kw_t = AC_t / 1000 * capacity_kwp
```

`weather_driven` 模式中，先将 `capacity_kwp` 换算为 MW：

```text
capacity_mw = capacity_kwp / 1000
```

再调用 `SolarSimulator.simulate(..., target_capacity_mw=capacity_mw)`，最后把 MW 输出转成 kW：

```text
pv_kw_t = output_mw_t * 1000
```

因此 `load_or_build_pv_profile()` 的输出 `RenewableProfileResult.power_series` 是：

```text
指定 capacity_kwp 光伏装机下的 kW 总出力曲线
```

## `wind_profile.py` 原理

`wind_profile.py` 是风电 profile 构造层，面向业务流程封装风速外推、简化功率曲线、满发小时校准和缓存。核心入口是：

```python
load_or_build_wind_profile(config, weather_df=None, cache_path=None)
```

`WindProfileConfig` 主要参数：

| 字段 | 含义 |
|------|------|
| `year`, `freq` | 年份和时间粒度 |
| `farm_capacity_mw` | 风场装机容量，MW |
| `target_full_load_hours` | 目标等效满发小时数 |
| `mean_wind_speed_target` | 目标平均风速，用于资源强度校准 |
| `meteo_height_m`, `met_mast_height_m`, `hub_height_m` | 气象高度、测风塔高度和轮毂高度 |
| `shear_alpha` | 风切变指数 |
| `rated_power_kw` | 单机额定功率 |
| `cut_in`, `rated_speed`, `cut_out` | 切入、额定、切出风速 |
| `max_power_ratio` | 风场最大出力相对装机的上限 |
| `mode` | 构造模式 |

核心计算流程：

1. 读取或获取包含 `wind_speed_100m`、`temperature_2m` 的气象数据。
2. 使用幂律做高度外推：

   ```text
   v_140_t = v_100_t * (met_mast_height_m / meteo_height_m) ^ shear_alpha
   v_hub_t = v_140_t * (hub_height_m / met_mast_height_m) ^ shear_alpha
   ```

3. 如果配置了 `mean_wind_speed_target`，按目标平均风速整体缩放：

   ```text
   v_140_t = v_140_t * mean_wind_speed_target / mean(v_140)
   ```

4. 根据配置构造简化风机功率曲线：

   ```text
   P(v) = 0, v < cut_in
   P(v) = ((v - cut_in) / (rated_speed - cut_in)) ^ 3 * rated_power_kw
   P(v) = rated_power_kw, rated_speed <= v < cut_out
   P(v) = 0, v >= cut_out
   ```

5. 用 `ModelChain` 计算单台风机出力。
6. 按装机容量和单机容量估算风机台数：

   ```text
   turbine_count = round(farm_capacity_mw * 1000 / rated_power_kw)
   ```

7. 得到风场原始 MW 出力：

   ```text
   farm_output_mw_t = single_turbine_kw_t * turbine_count / 1000
   ```

8. 如设置 `target_full_load_hours`，将年发电量校准到目标：

   ```text
   E_target = farm_capacity_mw * target_full_load_hours
   ```

   同时限制最大出力：

   ```text
   P_t <= farm_capacity_mw * max_power_ratio
   ```

`simulate_wind_farm_output()` 内部返回 kW 曲线，但 `load_or_build_wind_profile()` 最终会除以 1000，因此 `RenewableProfileResult.power_series` 是：

```text
指定 farm_capacity_mw 风电装机下的 MW 总出力曲线
```

## 输出口径

- `SolarSimulator.simulate()` 输出指定 MW 光伏容量下的 MW 出力曲线。
- `WindSimulator.simulate()` 输出指定 MW 风电容量下的 MW 出力曲线。
- `load_or_build_pv_profile()` 输出指定 kWp 光伏容量下的 kW 出力曲线。
- `load_or_build_wind_profile()` 输出指定 MW 风电容量下的 MW 出力曲线。

容量规划算法如需单位出力曲线，应在调用侧显式归一化，例如：

```python
pv_unit_kw = pv_profile.power_series / pv_cfg.capacity_kwp
wind_unit_kw = wind_profile.power_series / wind_cfg.farm_capacity_mw * 1000.0
```

如果直接使用仿真器且 `target_capacity_mw = 1.0`：

```python
pv_unit_kw = solar_result.output_mw
wind_unit_kw = wind_result.output_mw * 1000.0
```

原因是容量规划中的单位出力约定为：

| 输入 | 语义 | 单位 |
|------|------|------|
| `pv_unit_kw` | 1 kWp 光伏装机对应的出力 | kW |
| `wind_unit_kw` | 1 MW 风电装机对应的出力 | kW |

所以：

```text
pv_unit_kw_t = pv_kw_t / capacity_kwp
wind_unit_kw_t = wind_mw_t / farm_capacity_mw * 1000
```

## 缓存边界

`load_or_build_pv_profile()` 和 `load_or_build_wind_profile()` 支持 `cache_path`。当缓存文件存在时会直接读取 CSV，不重新仿真；修改站点、容量、等效小时数或气象来源后，应刷新缓存或更换缓存路径。

当前缓存只按 `cache_path` 是否存在判断是否复用，不会自动校验缓存内容是否匹配当前配置。真实项目中建议让缓存文件名包含关键参数，例如年份、站点、装机容量、等效小时数和模式。
