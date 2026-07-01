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

### 模块命名与分布诊断

从第一性原理看，投资测算模块名应回答“这个模块处在输入、仿真、结算、财务、边界诊断哪一层”，而不是只暴露历史脚本来源、场景组合或求解器名称。当前代码功能已经覆盖投资测算雏形，但模块组织仍明显受历史实现影响，V1 不应先做大规模搬家，应先固化目标边界、内部合同和兼容测试。

| 当前模块/文件 | 真实职责 | 评价 | V1 重构决策 |
|---|---|---|---|
| `resource_simulation/` | 风光资源曲线生成、缓存和基础资源参数建模。 | 位置合理，属于基础输入层；但资源来源、坐标、风电等效满发小时数反向约束和可开发容量尚未形成统一输入合同。 | 保留当前位置；后续补 `PlanningInputBundle` 中的资源输入字段和资源反向约束校验。 |
| `interfaces.py` | 分布式容量搜索配置、拓扑配置、单节点 BESS 调度合同、分布式 BESS 调度合同。 | 文件名适合作公共合同入口，但当前混合了配置、拓扑、调度输入和调度输出，职责偏宽。 | 短期保留以避免破坏导入；V1 先按输入、仿真、结算、财务、案例分区规划合同，新增类型前列清旧字段映射。 |
| `models/dispatch_algo.py` | 风光储逐时步贪心平衡和年度汇总。 | 属于运行仿真层；当前输出偏年度汇总，不能独立支撑月度结算和 SOC 审计。 | 规划统一目标结果 `DispatchSimulationResult`，保留现有函数入口，先补结果适配层和一致性测试。 |
| `models/cvxp_bess_dispatch.py` | 单节点 BESS CVXPY 调度模型。 | 属于运行仿真层，本地化后符合容量测算调用面；但名称暴露求解器，不表达结算职责。 | 保留为调度模型；不承担结算和财务；未来若拆包，归入 `dispatch/` 或继续使用 `models/dispatch_*` 命名。 |
| `models/distributed_bess_dispatch.py` | 分布式 BESS 调度模型和求解 helper。 | 属于运行仿真层；应只表达调度结果，不混入投资决策。 | 保留 dispatcher 和 helper；通过统一仿真结果或适配层接入 planner。 |
| `models/resource_bess_planner_core.py` | 单源新能源+BESS 候选搜索和仿真 helper。 | 兼具候选生成、仿真和指标汇总，职责偏宽。 | 暂不搬移；V1 编码时先把候选生成、运行仿真、结算评价的边界在调用层拆开。 |
| `models/simulation_model.py` | BESS 策略回放、收益和电费计算 helper。 | 名称过泛，实际接近仿真回放和结算前置计算。 | 暂保留；后续月度结算独立后，将电费/需量口径迁入 `settlement.py`。 |
| `*_planner.py` | 场景包装、容量扫描、候选搜索、结果导出和诊断编排。 | 作为外部入口合理，但内部常混合数据读取、候选生成、仿真、价格倒推、CSV 写出。 | 保留 `plan_*`、`scan_*`、`run_*` 入口和结果字段；内部逐步迁移为输入归一化、候选生成、运行仿真、月度结算、财务评价、诊断导出。 |
| `irr_finance.py` | IRR、绿电/PPA 价格倒推和目标 IRR gap 财务 helper。 | 位置合理，属于财务测算层；当前仍是年化简化模型。 | 标记为 V1 基准财务工具；后续扩展逐年现金流、融资、O&M、更换、退化、税费和残值。 |
| `feasibility_analyzer.py` | 容量边界、可行性和约束诊断。 | 属于输入边界条件和诊断层，不应伪装成投资决策主流程。 | 保持诊断定位；输出用于解释候选不可行原因，不直接覆盖 planner 目标函数。 |
| `multi_node_scanner.py` | 多节点场景扫描和评估。 | 属于场景评估和边界诊断，容易被误读为正式组合投资优化。 | 保持 scanner 定位；进入正式主流程前必须接入统一输入、仿真、结算和财务合同。 |
| `wind_pv_bess_capacity_planner.py`、`wind_pv_bess_capacity_optimizer.py`、`wind_pv_bess_irr_planner.py`、`wind_pv_bess_planner.py` | 风光储不同目标下的容量搜索、优化、IRR 和可行性包装。 | 命名基本可读，但同一技术组合下目标差异依靠后缀表达，仍偏场景脚本化。 | 保留兼容入口；文档和后续代码中明确每个 planner 的目标函数、输入边界、输出字段和是否包含财务口径。 |

