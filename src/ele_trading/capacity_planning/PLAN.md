# Capacity Planning 投资测算模型体系 PLAN

本文档用于长期维护 `capacity_planning` 投资测算模型体系建设方案。版本以二级标题组织；当前权威基线为 `V4`（`V1`/`V2`/`V3` 保留为历史与变更溯源），后续小修订直接在 `V4` 内继续，或在形成新的完整基线时新增下一个二级版本。

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

## V3 - 源码核实修正：物理常数收敛、调度碎片化全景与导入阻断修复

> **构建信息（provenance）**
> - 构建代理（Agent）：Machine-C（MC，Hermes coder profile，主会话直接构建）
> - 构建模型（Model）：glm-5.2（1M context）
> - 构建日期：2026-07-02
> - 构建依据：通读 `capacity_planning` 全部 31 个文件（`.py` + `.md`）及 `app/capacity_planning/` 全部 runner、`tests/` 相关测试、`src/ele_trading/utils/` 公共 helper、`src/ele_trading/evaluation/`。所有论断附源码行号，可逐条复核。
> - 补充修订（同日，同代理/模型）：在源码二次核实基础上增补 6 个 V3 变更摘要表未覆盖的结构维度（PV 单位命名、计算复杂度、运行时数据质量、中间结果持久化、planner 迁移状态、config schema 校验），对应补充诊断 G–L、第一性原理准则 4 条、对抗式审查 4 条、实施项 P4-d 至 P4-i、维护规则 3 条。
> - 与 V1/V2 的关系：V3 是 V1+V2 的完整修订基线。V1/V2 保留为历史与变更溯源。V3 直接继承 V2 的核心架构决策（canonical 物理核、仿真→结算 seam、TimeIndex、双视角 KPI、行为优先验证），并修正 V2 中因源码核实不完整导致的诊断缺口、实施顺序矛盾和遗漏的阻断级 bug。自 V3 起所有新增规划以 V3 为准。

### V2 → V3 变更摘要

| 维度 | V2 立场 | V3 修正 | 触发依据（源码行号） |
|---|---|---|---|
| 效率口径碎片化 | 3 种口径（`eta_rt**0.5` / 0.95 / 0.92） | 实测 **5 种并存**，含 0.92/0.95 混合口径 | `dispatch_algo.py:70-71`；`resource_bess_planner_core.py:41-42`；`interfaces.py:109-110,156-157`；`bess_capacity_economic_planner.py:49-50`；`bess_capacity_operating_planner.py:36-37`；`bess_capacity_distributed_planner.py:163`；`wind_pv_bess_capacity_planner.py:46,109` |
| 调度路径数量 | 5 条 | 实测 **6 条**，漏了 `wind_pv_bess_capacity_planner.py` 内嵌的独立 `_dispatch_numba` | `wind_pv_bess_capacity_planner.py:82-145`（与 `dispatch_algo.py:24-155` 几乎同构但独立维护） |
| cvxpy 延迟导入 | 未提及（V1/V2 均遗漏） | **P0 阻断级 bug**：顶层 `import cvxpy` 违反 AGENTS.md 硬约束，阻塞整个 `capacity_planning` 包 | `cvxp_bess_dispatch.py:3`；`distributed_bess_dispatch.py:3`；经 `bess_capacity_distributed_planner.py:26` → `__init__.py:68` 链式触发 |
| data/ 硬编码 | "实测仅 1 处"（`run_wind_pv_bess_irr_planning.py:317`） | 实测 **≥2 处**直接硬编码 + 多处经配置间接指向 `data/` | `run_wind_pv_bess_irr_planning.py:317`；`run_wind_bess_capacity_planning.py:173`；`run_dist_bess_dispatch.py:31`（经 `cfg['data']['base_dir']`） |
| 实施顺序 | P0-b 先录 golden，P1-a 后收敛效率口径 | **顺序反转**：效率口径未收敛时录的 golden 在 P1-a 后全部失效 | 逻辑矛盾：golden 依赖物理常数，物理常数在 P1-a 变更 |
| canonical 核建设量 | "优先改造现有路径" | 实测**无任何现有路径能同时产出逐时步+SOC+月净负荷峰值**，接近重写 | `dispatch_annual` 只输出标量；`resource_core` 无 net_load/月峰值；`cvxp/distributed` 无月峰值汇总 |
| Python fallback | "须等价或显式失败" | 当前 fallback **静默产出错误数值**（`bess_dis=0`，弃电量推导物理错误），不是"简化" | `dispatch_algo.py:183-200`；`wind_pv_bess_capacity_planner.py:167-171` |
| SOC 单位冲突 | "分数 vs kWh" | 实质是**输出序列 SOC 单位不统一**（分数/kWh/无序列三种），非输入端冲突 | `resource_core` 输出分数(`:112`)；`cvxp/distributed` 输出 kWh；`dispatch_annual` 不输出 SOC 序列 |
| simulation_model 定位 | 仅 V1 诊断表提了一句 | 补入 V3：其 `revenue_calculation()` 含独立需量计算逻辑，与规划中的 `settlement.py` 直接职责重叠 | `simulation_model.py:154-205`（`revenue_calculation`，含 `:200-203` 月度需量折算） |
| "搜索 vs 优化"范围 | 仅覆盖风/光定址 | 延伸到 **BESS sizing**：同一决策变量（储能容量）有三种方法论（网格搜索/二分搜索/MILP） | `wind_pv_bess_irr_planner.py:390-394`（网格）；`resource_core:340-399`（二分）；`bess_capacity_economic_planner.py:44-256`（PuLP MILP） |
| 代码残留 | 未提及 | `resource_bess_planner_core.py:134` 有 `cfg.soc/WESS` 语法残留（被 `if False` 短路掩盖） | `resource_bess_planner_core.py:134` |

### 补充诊断：V3 新增维度（源码二次核实）

