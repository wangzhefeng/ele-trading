# DR 联合优化与主链路接入改造方案

> 状态：已实现（2026-07-31）。6 个 Phase 全部完成，50 个交易链测试通过。
> 背景：当前 DR 仅有 `trading/dr_allocator.py` 的事后经济评估，未接入 orchestrator/backtest，
> `dr_adjustment` 在日前/日内目标函数中是常数项（不影响最优解），机会成本口径不准，
> 响应量无 SOC 可行性校验，决策为二值无边际优化。

## 目标

1. DR 参与决策与充放电计划**联合优化**（响应量作为决策变量，机会成本内生）。
2. DR 接入主链路：orchestrator 内部完成 决策 → 计划 → 履约结算，`dr_adjustment` 不再外部传入。
3. `dr_enabled=False` 时行为与现状完全一致（回归基线不变）。

## 总体设计

**DR 语义**（沿用现有代码假设，规则确认前不再扩展）：窗口 `W = [dr_window_start, dr_window_end)` 内
"增量放电"获得补偿 `dr_compensation_per_mwh`（元/MWh）；申报后履约缺口按 `dr_penalty_per_mwh` 罚款；
申报门槛 `dr_minimum_response_mwh`。

**核心模型（两阶段求解，均在 `solve_day_ahead_operational` 内部完成）**：

- Pass A：现有模型原样求解（不加 DR 项），得窗口基线放电能量
  `Q0 = Σ_{t∈W} p_discharge[t] * dt`。这就是"本来就会放的电"，补偿不应覆盖它。
- Pass B：同一模型追加 DR 项后重解：
  - 新变量：`y ∈ {0,1}`（是否申报）、`inc ≥ 0`（窗口增量放电能量，MWh）。
  - 约束：
    - `inc ≥ Σ_{t∈W} p_discharge[t] * dt − Q0`（增量相对基线，线性）
    - `inc ≤ M * y`，`M = (window 长度) * p_dis_max * dt`（未申报则增量为 0）
    - `inc ≥ y * dr_minimum_response_mwh`（申报则必须达门槛；能量不足时 y=0，天然可行）
  - 目标函数追加：`- dr_compensation_per_mwh * inc`（补偿为负成本）。
  - 退化**不**单独加项：DR 引起的放电吞吐已包含在现有 `degradation_cost`（按吞吐线性）中，避免双计。
- 为什么两阶段而不是单 MILP：增量基线 Q0 依赖"无 DR 最优解"，单模型无法表达（双层级问题）。
  两次 CBC 求解在 96 步规模下成本可忽略。`dr_baseline_mode="fixed"` 时跳过 Pass A，
  直接用 `Q0 = dr_baseline_mwh`（保留给规则确认后的口径切换）。

**为什么不动 `optimization/bess_model.py` 内核**：响应就是放电本身，复用既有 `p_discharge`
变量和 SOC 递推，可行性（SOC/能量/功率/末端 SOC）天然满足，无需给内核加 extra-flow 通道。

**履约结算（新函数 `compute_dr_settlement` in settlement_mengxi.py）**：

- 输入：申报量 `committed_qty`、实际执行计划、基线 Q0、config。
- 实际增量 `inc_actual = max(0, Σ_{t∈W} p_discharge_exec[t]*dt − Q0)`。
- `compensation = dr_compensation_per_mwh * min(inc_actual, committed_qty)`（超出申报不补，保守口径）。
- `shortfall = max(0, committed_qty − inc_actual)`；`penalty = dr_penalty_per_mwh * shortfall`。
- `dr_adjustment = penalty − compensation`（正值为成本，与 SettlementReport 现有符号约定一致）。

**日内滚动**：日内重优化继续携带 DR 激励项（窗口未执行部分）：把剩余窗口与已执行窗口放电量传入，
约束 `Σ_{剩余∩W} p_dis*dt ≥ committed_qty − 已执行窗口放电能量`（履约承诺作为硬约束）；
若该约束导致不可解，走既有 `_clip_fallback` 回退路径，缺口由结算罚金体现——不新增异常分支。
窗口已完全执行后日内不再带 DR 项。

## 改动点（按 Phase）

### Phase 0 — 配置与契约（纯增量，不改行为）

