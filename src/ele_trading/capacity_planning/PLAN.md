# Capacity Planning 投资测算模型体系 PLAN

本文档用于长期维护 `capacity_planning` 投资测算模型体系建设方案。版本以二级标题组织；当前重构计划即 `V1`，后续调整应在该版本基础上继续修订，或在形成新的完整基线时新增下一个二级版本。

## V1 - 现有算法体系重构与口径固化

### 目标

V1 不新增业务功能，先把当前已有能力整理成可维护的投资测算流水线。当前模块职责仍是容量扫描、场景编排、收益测算和导出；容量规划当前实际使用的公共合同、资源输入、调度模型和规划 helper 归属 `src/ele_trading/capacity_planning/`，交易/调度侧原始内核仍保留在 `src/ele_trading/optimization/`。

核心目标是让每个候选方案都能解释清楚三个问题：

1. 物理上是否成立：发电、消纳、购网、弃电、充放电、SOC 是否守恒。
2. 结算上是否成立：年度汇总是否能追溯到月度电量、电价和需量口径。
3. 财务上是否成立：投资方 IRR 和业主综合电价/节费口径是否来自同一组结算结果。

### 当前结构收敛状态

本轮已完成的脚本结构调整应作为 V1 的当前基线，而不是后续待办：

- 风光资源仿真源码已经归入 `capacity_planning/resource_simulation/`。从第一性原理看，资源曲线是投资测算的基础输入，不是独立交易优化能力；因此它应跟容量测算输入边界同仓维护，并由 forecasting 的物理预测模式按需复用。
- `capacity_planning` 的 `.py` 源码不再直接引用 `ele_trading.optimization` 或 `..optimization`。从第一性原理看，一个投资测算候选应在同一测算域内完成输入、仿真、结算和财务追溯；跨域引用优化内核会让口径归属和验证责任不清。
- 容量规划实际使用的公共合同已经并入 `interfaces.py`，包括分布式容量搜索配置、单节点 BESS CVXPY 调度合同和分布式 BESS 调度合同。原临时副本 `optimization_interfaces.py` 已删除。
- 单节点 BESS CVXPY 调度模型已经收敛到 `models/cvxp_bess_dispatch.py`，只保留 `CvxpBESSDispatcher` 和 `get_cvxp_profile()` 等容量规划实际调用能力。
- 分布式 BESS 调度模型已经收敛到 `models/distributed_bess_dispatch.py`，只保留 `DistributedBESSDispatcher` 及其求解所需 helper。
- 原本只用于优化模块通用入口的 wrapper 函数不进入 `capacity_planning` 本地模型，避免把“容量测算所需算法”误扩展成“优化模块 API 副本”。
- `models/dispatch_algo.py`、`models/resource_bess_planner_core.py`、`models/simulation_model.py` 仍是当前规划流程内的贪心调度、单源 BESS 搜索和策略回放 helper；BESS CVXPY/分布式调度模型是新增收敛到 `models/` 的同层 helper。

### 体系对标总览

公司投资测算体系可以拆为四层。当前 `capacity_planning` 已经覆盖部分算法能力，但还没有形成统一的输入、仿真、结算和财务合同。

| 体系层次 | 当前覆盖 | 当前缺口 |
|---|---|---|
| 基础输入层 | 已有 `capacity_planning/resource_simulation/` 风光单位出力曲线生成与缓存、负荷时序读取、电价曲线对齐、风资源等效满发小时数调参。 | 缺统一输入 schema、业主电费单解析、生产 `data_provider` 接入、资源反向约束合同、行政分时分类电价与输配电价统一口径。 |
| 运行仿真层 | 已有按输入固定步长的风光储贪心平衡、`models/cvxp_bess_dispatch.py` 单节点 BESS 调度、`bess_capacity_economic_planner.py` PuLP BESS sizing、`models/distributed_bess_dispatch.py` 分布式 BESS 调度、容量搜索和资源敏感性诊断。 | 缺统一仿真结果对象、月度电量结算模型、numba/Python 调度等价保障、15min 需量口径一致性、SOC 时序在所有路径中的一致输出。 |
| 财务测算层 | 已有 `irr_finance.py`、PPA/绿电价格倒推、目标 IRR gap 诊断、等额年现金流 IRR。 | 缺逐年现金流、融资成本、储能更换、退化、税费、残值、投资方 IRR 与业主节费率双视角 KPI。 |
| 输入边界条件 | 已有容量上限、自用率、负荷覆盖率、BESS 搜索上下限和 `resource_tuning` 资源诊断。 | 缺政策硬边界、风光储投建比例、可开发容量、特殊负荷可中断/保电要求、输配电价、EPC/融资/O&M/更换等前期输入合同。 |

