# resource_simulation

本模块承载 `investment_estimation` 内独立的风光资源仿真算法。代码从原容量规划资源仿真模块迁移而来，但迁移后运行时不再导入主项目旧包路径。

## 功能定位

资源仿真模块用于把场址、气象、设备和等效小时数约束转换成投资测算链路可用的出力曲线：

```text
PV 仿真  -> time,pv_kw
Wind 仿真 -> time,wind_kw
资源合并 -> time,pv_kw,wind_kw
```

当前已迁移：

1. `PVProfileConfig` + `load_or_build_pv_profile()`：光伏 v1，支持 `clear_sky`、`weather_driven`、`replay`。
2. `PVSimulator`：光伏 v2，基于 GHI、温度、风速进行物理仿真和等效小时数校准。
3. `WindProfileConfig` + `load_or_build_wind_profile()`：风电 v1，支持自定义功率曲线、风速高度外推和 `target_full_load_hours` 削峰回补校准。
4. `WindSimulator`：风电 v2，基于 `windpowerlib` 内置机型库和等效小时数校准。
5. `weather.py`：本地 Open-Meteo ERA5 获取函数和本地气象 CSV 读写函数。

## 独立性边界

本模块允许依赖：

```text
pandas
numpy
pvlib
windpowerlib
requests
```

本模块不允许导入主项目旧包路径，资源仿真所需的气象读取能力已在本目录内独立实现。

如需气象数据，优先通过配置传入本地 CSV；未传入本地 CSV 时，入口脚本才会调用 `weather.fetch_weather_open_meteo()` 联网获取 Open-Meteo 数据。

## 运行入口

入口脚本位于 `src/investment_estimation/app/`：

```text
python -m investment_estimation.app.run_pv_simulation_v1 --config src/investment_estimation/configs/resource_pv_simulation_v1.yaml
python -m investment_estimation.app.run_pv_simulation_v2 --config src/investment_estimation/configs/resource_pv_simulation_v2.yaml
python -m investment_estimation.app.run_wind_simulation_v1 --config src/investment_estimation/configs/resource_wind_simulation_v1.yaml
python -m investment_estimation.app.run_wind_simulation_v2 --config src/investment_estimation/configs/resource_wind_simulation_v2.yaml
python -m investment_estimation.app.build_resource_profile --config src/investment_estimation/configs/resource_profile_demo.yaml
```

默认配置中，PV v2 和 Wind v1/v2 使用 `dataset/resource_simulation/` 下的小型本地气象样例，避免示例运行依赖网络。

## 算法进度

MVP 版本已完成：

1. 光伏 v1/v2 算法迁移。
2. 风电 v1/v2 算法迁移。
3. Open-Meteo 最小气象获取能力迁移。
4. 单资源 CSV 输出。
5. `time,pv_kw,wind_kw` 合并输出。
6. 独立性测试、PV replay 测试、风电满发小时数校准测试和资源合并测试。

后续可继续补充：

1. 更完整的全年本地气象样例。
2. 资源仿真缓存策略。
3. 15 分钟气象数据重采样和插值规则。
4. 风电 v1 与 v2 的等效小时数校准误差诊断。
