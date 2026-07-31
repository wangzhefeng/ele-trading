# investment_estimation 复用 capacity_planning 算法分析 TODO

本文档分析 `src/ele_trading/capacity_planning/`、项目根目录 `app/` 和 `configs/` 中已有算法与运行配置，判断哪些能力对继续构建 `src/investment_estimation/` 有用，哪些暂不适合接入。

分析目标不是把旧代码整体迁入 `investment_estimation`，而是识别可以服务以下目标的算法资产：

1. 风光资源输入建模。
2. 逐时或 15 分钟风光储运行仿真。
3. 月度结算和业主节费计算。
4. 投资方 IRR、PPA 价格反求和现金流测算。
5. 风光储容量搜索、约束过滤和不可行诊断。
6. 后续从规则搜索升级到更严谨的运筹优化模型。

## 总体判断

`capacity_planning` 中最有价值的部分不是所有 planner，而是已经形成的几类“可沉淀算法内核”：

1. `resource_simulation/`：风光资源曲线生成和等效小时数校准。
2. `models/canonical_dispatch.py` + `models/physics_contract.py`：更标准的风光储逐时能量守恒合同。
3. `settlement.py`：以月度结算作为财务输入来源的结算层。
4. `irr_finance.py`：PPA 反推、目标 IRR 缺口诊断、逐年现金流框架。
5. `wind_pv_bess_irr_planner.py`：与 `investment_estimation` 当前 V1-V5 最接近的风光储 IRR 目标型容量搜索。
6. `models/price_aware_dispatch.py`：可作为当前规则调度向价格感知调度升级的中间方案。
7. `configs/capacity_planning/wind_pv_bess_irr_planning.yaml`：完整风光储 IRR 测算场景配置模板。
8. `app/capacity_planning/run_wind_pv_bess_irr_planning.py`：正式输入和 demo 输入边界、中文表头导出、资源曲线缓存等运行组织方式。

暂不适合直接接入的部分主要是历史场景包装、分布式储能、多节点扫描、聚合 IRR 快速扫描和部分旧 planner。它们要么目标与 `investment_estimation` 当前的“投资方/业主双目标测算”不一致，要么依赖特定园区、多变压器、旧数据口径或求解器路径，直接接入会增加复杂度。

## 对 investment_estimation 最有用的算法

### 1. 风光资源仿真：`capacity_planning/resource_simulation/`

相关文件：

```text
src/ele_trading/capacity_planning/resource_simulation/
configs/resource_simulation/pv_simulation_v*.yaml
configs/resource_simulation/wind_simulation_v*.yaml
app/resource_simulation/run_*_simulation_v*.py
```

有用原因：

1. `investment_estimation` 当前把风光资源作为外部 CSV 输入，尚未实现资源仿真。
2. 用户已说明后续会补充已有风光资源仿真脚本；该目录正好提供已有 PV/Wind 物理仿真和等效小时数校准能力。
3. 风电 v1 支持 `target_full_load_hours` 反向校准，直接对应原始需求中“合作方提供风电年发电利用小时数反向约束”。
4. 光伏 v1/v2 已覆盖清晰天空、气象驱动、回放、PVWatts、温度修正、系统损耗等逻辑。
5. 输出 `SimulationResult.power_series`，单位 kW，能较容易转为 `investment_estimation` 当前资源 CSV 合同：

   ```text
   time,pv_kw,wind_kw
   ```

建议接入方式：

1. 不把全部实现复制进 `investment_estimation`。
2. 先在 `investment_estimation` 新增资源适配层，例如：

   ```text
   resource_adapter/
     README.md
     pv_resource_adapter.py
     wind_resource_adapter.py
   ```

3. 适配层只负责把 `SimulationResult` 转成 `data_provider` 可读的资源 CSV。
4. 资源仿真脚本本身保持在 `capacity_planning/resource_simulation/`，直到用户明确要求迁移。

待办：

1. 定义资源适配输出字段：`time,pv_kw,wind_kw`。
2. 明确单位：PV/Wind 输出统一为 kW，不混用 MW。
3. 对风电等效小时数校准写入 README 和配置说明。
4. 为 8760 点和 15 分钟点分别做样例输出校验。

### 2. BESS 物理合同：`models/physics_contract.py`

相关文件：

```text
src/ele_trading/capacity_planning/models/physics_contract.py
```

有用原因：

1. `investment_estimation` 当前 `BESSConfig` 已有功率、容量、效率、SOC 边界，但物理语义分散在配置和调度函数里。
2. `BESSPhysicsContract` 明确了：
   - 充电效率。
   - 放电效率。
   - SOC 内部单位为 kWh。
   - C-rate 语义。
   - SOC 初始值、上下限校验。
3. 这能减少后续 V6/V7 引入优化调度时的口径漂移。

建议接入方式：

1. 不直接替换当前 `BESSConfig`。
2. 增加一个内部转换函数：

   ```text
   ProjectConfig.bess -> BESSPhysicsContract-like internal object
   ```

3. 当前 `dispatch/rule_based.py` 可继续用 `BESSConfig`，但 README 中应声明后续会收敛到统一物理合同。

待办：

1. 给 `investment_estimation` 增加内部物理合同或 adapter。
2. 增加 SOC 单位测试，避免 fraction/kWh 混用。
3. 明确 `power_kw` 和 `energy_kwh` 与 C-rate 的关系。