### 目标模块分布

V1 的目标不是一次性把文件移成最终形态，而是先让代码可以按下列层次迁移。只有当内部合同和兼容测试齐备后，才执行真实拆包或改名。

| 目标层 | 建议位置 | 职责边界 |
|---|---|---|
| 基础输入层 | `inputs/` 或 `input_models.py`，短期可继续由 `interfaces.py` 承载目标合同 | 资源、负荷、电价、政策、成本、融资和项目边界输入；生产数据通过 `data_provider` 接入，`data/` 仅用于 demo、接口验证和回归测试。 |
| 运行仿真层 | `dispatch/` 或继续使用 `models/dispatch_*` | 逐时步能量平衡、BESS 调度、SOC、购网、弃电和仿真元数据；不负责 PPA、税费或 IRR。 |
| 结算层 | `settlement.py` | 月度电量、电价、需量、电价提升优势、PPA 收入和业主综合电价；年度值必须由月度汇总得到。 |
| 财务测算层 | `finance.py` 或继续扩展 `irr_finance.py` | 逐年现金流、CAPEX、OPEX、融资、税费、储能更换、退化、残值、投资方 IRR 和业主节费率。 |
| 案例聚合层 | `cases.py` | `InvestmentPlanningCase` 聚合输入、候选、仿真、结算、财务和诊断，使任意 IRR 结果可追溯到同一组时序与结算。 |
| 编排入口层 | `planners/` 或保留现有 `*_planner.py` | 只做流程编排和兼容入口；外部 `plan_*`、`scan_*`、`run_*` 名称、`capacity_planning.__all__` 和结果字段先保持稳定。 |

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

### 第一性原理判断准则

