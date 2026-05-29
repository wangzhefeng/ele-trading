# capacity_planning — 容量规划与资源仿真模块

本模块提供 PV、风电、BESS、Wind+BESS、Wind+PV+BESS 和多节点储能容量评估工具。它分为资源出力仿真、profile 构造、容量搜索、可行性/IRR/多节点评估几类能力。

## 当前文件

| 文件 | 职责 |
|------|------|
| `solar_simulation.py` | 基于 `pvlib` 的 PV 物理仿真，支持等效小时数校准 |
| `wind_simulation.py` | 基于 `windpowerlib` 的风电物理仿真，支持机型选择和等效小时数校准 |
| `pv_profile.py` | PV profile 构造，支持 `clear_sky`、`weather_driven`、`replay` 和 CSV 缓存 |
| `wind_profile.py` | 风电 profile 构造，支持高度外推、功率曲线和满发小时校准 |
| `capacity_optimizer.py` | 风光储联合容量优化，粗-精两阶段网格搜索和运行测算 |
| `bess_capacity_planner.py` | 固定新能源出力下 BESS 最小容量规划 |
| `wind_bess_planner.py` | Wind+BESS 容量规划、平移充电策略和可行性诊断 |
| `wind_pv_bess_planner.py` | Wind+PV+BESS 容量规划、能量门槛检查和运行评估 |
| `feasibility_analyzer.py` | 储能项目可行性分析，覆盖价格、负荷、变压器和匹配度 |
| `multi_node_scanner.py` | 多节点储能容量扫描和退化收益评估 |
| `pv_storage_irr_scanner.py` | PV+storage 年收益和 IRR 扫描 |

## 分层关系

```text
气象 / 历史出力 / 负荷
→ solar_simulation / wind_simulation / pv_profile / wind_profile
→ capacity_optimizer / bess_capacity_planner / wind_bess_planner / wind_pv_bess_planner
→ feasibility_analyzer / multi_node_scanner / pv_storage_irr_scanner
→ app 容量规划入口和结果解释
```

## 资源仿真

- `SolarSimulator`：输入 GHI、温度、风速，经过辐照分解、斜面辐照、组件温度、PVWatts 和等效小时数校准，输出 MW 级 PV 出力。
- `WindSimulator`：输入风速、温度、气压，做高度外推、机型选择、功率曲线仿真和系统折减，输出 MW 级风电出力。
- `load_or_build_pv_profile()` / `load_or_build_wind_profile()`：面向业务流程的 profile 构造入口，支持缓存复用。

## 容量规划

- `CapacityOptimizer`：在风、光、储容量网格上搜索满足绿电率、自用率等约束的低成本方案。
- `plan_energy_system()`：在给定负荷和风光出力下搜索满足新能源自消纳率、负荷覆盖率的最小 BESS 容量。
- `plan_wind_bess_system()`：Wind+BESS 场景，支持平移充电策略、容量二分搜索和可行性诊断。
- `plan_wind_pv_bess()`：Wind+PV+BESS 场景，支持 PV 搜索、BESS 搜索和能量门槛检查。

## 扫描与诊断

- `StorageFeasibilityAnalyzer`：从价格价差、负荷形态、变压器约束和充放匹配度评估储能策略可行性。
- `scan_single_node()` / `scan_multiple_nodes()`：对单节点或多节点做储能容量扫描。
- `scan_pv_storage_irr()`：扫描 PV+storage 年收益、IRR 和增量 IRR。

## 对应入口

```bash
uv run python app/run_wind_solar_storage.py
uv run python app/run_bess_capacity_planning.py
uv run python app/run_wind_bess_capacity_planning.py
uv run python app/run_wind_pv_bess_capacity_planning.py
```

legacy 数据准备入口：

```bash
uv run python app/run_legacy_data_preparation.py
```

## 使用边界

- 容量规划里的运行测算是研究型近似，不替代生产级经济测算。
- PV/风电物理仿真依赖气象输入质量；使用合成气象时只能验证流程，不能解释真实收益。
- profile 缓存用于提高重复运行效率，修改核心参数后应主动刷新或更换缓存路径。