### 3. canonical dispatch：`models/canonical_dispatch.py`

相关文件：

```text
src/ele_trading/capacity_planning/models/canonical_dispatch.py
```

有用原因：

1. 当前 `investment_estimation/dispatch/rule_based.py` 可以运行，但输出表是 DataFrame，缺少独立的调度结果合同。
2. `canonical_dispatch()` 产出 `DispatchSimulationResult`，字段更适合审计：
   - `generation_kwh`
   - `direct_used_kwh`
   - `charge_kwh`
   - `discharge_kwh`
   - `soc_kwh`
   - `grid_buy_kwh`
   - `curtail_kwh`
   - `net_load_kw`
   - `monthly_summary`
   - `annual_summary`
3. 它强调“年度汇总必须由月度汇总得到”，这比当前 `investment_estimation` 直接 DataFrame 聚合更适合作为长期主链。
4. 它不允许电网充电，适合作为“自发自用/消纳型”物理基线。

与当前实现差异：

1. `investment_estimation` 当前允许电网低价充电，由 `allow_grid_charge` 控制。
2. `canonical_dispatch()` 当前默认 `grid_charge_kwh=0`，更偏风光余电消纳。
3. `investment_estimation` 当前 PPA 电量包括 `renewable_to_load + charge_from_renewable`；canonical 中 green_used 是 `direct_used + discharge`，口径不同，需要谨慎对齐。

建议接入方式：

1. 不立即替换当前规则调度。
2. 在后续版本新增一个可选调度模式：

   ```yaml
   dispatch_mode: canonical_self_consumption
   ```

3. 增加 adapter，将 `DispatchSimulationResult` 转成当前 `settlement/monthly.py` 需要的字段。
4. 用同一小样例对比 `rule_based` 与 `canonical_dispatch` 的能量守恒差异。

待办：

1. 设计 `DispatchResult` 内部合同。
2. 增加 `generation = direct_used + charge + curtail` 校验。
3. 增加 `load = direct_used + discharge + grid_buy` 校验。
4. 明确 PPA 电量采用“充入绿电”还是“最终放出绿电”口径。

### 4. 月度结算层：`capacity_planning/settlement.py`

相关文件：

```text
src/ele_trading/capacity_planning/settlement.py
```

有用原因：

1. `investment_estimation` 当前已经有 `settlement/monthly.py`，但结构偏 DataFrame 汇总。
2. `capacity_planning` 的 `MonthlySettlementResult` 明确把月度结算作为财务输入来源：
   - `green_used_kwh`
   - `grid_buy_kwh`
   - `curtail_kwh`
   - `energy_charge_yuan`
   - `demand_charge_yuan`
   - `ppa_revenue_yuan`
   - `owner_avg_price_yuan_per_kwh`
   - `savings_yuan`
   - `savings_ratio`
   - `net_load_peak_kw`
3. 它支持 `Tariff` 和逐时 TOU 价格，能补强当前 `investment_estimation` 中电价模型较弱的问题。
4. 它将年度汇总严格从月度结果相加得到，适合长期财务测算审计。

建议接入方式：

1. 保留当前 `settlement/monthly.py`，作为 MVP/V1-V5 已实现口径。
2. 后续新增结构化结算结果：

   ```text
   SettlementResult
   MonthlySettlementRecord
   ```

3. 把 `owner_saving_pct` 与 `savings_ratio` 口径统一。
4. 将需量费从当前“月内 grid_buy_kwh / dt 最大值”逐步升级为使用 `net_load_kw` 序列月内峰值。

待办：

1. 对齐 `green_used_kwh`、`ppa_energy_kwh`、`renewable_to_load_kwh` 的业务含义。
2. 增加月度汇总到年度汇总的一致性测试。
3. 引入 `Tariff` 前先定义 YAML 配置格式。

### 5. 财务工具：`irr_finance.py`

相关文件：

```text
src/ele_trading/capacity_planning/irr_finance.py
```

有用原因：

1. `investment_estimation/finance/irr.py` 当前已实现 CAPEX、年度现金流、IRR、NPV、回收期、PPA 反求。
2. `irr_finance.py` 提供更多对后续版本有价值的能力：
   - `backsolve_green_ppa_price()`：由业主目标综合电价反推绿电结算价和 PPA 价格。
   - `compute_target_irr_gap_metrics()`：目标 IRR 不满足时给出缺口诊断。
   - `build_project_cashflows()`：逐年现金流、税费、储能更换、残值、折现率。
   - `compute_npv()`、`compute_payback_year()`。
3. 这些能力直接对应原始需求中的融资成本、运维、储能更换、PPA 锁价后反求 IRR 等输入边界。

建议接入方式：

1. 短期不要替换 `investment_estimation/finance/irr.py`。
2. 先把 `compute_target_irr_gap_metrics()` 的思想引入不可行诊断：

   ```text
   为什么 IRR 不达标？
   需要提高多少 PPA？
   需要降低多少 CAPEX？
   业主综合电价会偏离多少？
   ```

3. 中期将 `annual_cashflows()` 升级为逐年现金流表，纳入：
   - 税费。
   - 储能更换。
   - 残值。
   - 衰减曲线。
   - 折现率。

待办：

1. 在 `finance/` 下新增 `cashflow.py` 或扩展 `irr.py`。
2. 增加 `replacement_events` 配置。
3. 增加 `salvage_ratio`、`tax_rate`、`discount_rate` 配置。
4. 增加目标 IRR 缺口诊断输出字段。