- 模块名应说明投资测算流水线层次。只描述求解器、历史脚本或场景组合的名称可以保留为兼容入口，但新增内部模块应优先表达输入、仿真、结算、财务或诊断职责。
- 输入、仿真、结算、财务和诊断之间必须保持单向数据流。上游不读取下游字段，下游不重新解释上游原始文件。
- 任何 IRR、PPA 或节费率结果都必须能追溯到同一组 `DispatchSimulationResult` 和 `MonthlySettlementResult`，不能由另一套年度散字段单独推导。
- 任何生产策略评估不得依赖 `data/` 样例路径。`data/` 只能用于 demo、接口验证和回归测试，正式输入必须来自 `data_provider` 或显式传入的输入包。
- 任何“资源调参”都必须标注为诊断，不能伪装成真实资源可开发能力或最终投资约束。
- 月度结算是财务测算的来源，不是导出报表的附属物。年度收入、业主综合电价和 IRR 输入必须可由月度明细汇总复算。

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
4. 下一步：先在本文档固化目标分层和命名规则；任何新增模块先回答其所属层次、输入对象、输出对象和旧字段映射。
5. 下一步：盘点并冻结现有输出字段，保持 `capacity_planning.__all__`、`plan_*`/`scan_*`/`run_*` 入口、`WindPVBESSIRRResult`、CSV 英文字段和中文表头稳定。
6. 下一步：新增内部合同类型前，先列出字段和旧字段映射，不删除旧 dataclass；新增类型先作为 adapter 目标，不直接替换 public API。
7. 下一步：为候选仿真定义内部目标合同名称：`DispatchSimulationResult`，字段至少覆盖 `timestamps`、`generation_kwh`、`direct_used_kwh`、`charge_kwh`、`discharge_kwh`、`soc_kwh`、`grid_buy_kwh`、`curtail_kwh`、`load_kwh`、`monthly_summary`、`annual_summary`、`metadata`。
8. 下一步：为月度结算定义内部目标合同名称：`MonthlySettlementResult`，字段至少覆盖 `month`、`green_used_kwh`、`grid_buy_kwh`、`curtail_kwh`、`energy_charge_yuan`、`demand_charge_yuan`、`ppa_revenue_yuan`、`owner_avg_price_yuan_per_kwh`、`baseline_price_yuan_per_kwh`、`savings_yuan`、`savings_ratio`。
9. 下一步：将 planner 内部流程统一为输入归一化、候选生成、运行仿真、月度结算、财务评价、诊断导出；先在 `wind_pv_bess_irr_planner.py` 和 BESS planner 中通过私有 adapter 落地，不改外部入口。
10. 下一步：将 `dispatch_annual()` 的 Python 路径补齐为等价 BESS 仿真，或在无 numba 且需要 BESS 结果时明确失败。
11. 下一步：将 IRR 计算输入从年度散字段收敛到结算结果；保留 `evaluate_levelized_irr()` 作为 V1 基准财务评价。
12. 下一步：将 runner 的硬编码样例路径标注为 demo 路径；正式路径后续通过 `PlanningInputBundle` 和 `data_provider` 接入。
13. 下一步：后续如果真的移动文件，必须同步 `__init__.py`、`capacity_planning/README.md`、`configs/README.md`、app 入口和对应测试，且先提供旧入口兼容测试。

### 可直接编码的重构步骤

1. 文档和合同准备：在 V1 中保持本节为编码基线；为每个拟新增合同写字段清单、单位、来源、旧字段映射和是否对外公开。
2. `interfaces.py` 内部分区：先用注释或局部排序把现有合同分为容量搜索配置、拓扑配置、单节点 BESS 调度合同、分布式 BESS 调度合同；不删除现有 dataclass，不改变导入路径。
3. `DispatchSimulationResult` adapter：新增私有转换函数，把 `dispatch_annual()` 和 BESS dispatcher 输出转换为统一字段；初期 adapter 可以只服务新增测试和 planner 内部，不替换既有结果类。
4. `MonthlySettlementResult` adapter：从仿真结果、电价曲线、PPA 价格和需量配置生成月度结算；年度汇总只能由 12 个月或实际结算周期汇总得到。
5. Planner 流水线拆分：把现有长函数内部拆成私有阶段函数，命名固定为 `_normalize_inputs()`、`_generate_candidates()`、`_simulate_candidate()`、`_settle_candidate()`、`_evaluate_finance()`、`_export_diagnostics()`；外部入口和返回字段不变。
6. 财务输入收敛：`irr_finance.py` 暂保留年化简化模型，但调用方应从结算结果提取年收入、OPEX 和节费指标，不再直接拼年度散字段。
7. 生产输入边界：app runner 中样例路径保留为 demo；正式测算入口必须接受显式输入包或 `data_provider`，不能把 `data/profit_calc/...` 作为默认生产路径。
8. 文件移动延后：只有在 adapter、兼容导入和回归测试都通过后，才考虑建立 `inputs/`、`dispatch/`、`planners/` 等目录；移动时用兼容 shim 或同步更新全部调用方，避免半迁移。

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

本次文档更新后必须检查 V1 是否包含模块重构计划的关键小节：

```bash
rg -n "模块命名与分布诊断|目标模块分布|可直接编码的重构步骤|第一性原理|基础输入层|运行仿真层|财务测算层|输入边界条件" src/ele_trading/capacity_planning/PLAN.md
```

当前源码边界必须保持：

