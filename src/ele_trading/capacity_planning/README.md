# Capacity Planning 容量规划模块

`capacity_planning` 负责容量搜索、场景编排、收益测算和 CSV/表格结果组织。容量规划专用的 BESS 调度内核副本已归入本目录；`optimization/` 仍保留交易/调度侧原始内核。`models/` 是容量规划流程内复用的仿真与搜索 helper。

风光资源物理仿真归属本目录下的 `resource_simulation/` 子包，用于容量规划输入曲线构造，也可被 forecasting 的物理预测模式复用。

入口脚本应位于 `app/capacity_planning/run_*.py`。测试或 notebook 不应绕过入口脚本直接调用底层求解器。

长期重构计划维护在 `PLAN.md`，当前权威基线为 V2（V1 保留为历史）。V2 的核心是先建一条 canonical 物理+结算链（时序调度→月度结算→财务）并用 golden-output 回归与 24h 手算 oracle 验证，再迁源码；不先做破坏式改名。

## 算法分层

```text
app/capacity_planning/run_*.py
        |
        v
capacity_planning/*.py
  - 容量候选搜索
  - 风/光/储资源场景编排
  - 收益、IRR、诊断和导出
        |
        +--> capacity_planning/interfaces.py
        |      - 容量规划公共合同
        |
        +--> capacity_planning/models/*
        |      - 规划流程内的贪心仿真、BESS 调度、单源 BESS 搜索、策略回放
        |
        +--> capacity_planning/resource_simulation/*
               - PV/Wind 物理出力仿真与 profile 构造
```

## 当前脚本索引

| 文件 | 场景 | 核心入口 | 主要算法 |
|---|---|---|---|
| `bess_capacity_distributed_planner.py` | 多变压器/多节点分布式 BESS | `run_dist_bess_dispatch()`, `run_capacity_search()`, `optimize_combo()` | 机柜组合枚举 + 按月分段调用 `DistributedBESSDispatcher` |
| `bess_capacity_economic_planner.py` | 单节点 BESS 容量和调度联合 sizing | `solve_capacity_sizing()` | PuLP MILP，容量 `Cap_rated` 与充放电策略联合优化 |
| `bess_capacity_operating_planner.py` | 给定容量候选的运营收益测算 | `plan_energy_system()`, `simulate_bess_operation()` | 容量线性扫描 + CVXPY 调度 + `EssSimulationModel` 回放 |
| `interfaces.py` | 容量规划公共合同 | `DistBESSDispatchInput`, `UserSideBESSParams`, `CvxpBESSDispatchInput`, `DistributedBESSDispatchInput` | 合并容量规划实际使用的容量搜索与调度合同 |
| `models/cvxp_bess_dispatch.py` | 单节点 BESS CVXPY 调度 | `CvxpBESSDispatcher`, `get_cvxp_profile()` | 容量规划本地调度模型 |
| `models/distributed_bess_dispatch.py` | 多节点分布式 BESS 调度 | `DistributedBESSDispatcher` | 容量规划本地调度模型 |
| `pv_bess_planner.py` | 固定 PV 装机，求满足消纳约束的最小 BESS | `plan_pv_bess_system()` | 单源共享内核 + 二分搜索 |
| `wind_bess_planner.py` | 固定 Wind 装机，求满足消纳约束的最小 BESS | `plan_wind_bess_system()` | 单源共享内核 + 二分搜索 |
| `pv_bess_irr_planner.py` | 光储前期 IRR 敏感性 | `scan_pv_bess_irr()` | 月度/时段聚合三段式收益公式 + BESS x 电价网格扫描 |
| `wind_bess_irr_planner.py` | 风储前期 IRR 敏感性 | `scan_wind_bess_irr()` | 与光储 IRR 对称的聚合收益公式 + BESS x 电价网格扫描 |
| `wind_pv_bess_planner.py` | Wind+PV+BESS 成本型容量规划 | `plan_wind_pv_bess()`, `evaluate_wind_pv_bess()` | PV 粗扫/细扫 + BESS 二分 + `dispatch_annual()` |
| `wind_pv_bess_capacity_planner.py` | 给定风/光装机，仅搜索 BESS | `plan_energy_system()` | BESS 线性扫描 + 内联 Numba 贪心调度 |
| `wind_pv_bess_capacity_optimizer.py` | 风/光/储三维最低成本组合 | `CapacityOptimizer.optimize()` | 粗扫 + 细扫两阶段网格搜索 + 内联贪心仿真 |
| `wind_pv_bess_irr_planner.py` | 风/光/储 IRR 目标型规划 | `plan_wind_pv_bess_for_target_irr()` | 三维容量网格 + `dispatch_annual()` + PPA/IRR 约束筛选 |
| `wind_pv_bess_irr_tuning.py` | 风光储 IRR 资源敏感性诊断 | `run_wind_pv_bess_irr_resource_tuning()` | 多资源场景 coarse/fine 调参诊断 |
| `multi_node_scanner.py` | 多电价节点 BESS 容量扫描 | `scan_single_node()`, `scan_multiple_nodes()` | 单容量 MILP 调度 + 退化/寿命经济性评估 |
| `feasibility_analyzer.py` | BESS 规划前置可行性诊断 | `BESSFeasibilityAnalyzer.analyze()` | 电价、负荷、变压器裕度和策略可执行性评分 |
| `resource_simulation/` | 风光资源曲线构造 | `PVProfileConfig`, `WindProfileConfig`, `PVSimulator`, `WindSimulator` | PV/Wind 物理仿真、等效小时数校准和缓存 |

