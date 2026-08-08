# V6 全量内部工程实现计划

> **For Hermes:** 按本计划逐任务执行；每个代码任务坚持 RED → GREEN → 回归，不自动提交。

**Goal:** 完成 V6-0～V6-4 的全部市场无关内部工程闭环；所有真实市场 adapter、正式账单确认、自动申报与生产默认保持 disabled，直至外部规则、数据和审批到位。

**Architecture:** 以 `MarketProfile` 作为模式语义入口，以 `ResourceOperationalPlan` 取代单 BESS/多资源并列结果，以统一的产品级履约和结算输入服务单/双结算；数字孪生仅发布含证据级别的离线信号，不反向依赖 `trading`。

**Tech Stack:** Python 3.11、numpy、pandas、PuLP/CBC、pytest、项目 `.venv`/`uv`。

**授权边界（2026-08-09）：** D-017～D-025 采用推荐方向；无目标省份规则、真实接口、网架、计量、账单或审批时，必须拒绝/降级，绝不模拟为正式能力。

---

## 实施依赖

```text
P0 profile 与输入准入
 ├─ P1 统一资源运行
 │   └─ P2 产品级报价/履约/结算
 ├─ P3 场景/风险候选与经济报告
 └─ P4 数字孪生物理/风险信号
P1 + P2 + P3 + P4 + 外部证据 ─→ P5（本计划只建立 disabled gate）
```

## P0 — V6-0 市场语义完整化

### Task 0.1：将 profile 子策略从骨架变成可验证契约

**Files:**
- Modify: `src/ele_trading/markets/profile.py`
- Test: `tests/markets/test_v6_market_profile.py`

1. RED：为阶段 cutoff、actor、输入 source/quality/revision/permission、价格点与安全结果有效期、信用/资源敞口编写拒绝反例。
2. GREEN：新增不可变输入证据和准入结论；profile 未确认或任一 policy 缺证时结构化拒绝。
3. Verify：`uv run pytest -q tests/markets/test_v6_market_profile.py -W error::RuntimeWarning`。

### Task 0.2：把统一市场输入准入接到编排器边界

**Files:**
- Create: `src/ele_trading/trading/market_admission.py`
- Modify: `src/ele_trading/trading/orchestrator.py`, `src/ele_trading/trading/__init__.py`
- Test: `tests/trading/test_v6_market_admission.py`

1. RED：迟到/无权限/质量不合格的 forecast、award、metering 与安全结果不得进入正式层级；research 只能带明确降级事件。
2. GREEN：在日前、日内、结算前通过 profile 做阶段/数据准入并写入 trace。
3. Verify：定向测试及现有 Bid/Award/场景回归。

### Task 0.3：补规则、数据、网架证据目录的内部契约

**Files:**
- Modify: `src/ele_trading/data_provider/contracts.py`, `src/ele_trading/data_provider/__init__.py`
- Test: `tests/data_provider/test_v6_evidence_catalog.py`

1. RED：缺 owner、许可、available_at、版本或修订历史的 evidence 不可标为可晋级。
2. GREEN：`DataCatalog` 与 entry 只存元数据和访问约束，不内置任何省级参数。
3. Verify：证据目录测试与分层结构守卫。

## P1 — V6-1 统一资源运行主链

### Task 1.1：定义唯一运行、实测、履约与结算输入契约

**Files:**
- Create: `src/ele_trading/operations/resource_runtime.py`
- Modify: `src/ele_trading/operations/__init__.py`
- Test: `tests/operations/test_v6_resource_runtime.py`

1. RED：资源数为 1 的 BESS 适配、资源名/时段不一致、缺实际质量/版本、未映射 Award 分配、fallback 覆盖无效区间。
2. GREEN：实现 `ResourceSchedule`、`ResourceActual`、`CommitmentAllocation`、`PortfolioSettlementInput`、`RuntimeFallback`、`ResourceOperationalPlan`；禁止资源级计划/实测混用。
3. Verify：契约测试与单 BESS 适配金样。

### Task 1.2：将多资源日前求解结果转换为统一计划

**Files:**
- Modify: `src/ele_trading/operations/multi_resource.py`, `src/ele_trading/operations/resource_runtime.py`
- Test: `tests/operations/test_v6_resource_runtime.py`, `tests/operations/test_multi_resource.py`