- `trading/contracts.py`
  - `MarketConfig` 新增：`dr_enabled: bool = False`、`dr_baseline_mode: str = "auto"`（`"auto"|"fixed"`）、
    `dr_baseline_mwh: float = 0.0`。
  - 新增 `DRCommitment` dataclass：`committed_qty / window / baseline_qty / expected_compensation /
    expected_incremental / participate / reject_reason`。
  - `OperationalPlan` 新增字段 `dr_commitment: DRCommitment | None = None`（slots dataclass 加默认值，向后兼容）。
- `configs/trading/market_mengxi.yaml`：`dr:` 节新增 `dr_enabled: false`、`dr_baseline_mode: auto`、`dr_baseline_mwh: 0.0`
  （均带 `# TODO(rule-confirm)` 注释口径说明）。
- `trading/config_loader.py`：映射 3 个新字段；校验 `dr_baseline_mode ∈ {auto, fixed}`、
  `dr_baseline_mwh ≥ 0`、`fixed 模式必须 dr_baseline_mwh > 0`。
- `configs/README.md`：同步新字段说明。
- 测试：config_loader 新字段映射/校验用例（沿用 `tests/trading/` 现有风格）。

### Phase 1 — 日前联合优化内核

- `trading/day_ahead_coupled.py`
  - `solve_day_ahead_operational(...)` 新增关键字参数 `dr_enabled: bool | None = None`
    （`None` 时取 `config.dr_enabled`，便于 oracle/backtest 显式控制）。
  - `dr_enabled=False`：走现有路径，**目标函数移除 `dr_adjustment` 常数项**（见"破坏性行为变更"）。
  - `dr_enabled=True`：Pass A（现有模型）→ Pass B（加 DR 变量/约束/补偿项）；
    场景 CVaR 分支中 DR 项只加进 `expected_cost` 基线分支（补偿是确定性的，不进场景成本），
    CVaR 辅助变量口径不变。
  - `OperationalPlan.dr_commitment` 填决策证据；`DecisionTrace.objective_components` 增加
    `dr_compensation` 项；`model_versions` 标记 `"single-settlement-operational-v2-dr"`。
  - **移除** `dr_adjustment` 入参（日内 `_clip_fallback` 同步移除）。
- 测试（TDD，先写后实现）：
  1. 高补偿（5000 元/MWh）→ 参与，窗口放电能量 ≥ 门槛，`inc > 0`。
  2. 低补偿（0 元）→ 不参与，计划与 `dr_enabled=False` 逐点一致。
  3. SOC/能量受限场景（小容量电池）→ 增量受能量约束，`inc` 不超过可用能量；达不到门槛时 y=0。
  4. `dr_enabled=False` 与当前 main 分支输出一致（回归）。
  5. 场景 CVaR 开启时求解成功且 `dr_commitment` 一致。

### Phase 2 — 履约结算

- `trading/settlement_mengxi.py`：新增 `compute_dr_settlement(...)`（签名见总体设计）。
  `build_settlement_report` 签名不变（`dr_adjustment` 仍作为标量项传入，但由调用方用新函数算出）。
- 测试：全额履约 penalty=0；部分履约 penalty=shortfall×rate；inc_actual 超申报时补偿封顶；
  未申报（commitment=None）返回 0。

### Phase 3 — 日内滚动履约约束

- `trading/intraday_rolling.py`
  - `solve_intraday_rolling` 新增 `dr_commitment: DRCommitment | None = None` 与
    `executed_window_discharge_mwh: float = 0.0`。
  - 剩余窗口与 W 有交集且 commitment 存在：向 `solve_day_ahead_operational` 传履约下限参数
    （Phase 1 需预留 `dr_min_window_discharge_mwh: float | None = None` 入参，加线性约束
    `Σ_{剩余∩W} p_dis*dt ≥ 该值`）。
  - 不可解 → 既有 `_clip_fallback` 回退（缺口进结算罚金）。
- 测试：承诺 2 MWh、已执行 0.5 MWh → 剩余窗口放电 ≥ 1.5 MWh；能量不足 → fallback_used=True 且不抛异常。

### Phase 4 — orchestrator 接入

- `trading/orchestrator.py`
  - `run()` **移除 `dr_adjustment` 外部参数**；新增返回字段 `TradingPipelineResult.dr_commitment`。
  - 日前：透传 `dr_enabled=None`（随 config）。
  - 日内：传入 `day_ahead.dr_commitment` 与已执行窗口放电能量（从 `executed_prefix` 算）。
  - 结算：`dr_adjustment = compute_dr_settlement(day_ahead.dr_commitment, executed_schedule, ...)`，
    替换现在的外部透传。