## BESS 容量规划

### 分布式多节点：`bess_capacity_distributed_planner.py`

该脚本用于工业园区多变压器场景，决策变量是各变压器节点配置多少个标准储能机柜。固定拓扑在 `TRANSFORMERS` / `SYSTEMS` 中定义，当前包含 `338`、`342` 和 `park` 三类系统组合。

主要流程：

1. 读取各变压器负荷、园区总负荷和 `ele_price.csv`。
2. 按 `CabinetEqualityMode` 生成机柜数组合：`NONE` 独立枚举、`GLOBAL` 全局相等、`GROUP` 组内相等。
3. 对每个组合调用 `DistributedBESSDispatcher`，按月分段求解分布式 BESS 调度。
4. 用需量电费和调度收益评估组合，并写出 summary/schedule。

`v1` 到 `v5` 预设通过 `DistBESSSchedulerConfig` 控制求解器类型、网侧购电公式、平滑惩罚、爬坡约束和放电窗口策略。`v5` 是 rule-based 预设，其余主要走 LP。

### 单节点联合 sizing：`bess_capacity_economic_planner.py`

`solve_capacity_sizing()` 将储能容量、功率、SOC 和充放电状态放入同一个 MILP：

- 决策变量：`P_ch[t]`、`P_dis[t]`、`E[t]`、`u_ch[t]`、`u_dis[t]`、`Cap_rated`。
- 目标：最大化放电收益减充电成本、年化 CAPEX 和循环 OPEX。
- 约束：容量-C-rate 功率上限、充放互斥、SOC 动态、SOC 上下限、变压器容量、禁止超过负荷放电、周期性 SOC、切换间隔和最小连续时段。
- `min_power_ratio > 0` 时启用 McCormick 包络，近似约束最小充放功率比例。

这是单节点“容量和策略一起优化”的路径，依赖 PuLP/CBC。

### 运营扫描：`bess_capacity_operating_planner.py`

`plan_energy_system()` 用外层容量扫描替代联合 sizing：

1. 在 `[0, batt_hi_max_kwh]` 上生成 `search_points` 个容量候选。
2. 对每个候选调用 `CvxpBESSDispatcher` 求解充放电策略，默认按 profile 切成 day/month 窗口。
3. 将求解得到的净功率策略交给 `EssSimulationModel` 做物理回放。
4. 计算原始成本、优化后成本、收益和需量电费变化，选择收益最高的容量。

该路径需要 `ele_price` 输入。零容量候选会走 zero schedule，不进入 CVXPY 调度器。

## 单源新能源 + BESS

`pv_bess_planner.py` 和 `wind_bess_planner.py` 是同构包装，实际调度和二分搜索由 `models/resource_bess_planner_core.py` 提供。

共享内核支持两种模式：

- `simulate_surplus_shift()`：纯弃电搬运。资源大于负荷时充电，资源小于负荷时放电，不允许电网充电。
- `simulate_shift()`：平移充电。允许在资源仍小于负荷时，根据 lookahead 未来缺口主动留出部分资源充电。