V2→V3 变更摘要表已覆盖物理常数、调度路径、导入阻断等核心发现。以下 6 个维度是 V3 变更摘要表未覆盖、但同样影响投资测算可信度的结构性问题，作为 V3 的补充诊断。

#### 维度 G：PV 容量单位命名掩盖物理含义（命名 bug）

`wind_pv_bess_irr_planner.py` 的 `pv_mw` 字段（`:69`）名义单位是 MW，但实际语义是 **MWp（峰值装机容量）**：CAPEX 计算 `pv_mw * 1000.0 * cfg.pv_capex_yuan_per_kwp`（`:195`）隐含了 MWp→kWp 换算，调度缩放 `pv_unit_arr * float(pv_mw) * 1000.0`（`:393`）同样隐含此换算。同一仓库内 `wind_pv_bess_planner.py` 在不同函数中混用 `pv_mw`（`:216`）和 `pv_kwp`（`:371`），语义不一致。

物理上 `pv_mw=1.0`（MWp）与 `pv_kwp=1000` 等价，但读者会把 `pv_mw` 当成功率（MW），与确为功率单位的 `wind_mw` 混淆。这不是纯命名问题——它掩盖了光伏装机（MWp，峰值容量）与风电装机（MW，额定功率）的物理语义差异。

**V3 处置**：P4 级。IRR planner 的 PV 字段统一为 `pv_mwp` 或 `pv_kwp`，消除 `× 1000.0` 隐含换算。`WindPVBESSIRRResult` 和 `WindPVBESSIRRPlanConfig` 的字段名同步更新，旧名保留为 property alias 至 sunset。

#### 维度 H：网格扫描计算复杂度未标注

典型 3D 网格扫描规模：wind（29 点） × pv（15 点） × bess（201 点） = 87,435 次 dispatch 调用 × 8760 时步/调用。numba 路径 ≈0.9s；Python fallback（当前静默错误）>10s。叠加 `wind_pv_bess_irr_tuning` 的 coarse/fine 嵌套后，单次运行可达分钟级。当前无任何复杂度标注，用户无法预判运行时间。

**V3 处置**：P4 级。每个 scanner/planner 的 docstring 必须标注组合规模公式和预期 wall-clock 时间（numba / Python 两路径）。超过 60s 的扫描必须支持并行化或已有级联粗-细搜索。

#### 维度 I：运行时数据质量检查缺失

当前无机制检测输入数据的静默错误：时间戳空洞、负价格、分辨率与 `dt` 不匹配、负荷/发电/价格序列长度不一致。TimeIndex canonization（P2-b）解决时间轴结构层一致性，但不覆盖数值层检查（负值、异常值、全零序列）。

**V3 处置**：P4 级。在 canonical 核入口或 planner 的 `_normalize_inputs()` 阶段插入运行时断言：非负性（价格允许负但需特殊标注）、序列同长、全零检测。失败时 `raise ValueError` 明确原因，而非静默产出错误指标。

#### 维度 J：中间结果无持久化

当前 dispatch 结果仅在内存中传递。CPU 密集型扫描（87K 组合）失败后，所有中间结果丢失，无法事后审计。canonical 核建成后，逐时步 + 月度结算的完整输出也不落盘，无法支持 IRR 结果的事后复算。

**V3 处置**：P4 级。canonical dispatch + settlement 结果支持写入 parquet（`results/<run_id>/`），作为可选项。golden-output 基线（P3-a）是此能力的首个消费者。

#### 维度 K：现有 planner 缺逐个迁移状态标注

V3 已在"搜索 vs 优化"小节声明了 BESS sizing 的三种方法论，但现有 8 个 planner 入口（`plan_wind_pv_bess_for_target_irr`、`plan_wind_pv_bess`、`plan_wind_bess_system`、`plan_pv_bess_system`、`evaluate_fixed_wind_pv_bess_capacity`、`scan_pv_bess_irr`、`scan_wind_bess_irr`、`run_capacity_search`）尚未逐个标注 V3 迁移状态（保留/改造/冻结/弃用）和 sunset 时间。

**V3 处置**：P4 级。补充 planner 迁移状态表（见下方"planner 迁移状态"小节）。

#### 维度 L：config YAML 加载无 schema 校验

`read_yaml()` 仅做 YAML 解析，不校验字段存在性和类型。配置缺字段时，错误在 runner 深处（如 `_to_config()` 的 `KeyError`）才暴露，排查成本高。

**V3 处置**：P4 级。config 加载后做 schema 检查（字段存在性 + 类型），在 runner 启动时即报错。

### 目标

V3 继承 V2"可证伪优先 + canonical 唯一"的核心目标，但把**阻断级修复**提到一切计划之前。V3 的核心判断是：在物理常数（效率/SOC 口径）未收敛、cvxpy 导入未解耦之前，任何 golden 基线、canonical 核改造和月度结算建设都建立在流沙上。

V3 的交付优先级：

1. **P0-阻断修复**（不解决则后续全部无效）：cvxpy 延迟导入；效率口径收敛到单一合同。
2. **P1-物理可证伪基础**：canonical 物理核（基于 `resource_core` 重写，非改造）；24h 手算 oracle。
3. **P2-结算链**：`settlement.py`（含需量计算）；`年=Σ月=Σ时步` 不变量。
4. **P3-验证基础设施**：golden-output 回归（在 P1 完成后录制）；TimeIndex canonization。
5. **P4-收敛与清理**：data/ 硬编码路径修复；死代码清理（内嵌 `_dispatch_numba`、`simulation_model` 需量逻辑归属）；BESS sizing 方法论声明。

### P0-阻断修复（V3 新增，最高优先级）

#### P0-1 cvxpy 延迟导入修复

**问题**：AGENTS.md 硬约束明确要求"`cvxpy` 是可选依赖：CVXPY 路径通过 `__getattr__` 延迟导入，缺失时 PuLP/Pyomo 路径正常可用，不阻塞项目主链路"。当前实现违反此约束：