- `dispatch_annual` numba 与 Python 路径同输入同结果。
- `capacity_planning` 的 `.py` 源码不直接引用 `ele_trading.optimization` 或 `..optimization`。
- `capacity_planning` 的 `.py` 源码不再引用已删除的 `optimization_interfaces.py`、`user_side_bess_dispatch_cvxpy.py` 和 `user_side_bess_distributed_dispatch_class.py`。
- `models/cvxp_bess_dispatch.py` 和 `models/distributed_bess_dispatch.py` 可被直接导入。

后续真正编码 V1 重构时必须新增或补强以下测试：

- 旧入口兼容：`capacity_planning.__all__`、`plan_*`、`scan_*`、`run_*`、app runner 和已有结果字段保持可用。
- 结果字段兼容：新增 adapter 不删除旧 dataclass 字段，不改 CSV 稳定英文字段；中文表头只在导出边界映射。
- IRR planner 输出候选满足能量守恒：`generation = used + curtail`，`load = green_used + grid_buy`。
- PPA 反推后业主综合电价回到目标值。
- 月度结算汇总之和等于年度汇总。
- 正式测算 runner 不通过硬编码样例数据路径读取生产输入。
- `ProjectCashflowResult` 或后续逐年现金流实现必须能从同一组月度结算结果复算年度收入。

### 维护规则

- 本文档记录建设路线，不代表未实现功能已经存在。
- 后续对当前方案的小修订应直接修改 V1 内容；只有形成新的完整基线时，才新增下一个二级版本标题。
- 未来真正新增 `PlanningInputBundle`、`DispatchSimulationResult`、`MonthlySettlementResult`、`ProjectCashflowResult` 或 `InvestmentPlanningCase` 时，必须同步更新 `capacity_planning/README.md`、`configs/README.md`、`__init__.py` 和对应测试。
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

## V2 - 第一性原理修正：canonical 物理+结算链与可证伪验证

> **构建信息（provenance）**
> - 构建代理（Agent）：Claude Code（CLI，主会话直接构建，未派生子代理/工作流）
> - 构建模型（Model）：glm-5.2（1M context）
> - 构建日期：2026-07-02
> - 构建依据：V1 第一性原理审查（P0–P4），并经实际通读 `capacity_planning` 全部 `.py` 核实（行号见各条）。
> - 与 V1 的关系：V2 是 V1 的完整修订基线。V1 保留为历史与变更溯源；自 V2 起所有新增规划以 V2 为准。V1 中仍成立的内容（六层分层、跨域引用已清零、`__init__`/CSV/中文表头稳定性规则等）V2 直接继承；V1 的逻辑矛盾、根因误判、过度设计与验证缺口，V2 逐条修正。本新增符合 V1 维护规则（204 行）关于“形成新的完整基线时新增下一个二级版本”的约定。

### V1 → V2 变更摘要

| 维度 | V1 立场 | V2 修正 | 触发依据 |
|---|---|---|---|
| 范围定性 | “不新增业务功能” | 承认 V2 必须新建 canonical 时序调度 + 月度结算，这是新增能力，不再伪装成纯重构 | V1 第 8 行与 117/197 行自相矛盾 |
| 月度结算 | 列为“后续扩展” | 提升为 V2 核心交付，否则可追溯验证无法成立 | 无法验证系统算不出的性质 |
| 调度多路径 | “加一致性测试”对齐 | 指定一条 canonical 物理核，其余分类为弃用/异范围 | N 条冗余需 O(N²) 测试；异构模型本就不该“一致” |
| 需量电费 | 合同当作可由月度推导 | 强制仿真→结算 seam 携带月内净负荷峰值/15min 分辨率 | 需量不可由月度电量反推 |
| 验证对象 | rg 关键词 + compileall（验证文档） | golden-output 回归 + 24h 手算 oracle（验证行为） | 验证应是重构结果而非文档 |
| 时间轴 | “对齐时序”为既有能力，无不变量 | 新增顶层 TimeIndex 合同与每次 run 对齐测试 | 时间错位是头号静默 bug |
| data 边界 | 推 PlanningInputBundle 作强制边界 | 仅修 1 个硬编码 runner；PlanningInputBundle 延后 | 实测仅 1/6 runner 硬编码路径 |
| 场景缩减 | 钉死 LHS+Kantorovich 算法 | 算法随 case 结构一并确定（前置结构或后置算法） | 算法定、结构未定为最差组合 |
| 业主 KPI | 节费率散落在目标/后续扩展 | savings/savings_ratio 提为结算结果一等字段 | 两方 KPI 对立，须同点共存 |
| 效率口径 | 笼统“不统一” | 列为显式 bug：0.92/0.95/sqrt 拆分并存须收敛 | 实测物理口径冲突，直接影响 IRR |