### 当前能力对标

基础输入层：

| 内容 | 已覆盖 | 缺口 |
|---|---|---|
| 风光资源建模 | `capacity_planning/resource_simulation/` 可按坐标、风电等效满发小时数、PV 云量因子和系统损耗生成单位曲线，并供容量规划 runner 和 forecasting 物理预测模式复用。 | 资源反向约束目前散落在 `resource_tuning` 配置中，没有形成独立输入合同；风资源可开发容量、坐标、测风塔、业主确认值之间缺少校验关系。 |
| 负荷模型 | 当前 runner 可读取 `demand_load.csv` 并对齐时序。 | 缺业主历史电费单解析、异常负荷清洗、可中断负荷、保电负荷和生产 `data_provider` 接入。 |
| 电价模型 | BESS 运营路径可对齐电价曲线，IRR 路径支持业主综合电价和电网购电价倒推 PPA。 | 缺行政分时分类电价、输配电价、需量电价、政府基金/附加、尖峰浮动和业主实际合同价的统一结算对象。 |

运行仿真层：

| 内容 | 已覆盖 | 缺口 |
|---|---|---|
| 贪心平衡 | `models/dispatch_algo.py` 的 `dispatch_annual()` 可对 wind/PV/other/BESS 做逐点贪心平衡，输出新能源发电、消纳、负荷、直供、BESS 放电和弃电汇总。 | 输出只保留汇总，缺每月、每时步和 SOC 明细；Python fallback 当前不模拟 BESS 充放电。 |
| BESS 调度 | 单节点运营扫描复用 `models/cvxp_bess_dispatch.py` 中的 `CvxpBESSDispatcher`，联合 sizing 复用 `bess_capacity_economic_planner.py` 的 PuLP MILP，分布式路径复用 `models/distributed_bess_dispatch.py` 中的 `DistributedBESSDispatcher`。 | 多条路径的效率、SOC、需量、电价和月度切分口径不统一；本地模型与 `optimization/` 原始内核存在语义漂移风险，需要一致性测试。 |
| 资源敏感性 | `wind_pv_bess_irr_tuning.py` 支持 coarse/fine 资源场景诊断。 | 该能力应保持为诊断工具，不能被误认为最终设计目标函数。 |

财务测算层：

| 内容 | 已覆盖 | 缺口 |
|---|---|---|
| 投资方 IRR | `evaluate_levelized_irr()` 支持总 CAPEX、年收入、年 OPEX 和寿命期 IRR。 | 等额年现金流过于简化，不能表达融资、还本付息、税费、储能更换、退化和残值。 |
| 业主电价倒推 | `backsolve_green_ppa_price()` 可由目标业主综合电价反推绿电结算价和 PPA 价格。 | 当前倒推是年度口径，不能证明每月电量结算和电价提升优势。 |
| 无解诊断 | `compute_target_irr_gap_metrics()` 和 planner diagnostics 能解释 IRR/PPA 缺口。 | 诊断仍依赖年度候选汇总，缺按结算组件拆分的缺口原因。 |

输入边界条件：

| 内容 | 已覆盖 | 缺口 |
|---|---|---|
| 利益方约束 | `irr_constraint_mode=minimum` 可表达投资方 IRR 硬约束；目标业主电价可反推 PPA。 | 缺“PPA 价格锁定后反向求 IRR”的正式场景合同，缺投资方和用电方目标同时存在时的优先级。 |
| 投建规模 | 支持风、光、储容量上下限和步长。 | 缺风资源可开发容量、硬性配储比例、风光储比例上限、EPC 造价版本和融资成本输入。 |
| 负荷特殊约束 | BESS 运营路径有变压器容量约束。 | 缺可中断负荷、全年用电特质、保电要求、关键时段不得放电/必须保留 SOC 等约束。 |