- `models/cvxp_bess_dispatch.py:3` — `import cvxpy as cp`（顶层）
- `models/distributed_bess_dispatch.py:3` — `import cvxpy as cp`（顶层）
- `bess_capacity_distributed_planner.py:26` — `from .models.distributed_bess_dispatch import DistributedBESSDispatcher`（顶层，触发 cvxpy 导入）
- `__init__.py:68` — `from .bess_capacity_distributed_planner import ...`（顶层，链式触发）

后果：`import ele_trading.capacity_planning` 即触发 cvxpy 导入。cvxpy 缺失时，整个包不可用——包括不依赖 cvxpy 的 PuLP sizing、Pyomo 路径、IRR planner、贪心调度。

**修复方案**：
- `cvxp_bess_dispatch.py` 和 `distributed_bess_dispatch.py` 内部改为函数级 `import cvxpy as cp`（在 `solve()` / `_solve_lp()` 内部导入）。
- `bess_capacity_distributed_planner.py` 对 `DistributedBESSDispatcher` 的导入改为 `__getattr__` 延迟加载或 TYPE_CHECKING 守卫。
- `__init__.py` 中 cvxpy 相关的导出项走 `__getattr__` 机制。
- 验证：`pip uninstall cvxpy` 后 `import ele_trading.capacity_planning` 成功，PuLP/IRR 路径正常可用。

#### P0-2 效率口径收敛

**现状全景**（V3 实测，5 种并存）：

| 路径 | 充电效率 | 放电效率 | 往返效率 | 源码行号 |
|---|---|---|---|---|
| `dispatch_algo._dispatch_annual_numba` | `eta_rt**0.5` | `eta_rt**0.5` | `eta_rt`（调用方传 0.92）→ 单边 ≈0.959 | `dispatch_algo.py:70-71` |
| `wind_pv_bess_capacity_planner._dispatch_numba` | `eta**0.5` | `eta**0.5` | `eta`（配置传 0.92）→ 单边 ≈0.959 | `wind_pv_bess_capacity_planner.py:109-110` |
| `resource_bess_planner_core` | 0.92 | 0.92 | **0.8464** | `resource_bess_planner_core.py:41-42` |
| `cvxp_bess_dispatch` / `distributed_bess_dispatch` | 0.95 | 0.95 | **0.9025** | `interfaces.py:109-110,156-157` |
| `bess_capacity_economic_planner` | 0.95 | 0.95 | **0.9025** | `bess_capacity_economic_planner.py:49-50` |
| `bess_capacity_operating_planner` | **0.92** | **0.95** | **0.874**（混合！） | `bess_capacity_operating_planner.py:36-37` |
| `bess_capacity_distributed_planner.build_devices_info` | **0.92** | **0.95** | **0.874**（混合！） | `bess_capacity_distributed_planner.py:163` |

同一项目内，储能往返效率从 0.8464 到 0.9025 跨越 5.6 个百分点，直接改变 IRR 和 sizing 结论。

**收敛方案**：
- 定义唯一物理常数合同 `BESSPhysicsContract`（dataclass），字段：`eta_charge`、`eta_discharge`、`soc_unit`（"kwh" | "fraction"）、`c_rate_definition`。
- 所有调度路径从该合同读取效率参数，禁止局部默认值。
- 过渡期：各路径现有默认值保留为 fallback，但新增 `deprecation_warning` 指向合同。
- V3 不预设具体效率数值（0.92/0.95 还是其他），由 wang zf 在合同定义时确定；V3 只负责收敛到单一来源。

### 调度路径全景分类（V3 修正，6 条）

V2 列了 5 条调度路径，V3 实测有 6 条。新增的第 6 条是 `wind_pv_bess_capacity_planner.py:82-145` 内嵌的 `_dispatch_numba`——它与 `dispatch_algo.py` 的 `_dispatch_annual_numba` 几乎同构（同为贪心 surplus→charge / deficit→discharge），但独立维护，签名不同、无 `switch_gap_steps`、无 `other_kw`、Python fallback 也不同。这是比 V2 认知更严重的代码重复。

| # | 路径 | 物理模型 | 输出粒度 | SOC 输出 | 效率口径 | V3 定位 |
|---|---|---|---|---|---|---|
| 1 | `dispatch_algo.dispatch_annual` | 贪心平衡（wind+pv+other+BESS） | **仅年度标量** | 无序列 | `eta_rt**0.5` | canonical 演进底座候选；补时步/SOC/月峰值后升级 |
| 2 | `wind_pv_bess_capacity_planner._dispatch_numba` | 贪心平衡（wind+pv+BESS） | **仅年度标量** | 无序列 | `eta**0.5` | **与 #1 重复**，V3 标记为死代码候选，统一到 #1 |
| 3 | `resource_bess_planner_core.simulate_dispatch` | 贪心平衡（单源+BESS） | **逐时步** | 分数 | 0.92/0.92 | canonical 核重写底座（输出最接近目标，但无 net_load/月峰值） |
| 4 | `cvxp_bess_dispatch.CvxpBESSDispatcher` | CVXPY 凸规划（单节点） | **逐时步** | kWh | 0.95/0.95 | 异范围模型（运营调度），不作投资测算结算上游 |
| 5 | `distributed_bess_dispatch.DistributedBESSDispatcher` | CVXPY 凸规划（多变压器） | **逐时步** | kWh | 0.95/0.95 | 异范围模型；其 sliding_window 需量逻辑作为 canonical 月峰值实现参考 |
| 6 | `bess_capacity_economic_planner.solve_capacity_sizing` | PuLP MILP（sizing+调度联合优化） | **逐时步** | kWh | 0.95/0.95 | sizing 优化器；与 canonical 关系：sizing 结果再经 canonical 复算结算 |