- 测试：orchestrator 端到端（dr_enabled=True）跑通且 settlement.dr_adjustment 与手工核算一致；
  dr_enabled=False 时 dr_adjustment=0。

### Phase 5 — dr_allocator 退役与入口/文档

- `trading/dr_allocator.py`：从主链路退役。二选一（**推荐 a**）：
  - a) 删除文件，相关参数驱动测试迁移到 Phase 1 测试；`app/trading/run_dr.py` 改为演示
    `dr_enabled=True` 的主链路（日前计划 + commitment + 模拟履约结算）。
  - b) 保留为独立"事后评估工具"，docstring 标注不参与主链路。
- `trading/__init__.py`：导出 `DRCommitment`（删 `DRDecision` 若选 a）。
- `trading/README.md`：模块表更新（dr_allocator 行替换为 DR 联合优化说明）。
- `MEMORY.md` / 根 `README.md`：架构摘要同步一行。
- 复跑 30 天 walk-forward 回归基线（`app/trading/run_backtest.py`，dr_enabled=False），
  更新 `results/trading/backtest/v2_baseline/`。

## 影响面

| 类别 | 内容 |
|---|---|
| 修改文件 | contracts.py, day_ahead_coupled.py, intraday_rolling.py, orchestrator.py, settlement_mengxi.py, config_loader.py, trading/__init__.py, configs/trading/market_mengxi.yaml, configs/README.md, app/trading/run_dr.py, trading/README.md, MEMORY.md, README.md |
| 删除文件 | dr_allocator.py（推荐方案 a）+ 其专属测试段 |
| 新增测试 | tests/trading/ 内 DR 联合优化/结算/日内/orchestrator 用例（估 15+ 个） |
| 不动 | optimization/ 全部内核（bess_model/risk/solver）、scenario/、forecasting/、月度/中长期模块、todo/ 封存区 |
| 求解器 | 引入 1 个 binary → MILP；CBC 支持；`solver_mip_gap` 配置已有；求解时间约 ×2（两阶段） |

**破坏性行为变更（需要 wangzf 知晓）**：

1. `orchestrator.run()` 删除 `dr_adjustment` 参数 → 外部调用方（若有）编译期报错，易发现。
   当前 `run_pipeline.py` / `backtest.py` 均未传该参数，实际影响为零。
2. `dr_adjustment` 常数项从日前/日内目标函数移除：`dr_enabled=False` 时**最优解不变**
   （常数不影响 argmin），但 `expected_cost` 数值会比旧版小 `dr_adjustment` 的量；
   由于全链路当前都传 0.0，30 天基线数值应完全一致——回归复跑会验证这一点。
3. `SettlementReport.dr_adjustment` 语义从"外部输入透传"变为"履约结算结果"，字段保留。

## 风险与开放问题

1. **规则未确认**：补偿/罚金/基线口径全部 `TODO(rule-confirm)`。本方案按"增量放电补偿 + 缺口罚金"
   假设建模；蒙西实际规则（基线负荷 CBL 口径、聚合商分成、考核豁免）确认后可能需调整
   `compute_dr_settlement` 与基线模式——已通过 config 隔离，模型结构不变。
2. **两阶段基线的博弈空间**：Pass A 基线 Q0 是"无 DR 激励时的最优"，理论上存在
   "Pass A 故意少放以抬基线"的激励，但单主体自用储能无申报分离，该风险可忽略；规则确认后如有
   官方基线口径，切 `dr_baseline_mode="fixed"`。
3. **日内履约硬约束 vs 罚金**：方案选择"硬约束 + 不可解回退"，避免日内主动违约；
   若规则允许"主动付罚款违约更优"，需改为软约束（罚金进日内目标）——留待规则确认。
4. **退化口径**：全项目退化目前为吞吐线性（orchestrator 结算侧），AGENTS.md 要求的雨流口径
   （`compute_rainflow_degradation`）是独立改造项，本方案不处理，DR 吞吐沿用现有线性口径不双计。

## 验证

- 每 Phase：`uv run pytest tests/trading -q` 全绿。
- Phase 5 末：`uv run pytest -q` 全项目绿 + 复跑 `app/trading/run_backtest.py`，
  dr_enabled=False 的 30 天基线与现基线逐日数值一致。