### 6. 风光储 IRR 主规划器：`wind_pv_bess_irr_planner.py`

相关文件：

```text
src/ele_trading/capacity_planning/wind_pv_bess_irr_planner.py
app/capacity_planning/run_wind_pv_bess_irr_planning.py
configs/capacity_planning/wind_pv_bess_irr_planning.yaml
```

有用原因：

1. 这是与 `investment_estimation` 当前 V1-V5 最接近的已有算法。
2. 它已经包含：
   - 风/光/储三维容量网格。
   - canonical dispatch。
   - monthly settlement。
   - PPA 价格反推。
   - IRR 约束筛选。
   - `minimum` / `range` 两种 IRR 约束模式。
   - `maximize_irr` / `maximize_savings_ratio` 等 objective 字段雏形。
   - `ppa_price_locked` 正向求 IRR。
   - diagnostics 和 `diagnostic_summary`。
3. 当前 `investment_estimation` 已实现 V1-V5 的粗网格搜索，但诊断和结算口径还没有这个 planner 完整。

建议接入方式：

1. 不直接调用 `plan_wind_pv_bess_for_target_irr()` 替换现有 `run_capacity_search()`。
2. 将其中成熟逻辑拆成 TODO：
   - `irr_constraint_mode` 加入 `CapacitySearchConfig`。
   - `ppa_price_locked` 场景加入 V6。
   - `diagnostic_summary` 加入搜索输出。
   - `target_owner_price` 反推 PPA 加入 V7。
3. 先把配置结构和诊断字段映射到 `investment_estimation` 的 YAML 和 CSV 输出。

待办：

1. 新增 V6：PPA 锁价后正向求 IRR。
2. 新增 V7：业主目标综合电价反推 PPA，并校验投资方 IRR。
3. 新增 diagnostics 输出：
   - 约束失败计数。
   - 最接近可行候选。
   - 需要提高 PPA 或降低 CAPEX 的幅度。
4. 增加 `irr_constraint_mode` 配置，当前先支持 `minimum`。

### 7. 价格感知调度：`models/price_aware_dispatch.py`

相关文件：

```text
src/ele_trading/capacity_planning/models/price_aware_dispatch.py
```

有用原因：

1. `investment_estimation` 当前规则调度通过 `price_type` 控制低价充电和高价放电。
2. `price_aware_dispatch()` 用价格阈值自动划分充放电时段：
   - `SELF_CONSUMPTION`：高价缺口时放电，不向网充电。
   - `ARBITRAGE`：低价向网充电，高价放电。
3. 输出仍是 `DispatchSimulationResult`，有利于统一调度合同。

限制：

1. 它是启发式，不是 LP/MILP 最优调度。
2. 价格阈值使用 `(price.min + price.max) / 2`，不一定适合所有分时电价场景。
3. 当前 `investment_estimation` 已有 `price_type`，是否改成价格阈值需要业务确认。

建议接入方式：

1. 作为 V6/V7 的可选 dispatch mode，不替换当前规则。
2. 支持配置：

   ```yaml
   dispatch:
     mode: price_aware_arbitrage
   ```

3. 继续保留 `price_type` 规则调度。

### 8. app/configs 的运行边界和配置组织

相关文件：

```text
app/README.md
configs/README.md
app/capacity_planning/run_wind_pv_bess_irr_planning.py
configs/capacity_planning/wind_pv_bess_irr_planning.yaml
```

有用原因：

1. `app/README.md` 明确入口脚本只做配置解析、样例数据组装和调用算法，不应堆算法逻辑。这个边界与 `investment_estimation/app/` 当前设计一致。
2. `run_wind_pv_bess_irr_planning.py` 已明确：
   - 正式测算必须显式传 `--data-dir`。
   - demo 输入必须显式传 `--demo`。
   - 输出 CSV 使用中文表头 + 稳定英文机器字段。
3. `configs/README.md` 对配置目录职责有明确约束：配置只描述参数、路径和开关，算法逻辑放在源码中。
4. `wind_pv_bess_irr_planning.yaml` 是比 `investment_estimation/configs/v*_demo.yaml` 更完整的场景模板，包含资源仿真、价格、约束、容量、搜索、BESS、成本和 resource_tuning。

建议接入方式：

1. `investment_estimation/app/` 继续只做编排，不新增算法逻辑。
2. `configs/` 中后续新增真实项目模板时，参考 `wind_pv_bess_irr_planning.yaml` 的分组：

   ```text
   scenario/site/resource/price/constraints/capacity/search/bess/cost
   ```

3. 输出 CSV 可考虑采用“中文说明行 + 英文字段行”的边界导出方式，但内部字段仍保持英文。

## 可改造后有用的算法

### 1. `wind_pv_bess_capacity_optimizer.py`

定位：

```text
风/光/储三维最低投资组合搜索
```

有用点：

1. 粗扫 + 细扫两阶段网格搜索。
2. 快速剪枝：如果单位风光出力即使全部消纳也不足以满足绿电比例，则跳过。
3. 可固定 wind 或 pv 某一轴。

不适合直接接入原因：

1. 目标是最低 CAPEX，不是投资方 IRR 或业主节费比例。
2. 内联贪心仿真口径与 `investment_estimation` 当前调度/结算口径不同。
3. 结果单位和字段偏历史场景，如 `total_cost_wan`。