1. RED：BESS、DR、风光与电网购电均必须进入同一 `PortfolioSettlementInput`。
2. GREEN：建立 `MultiResourceResult → ResourceOperationalPlan` 适配器；单 BESS 也经同一对象暴露。
3. Verify：单资源与多资源能量守恒、计划版本和逐资源合计。

### Task 1.3：用实际资源状态重解日内后缀

**Files:**
- Modify: `src/ele_trading/operations/multi_resource_intraday.py`, `src/ele_trading/operations/resource_runtime.py`
- Test: `tests/operations/test_v6_multi_resource_actuals.py`

1. RED：实际充放、DR 响应、可再生出力、可用性和计量修订与计划不同；冻结前缀不可重写。
2. GREEN：以资源实际状态替代原计划作为后缀初值；fallback 同时输出安全裁剪、有效期和原因。
3. Verify：各资源物理边界、执行前缀逐点不变及失败 fallback。

### Task 1.4：切换编排器的执行、履约和结算唯一来源

**Files:**
- Modify: `src/ele_trading/trading/orchestrator.py`, `src/ele_trading/domain/contracts.py`
- Test: `tests/trading/test_v6_resource_runtime_chain.py`

1. RED：启用 portfolio 时，若任何结算量仍来自 `executed_schedule` 单 BESS 旁路即失败。
2. GREEN：所有执行量、`ResourceMetering` 比较、履约与结算输入只消费 `ResourceOperationalPlan`；旧单 BESS 由一资源适配器保持结果兼容。
3. Verify：单 BESS 金样、含 DR/风光的 portfolio 结算与迟到 Award 反例。

## P2 — V6-2 产品级报价、履约与结算

### Task 2.1：扩展 bid 生命周期与产品义务投影

**Files:**
- Modify: `src/ele_trading/domain/contracts.py`, `src/ele_trading/markets/profile.py`
- Test: `tests/domain/test_v6_bid_lifecycle.py`, `tests/markets/test_v6_commitment_projection.py`

1. RED：无 idempotency、非法 amend/cancel、产品方向无映射、累计 Award 超量、无资源覆盖均拒绝。
2. GREEN：显式 lifecycle/version/idempotency 与 `product × direction → resource constraint` 投影；仍无真实 adapter。
3. Verify：生命周期状态机和所有硬反例。

### Task 2.2：实现价格接受者 plan-only BidOptimizationPolicy

**Files:**
- Create: `src/ele_trading/trading/bid_optimization.py`
- Modify: `src/ele_trading/markets/profile.py`
- Test: `tests/trading/test_v6_bid_optimization.py`

1. RED：最小量/步长/单调量价段、资源重复预留、已成交义务、信用/担保限制失败必须拒绝，不能裁剪后提交。
2. GREEN：只在 confirmed profile + 校准证据下输出候选；无证据始终 `plan_only`，不调用提交接口。
3. Verify：资源跨能量/备用/DR 的不重复预留及 deterministic replay。

### Task 2.3：统一产品级结算输入并适配单/双结算

**Files:**
- Create: `src/ele_trading/markets/settlement_input.py`
- Modify: `src/ele_trading/markets/single_settlement/*`, `src/ele_trading/markets/dual_settlement/*`, `src/ele_trading/trading/orchestrator.py`
- Test: `tests/markets/test_v6_settlement_input.py`, `tests/trading/test_v6_bid_award_settlement_chain.py`

1. RED：双结算缺必需规则、物理合同未覆盖、DR/辅助服务无 rule mapping 必须失败，不能回落到单结算。
2. GREEN：单/双结算仅接受统一的资源实际、Award、合同、规则和账单项映射输入；未确认 profile 不生成正式确认账单。
3. Verify：可重放账单、confirmed/synthetic 隔离、单 BESS 兼容回归。

## P3 — V6-3 场景、风险与经济候选

### Task 3.1：接 archived-vintage 与候选层级的场景证据

**Files:**
- Modify: `src/ele_trading/trading/scenario_admission.py`, `src/ele_trading/data_provider/contracts.py`
- Test: `tests/trading/test_v6_scenario_vintage_admission.py`

1. RED：无 archived reference、保留期泄漏、不可复现 scenario 在 candidate/shadow/production 均失败。
2. GREEN：把 evidence catalog 的历史输入绑定入 admission event；研究层保留降级。
3. Verify：前视反例与同输入 deterministic replay。