### 目标

V2 保留 V1“解释清楚三个问题”的目标，但把**可证伪性**提到第一位。每个候选方案必须通过两道外部验证：

1. **物理守恒**：发电、消纳、购网、弃电、充放电、SOC 逐时步守恒，且能对 24h 手算 oracle 复算。
2. **结算可汇总**：年度收入、业主综合电价、IRR 输入必须由 12 个月（或实际结算周期）的月度结算汇总复算，差额在容差内。

财务成立性（投资方 IRR 与业主节费率）作为 V2 的**双视角一等 KPI**，二者必须来自同一组结算结果，并在结果对象中同时携带（不再只携带被求解的那一方）。

V2 不再宣称“不新增业务功能”。V2 的核心交付是**一条 canonical 物理+结算链**（时序调度 → 月度结算 → 财务评价），其余能力围绕它归类、收敛或弃用。

### 当前结构收敛状态（V1 已完成项，V2 继承）

V1 已落地、V2 直接继承的事实基线（不重做）：

- 风光资源仿真归属 `capacity_planning/resource_simulation/`，定位为基础输入层。
- `capacity_planning` 的 `.py` 源码对 `ele_trading.optimization` / `..optimization` 的引用已**清零**（实测 0 处 import），跨域边界已达成，V2 仅守。
- 公共合同并入 `interfaces.py`；`optimization_interfaces.py` 等临时副本已删。
- 单节点/分布式 BESS 调度模型收敛到 `models/cvxp_bess_dispatch.py` 与 `models/distributed_bess_dispatch.py`。

V2 在此基础上**新增**两条必须先固化的事实：

- **储能效率口径冲突（显式 bug）**：`dispatch_algo.py` 用 `eta_rt**0.5` 对称拆分（`:70-71`）；`cvxp_bess_dispatch.py`/`distributed_bess_dispatch.py`/`bess_capacity_economic_planner.py` 用非对称 0.95；`resource_bess_planner_core.py:41-42,105` 用非对称 **0.92**。同一项目里储能效率差 3 个百分点，直接改变 IRR。V2 必须先收敛到单一效率合同再谈一致性。
- **SOC 单位冲突**：`dispatch_algo` 与 `resource_core` 用分数，CVXPY/分布式/PuLP 用 kWh。canonical 核须选定一种并在文档/代码标注换算。

### canonical 物理核与路径分类

V2 指定**唯一一条 canonical 小时级物理核**作为结算与财务的唯一上游：

- 候选 canonical：在 `models/` 内确立一个 canonical dispatch，**优先以现有能产出逐时步 + SOC + 每月净负荷峰值的路径为底座改造**，而非新写。
- canonical 核强制输出：逐时步 `generation/direct_used/charge/discharge/soc/grid_buy/curtail/net_load`，外加**每月 net_load 峰值序列**（供需量电费结算），以及 `metadata`（效率、分辨率、时间轴指纹）。
- canonical 核的 Python/numba 双路径**必须等价**；若不可等价，则在无 numba 且需 BESS 结果时**显式失败**（V1 已立此规则，V2 落地为测试）。

其余 4 条现有调度路径按下表分类，**不再要求与 canonical “一致”**，只要求关系被写清：