### canonical 物理核（V3 修正建设量评估）

V2 说"优先以现有能产出逐时步 + SOC + 每月净负荷峰值的路径为底座改造"。V3 实测：**没有任何一条现有路径能同时产出这三个输出**。

| 输出项 | dispatch_annual | resource_core | cvxp | distributed |
|---|---|---|---|---|
| 逐时步序列 | ✗（仅标量） | ✓ | ✓ | ✓ |
| SOC 序列 | ✗ | ✓（分数） | ✓（kWh） | ✓（kWh） |
| 月净负荷峰值 | ✗ | ✗（无 net_load） | ✗（有 net_load 但无月峰值汇总） | ✗（有 sliding_window 但无月峰值输出字段） |
| net_load 序列 | ✗ | ✗ | ✗ | ✓ |

**V3 结论**：canonical 核应基于 `resource_core.simulate_dispatch` 的循环结构**重写**（而非"改造"），因为：
- `resource_core` 输出逐时步 + SOC，离目标最近；
- 但需新增 net_load 计算、月度峰值聚合、metadata 输出；
- 且需从"单源"扩展到"多源（wind+pv+other）"。

canonical 核不新建目录，放 `models/canonical_dispatch.py`（继承 V2 命名）。核心函数签名：

```python
def canonical_dispatch(
    load_kw: np.ndarray,
    generation_kw: dict[str, np.ndarray],  # {"wind": ..., "pv": ..., "other": ...}
    bess: BESSPhysicsContract,
    bess_capacity_kwh: float,
    time_index: TimeIndex,
    switch_gap_steps: int = 0,
) -> DispatchSimulationResult
```

### Python fallback 问题（V3 显式升级为 bug）

V2 保留 V1 的"numba/Python 须等价否则失败"规则。V3 实测发现：当前 Python fallback 不是"简化"，而是**静默产出物理错误数值**：

- `dispatch_algo.py:183-200`：Python fallback 设 `bess_dis = 0.0`（不模拟充放电），然后通过 `curtail_e = max(surplus_e - (gen_e - used_e - bess_dis), 0.0)` 推导弃电量。在有 BESS 配置时，这条路径返回的弃电量和消纳量都是错误的。
- `wind_pv_bess_capacity_planner.py:167-171`：同样设 `b = 0.0`。

**V3 处置**：在 P1 canonical 核建设中，Python fallback 必须实现与 numba 等价的完整 BESS 仿真（贪心逻辑不复杂，纯 Python 可承受）。在 canonical 核落地前，现有 fallback 路径加 `RuntimeError("Python fallback does not simulate BESS; results are physically incorrect when batt_kwh > 0")`，而非静默返回错误数值。

### 仿真→结算 seam 合同（继承 V2，补 net_load 强制性）

继承 V2 的 `DispatchSimulationResult` 字段定义，V3 新增约束：

- `net_load_kwh`（逐时步净负荷 = load - generation + charge - discharge）为**必填字段**，不可缺失。需量电费计算依赖此序列的月内峰值，不可由月度电量聚合反推。
- `monthly_net_load_peak_kw` 由 canonical 核在仿真时直接计算（按结算周期和滑窗口径），不从 `net_load_kwh` 事后聚合（避免口径二次分裂）。

`MonthlySettlementResult` 继承 V2 字段定义，不修改。

### 目标分层与模块分布（V3 修正）

| 目标层 | V3 落地动作 | 命名 | 与 V2 的差异 |
|---|---|---|---|
| 物理常数合同 | **V3 新增 P0 层**：定义 `BESSPhysicsContract` | `models/physics_contract.py` 或 `interfaces.py` 内 | V2 无此层 |
| 运行仿真 | 落地 canonical 核（基于 resource_core **重写**） | `models/canonical_dispatch.py` | V2 说"改造"，V3 修正为"重写" |
| 死代码清理 | **V3 新增**：删除 `wind_pv_bess_capacity_planner._dispatch_numba`，统一到 canonical | 原地清理 | V2 未发现此重复 |
| 需量计算归属 | **V3 新增**：`simulation_model.revenue_calculation()` 的需量逻辑迁入 `settlement.py` | `simulation_model.py:200-203` → `settlement.py` | V2 完全遗漏 simulation_model 的需量计算 |
| 结算 | 新建（V3 必交付） | `settlement.py` | 同 V2 |
| 财务 | 扩展 `irr_finance.py` | 保留 | 同 V2 |
| 案例聚合 | 仅定合同 | 合同先行 | 同 V2 |
| 编排入口 | 保留现有入口 | 不动 | 同 V2 |

文件/目录搬家 V3 **不做**，继承 V2 规则。

### "搜索 vs 优化" canonical 声明（V3 扩展到 BESS sizing）

继承 V2 对风/光定址的方法论声明要求，V3 扩展到 BESS sizing：

当前 BESS 容量决策有三种方法论并存：

| 路径 | 方法论 | 决策变量类型 | 源码 |
|---|---|---|---|
| `wind_pv_bess_irr_planner` | 网格搜索（`_capacity_candidates` 枚举） | 离散（步长 10MWh） | `wind_pv_bess_irr_planner.py:232-242,390-394` |
| `resource_core.find_min_capacity_bisect` | 二分搜索 | 连续（容差 0.1MWh） | `resource_bess_planner_core.py:340-399` |
| `bess_capacity_economic_planner.solve_capacity_sizing` | PuLP MILP 联合优化 | 连续（Cap_rated 为 LpVariable） | `bess_capacity_economic_planner.py:141,44-256` |

V3 要求编码前声明：
- BESS sizing 的 canonical 方法论：是 (a) 经 canonical 物理核复算的网格搜索，还是 (b) MILP 联合优化后经 canonical 复算？
- 网格搜索路径须标注网格分辨率与对 IRR 的敏感性。
- 二分搜索路径须标注适用前提（覆盖率/自用率对容量的单调性假设）。
- MILP 路径须标注其内部调度模型与 canonical 的关系（sizing 结果必须经 canonical 复算结算，不可直接用 MILP 内部目标值做 IRR 上游）。