建议：

1. 借鉴“粗扫 + 细扫”和“快速剪枝”。
2. 不直接复用其 `simulate_operation()`。
3. 在 `investment_estimation/capacity_search/` 增加后续 TODO：

   ```text
   coarse_to_fine_search.py
   ```

### 2. `bess_capacity_economic_planner.py`

定位：

```text
储能容量与调度联合 MILP
```

有用点：

1. 将 `Cap_rated` 作为决策变量。
2. 同时优化充电、放电、SOC、充放互斥、容量和功率。
3. 目标函数包含放电收益、充电成本、年化 CAPEX 和循环 OPEX。
4. 包含变压器容量、周期性 SOC、切换间隔、最小连续时段等约束。

不适合直接接入原因：

1. 依赖 PuLP/CBC，复杂度高于当前 MVP/V1-V5。
2. 主要用于用户侧 BESS 套利和容量 sizing，不直接处理风光储 PPA/IRR 双目标。
3. 目标函数是储能套利收益，不是项目 IRR 或业主节费比例。

建议：

1. 作为未来“优化调度层”的参考，不进入当前 capacity_search。
2. 后续如果要做 V8：`dispatch_optimization_mode=milp`，可参考其约束建模。
3. 先保留规则调度和价格感知调度，避免过早引入求解器依赖。

### 3. `models/resource_bess_planner_core.py`

定位：

```text
单源新能源 + BESS 最小容量二分搜索
```

有用点：

1. 资源无关：PV 和 Wind 复用同一套 BESS 调度/搜索内核。
2. 二分搜索最小可行储能容量。
3. 支持纯弃电搬运和平移充电两种策略。

不适合直接接入原因：

1. 它只处理单源资源，不处理风光组合。
2. 目标是满足自用率/覆盖率的最小 BESS，不是投资收益或业主节费。
3. 文件中存在明显历史遗留表达，例如 `cfg.soc/WESS` 被不可达分支包裹，说明需要先清理再复用。

建议：

1. 借鉴“固定风光容量下二分搜索最小 BESS”的思路。
2. 不直接复制代码。
3. 后续可在 `investment_estimation` 增加：

   ```text
   capacity_search/min_bess_bisect.py
   ```

### 4. `feasibility_analyzer.py`

定位：

```text
BESS 项目前置可行性诊断
```

有用点：

1. 电价价差统计。
2. 负荷峰谷特性统计。
3. 变压器剩余容量分析。
4. 电价-负荷-充电窗口匹配性评分。

不适合直接接入原因：

1. 当前更偏 BESS 单体项目可行性，不直接服务风光储投资 IRR。
2. 文件中存在命名不一致风险：结果类型引用 `StorageStrategyRecommendation`，但定义为 `BESSStrategyRecommendation`。
3. 推荐策略是经验评分，不应进入正式容量最优目标函数。

建议：

1. 可改造成 `investment_estimation` 的输入诊断模块。
2. 仅作为“运行前提示/诊断”，不能替代 capacity_search。
3. 接入前先修复类型命名和单元测试。

### 5. `wind_pv_bess_irr_tuning.py`

定位：

```text
资源敏感性和无解诊断工具
```

有用点：

1. 遍历风电 FLH、PV 云量因子、系统损耗等资源场景。
2. 支持 coarse/fine 两阶段缩小搜索范围。
3. 可解释无解是否来自资源不足或约束过严。

不适合直接接入原因：

1. 它是诊断工具，不是最终投资目标函数。
2. 资源调参可能被误读为真实可开发资源优化，需严格标注“诊断”。

建议：

1. 后续作为 `investment_estimation` 的 sensitivity/diagnostics 扩展。
2. 不进入当前 V1-V5 主流程。

## 暂不建议接入或基本无用的算法

### 1. 分布式多节点储能：`bess_capacity_distributed_planner.py`

暂不建议原因：

1. 面向多变压器、多储能柜、标准机柜组合，不是当前 `investment_estimation` 的风光储投资测算主目标。
2. 依赖园区拓扑、节点变压器、跨节点支援、机柜组合等特定结构。
3. 输出关注多节点调度成本和机柜数量，不直接对应投资方 IRR/业主节费双目标。

结论：

```text
暂不接入。
```

可保留参考：

1. 多节点场景未来可作为独立高级模块。
2. 中文表头导出和 preset 管理可以借鉴。

### 2. `multi_node_scanner.py`

暂不建议原因：

1. 面向多电价节点 BESS 扫描。
2. 与当前单项目风光储 PPA/IRR 测算口径距离较远。
3. 容易引入节点级复杂度，干扰 `investment_estimation` 的主链路稳定。

结论：

```text
暂不接入。
```

### 3. 聚合 IRR 快速扫描：`pv_bess_irr_planner.py` 和 `wind_bess_irr_planner.py`

暂不建议原因：

1. 它们使用聚合收益公式，不生成逐时 SOC 和调度时序。
2. 当前 `investment_estimation` 的核心要求是 8760 或 15 分钟动态平衡、月度结算和财务闭环。
3. 聚合模型无法直接审计每月电量结算和电价优势。

结论：

```text
不作为主链路接入。
```

可保留参考：

1. 前期快速估算。
2. PPA/电价敏感性粗筛。

### 4. `wind_pv_bess_capacity_planner.py`

暂不建议原因：