| 路径 | V2 定位 | 处置 |
|---|---|---|
| `dispatch_annual`（贪心，仅年度标量） | canonical 的演进底座 | 补逐时步/SOC/月峰值输出后升为 canonical；在此之前不得作为结算上游 |
| `cvxp_bess_dispatch`（单节点 CVXPY） | 异范围模型（运营调度） | 保留为运营场景 dispatcher；不作投资测算结算上游；写明与 canonical 的输入/输出差异 |
| `distributed_bess_dispatch`（分布式 CVXPY） | 异范围模型（多变压器） | 同上；其 sliding_window 需量逻辑作为 canonical 月峰值实现的设计参考 |
| `resource_bess_planner_core`（0.92） | 候选生成/可行性 helper | 收敛效率到 canonical 合同；定位为诊断/可行性，不作结算上游 |
| `bess_capacity_economic_planner`（PuLP sizing） | sizing 优化器 | 保留；与 canonical 的关系（sizing 结果再经 canonical 复算结算）须写清 |

### 仿真→结算 seam 合同（V2 最关键的新增）

第一性原理：**需量电费不可由月度电量聚合反推**。因此 V2 规定 `DispatchSimulationResult`（adapter 目标）字段必须包含：

- 逐时步序列：`timestamps`、`generation_kwh`、`direct_used_kwh`、`charge_kwh`、`discharge_kwh`、`soc_kwh`、`grid_buy_kwh`、`curtail_kwh`、`load_kwh`、`net_load_kwh`；
- **月度结算前置量**：`monthly_net_load_peak_kw`（每月净负荷峰值，按结算周期与滑窗口径），供 `MonthlySettlementResult.demand_charge_yuan` 计算；
- `monthly_summary`、`annual_summary`（由月度汇总得到，不可独立推导）、`metadata`（含效率、分辨率、时间轴指纹）。

`MonthlySettlementResult` 字段：`month`、`green_used_kwh`、`grid_buy_kwh`、`curtail_kwh`、`energy_charge_yuan`、`demand_charge_yuan`（由 `monthly_net_load_peak_kw` 计算）、`ppa_revenue_yuan`、`owner_avg_price_yuan_per_kwh`、`baseline_price_yuan_per_kwh`、`savings_yuan`、`savings_ratio`。

**不变量**：`annual_summary` ≡ Σ `monthly_summary`，且二者 ≡ Σ 逐时步（容差内）。该不变量由测试强制。

### 时间轴 canonization（V2 新增顶层合同）

- 唯一 `TimeIndex`：固定分辨率（15min 场景 `dt=0.25`，与 `AGENTS.md` 一致）、时区、日历完整性（无空洞）、资源/负荷/价格/SOC 五序列同轴。
- 每次 run 须通过时间轴对齐测试：分辨率一致、长度一致、时间戳一致、跨年/闰年处理一致。
- 时间轴指纹写入 `metadata`，便于回归比对。

### 目标分层与模块分布（继承 V1，收敛命名）

V2 保留 V1 六层分层（基础输入 / 运行仿真 / 结算 / 财务 / 案例聚合 / 编排入口），但把“是否在 V2 落地”明确化，消除 V1“大量目录搬家推到以后”的文档冗余：

| 目标层 | V2 落地动作 | 命名 |
|---|---|---|
| 运行仿真 | 落地 canonical 核（改造现有路径，非新建目录） | `models/canonical_dispatch.py`（或选定底座） |
| 结算 | 新建（V2 必交付） | `settlement.py` |
| 财务 | 扩展 `irr_finance.py`，输入取自结算结果 | 保留 `irr_finance.py` |
| 案例聚合 | V2 仅定合同 `InvestmentPlanningCase`，不强制建目录 | 合同先行 |
| 编排入口 | 保留现有 `*_planner.py` 入口与字段 | 不动外部名 |

文件/目录搬家（`inputs/`、`dispatch/`、`planners/`）V2 **不做**，待 adapter、兼容导入、回归测试齐备后再议（继承 V1 第 157 行规则）。