### planner 迁移状态（V3 补充，对应维度 K）

现有 8 个 planner 入口的 V3 迁移状态：

| planner 入口 | V3 状态 | 动作 | sunset |
|---|---|---|---|
| `plan_wind_pv_bess_for_target_irr` | 改造 | 内部迁移到 canonical + settlement 流水线；外部入口和返回字段不变 | — |
| `plan_wind_pv_bess` | 改造 | 同上；PV 搜索沿用现有 coarse/fine | — |
| `plan_wind_bess_system` / `plan_pv_bess_system` | 改造 | 资源特定路径，内部 `resource_core` 迁移到 canonical 核 | — |
| `evaluate_fixed_wind_pv_bess_capacity` | 保留 | 已适配 canonical；无 BESS 时 `dispatch_annual` 保留为兼容 | — |
| `scan_pv_bess_irr` / `scan_wind_bess_irr` | 冻结 | 仅修复阻断级 bug（效率合同、cvxpy 导入），不新增功能 | 2027-01（或下一完整基线） |
| `solve_capacity_sizing`（PuLP MILP） | 保留 | sizing 优化器；与 canonical 的关系见上方声明（sizing 结果经 canonical 复算结算） | — |
| `run_capacity_search`（分布式） | 保留 | 分布式场景，使用 `DistributedBESSDispatcher`；不作投资测算结算上游 | — |

### 第一性原理判断准则（V3 增订）

继承 V2 全部准则，增订：

- **阻断优先**：cvxpy 延迟导入和效率口径收敛是前置阻断项，未解决前不投入 golden/canonical 建设。
- **物理常数单一来源**：所有调度路径的效率/SOC 口径从 `BESSPhysicsContract` 读取，禁止局部默认值。新增路径不接入合同则审查否决。
- **fallback 不可静默错误**：Python fallback 在 BESS 配置 > 0 时必须模拟充放电，或在无法等价时显式 `raise`，禁止返回 `bess_dis=0` 的错误数值。
- **重复调度核零容忍**：项目内同一物理模型（贪心平衡）只允许一份实现。新增第二份须证明输入/输出/约束有本质差异。
- **单位语义精确**：装机容量（MWp/kWp）与额定功率（MW/kW）不可混用同一字段名。`pv_mw` 实为 MWp 须更名，隐含单位换算（`× 1000.0`）须消除。
- **复杂度透明**：网格/二分/MILP 扫描入口必须标注组合规模公式和预期 wall-clock 时间，用户可预判运行成本。
- **输入数据先验检查**：dispatch 前须检测时间轴连续性、序列同长、异常值（全零、负价格未标注），失败早于仿真。
- **中间结果可追溯**：CPU 密集型扫描的 dispatch/settlement 中间结果须可持久化，支持事后审计和 IRR 复算。

### 对抗式审查（V3 增订）

继承 V2 全部审查条目，增订：

- 若 `import ele_trading.capacity_planning` 在 cvxpy 缺失时失败，审查否决。
- 若任一调度路径使用未从 `BESSPhysicsContract` 读取的效率参数，审查否决。
- 若 Python fallback 在 `batt_kwh > 0` 时返回 `bess_dis = 0` 且未 raise，审查否决。
- 若存在两份独立的贪心调度 numba 实现（如 `dispatch_annual` 与 `wind_pv_bess_capacity_planner._dispatch_numba`），审查否决——除非证明本质差异。
- 若 golden-output 回归基线在效率口径收敛之前录制，审查否决（基线会在物理常数变更后失效）。
- 若 BESS sizing 结果（来自网格/二分/MILP 任一路径）未经 canonical 物理核复算结算就进入 IRR 计算，审查否决。
- **新增**：若新增/修改的 planner 或 dataclass 字段名隐含单位换算（如 `pv_mw` 实为 MWp 且内部 `× 1000.0`），审查否决——须显式命名（`pv_mwp` / `pv_kwp`）。
- **新增**：若扫描入口未标注组合规模和预期 wall-clock 时间，审查否决。
- **新增**：若输入数据未通过运行时质量检查（时间轴连续、序列同长、异常值检测）就进入 dispatch，审查否决。
- **新增**：若 planner 入口未在本文档"planner 迁移状态"表中登记其 V3 状态（保留/改造/冻结/弃用），审查否决。

### 实施顺序（V3 修正）

V2 实施顺序的核心问题是 P0-b（golden 基线）在 P1-a（效率收敛）之前——物理常数未定时录的 golden 在常数变更后全部失效。V3 把效率收敛和 cvxpy 修复提到最前。