1. 固定风光装机，只扫描 BESS。
2. 内联 Numba 贪心调度，口径与 canonical/current rule-based 均不同。
3. 目标是找到满足自用率和覆盖率的 BESS，不计算完整 IRR/节费双目标。

结论：

```text
不直接复用。
```

可借鉴：

1. BESS 容量线性扫描。
2. 零容量候选保留。

### 5. `wind_pv_bess_planner.py`

暂不建议原因：

1. 成本型容量规划，目标是满足约束下 CAPEX 最低。
2. 与当前 V1-V5 的 IRR/节费目标不同。
3. 内部调度和结果字段偏旧口径。

结论：

```text
仅借鉴粗扫/细扫和能量门槛检查，不直接接入。
```

### 6. 旧 legacy app/config

相关文件：

```text
app/legacy/   （已删除，2026-08-01）
configs/legacy/（已删除，2026-08-01）
```

legacy 链路（`run_wind_pv_legacy_profit_eval`、`run_wind_pv_legacy_market_trading` 及其配置、`test_legacy_data_bridge`）已整链删除。原暂不建议接入原因（旧数据链路、字段口径与当前设计不一致）仍成立，勿在 `investment_estimation` 中重建 legacy 兼容层。

结论：

```text
不接入。
```

### 7. optimization 入口和配置

相关文件：

```text
app/optimization/
configs/optimization/
```

暂不建议原因：

1. 主要服务交易/调度 demo、MPC、CVXPY、Two-stage、用户侧调度。
2. 与 `investment_estimation` 的投资测算主链不是同一层。
3. 求解器、市场策略、调度目标会增加依赖和复杂度。

结论：

```text
当前不接入。
```

可借鉴：

1. 市场参数配置风格。
2. 需量电费和偏差考核配置应从 `configs/market/market_*.yaml` 借鉴。

## app 和 configs 中值得借鉴的内容

### 有用配置

1. `configs/capacity_planning/wind_pv_bess_irr_planning.yaml`

   用作未来真实项目场景模板。建议借鉴其分组：

   ```text
   scenario
   site
   pv_simulation
   wind_simulation
   price
   constraints
   capacity
   search
   bess
   cost
   resource_tuning
   ```

2. `configs/resource_simulation/*.yaml`

   用于资源仿真配置模板，尤其是：
   - `equiv_hours`
   - `target_capacity_mw`
   - `target_full_load_hours`
   - `cloud_factor`
   - `system_loss`

3. `configs/market/market_guangdong.yaml`

   可用于后续偏差考核、价格限幅、15 分钟市场参数配置参考。

4. `configs/capacity_planning/capacity_planning.yaml`

   可借鉴粗扫/细扫搜索步长、成本参数和绿电约束配置。

### 有用 app 入口设计

1. `app/capacity_planning/run_wind_pv_bess_irr_planning.py`

   可借鉴：
   - 正式输入必须 `--data-dir`。
   - demo 输入必须 `--demo`。
   - 资源曲线缓存。
   - 中文表头 + 英文字段导出。
   - 将 config 分组转成 dataclass。

2. `app/resource_simulation/run_*`

   可借鉴：
   - 资源仿真独立运行。
   - 输出资源 CSV 后再进入投资测算。

3. `app/README.md`

   可直接继承边界原则：

   ```text
   app 脚本只做配置解析、样例数据组装、调用算法模块和输出结果；
   不在 app 中新增核心约束、目标函数或业务规则。
   ```

## 对 investment_estimation 的建议迁移路线

### 第一阶段：补资源接入能力

目标：

```text
把 capacity_planning/resource_simulation 的输出转为 investment_estimation 的 resource_csv。
```

待办：

1. 新增 `resource_adapter/`。
2. 支持 PV/Wind `SimulationResult` 到 `time,pv_kw,wind_kw`。
3. 新增 `configs/v6_resource_simulation_demo.yaml`。
4. 保持当前 V1-V5 不变。

### 第二阶段：引入结构化调度结果

目标：

```text
把当前 DataFrame 调度结果升级为可审计 DispatchResult。
```

待办：

1. 参考 `DispatchSimulationResult` 设计内部合同。
2. 增加 energy balance 测试。
3. 保留 `rule_based.py` 输出兼容。
4. 增加可选 `canonical_self_consumption` 调度模式。

### 第三阶段：升级月度结算

目标：

```text
让年度 IRR 和业主节费都能从月度结算结果复算。
```

待办：

1. 参考 `MonthlySettlementResult` 设计结构化结算结果。
2. 引入 `Tariff` 或等价电价配置。
3. 补需量电费、输配电价、偏差考核口径。
4. 增加月度汇总一致性测试。

### 第四阶段：增强财务和诊断

目标：

```text
从当前简化年现金流升级到逐年现金流和不可行原因诊断。
```

待办：

1. 参考 `build_project_cashflows()`。
2. 增加税费、残值、储能更换、衰减配置。
3. 参考 `compute_target_irr_gap_metrics()` 输出 IRR 缺口。
4. 增加 V6/V7：
   - PPA 锁价正向求 IRR。
   - 业主目标综合电价反推 PPA。

### 第五阶段：搜索算法升级

目标：

```text
在现有粗网格 capacity_search 基础上增加粗扫/细扫和剪枝。
```

待办：