### Task 3.2：将两阶段 CVaR 接为候选报价而非默认

**Files:**
- Modify: `src/ele_trading/optimization/two_stage_cvar.py`, `src/ele_trading/trading/bid_optimization.py`
- Test: `tests/trading/test_v6_two_stage_candidate.py`

1. RED：缺 profile 结算/偏差规则、场景拒绝或无校准时不可求解/晋级。
2. GREEN：只生成带规则、输入、模型、求解器版本的 challenger candidate。
3. Verify：尾部损失、规则缺失和保留期经济门。

### Task 3.3：风险调整经济报告与晋级 gate

**Files:**
- Create: `src/ele_trading/backtest/economic_evaluation.py`
- Test: `tests/backtest/test_v6_economic_evaluation.py`

1. RED：仅 MAE 改善、账单不确认、统计不稳健或约束违约均不可晋级。
2. GREEN：输出留出期净结算、CVaR、违约、账单差异和 evidence level。
3. Verify：champion/challenger 反例。

## P4 — V6-4 离线数字孪生物理与风险信号

### Task 4.1：补全 SCUC 机组、备用与可再生物理约束

**Files:**
- Modify: `src/ele_trading/market_simulation/scuc.py`, `src/ele_trading/market_simulation/grid/contracts.py`
- Test: `tests/market_simulation/test_v6_scuc_constraints.py`

1. RED：最小停机、停机成本、初态跨窗、备用、可再生、必开必停和不可行处置反例。
2. GREEN：在 MILP 内明确建模，记录松弛/短缺而非伪造可行解。
3. Verify：小规模解析算例与 MIP gap/超时证据。

### Task 4.2：分段报价出清、N-1 反馈与安全接口

**Files:**
- Modify: `src/ele_trading/market_simulation/sced.py`, `src/ele_trading/market_simulation/scuc.py`, `src/ele_trading/market_simulation/contingency.py`, `src/ele_trading/market_simulation/bidding.py`
- Test: `tests/market_simulation/test_v6_offer_stack_dispatch.py`, `tests/market_simulation/test_v6_n1_feedback.py`

1. RED：分段报价退化为单边际成本、N-1 失败仍提取价格、无授权安全结果仍发布信号。
2. GREEN：消费 `OfferStack`；以 profile 选择的 N-1 反馈约束/拒绝；AC/电压/稳定结果只定义外部证据接口。
3. Verify：LMP 对偶仅来自固定承诺的连续 SCED，且安全失败阻断发布。

### Task 4.3：MarketSignalModel 与竞争风险输出

**Files:**
- Create: `src/ele_trading/market_simulation/signals.py`
- Modify: `src/ele_trading/market_simulation/__init__.py`
- Test: `tests/market_simulation/test_v6_market_signals.py`

1. RED：未校准/无安全授权/无输入版本不得发布；不能把风险信号标为监管结论。
2. GREEN：输出版本化价格分布、阻塞/备用风险、CRn/HHI、RSI/PSI 类指标与异常线索，固定为 `offline/challenger`。
3. Verify：发布 gate 与输入可追溯性。

## P5 — disabled 的真实证据/生产 gate

### Task 5.1：建立无法绕过的外部晋级接口

**Files:**
- Create: `src/ele_trading/trading/production_gate.py`
- Test: `tests/trading/test_v6_production_gate.py`

1. RED：缺省份规则、正式账单、影子窗口、漂移/回滚证据、人工 approval 任一项时不得输出 production eligible。
2. GREEN：只存证据引用和 gate 状态；不实现真实 connector，不伪造数据。
3. Verify：所有缺项组合均结构化拒绝。

## 统一验收

每任务执行：

```bash
env -u PYTHONPATH UV_CACHE_DIR=.uv_cache uv run python -m pytest -q <target-tests> -W error::RuntimeWarning
env -u PYTHONPATH UV_CACHE_DIR=.uv_cache uv run python -m compileall -q src tests
git diff --check
```

每个阶段完成后执行：

```bash
env -u PYTHONPATH UV_CACHE_DIR=.uv_cache uv run pytest -q -W error::RuntimeWarning
```

**Hard rules:** 不自动提交；不得修改 `.env`、CI/CD、密钥或系统配置；不得在无外部资料时把任何 output 标为正式、confirmed 或 production。