### 第一性原理重构主线

1. 先固化物理能量守恒和结算口径，再计算 IRR。每个候选都必须能输出发电、直供、充电、放电、SOC、购网、弃电、月度汇总和年度汇总，且年度值由月度值汇总得到。
2. 把容量测算流水线拆为稳定阶段：候选生成、运行仿真、月度结算、财务评价、诊断导出。Planner 只编排阶段，不在同一函数里混合数据读取、调度、PPA 倒推和 CSV 写出。
3. 接口归属跟随测算责任。属于容量测算配置、输入、仿真和结果解释的公共合同放在 `interfaces.py`；属于容量测算专用执行模型的算法放在 `models/`；属于交易/调度侧通用能力的原始内核仍保留在 `optimization/`。
4. 保留现有外部入口和结果字段，先抽公共合同和一致性测试，不做大规模模型替换。未来新增类型时只作为内部合同逐步接入，避免一次性重写所有 planner。

### 对抗式审查

- 如果 `use_numba=False` 会弱化 BESS 仿真，Python fallback 不能作为可接受的经济测算路径。V1 必须要求 numba/Python 路径同输入同结果，或在无等价 fallback 时显式失败。
- 如果只用年汇总 PPA 收入，不能证明“每月电量结算”和“电价提升优势”。V1 必须规划月度结算结果，并让年度 IRR 输入来自结算结果。
- 如果 app runner 硬编码 `data/profit_calc/...` 样例数据路径，不能进入生产策略评估。正式测算必须从 `data_provider` 或显式传入的输入包读取数据。
- 如果 IRR 只用等额年现金流，不能代表融资、更换和退化后的投资方收益。该模型只能作为 V1 基准口径，不能标记为完整财务模型。
- 如果资源调参被当作最终设计目标，可能把“诊断无解原因”误写成“优化资源条件”。`resource_tuning` 在 V1 中继续定义为诊断工具。
- 如果把 `models/cvxp_bess_dispatch.py` 和 `models/distributed_bess_dispatch.py` 当作 `optimization/` 的完整复制品，会重新引入 API 膨胀。V1 只允许保留容量规划实际调用的 dispatcher、profile 和求解 helper。
- 如果资源仿真迁入 `capacity_planning/resource_simulation/` 后没有输入合同，仍然无法证明风光曲线来源可审计。迁目录只解决代码归属，不等于完成资源输入标准化。

### 实施顺序

1. 已完成：将风光资源仿真迁入 `capacity_planning/resource_simulation/`，并更新容量规划 runner、forecasting 物理预测模式和相关测试的导入。
2. 已完成：消除 `capacity_planning` 源码对 `ele_trading.optimization` / `..optimization` 的直接引用，将容量规划实际需要的公共合同并入 `interfaces.py`。
3. 已完成：将单节点和分布式 BESS 调度模型收敛到 `models/cvxp_bess_dispatch.py` 与 `models/distributed_bess_dispatch.py`，删除不属于容量规划调用面的临时 wrapper。
4. 下一步：盘点并冻结现有输出字段，保持 `WindPVBESSIRRResult`、CSV 英文字段和中文表头稳定。
5. 下一步：为候选仿真定义内部目标合同名称：`DispatchSimulationResult`，字段至少覆盖 generation、direct_used、charge、discharge、soc、grid_buy、curtail、monthly_summary。
6. 下一步：为月度结算定义内部目标合同名称：`MonthlySettlementResult`，字段至少覆盖 green_used、grid_buy、demand_charge、energy_charge、ppa_revenue、owner_avg_price。
7. 下一步：将 `dispatch_annual()` 的 Python 路径补齐为等价 BESS 仿真，或在无 numba 且需要 BESS 结果时明确失败。
8. 下一步：将 IRR 计算输入从年度散字段收敛到结算结果；保留 `evaluate_levelized_irr()` 作为 V1 基准财务评价。
9. 下一步：将 runner 的硬编码样例路径标注为 demo 路径；正式路径后续通过 `PlanningInputBundle` 和 `data_provider` 接入。