1. **P0-1**：修复 cvxpy 延迟导入（函数级导入 + `__getattr__`）。→ 验证：`pip uninstall cvxpy; python -c "import ele_trading.capacity_planning"` 成功，PuLP/IRR 路径正常。
2. **P0-2**：定义 `BESSPhysicsContract`，收敛全部 6 条调度路径的效率参数到单一合同。→ 验证：6 条路径效率参数同源；golden 偏差记录归档（此时允许偏差，因修 bug 必然变化）。
3. **P0-3**：修复 `resource_bess_planner_core.py:134` 代码残留（`cfg.soc/WESS`）。→ 验证：`compileall` 通过。
4. **P1-a**：建立 24h 手算 oracle（固定发/荷/价/效率，手算守恒与收益）。→ 验证：oracle 用例与手算值落盘。
5. **P1-b**：建设 canonical 物理核（基于 `resource_core` 循环结构重写，补 net_load/月峰值/metadata）。→ 验证：canonical 通过 24h oracle。
6. **P1-c**：删除 `wind_pv_bess_capacity_planner._dispatch_numba`（死代码），其调用方改用 canonical 核。→ 验证：该文件调度结果不变（或变化已解释）。
7. **P1-d**：现有 Python fallback 加显式 `raise`（在 `batt_kwh > 0` 时）。→ 验证：无 numba + BESS 场景显式失败。
8. **P2-a**：落地 `settlement.py`（含需量计算，迁移 `simulation_model.revenue_calculation` 的需量逻辑）。→ 验证：`年=Σ月=Σ时步` 不变量测试通过。
9. **P2-b**：落地 TimeIndex canonization 与对齐测试。→ 验证：每次 run 对齐测试通过。
10. **P3-a**：建立 golden-output 回归基线（**此时**物理常数已收敛、canonical 已通过 oracle）。→ 验证：基线落盘且可复现。
11. **P3-b**：为 canonical/结算 adapter 指定 V3 消费者（IRR planner 私有路径 + oracle 测试）。→ 验证：adapter 有真实调用方。
12. **P4-a**：修复 data/ 硬编码路径（≥2 处 runner 改为接受显式输入参数）。→ 验证：相关 runner 不默认读 `data/`。
13. **P4-b**：声明 BESS sizing canonical 方法论（网格/二分/MILP 择一为主，其余标注关系）。→ 验证：声明写入文档且代码一致。
14. **P4-c**：场景缩减算法与 `InvestmentPlanningCase` 结构一并确定。→ 验证：二者在同一 PR 定稿。
15. **P4-d**：PV 单位命名修正——IRR planner 的 `pv_mw` → `pv_mwp`，消除 `× 1000.0` 隐含换算；`WindPVBESSIRRResult` / `WindPVBESSIRRPlanConfig` 字段同步，旧名 property alias。→ 验证：修改前后物理值不变（pv_mw=1.0 → pv_mwp=1.0，CAPEX 不变）。
16. **P4-e**：扫描入口复杂度标注——每个 scanner/planner 的 docstring 标注组合规模公式和预期 wall-clock（numba/Python 两路径）。→ 验证：docstring 可查。
17. **P4-f**：运行时数据质量检查——在 canonical 核入口或 `_normalize_inputs()` 插入断言（时间轴连续、序列同长、异常值检测）。→ 验证：畸形输入在 dispatch 前即 `raise ValueError`。
18. **P4-g**：中间结果持久化——canonical dispatch + settlement 结果支持写 parquet（`results/<run_id>/`），golden-output（P3-a）作为首个消费者。→ 验证：`results/<run_id>/` 目录可写且可读回复算。
19. **P4-h**：config schema 校验——`read_yaml()` 后做字段存在性 + 类型检查。→ 验证：缺字段 config 在 runner 启动时报错。
20. **P4-i**：planner 迁移状态登记——现有 8 个 planner 入口在"planner 迁移状态"表标注 V3 状态（保留/改造/冻结/弃用）。→ 验证：表已填写完整。

### 验证要求（V3，阻断优先 + 行为优先）

V3 验证分两级：阻断级（P0）和行为级（P1+）。

**阻断级验证**（P0 完成后必须通过）：

```bash
# cvxpy 延迟导入验证
pip uninstall cvxpy -y
python -c "from ele_trading.capacity_planning import plan_wind_pv_bess_for_target_irr; print('OK')"
python -c "from ele_trading.capacity_planning import solve_capacity_sizing; print('OK')"
pip install cvxpy  # 恢复
```

```bash
# 效率口径单一性验证（6 路径同源）
python -c "
from ele_trading.capacity_planning.models.dispatch_algo import dispatch_annual
# ... 逐路径检查效率参数来源
"
```

**行为级验证**（继承 V2，顺序修正）：

- 24h 手算 oracle（canonical 核 + 结算层）。
- `年=Σ月=Σ时步` 不变量。
- PPA 反推后业主综合电价回到目标值（容差内）。
- 双视角 KPI 同点共存。
- canonical 核 numba/Python 等价。
- TimeIndex 对齐测试每次 run 通过。
- 正式 runner 不默认读 `data/`。

文档自检命令（沿用 V2）：

```bash
rg -n "^## V[0-9]+|对抗式审查|第一性原理|canonical|golden|oracle|TimeIndex|月度结算|阻断|BESSPhysicsContract" src/ele_trading/capacity_planning/PLAN.md
```

### 后续扩展方向（V3 重新归类）

V2 列为"后续扩展"的合同，V3 按阻断/行为/延后重新归类：

- **V3 内落地**：`BESSPhysicsContract`（P0-2）、canonical 物理核（P1-b）、`settlement.py`（P2-a）、`DispatchSimulationResult`/`MonthlySettlementResult`/`TimeIndex`（随 P1/P2 落地）。
- **V3 仅定合同、不强制实现**：`InvestmentPlanningCase`、`PlanningInputBundle`、`SettlementInput`、`ProjectCashflowResult`。
- **明确延后且须成对定稿**：场景采样（LHS/mc）与场景缩减（Kantorovich/Wasserstein L1）须与 case 结构在同一变更中确定。

### 维护规则（V3 增订）

- 继承 V1/V2 全部维护规则。
- **新增**：V3 内容为本维护规则的当前权威；V1/V2 仅作历史。后续小修订直接改 V3；下一个完整基线再新增 `## V4`。
- **新增**：修改任一调度路径的物理常数（效率/SOC 口径），须同步 `BESSPhysicsContract`、canonical 核和 golden/oracle，不得仅改本地默认值。
- **新增**：新增调度路径须在本文档"调度路径全景分类"表中登记，并标注与 canonical 的关系。
- **新增**：cvxpy/pulp/pyomo 导入必须延迟化（函数级或 `__getattr__`），顶层导入物理常数类（numpy/pandas）以外的第三方求解器依赖会被审查否决。
- **新增**：装机容量（MWp/kWp）与额定功率（MW/kW）字段名不可混用；新增字段隐含单位换算（如 `pv_mw` 内部 `× 1000.0`）会被审查否决。
- **新增**：每个 scanner/planner 入口须在 docstring 标注组合规模公式和预期 wall-clock 时间。
- **新增**：每个 planner 入口须在本文档"planner 迁移状态"表登记其当前版本状态（保留/改造/冻结/弃用）和 sunset。