`find_min_capacity_bisect()` 先做上界可行性诊断，再用二分搜索找到满足 `min_self_consumption` 和 `min_load_coverage` 的最小 BESS 容量。效率模型为充放分离 `eta_charge` / `eta_discharge`。

PV 与 Wind 的差异主要在输入列、单位缩放、月度统计和成本字段；不要复制一份新的调度逻辑。

## 聚合 IRR 扫描

`pv_bess_irr_planner.py` 和 `wind_bess_irr_planner.py` 不做逐时步优化，也不生成 SOC 策略。它们用于前期可研或价格敏感性分析，输入可以是月度或时段聚合数据。

光储收益模型：

1. PV 自用：`min(PV, Load) * buy_price`。
2. BESS 平移弃光：`min(BESS, Curtail, load_after_PV) * buy_price`。
3. 余电上网：`min(PV_left, PV * max_export_ratio) * export_price`。

风储收益模型与光储对称，但多数风电场景下余电上网项较少触发。两个脚本都会扫描 `bess_range x buy_price_range`，计算全生命周期现金流 IRR，并输出相邻储能容量的 IRR 增量表。

## Wind+PV+BESS 容量规划

当前有四个风光储脚本，目标不同，不能互换。

### `wind_pv_bess_planner.py`

这是当前成本型 Wind+PV+BESS 主规划器。给定负荷、风电出力和单位 PV 出力，搜索 PV 装机和 BESS 容量：

1. 可选能量门槛检查：判断最大 PV 范围下风+光+其他电源年发电量是否达到 `gate_target_ratio`。
2. PV 粗扫：按 `pv_step_coarse_kwp` 枚举 PV 装机。
3. 对每个 PV 候选调用 `_find_min_bess_kwh()`，用 `dispatch_annual()` 二分搜索最小 BESS。
4. 在粗扫最优 PV 附近按 `pv_step_fine_kwp` 细扫。
5. 在满足自用率和负荷覆盖率的候选中选择总 CAPEX 最低者。

`evaluate_wind_pv_bess()` / `evaluate_fixed_wind_pv_bess_capacity()` 只评估固定容量组合，不执行完整容量搜索。

### `wind_pv_bess_capacity_planner.py`

这是固定风光装机时的 BESS 扫描器。它不搜索 PV 或 Wind，只在 `linspace(0, batt_hi_max, search_points)` 上扫描 BESS 容量，使用内联 Numba 贪心调度，返回第一个满足 `self_use_ratio_min` 和 `load_cover_ratio_min` 的容量。

### `wind_pv_bess_capacity_optimizer.py`

`CapacityOptimizer.optimize()` 同时搜索 Wind、PV 和 ESS 三个维度，以最低投资成本满足 `green_ratio_min` 和 `self_use_ratio_min`：

1. 粗扫：按 `coarse_step_mw` / `coarse_step_mwh` 枚举候选。
2. 快速剪枝：单位风光出力即使全部消纳也不足以满足绿电比例时跳过。
3. 细扫：在粗扫最优解附近按 `fine_step_mw` / `fine_step_mwh` 重新搜索。
4. 可通过 `fixed_wind_mw` 或 `fixed_pv_mw` 固定某一资源轴。

该脚本内联贪心仿真，返回成本单位为万元。

### `wind_pv_bess_irr_planner.py`

`plan_wind_pv_bess_for_target_irr()` 用三维容量网格寻找满足 PPA/IRR 约束的风光储组合：

1. 枚举 `wind_mw x pv_mw x bess_mwh`。
2. 调用 `dispatch_annual()` 计算绿电发电、绿电消纳、负荷、弃电。
3. 先筛选自用率和负荷覆盖率。
4. 根据业主目标综合电价和电网购电价反推绿电结算价，再扣除 `green_price_adder_yuan_per_kwh` 得到 PPA 价格。
5. 用 CAPEX、OPEX 和 PPA 收入计算 IRR。
6. 按 `irr_constraint_mode` 筛选：`range` 要求 IRR 在目标附近，`minimum` 要求不低于目标。

失败时 `diagnostics` 和 `diagnostic_summary` 会保留 PPA/IRR 不满足的候选摘要，用于解释无解原因。