1. 借鉴 `CapacityOptimizer.optimize()` 的 coarse/fine 搜索。
2. 增加能量门槛剪枝。
3. 保留当前全枚举模式用于回归测试。
4. 输出每个阶段的候选数量和剪枝原因。

### 第六阶段：可选优化调度

目标：

```text
在规则调度之外增加价格感知或 MILP 调度。
```

待办：

1. 先接入 `price_aware_dispatch` 思路。
2. 后续再考虑 `bess_capacity_economic_planner.py` 的 MILP。
3. 不在 V1-V5 主链路中强制引入求解器依赖。

## 优先级清单

### P0：最应优先借鉴

1. `resource_simulation/` 的 PV/Wind 资源仿真和等效小时数校准。
2. `BESSPhysicsContract` 的 SOC/效率/C-rate 物理合同。
3. `DispatchSimulationResult` 的调度结果合同。
4. `settlement.py` 的月度结算结果和年度汇总由月度复算原则。
5. `irr_finance.py` 的目标 IRR 缺口诊断。

### P1：短期可改造

1. `wind_pv_bess_irr_planner.py` 的 diagnostics、`irr_constraint_mode`、`ppa_price_locked`。
2. `price_aware_dispatch.py` 的价格感知规则调度。
3. `run_wind_pv_bess_irr_planning.py` 的正式输入/demo 输入边界和中文表头导出。
4. `capacity_planning/wind_pv_bess_irr_planning.yaml` 的配置分组。

### P2：中期参考

1. `wind_pv_bess_capacity_optimizer.py` 的粗扫/细扫和剪枝。
2. `resource_bess_planner_core.py` 的最小 BESS 二分搜索。
3. `bess_capacity_economic_planner.py` 的 MILP 容量与调度联合优化。
4. `feasibility_analyzer.py` 的前置可行性诊断。

### P3：当前不接入

1. 分布式多节点储能。
2. 多节点扫描。
3. optimization demo 入口。
4. 聚合 IRR 快速扫描作为主链路。

## 需要特别避免的迁移风险

1. 不要把 `capacity_planning` 的所有 planner 直接搬进 `investment_estimation`。目标函数和口径不同，会导致重复和混乱。
2. 不要让 `app/` 脚本承载算法逻辑。算法必须留在模块内。
3. 不要混用 PPA 电量口径：

   ```text
   renewable_to_load + charge_from_renewable
   direct_used + discharge
   green_used_kwh
   ```

   三者必须先业务确认后才能统一。

4. 不要把资源调参当作真实资源优化。`resource_tuning` 应标为诊断。
5. 不要过早引入 MILP/CVXPY 依赖。当前 V1-V5 的优势是可运行、可解释、依赖轻。
6. 不要替换稳定英文输出字段。如需中文，放在导出边界。
7. 不要让样例数据路径成为生产默认输入。

## 风光资源仿真迁移计划

### 迁移目标

根据“风光资源仿真”部分的分析，下一步不再建立 `capacity_planning` 到 `investment_estimation` 的适配层，而是把已有风光资源仿真算法代码迁移到 `src/investment_estimation/` 内，形成 `investment_estimation` 自身可维护、可运行、可被后续测算链路调用的资源仿真模块。

迁移后的目标能力包括：

1. 在 `investment_estimation` 内直接生成光伏出力时序。
2. 在 `investment_estimation` 内直接生成风电出力时序。
3. 保留原有 PV/Wind v1/v2 的算法结构和核心函数，不重写物理算法。
4. 将输出统一整理为当前测算模型需要的资源口径：

   ```text
   time,pv_kw,wind_kw
   ```

5. 保留风电 `target_full_load_hours` 反向校准能力，对应原始需求中的“合作方提供风电年发电利用小时数反向约束”。
6. 迁移后不要求 `investment_estimation` 运行时依赖 `ele_trading.capacity_planning.resource_simulation`。

### 迁移范围

计划迁移以下源码文件：

```text
src/ele_trading/capacity_planning/resource_simulation/models.py
src/ele_trading/capacity_planning/resource_simulation/pv_simulation_v1.py
src/ele_trading/capacity_planning/resource_simulation/pv_simulation_v2.py
src/ele_trading/capacity_planning/resource_simulation/wind_simulation_v1.py
src/ele_trading/capacity_planning/resource_simulation/wind_simulation_v2.py
src/ele_trading/capacity_planning/resource_simulation/__init__.py
```

计划参考并重建以下入口脚本：

```text
app/resource_simulation/run_pv_simulation_v1.py
app/resource_simulation/run_pv_simulation_v2.py
app/resource_simulation/run_wind_simulation_v1.py
app/resource_simulation/run_wind_simulation_v2.py
```

计划参考并迁移以下配置模板：

```text
configs/resource_simulation/pv_simulation_v1.yaml
configs/resource_simulation/pv_simulation_v2.yaml
configs/resource_simulation/wind_simulation_v1.yaml
configs/resource_simulation/wind_simulation_v2.yaml
```

### 目标目录设计

建议在 `src/investment_estimation/` 内新增独立子模块：

```text
src/investment_estimation/
  resource_simulation/
    README.md
    __init__.py
    models.py
    pv_simulation_v1.py
    pv_simulation_v2.py
    wind_simulation_v1.py
    wind_simulation_v2.py
```

该目录直接承载资源仿真算法，不命名为 `resource_adapter`，也不只做转接。

入口脚本放在现有 `investment_estimation/app/` 下：