### 第一性原理判断准则（V2 修订）

在 V1 准则基础上增订/改订：

- **可证伪优先**：任何结论须能被 golden 或 oracle 证伪；内部自洽测试不够（可能一起错）。
- **canonical 唯一**：结算与财务的上游物理模型有且仅有一条；异范围模型写明关系，不强求一致。
- **seam 携带结算所需分辨率**：仿真→结算 seam 须携带需量计算所需的月内峰值/15min 序列，不可只传月度电量。
- **每个兼容 shim 有 sunset**：旧 dataclass、旧年度散字段、`method='mc'` 等兼容项须注明计划移除点，不接受永久并存。
- **诊断结构隔离**：`resource_tuning` 等诊断输出类型须与 planner 目标函数输入类型不兼容（结构上喂不进去），而非仅靠命名。
- **两方 KPI 同点共存**：结果对象同时携带投资方 IRR 与业主节费率，二者来自同一结算。
- **adapter 必须带消费者**：无 V2 内消费者的内部合同不建。
- 继承 V1：单向数据流、生产输入不经 `data/`、资源调参为诊断、月度结算是财务来源。

### 对抗式审查（V2 修订）

V1 审查条目中，以下在 V2 被改写或新增：

- V1“numba/Python 须等价否则失败”——V2 保留，并落地为 canonical 核的强制测试（非仅文档）。
- V1“年汇总 PPA 不能证明月度”——V2 不再仅“规划月度结算”，而是**交付**月度结算并强制 `年=Σ月` 测试。
- V1“app runner 硬编码 data/profit_calc”——V2 修正为：实测仅 `run_wind_pv_bess_irr_planning.py:317` 一处；V2 直接改为接受显式输入参数（一行级修复），不引入 `PlanningInputBundle` 重机器。
- V1“等额年现金流只能作基准”——V2 保留为基准口径，并要求 `evaluate_levelized_irr()` 的年收入/OPEX 从结算结果提取。
- **新增**：若 canonical 核与任一异范围模型被要求“对齐结果”，审查应否决——异范围模型只对齐“输入合同与物理常数”，不对齐“输出数值”。
- **新增**：若结算层从月度电量推导需量电费，审查应否决。
- **新增**：若新增 adapter 无任何 V2 内消费者，审查应否决。
- **新增**：若储能效率（0.92/0.95/sqrt）未收敛到单一合同，审查应否决任何“一致性”声明。

### 关于“搜索 vs 优化”的 canonical 声明（V2 新增）

V1 通篇用“搜索/扫描”描述风/光定址、BESS 却用 PuLP MILP，方法论不自洽。V2 要求编码前声明：

- 被最大化的 canonical 目标：投资方 IRR（受业主综合电价/PPA 约束），业主节费率作为对立面 KPI 同点报告。
- 风/光定址：明确是 (a) 网格搜索（接受假精度，须标注网格分辨率与敏感性），还是 (b) 优化（连续/MINLP）。二者择一并写入文档；不允许“coarse-fine resource tuning”隐式充当资源不确定性代理（与诊断定位冲突）。
- 8 个现有 planner：逐一标注其目标函数、输入边界、输出字段、是否含财务口径；目标函数与 canonical 一致的保留为兼容入口，其余标注为诊断或弃用。

### 实施顺序（V2）