### `wind_pv_bess_irr_tuning.py`

该脚本不是最终规划目标函数，而是资源敏感性和无解诊断工具。它遍历风/光资源调整场景，对每个场景运行 `plan_wind_pv_bess_for_target_irr()`，支持 coarse/fine 两阶段重新收窄容量边界。输出用于判断最优方案是否对资源波动敏感，或定位 IRR/覆盖率/价格约束导致的无解原因。

## 规划 helper

### `models/dispatch_algo.py`

`dispatch_annual()` 是 Wind+PV+BESS 规划流程内使用的年度贪心调度函数。每个时步按以下顺序处理：

1. 新能源直供负荷。
2. 盈余新能源给 BESS 充电。
3. 负荷缺口由 BESS 放电补足。
4. 仍然剩余的新能源计为弃电。

效率使用对称开方模型：`eta_charge = eta_discharge = sqrt(eta_roundtrip)`。`switch_gap_steps` 可限制充放电频繁切换。

### `models/resource_bess_planner_core.py`

单源新能源 + BESS 的共享内核。它只关心 `load_kw`、`resource_kw`、时间步长和 `ResourceBESSConfig`，不关心资源来自 PV 还是 Wind。

### `models/simulation_model.py`

`EssSimulationModel` 是策略回放器：输入外部生成的 `es_strategy`，按变压器容量、充放电功率、SOC、可用深度和效率落地为物理可行曲线，并计算原始/优化收益对比。它不负责生成最优策略。

## 选型指南

| 需求 | 使用脚本 |
|---|---|
| 多变压器园区，各节点配多少标准机柜 | `bess_capacity_distributed_planner.py` |
| 单节点容量和调度联合求最优 | `bess_capacity_economic_planner.py` |
| 给定 BESS 容量范围，看运营收益最优点 | `bess_capacity_operating_planner.py` |
| 固定 PV 装机，求最小 BESS 满足自用/覆盖约束 | `pv_bess_planner.py` |
| 固定 Wind 装机，求最小 BESS 满足自用/覆盖约束 | `wind_bess_planner.py` |
| 只有聚合数据，快速看光储/风储 IRR 敏感性 | `pv_bess_irr_planner.py`, `wind_bess_irr_planner.py` |
| 给定 Wind 和单位 PV 曲线，搜索 PV+BESS 最低 CAPEX | `wind_pv_bess_planner.py` |
| 风/光固定，只搜索最小 BESS | `wind_pv_bess_capacity_planner.py` |
| 风/光/储三维最低投资组合 | `wind_pv_bess_capacity_optimizer.py` |
| 根据目标 IRR 和业主电价反推风光储配比 | `wind_pv_bess_irr_planner.py` |
| 解释 wind_pv_bess_irr 无解或资源敏感性 | `wind_pv_bess_irr_tuning.py` |
| MILP 前先判断 BESS 是否有经济调度空间 | `feasibility_analyzer.py` |

## 口径差异

- `dispatch_annual()` 使用 `sqrt(eta_roundtrip)` 的对称效率模型。
- `resource_bess_planner_core.py`、`bess_capacity_economic_planner.py`、`bess_capacity_operating_planner.py` 使用充放分离效率。
- `pv_bess_irr_planner.py` / `wind_bess_irr_planner.py` 是聚合收益模型，不产生充放电时序，不能与逐时步调度结果直接逐点对账。
- `wind_pv_bess_capacity_optimizer.py` 和 `wind_pv_bess_capacity_planner.py` 各自内联贪心仿真；修改共享口径时需要同步审视这些脚本。
- `capacity_planning/interfaces.py`、`models/cvxp_bess_dispatch.py` 和 `models/distributed_bess_dispatch.py` 是容量规划公共合同与本地调度模型；修改调度口径时需要同步审视 `optimization/` 中的原始交易/调度侧内核，避免两边语义漂移。
- `capacity_planning/models/` 是规划层 helper。新增容量规划专用求解器可放在本目录；跨业务复用的交易/调度内核仍应优先放入 `optimization/`。

## 文档校验

更新本 README 后，建议至少执行：

```bash
python3 -m compileall src/ele_trading/capacity_planning
```

如果改动涉及分布式或 CVXPY 路径，还需要运行对应测试，避免 README 中的入口和实际导出漂移。