```text
src/investment_estimation/app/
  run_pv_simulation_v1.py
  run_pv_simulation_v2.py
  run_wind_simulation_v1.py
  run_wind_simulation_v2.py
  build_resource_profile.py
```

其中：

1. `run_pv_simulation_v1.py`：运行光伏 v1，输出单列 `time,pv_kw`。
2. `run_pv_simulation_v2.py`：运行光伏 v2，输出单列 `time,pv_kw`。
3. `run_wind_simulation_v1.py`：运行风电 v1，输出单列 `time,wind_kw`。
4. `run_wind_simulation_v2.py`：运行风电 v2，输出单列 `time,wind_kw`。
5. `build_resource_profile.py`：将已有 PV/Wind 单资源输出合并为 `time,pv_kw,wind_kw`，供当前 `data_provider` 直接读取。

配置文件放在现有 `investment_estimation/configs/` 下：

```text
src/investment_estimation/configs/
  resource_pv_simulation_v1.yaml
  resource_pv_simulation_v2.yaml
  resource_wind_simulation_v1.yaml
  resource_wind_simulation_v2.yaml
  resource_profile_demo.yaml
```

命名加 `resource_` 前缀，是为了避免和现有 `mvp_demo.yaml`、`v1_capacity_search_demo.yaml` 到 `v5_investor_irr_uplift_demo.yaml` 混在一起。

### 保留原算法结构的原则

迁移时应尽量保持以下结构不变：

1. 保留 `SimulationResult` 作为 PV/Wind 统一输出对象。
2. 保留 `PVProfileConfig` 和 `WindProfileConfig` 配置类。
3. 保留 `load_or_build_pv_profile()` 和 `load_or_build_wind_profile()` 作为 v1 配置驱动入口。
4. 保留 `PVSimulator` 和 `WindSimulator` 作为 v2 类封装入口。
5. 保留风电 v1 的 `rescale_wind_output_to_target_flh()` 削峰回补算法。
6. 保留光伏 v1 的 `clear_sky`、`weather_driven`、`replay` 三种模式。
7. 保留光伏 v2 的 GHI 到 DNI/DHI、斜面辐照、组件温度、PVWatts 流程。
8. 保留风电 v2 的风机机型选择、风速外推、ModelChain、等效小时数校准流程。

允许做的本地化改动：

1. 将包导入从 `ele_trading.capacity_planning.resource_simulation` 改为 `investment_estimation.resource_simulation`。
2. 将入口脚本的 YAML 读取改为复用 `investment_estimation.config_loader` 或本地轻量 YAML 读取函数。
3. 将输出字段统一为 `time,pv_kw`、`time,wind_kw` 和 `time,pv_kw,wind_kw`。
4. 将 README 示例导入路径改为 `investment_estimation.resource_simulation`。
5. 为 `.yaml` 补充中文注释，保持现有项目要求。

不应做的改动：

1. 不重写 PV/Wind 物理仿真算法。
2. 不把资源仿真混入 `data_provider/`，`data_provider/` 继续负责读取和校验输入数据。
3. 不把资源仿真混入 `capacity_search/`，容量搜索继续只消费资源时序。
4. 不修改 `mvp_demo.yaml`。
5. 不把 `capacity_planning` 的 planner、dispatch 或 settlement 一并迁移。
6. 不在入口脚本中硬编码生产数据路径。

### 外部依赖和边界

资源仿真模块涉及以下依赖：

```text
pvlib
windpowerlib
pandas
numpy
```

风电 v1 和部分入口脚本需要气象数据获取能力：

```text
investment_estimation.resource_simulation.weather.fetch_weather_open_meteo
```

已确认采用完全独立方案：

1. 新增 `investment_estimation/resource_simulation/weather.py`。
2. 迁移 `fetch_weather_open_meteo()`、`load_weather_csv()`、`save_weather_csv()` 和最小 `ensure_datetime_column()`。
3. `investment_estimation/resource_simulation/` 及其入口脚本不导入 `ele_trading.*`。
4. 默认示例配置优先使用本地 demo 气象 CSV；未配置本地气象时才调用 Open-Meteo。

实现进度：

1. 已完成资源仿真算法本体迁移。
2. 已完成最小气象获取能力迁移。
3. 已补充 `requests` 显式依赖。
4. 已补充独立性检查和 monkeypatch 气象测试，测试不访问真实网络。

### 与当前测算链路的衔接方式

当前 `investment_estimation` 的测算链路读取资源 CSV：

```text
time,pv_kw,wind_kw
```

资源仿真迁移后，建议保持两级输出：

1. 单资源输出：

   ```text
   time,pv_kw
   time,wind_kw
   ```

2. 合并资源输出：

   ```text
   time,pv_kw,wind_kw
   ```

合并逻辑放在 `app/build_resource_profile.py`，只负责文件合并、时间对齐、缺失值检查和字段命名，不参与物理仿真。

这样做的原因：

1. PV 和 Wind 可以独立仿真、独立校验。
2. 当前 `data_provider` 不需要改动。
3. 未来容量搜索仍可以按候选容量比例缩放资源曲线。
4. 资源仿真与投资测算解耦，便于替换真实资源文件。

### 实施步骤

#### 第一步：建立 resource_simulation 模块

待办：