1. **P0-a**：固化 V2 合同字段（`DispatchSimulationResult`/`MonthlySettlementResult`/`TimeIndex`）与字段映射表，不删旧 dataclass。→ 验证：合同字段表评审通过。
2. **P0-b**：建立 golden-output 回归基线——对现有 runner 录一组输出。→ 验证：基线落盘且可复现。
3. **P0-c**：建立 24h 手算 oracle（固定发/荷/价，手算收益与守恒）。→ 验证：oracle 用例与手算值落盘。
4. **P1-a**：收敛储能效率与 SOC 单位到单一合同。→ 验证：5 条路径效率参数同源；golden 偏差记录归档。
5. **P1-b**：选定并改造 canonical 物理核，补逐时步/SOC/月净负荷峰值输出。→ 验证：canonical 通过 24h oracle。
6. **P1-c**：落地 `settlement.py`，`年=Σ月=Σ时步` 不变量测试。→ 验证：不变量测试通过。
7. **P2-a**：落地 TimeIndex canonization 与对齐测试。→ 验证：每次 run 对齐测试通过。
8. **P2-b**：为 canonical/结算 adapter 指定 V2 消费者（IRR planner 私有路径 + oracle 测试）。→ 验证：adapter 有真实调用方。
9. **P3-a**：修复 `run_wind_pv_bess_irr_planning.py:317` 硬编码路径为显式输入。→ 验证：该 runner 不再默认读 `data/`。
10. **P3-b**：场景缩减算法与 `InvestmentPlanningCase` 结构一并确定（前置结构或后置算法）。→ 验证：二者在同一 PR 定稿。
11. **P4**：财务输入从结算结果提取；savings/savings_ratio 提为结算一等字段；诊断输出做结构隔离；声明 canonical 目标函数（见上）。→ 验证：双视角 KPI 同点共存测试通过。

### 验证要求（V2，行为优先）

V2 验证以**行为**而非文档关键词为准：

- **golden-output 回归**：现有 runner 输出在每步重构后比对，IRR 偏差须记录归档（允许因 bug 修复而变化，但须解释）。
- **24h 手算 oracle**：canonical 核与结算层须对固定 24h 用例复算手算值（发电=消纳+弃电+储能净变化；负荷=绿电+购网；收入=Σ月度）。
- `年=Σ月=Σ时步` 不变量。
- PPA 反推后业主综合电价回到目标值（容差内）。
- 双视角 KPI 同点共存：同一结算结果同时产出 IRR 与 savings_ratio。
- canonical 核 numba/Python 等价。
- 效率/SOC 合同单一性检查（5 路径同源）。
- TimeIndex 对齐测试每次 run 通过。
- 正式 runner 不默认读 `data/`（修复 `run_wind_pv_bess_irr_planning.py` 一处后即满足）。

文档自检命令仍保留（沿用 V1），但不再作为重构完成的判据：

```bash
rg -n "^## V[0-9]+|对抗式审查|第一性原理|canonical|golden|oracle|TimeIndex|月度结算" src/ele_trading/capacity_planning/PLAN.md
```

### 后续扩展方向（V2 收敛）

V1 列为“后续扩展”的合同，V2 按是否在 V2 落地重新归类：

- **V2 内落地**：`DispatchSimulationResult`、`MonthlySettlementResult`、`TimeIndex`、canonical 物理核、`settlement.py`。
- **V2 仅定合同、不强制实现**：`InvestmentPlanningCase`、`PlanningInputBundle`、`SettlementInput`、`ProjectCashflowResult`（逐年现金流/融资/税费/更换/退化/残值）。
- **明确延后且须成对定稿**：场景采样（LHS/mc）与场景缩减（Kantorovich/Wasserstein L1）须与 case 结构在同一变更中确定，不接受“算法已定、结构未定”。

### 维护规则（V2 增订）

- 继承 V1 全部维护规则（版本组织、英文机读字段、中文仅导出边界、修改测算口径先改合同与测试再迁 planner 等）。
- **新增**：每次新增/修改 adapter，须在 PR 内指明其 V2 消费者；无消费者不予合入。
- **新增**：每个兼容 shim 须注明 sunset（计划移除的版本/条件）。
- **新增**：修改任一调度路径的物理常数（效率/SOC 口径/需量口径），须同步 canonical 合同与 golden/oracle，不得仅改本地。
- **新增**：V2 内容为本维护规则的当前权威；V1 仅作历史。后续小修订直接改 V2；下一个完整基线再新增 `## V3`。