## V4 - 第一阶段整合实施基线：canonical dispatch + monthly settlement 主链

> **构建信息（provenance）**
> - 构建代理（Agent）：Codex，基于用户提供的 V4 第一阶段整合计划写入。
> - 构建日期：2026-07-02。
> - 与 V1/V2/V3 的关系：V4 是第一阶段实施基线。V4 继承 V2 的架构主线（canonical 唯一、月度结算必交付、行为测试优先），吸收 V3 的源码核实结论（optional solver 导入、效率合同、fallback 静默错误、重复调度核、runner 输入边界），并裁剪 V3 中不属于第一阶段阻断路径的治理项。

### 目标

V4 第一阶段只建立一条可验证的投资测算主链：

```text
canonical dispatch -> monthly settlement -> IRR / owner KPI
```

第一阶段以 Wind/PV/BESS IRR 投资测算链为主消费者，不试图统一所有运营调度、分布式调度和 MILP sizing 的目标函数。外部入口、结果 dataclass、CSV 英文字段和中文表头保持稳定；新增能力通过内部合同和 adapter 落地。

### V4 整合判断

- V1 的分层、兼容入口稳定性、生产输入边界和中文导出边界规则继续有效，但 V1 把月度结算等能力描述成“重构”不够准确。V4 承认第一阶段包含新增内部能力。
- V2 的核心分析最适合作为架构主线：canonical 唯一、月度结算是财务来源、行为 oracle/golden 优先、adapter 必须有消费者。
- V3 的源码事实更准确：`cvxpy` 顶层导入、效率口径碎片化、重复贪心调度核、Python fallback 静默错误和硬编码 runner 输入边界必须进入实施前置项。
- V3 的 P4 治理项需要裁剪：parquet 持久化、全部 planner wall-clock 标注、PV 字段公开改名和大规模目录搬迁不进入 V4 第一阶段阻断路径。

### Key Changes

- 新增统一物理合同 `BESSPhysicsContract`，作为容量测算调度路径读取效率、SOC 单位和 C-rate 语义的唯一来源。
  - 第一阶段默认值采用当前 Wind/PV/BESS IRR 主链语义：`roundtrip_efficiency=0.92`，`eta_charge=sqrt(0.92)`，`eta_discharge=sqrt(0.92)`，`soc_unit="kwh"`。
  - 现有 public config 字段保留；内部通过 adapter 构造合同，禁止新增局部硬编码效率默认值。
  - CVXPY、分布式和 PuLP 路径暂不强求与 canonical 数值一致，但其默认效率必须来自同一合同或显式 public input 转换。

- 修复 optional dependency 边界。
  - `models/cvxp_bess_dispatch.py`、`models/distributed_bess_dispatch.py` 内的 `cvxpy` 改为求解函数内延迟导入。
  - `bess_capacity_operating_planner.py`、`bess_capacity_distributed_planner.py` 和 `capacity_planning.__init__` 避免在普通包导入时触发 `cvxpy`。
  - PuLP/IRR/贪心路径在未安装 `cvxpy` 时仍可导入、可运行。

- 新建 `models/canonical_dispatch.py`，实现第一阶段唯一投资测算物理核。
  - 输入：同轴 `load_kw`、`generation_kw` 字典、`BESSPhysicsContract`、`bess_capacity_kwh`、`TimeIndex` 或等价时间轴、`switch_gap_steps`。
  - 输出 `DispatchSimulationResult`：逐时步 `generation_kwh`、`direct_used_kwh`、`charge_kwh`、`discharge_kwh`、`soc_kwh`、`grid_buy_kwh`、`curtail_kwh`、`load_kwh`、`net_load_kw`、`metadata`。
  - Python 与 numba 路径必须等价；若暂未实现 numba，则第一阶段只保留正确 Python 核，不保留错误 fallback。
  - `dispatch_annual()` 保留为兼容 wrapper；当 `batt_kwh > 0` 且 fallback 无法正确模拟时必须显式失败，不能返回“无电池”结果。

- 新建 `settlement.py`，把结算从调度和财务中拆出。
  - `MonthlySettlementResult` 由 `DispatchSimulationResult`、价格/PPA/需量参数和结算周期生成。
  - 需量峰值由 settlement 根据 `net_load_kw`、时间轴和需量口径计算；不要把月峰值硬塞进物理核，避免 tariff policy 污染 dispatch。
  - 年度收入、业主综合电价、节费额、节费率必须从月度结果汇总得到。

- 迁移主消费者，不全量迁移所有 planner。
  - 第一阶段主消费者：`plan_wind_pv_bess_for_target_irr`。
  - 该 planner 内部改为：输入归一化 -> 候选生成 -> canonical dispatch -> monthly settlement -> `evaluate_levelized_irr()`。
  - 外部入口、返回字段、CSV 英文字段和中文表头保持稳定。
  - `wind_pv_bess_capacity_planner._dispatch_numba` 标记为重复实现；第一阶段优先用 canonical wrapper 替代其调用，只有测试覆盖后再删除。

- 修正输入边界的真实问题。
  - `run_wind_pv_bess_irr_planning.py` 不再默认把 `data/profit_calc/...` 当生产输入；如保留 demo 默认，CLI/config 必须明确 `demo` 语义。
  - `run_dist_bess_dispatch.py` 的 `base_dir` 保持显式 config 输入，不在 V4 中视为同类硬编码生产路径，除非 config 默认直接指向样例数据。

### 实施顺序（V4）