### 后续扩展方向

这些名称是 V1 之后的目标合同，不代表当前已实现：

- `PlanningInputBundle`：承载项目、站址、负荷、风光资源、电价、政策、成本和融资输入。
- `SettlementInput`：承载运行仿真结果、电价表、PPA 价格、输配电价、需量电价和结算周期。
- `MonthlySettlementResult`：输出月度电量、电费、绿电收入、需量费用和业主综合电价。
- `ProjectCashflowResult`：承载逐年收入、O&M、融资成本、税费、储能更换、退化影响、残值和净现金流。
- `InvestmentPlanningCase`：承载一个可复现的投资测算案例，包括输入包、候选空间、约束、仿真结果、结算结果、财务结果和诊断结果。

后续扩展应在 V1 基础上逐步纳入：

- 输入层与结算层标准化：生产输入通过 `data_provider` 接入；`data/` 中样例数据只用于 demo、接口验证和回归测试；月度结算是财务评价的输入来源。
- 财务测算层增强：完整投资测算使用逐年现金流，纳入融资、O&M、储能更换、退化、税费、残值、项目 IRR、权益 IRR、投资回收期、业主节费额和节费率。
- 约束、场景与组合投资决策：纳入风资源可开发容量、风光储投建比例、硬性配储要求、自发自用比例、输配电价、保电要求和可中断负荷。
- 多场景鲁棒评估：资源场景、负荷场景、电价场景和成本场景应分别记录，不互相覆盖；场景采样默认保留 LHS，并继续兼容 `method="mc"`；场景缩减必须使用 Kantorovich/Wasserstein L1 后向缩减。

### 验证要求

- `dispatch_annual` numba 与 Python 路径同输入同结果。
- `capacity_planning` 的 `.py` 源码不直接引用 `ele_trading.optimization` 或 `..optimization`。
- `capacity_planning` 的 `.py` 源码不再引用已删除的 `optimization_interfaces.py`、`user_side_bess_dispatch_cvxpy.py` 和 `user_side_bess_distributed_dispatch_class.py`。
- `models/cvxp_bess_dispatch.py` 和 `models/distributed_bess_dispatch.py` 可被直接导入。
- IRR planner 输出候选满足能量守恒：`generation = used + curtail`，`load = green_used + grid_buy`。
- PPA 反推后业主综合电价回到目标值。
- 月度结算汇总之和等于年度汇总。
- 正式测算 runner 不通过硬编码样例数据路径读取生产输入。

### 维护规则

- 本文档记录建设路线，不代表未实现功能已经存在。
- 后续对当前方案的小修订应直接修改 V1 内容；只有形成新的完整基线时，才新增下一个二级版本标题。
- 未来真正新增 `PlanningInputBundle`、`DispatchSimulationResult`、`MonthlySettlementResult`、`ProjectCashflowResult` 或 `InvestmentPlanningCase` 时，必须同步更新 `configs/README.md`、`capacity_planning/README.md` 和对应测试。
- 修改测算口径时，先更新公共合同和测试，再迁移 planner；不要在单个 runner 中临时硬编码市场参数。
- 修改 `capacity_planning` 本地调度模型时，必须同步审查 `optimization/` 原始内核的语义差异；如果两者都需要同一行为，应优先补一致性测试，再决定是否同步实现。
- 内部机器可读字段保持英文；如需中文展示，在导出边界增加中文标签，不替换稳定字段名。
- 每次更新本文档后至少运行：

```bash
python3 -m compileall src/ele_trading/capacity_planning
```

并检查当前版本标题和关键审查词是否仍然存在：

```bash
rg -n "^## V[0-9]+|对抗式审查|第一性原理|基础输入层|运行仿真层|财务测算层|输入边界条件" src/ele_trading/capacity_planning/PLAN.md
```

检查源码引用时应排除本文档本身，因为本文档会保留已删除文件名作为历史审查记录：

```bash
rg -n "optimization_interfaces|user_side_bess_dispatch_cvxpy|user_side_bess_distributed_dispatch_class|ele_trading\.optimization|\.\.optimization" src/ele_trading/capacity_planning --glob '!PLAN.md'
```