1. 新建 `src/investment_estimation/resource_simulation/`。
2. 迁移 `models.py`、`pv_simulation_v1.py`、`pv_simulation_v2.py`、`wind_simulation_v1.py`、`wind_simulation_v2.py`、`__init__.py`。
3. 仅修改包导入路径和必要的本地依赖。
4. 新建模块级 `README.md`，记录算法逻辑、使用方式和迁移进度。

验证：

```text
PYTHONPATH=src python -m compileall src/investment_estimation/resource_simulation
```

#### 第二步：迁移配置模板

待办：

1. 新增 `resource_pv_simulation_v1.yaml`。
2. 新增 `resource_pv_simulation_v2.yaml`。
3. 新增 `resource_wind_simulation_v1.yaml`。
4. 新增 `resource_wind_simulation_v2.yaml`。
5. 给每个变量补充中文注释。
6. 输出路径统一指向 `src/investment_estimation/dataset/resource_simulation/` 或 `src/investment_estimation/results/resource_simulation/`。

验证：

```text
检查 YAML 字段是否能构造对应 Config 或 Simulator 参数。
```

#### 第三步：迁移入口脚本

待办：

1. 在 `src/investment_estimation/app/` 内新增 PV/Wind v1/v2 运行脚本。
2. 入口脚本只负责：
   - 读取 YAML。
   - 构造配置对象。
   - 调用资源仿真模块。
   - 写出 CSV。
3. 不在入口脚本中写投资测算逻辑。

验证：

```text
PYTHONPATH=src python -m investment_estimation.app.run_pv_simulation_v1 --config src/investment_estimation/configs/resource_pv_simulation_v1.yaml
PYTHONPATH=src python -m investment_estimation.app.run_wind_simulation_v1 --config src/investment_estimation/configs/resource_wind_simulation_v1.yaml
```

说明：如果入口脚本需要联网获取 Open-Meteo 气象数据，应先用本地 `weather_df` 或短周期样例测试；联网验证需要单独确认。

#### 第四步：新增资源合并脚本

待办：

1. 新增 `src/investment_estimation/app/build_resource_profile.py`。
2. 输入 PV CSV 和 Wind CSV。
3. 按 `time` 内连接或外连接后校验缺失值。
4. 输出 `time,pv_kw,wind_kw`。
5. 输出文件路径由 `resource_profile_demo.yaml` 配置。

验证：

```text
PYTHONPATH=src python -m investment_estimation.app.build_resource_profile --config src/investment_estimation/configs/resource_profile_demo.yaml
```

#### 第五步：补充测试

待办：

1. 增加 `tests/test_investment_estimation_resource_simulation.py`。
2. 测试 `SimulationResult` 字段。
3. 测试光伏 `replay` 模式，避免依赖 pvlib 清晰天空计算和网络。
4. 测试风电 `rescale_wind_output_to_target_flh()` 满发小时数校准。
5. 测试资源合并输出字段为 `time,pv_kw,wind_kw`。

验证：

```text
PYTHONPATH=src pytest tests/test_investment_estimation_resource_simulation.py -q
```

#### 第六步：文档同步

待办：

1. 更新 `src/investment_estimation/README.md` 的目录结构和数据输入说明。
2. 更新 `src/investment_estimation/PLAN.md` 中“基础输入层/风光资源建模”的实现进度。
3. 在 `resource_simulation/README.md` 中记录：
   - 已迁移 PV v1/v2。
   - 已迁移 Wind v1/v2。
   - 哪些能力仍依赖外部气象输入。
   - 如何生成当前测算链路可读的资源 CSV。

验证：

```text
README 中应能直接找到资源仿真运行入口、配置文件和输出字段说明。
```

### 建议迁移顺序

建议按以下顺序执行：

1. 先迁移 `models.py` 和 `pv_simulation_v1.py`。
2. 再迁移 `pv_simulation_v2.py`，确保 PV v1 的 `weather_driven` 依赖可用。
3. 再迁移 `wind_simulation_v1.py`，优先验证 `rescale_wind_output_to_target_flh()`。
4. 再迁移 `wind_simulation_v2.py`。
5. 最后迁移入口脚本和配置。

原因：

1. PV v1 的 `clear_sky` 模式最容易本地验证。
2. Wind v1 是原始需求中“年利用小时数反向约束”的关键算法，应优先保留。
3. PV/Wind v2 依赖更强，适合在基础模块稳定后迁移。

### 已确认的问题

1. 已确认 `investment_estimation` 需要完全独立，因此最小 Open-Meteo 气象获取能力已迁移到 `resource_simulation/weather.py`。
2. 已确认第一轮迁移包含 PV v1/v2 和 Wind v1/v2。
3. 已确认算法生成结果默认放 `results/resource_simulation/`，可复用样例气象输入放 `dataset/resource_simulation/`。

## 结论

对 `investment_estimation` 最有用的是 `capacity_planning` 中已经沉淀出的“主链路内核”：

```text
资源仿真
  -> 统一物理合同
  -> canonical/price-aware dispatch
  -> 月度结算
  -> 逐年现金流与 IRR 诊断
  -> IRR 目标型风光储容量搜索
```

最不应该直接复用的是“历史场景包装”和“特定业务场景 planner”：

```text
分布式储能
多节点扫描
聚合 IRR 快扫
optimization demo
```

后续开发应优先把 P0/P1 能力转化为 `investment_estimation` 的独立模块和配置字段，而不是把 `capacity_planning` 作为运行时依赖整体嵌入。