1. **P0-a 测试先行**：新增失败测试覆盖 canonical dispatch 24h oracle、monthly settlement oracle、IRR planner 结算来源、`cvxpy` 缺失时包级导入。→ 验证：测试在当前实现下失败，且失败原因对应缺口。
2. **P0-b optional solver 导入边界**：修复 `cvxpy` 顶层导入和 `__init__` 链式触发。→ 验证：模拟 `cvxpy` 不可用时，`import ele_trading.capacity_planning`、`plan_wind_pv_bess_for_target_irr`、`solve_capacity_sizing` 可用；CVXPY dispatcher 调用 `solve()` 时才要求 `cvxpy`。
3. **P0-c 物理合同**：新增 `BESSPhysicsContract`，主链由 public config adapter 构造合同。→ 验证：主链效率/SOC 口径来自合同，不再由局部散字段直接驱动。
4. **P1-a canonical dispatch**：新增 `models/canonical_dispatch.py`，输出逐时步结果和年度/月度物理汇总。→ 验证：24h oracle 通过能量守恒和 SOC 递推检查。
5. **P1-b fallback 修正**：`dispatch_annual()` 在无 numba 且 `batt_kwh > 0` 时不得静默返回错误结果；可选择正确 Python 实现或显式 `raise`。→ 验证：无 numba + BESS 场景不再返回 `bess_dis=0` 的错误经济测算。
6. **P2-a settlement 层**：新增 `settlement.py`，从 dispatch 结果计算月度电量、电费、需量费、PPA 收入、业主综合电价和节费率。→ 验证：`年度 = Σ月度 = Σ时步`，需量电费来自 `net_load_kw` 峰值而非月度电量反推。
7. **P2-b IRR planner 消费者迁移**：`plan_wind_pv_bess_for_target_irr` 内部接入 canonical dispatch + settlement，`evaluate_levelized_irr()` 的年收入来自 settlement。→ 验证：现有 IRR planner 测试继续通过；若物理 bug 修正导致数值变化，必须解释。
8. **P3-a 重复调度核收敛**：将 `wind_pv_bess_capacity_planner._dispatch_numba` 标记并逐步替换为 canonical wrapper；第一阶段不急于删除未覆盖路径。→ 验证：替换路径的兼容测试通过。
9. **P3-b runner 输入边界**：修正 `run_wind_pv_bess_irr_planning.py` 的生产/demo 输入语义。→ 验证：正式测算入口不默认读 `data/profit_calc/...`；demo 路径有显式语义。
10. **P4 文档同步**：更新 `capacity_planning/README.md` 和必要 config 文档，只描述已实现能力，不把后续合同写成已完成。→ 验证：`rg` 检查 V4 关键字，`compileall` 通过。

### 验证要求（V4）

- 导入边界：
  - 模拟 `cvxpy` 不可用时，`import ele_trading.capacity_planning`、`plan_wind_pv_bess_for_target_irr`、`solve_capacity_sizing` 仍可用。
  - CVXPY 相关 dispatcher 在调用 `solve()` 时才要求 `cvxpy`。
- 物理 oracle：
  - 新增 24h 手算用例，覆盖 surplus 充电、deficit 放电、SOC 上下限、弃电、购网。
  - 校验 `generation = direct_used + charge + curtail`，`load = direct_used + discharge + grid_buy`，SOC 递推符合效率合同。
- 结算 oracle：
  - 固定 24h 或跨 2 个月小样本，校验 `年度 = Σ月度 = Σ时步`。
  - 需量电费从 `net_load_kw` 峰值计算，不允许从月度电量反推。
  - PPA 反推后业主综合电价回到目标值。
- 兼容回归：
  - `tests/test_wind_pv_bess_irr_planner.py` 继续通过，除非因修正物理 bug 导致数值变化；变化必须说明。
  - `capacity_planning.__all__`、主要 `plan_*` / `scan_*` / `run_*` 入口仍可导入。
  - `python3 -m compileall src/ele_trading/capacity_planning` 通过。

文档自检命令：

```bash
rg -n "^## V[0-9]+|V4|canonical dispatch|monthly settlement|BESSPhysicsContract|对抗式审查|第一性原理" src/ele_trading/capacity_planning/PLAN.md
```

### 对抗式审查（V4）

- 若 `import ele_trading.capacity_planning` 在 `cvxpy` 缺失时失败，审查否决。
- 若 Wind/PV/BESS IRR 主链绕过 canonical dispatch 直接使用年度散字段进入 IRR，审查否决。
- 若 settlement 从月度电量推导需量电费，而不是使用 `net_load_kw` 峰值口径，审查否决。
- 若 annual revenue、owner average price、savings/savings_ratio 不能由月度 settlement 汇总复算，审查否决。
- 若 Python fallback 在 `batt_kwh > 0` 时静默返回“无储能放电”的经济测算结果，审查否决。
- 若新增 adapter 没有第一阶段消费者，审查否决。
- 若第一阶段引入 `inputs/`、`dispatch/`、`planners/` 等目录搬迁，或公开改名 `pv_mw` 字段，审查要求退回；这些属于后续阶段。

### 延后项与假设

- `evaluate_levelized_irr()` 继续作为 V4 基准财务模型；逐年现金流、融资、税费、退化、更换、残值放到后续阶段。
- `pv_mw` 实际语义为 MWp 的问题在 V4 记录为已知命名债务；第一阶段不改 public 字段名，只在新增内部字段和文档中写清单位。
- 场景采样和场景缩减不进入第一阶段实现；保留项目规则：默认 LHS、兼容 `method="mc"`、缩减必须是 Kantorovich/Wasserstein L1 后向缩减。
- 不新增 `inputs/`、`dispatch/`、`planners/` 目录；第一阶段只加最小必要模块和 adapter。
- V4 内容为当前权威；V1/V2/V3 仅作历史。后续小修订直接改 V4，下一个完整基线再新增 `## V5`。
