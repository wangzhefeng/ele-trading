# capacity_planning — 容量规划模块

本模块提供 PV、风电、BESS、Wind+BESS、Wind+PV+BESS 和多节点储能容量评估工具。风光资源仿真和 profile 构造已迁移到 `ele_trading.resource_simulation`，本模块只消费负荷、风电出力和光伏出力曲线。

## 当前文件

| 文件 | 职责 |
|------|------|
| `capacity_optimizer.py` | 风光储联合容量优化，粗-精两阶段网格搜索和运行测算 |
| `bess_capacity_planner.py` | 固定新能源出力下 BESS 最小容量规划 |
| `wind_bess_planner.py` | Wind+BESS 容量规划、平移充电策略和可行性诊断 |
| `wind_pv_bess_planner.py` | Wind+PV+BESS 容量规划、能量门槛检查和运行评估 |
| `wind_pv_bess_irr_planner.py` | IRR 目标型 Wind+PV+BESS 容量规划和 PPA 反推 |
| `feasibility_analyzer.py` | 储能项目可行性分析，覆盖价格、负荷、变压器和匹配度 |
| `multi_node_scanner.py` | 多节点储能容量扫描和退化收益评估 |
| `pv_bess_irr_planner.py` | PV+BESS 年收益和 IRR 扫描 |

## 分层关系

```text
气象 / 历史出力 / 负荷
→ resource_simulation / data_provider
→ capacity_optimizer / bess_capacity_planner / wind_bess_planner / wind_pv_bess_planner
→ feasibility_analyzer / multi_node_scanner / pv_bess_irr_planner
→ app 容量规划入口和结果解释
```

## 容量规划

- `CapacityOptimizer`：在风、光、储容量网格上搜索满足绿电率、自用率等约束的低成本方案。
- `plan_energy_system()`：在给定负荷和风光出力下搜索满足新能源自消纳率、负荷覆盖率的最小 BESS 容量。
- `plan_wind_bess_system()`：Wind+BESS 场景，支持平移充电策略、容量二分搜索和可行性诊断。
- `plan_wind_pv_bess()`：Wind+PV+BESS 场景，支持 PV 搜索、BESS 搜索和能量门槛检查。
- `plan_wind_pv_bess_for_target_irr()`：扫描风、光、储容量组合，反推 PPA 并筛选 IRR 目标方案。

## 扫描与诊断

- `BESSFeasibilityAnalyzer`：从价格价差、负荷形态、变压器约束和充放匹配度评估储能策略可行性。
- `scan_single_node()` / `scan_multiple_nodes()`：对单节点或多节点做储能容量扫描。
- `scan_pv_bess_irr()`：扫描 PV+BESS 年收益、IRR 和增量 IRR。

## 对应入口

```bash
uv run python app/run_wind_pv_bess.py
uv run python app/run_bess_capacity_planning.py
uv run python app/run_wind_bess_capacity_planning.py
uv run python app/run_wind_pv_bess_capacity_planning.py
uv run python app/run_wind_pv_bess_irr_planning.py
```

legacy 数据准备入口：

```bash
uv run python app/run_legacy_data_preparation.py
```

## 使用边界

- 容量规划里的运行测算是研究型近似，不替代生产级经济测算。
- 风光出力仿真依赖 `ele_trading.resource_simulation`，真实项目接入时应先明确单位出力或总出力口径。
