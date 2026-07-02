# 根因层建设设计：结构化电价 + 价格感知调度 + 财务深化 + 目标函数升级

> **provenance**
> - 构建代理：Claude Code（主会话）
> - 构建日期：2026-07-02
> - 上游依据：`src/ele_trading/capacity_planning/PLAN.md`（V4 第一阶段已落地的 canonical dispatch + monthly settlement 主链）+ 对当前源码的第一性原理 gap 分析
> - 范围：仅覆盖"根因层"三项（电价结构化+价格感知调度 / 财务深化 / PPA 反求+目标函数）。输入层、边界条件层、治理项（共享成本合同全量铺开、PV 自动拉天气、负荷电费单解析等）不在本 spec 内，后续独立 spec。
> - 与 PLAN.md 的关系：本 spec 是 PLAN.md V4 之后的**下一阶段能力建设设计**，不修改 V4 已确认的架构主线（canonical 唯一、月度结算是财务来源、行为测试优先、adapter 必须有消费者）。V4 的"延后项"中凡被本 spec 纳入的（逐年现金流、电价结构、PPA 反求），在此正式立项。

---

## 1. 背景与问题（gap 摘要）

V4 第一阶段已建成可追溯主链 `canonical_dispatch → settle_monthly → evaluate_levelized_irr`，物理守恒与 `年=Σ月` 不变量有测试强制。但第一性原理 gap 分析指出三条根因级缺口：

1. **仿真是能量驱动，不是价值驱动**：`canonical_dispatch` 的 `surplus→充 / deficit→放` 完全不看电价；`settlement.py` 电价为 4 个标量，无尖峰平谷时序。储能的"电价提升优势"既没有被求解，也没有被计价。
2. **财务太薄，IRR 不可融资**：`evaluate_levelized_irr` 为等额永续年现金流，unlevered、税前、无更换、无残值；已实现的 `evaluate_degraded_irr` 未接入主链。
3. **倒推/反馈不全**：只有"目标业主电价→反推 PPA"，缺"PPA 锁定→正向求 IRR"；搜索目标是"最低 CAPEX 满足目标 IRR"，与"IRR/节费整体性提升"语义不符。

本 spec 解决以上三项。

---

## 2. 设计决策（四个已确认叉路）

| 叉路 | 决策 | 理由 |
|---|---|---|
| 充电边界（Q1） | **两模式 config 切换**：`SELF_CONSUMPTION`（仅 RE 富余充电，放电按电价优先）+ `ARBITRAGE`（允许电网充电套利） | 真实项目按市场政策选；多数"绿电配储禁向网充电"场景用前者，套利场景用后者 |
| 求解方式（Q2） | **启发式主链 + MILP 单点精修** | 87k 级网格搜索必须保性能→启发式；sizing 定型后单点调度精修走已有 PuLP/cvxpy MILP |
| 财务深度（Q3） | **项目IRR + 逐年 + 更换 + 残值 + 简税，预留权益接口** | 覆盖目标明列的更换/残值/运维；权益IRR（融资/债务）留后续，dataclass 预留字段 |
| 搜索目标（Q4） | **多目标可切 + 保留约束模式** | `MAXIMIZE_IRR` / `MAXIMIZE_SAVINGS_RATIO` / `MIN_CAPEX_AT_TARGET_IRR` 三选，对应"整体性提升/倒推反馈" |

---

## 3. 设计总原则

**保留 `canonical_dispatch` 作为"物理守恒 oracle"不动；新增"价格感知调度"为其兄弟；两者产出同一个 `DispatchSimulationResult` 合同。**

- canonical 继续承担能量守恒/SOC 递推的**验证基线**（`年=Σ月` 测试不破坏），不作价值上游。
- 价格感知调度承担**价值最大化**，settlement 层无感消费同一合同。
- "电价提升优势"= 价格感知结算 − canonical 结算（同 TOU 价、不同调度策略），天然可证伪、可审计。
- 符合 PLAN.md V2/V4 既定准则：异范围模型只对齐输入合同与物理常数，不强求数值一致。

---

## 4. Item 1：结构化电价 + 价格感知调度

### 4.1 结构化 `Tariff` 合同（新文件 `tariff.py`）

把 `settle_monthly` 现有 4 个标量升级为结构化 tariff。市场规则（尖峰平谷小时映射）作为**数据/配置**，不硬编码省份。

```python
@dataclass
class TouTier:
    tier: str            # "sharp"|"peak"|"flat"|"valley"|"deep_valley"
    price_yuan_per_kwh: float

@dataclass
class Tariff:
    timestamps: pd.DatetimeIndex
    grid_buy_price_yuan_per_kwh: np.ndarray   # 逐时步购电价（TOU 曲线或扁平）
    green_price_yuan_per_kwh: float | np.ndarray  # 绿电结算价（通常扁平）
    demand_charge: DemandChargeConfig | None      # 由现有 DistributedBESSDemandChargeConfig 等收敛为统一字段（见下）
    td_price_yuan_per_kwh: float = 0.0            # 输配电价（可选）
    surcharges_yuan_per_kwh: float = 0.0          # 政府基金及附加（可选）

    @classmethod
    def from_flat(cls, timestamps, *, grid_buy_price, green_price,
                  demand_charge=None, ...) -> "Tariff": ...
    def validate(self, length: int) -> None: ...   # 长度/非负/同轴
```

- TOU 分档由调用方/配置把 `timestamps` 映射成 `grid_buy_price_yuan_per_kwh` 数组后传入；本合同只消费数组，不内置分时规则。
- `DemandChargeConfig`：当前 demand-charge 配置散落在 `DistributedBESSDemandChargeConfig`（`interfaces.py:161-163`，`point_max`/`sliding_window`）与 `optimization/interfaces.py:340` 的模式枚举中；本轮收敛为一个统一字段（复用语义，不重发明），作为 `Tariff` 的成员。
- `from_flat()` 作为旧标量签名的 adapter，保证现有调用零改动。

### 4.2 价格感知调度（新文件 `models/price_aware_dispatch.py`）

```python
class DispatchMode(str, Enum):
    SELF_CONSUMPTION = "self_consumption"
    ARBITRAGE = "arbitrage"

def price_aware_dispatch(
    *, load_kw, generation_kw, bess: BESSPhysicsContract, bess_capacity_kwh,
    price_yuan_per_kwh: np.ndarray, timestamps, dt_hours,
    mode: DispatchMode = DispatchMode.SELF_CONSUMPTION,
    switch_gap_steps: int = 0,
) -> DispatchSimulationResult   # 复用 canonical 的合同
```

- **物理常数**：从 `BESSPhysicsContract` 读取（继承 V4 单一来源）。
- **SELF_CONSUMPTION 模式**（2-pass 启发式，O(n log n)）：
  - Pass-1 充电：与 canonical 同源（仅 RE surplus 充电），受 C-rate/SOC 上限/切换间隔约束。
  - Pass-2 放电：把可放电能量按"价格最高的 deficit 小时"优先分配，受 C-rate/SOC 下限约束。相对 canonical 的"按时序 deficit 放电"，价值集中在峰时。
- **ARBITRAGE 模式**（价格阈值启发式）：
  - 允许电网充电：当 `price < discharge_price_threshold`（阈值由 `eta_roundtrip × 预期峰价` 反推）且 SOC 有空间时向网充电。
  - 峰时放电。`grid_charge_kwh` 单独追踪（结算/合规要用）。
  - **诚实标注**：启发式不做多小时协同最优；sizing 定型后的单点精修走 4.4 的 MILP 路径。
- **输出**：复用 `DispatchSimulationResult`；`metadata` 增 `dispatch_mode`、`grid_charge_kwh`（见 §7 合同演进）。

### 4.3 settlement 消费 TOU + "电价提升优势" KPI

- `settle_monthly` 新增 `tariff: Tariff | None` 参数；传 tariff 时电费按**逐时步 TOU 价 × 电量**计，月度自然汇总；不传时走旧标量 adapter。
- 需量电费不变（仍来自 `net_load_kw` 月内峰值，不反推）。
- 新增一等 KPI `price_advantage_yuan`（月度 + 年度）：定义为同 TOU 价下、价格感知调度相对 canonical 调度的结算增益（放电落在峰时创造的额外价值）。年度值由月度汇总。
- 套利模式另报 `arbitrage_revenue_yuan`（谷买峰卖的套利毛收益），与绿电 PPA 收入分列。

### 4.4 MILP 单点精修路径

- sizing 经网格搜索（启发式）定型后，对该单点候选调用已有 PuLP/cvxpy MILP（`bess_capacity_economic_planner` / `cvxp_bess_dispatch`）做调度精修，再回灌 settlement。
- 关系声明（写入 PLAN.md）：MILP 是 sizing 定型后的**单点精修器**，不是 87k 组合的上游；其 sizing 结果仍须经价格感知/canonical 复算结算。

---

## 5. Item 2：财务模型深化（项目IRR + 逐年 + 更换 + 残值 + 简税）

### 5.1 `ProjectCashflowResult` + `build_project_cashflows()`（扩展 `irr_finance.py`）

```python
@dataclass
class ReplacementEvent:
    year: int            # 1-based，如 10
    cost_yuan: float

@dataclass
class ProjectCashflowResult:
    capex_yuan: float
    annual_revenues_yuan: list[float]
    annual_opexes_yuan: list[float]
    annual_taxes_yuan: list[float]
    replacement_events_yuan: list[float]   # 逐年，无则 0
    salvage_yuan: float
    cashflows: list[float]
    irr: float | None
    npv_yuan: float | None = None          # 可选，给定折现率
    payback_year: float | None = None      # 可选
    # 权益层预留（Q3），本轮不填实现
    debt_service_yuan: list[float] | None = None
    equity_irr: float | None = None

def build_project_cashflows(
    *, capex_yuan, annual_revenue_y1_yuan, annual_opex_y1_yuan, life_years,
    capacity_degradation: list[float] | None = None,   # 来自 evaluate_degraded_irr 的衰减曲线
    tax_rate: float = 0.0, depreciation_years: int | None = None,
    replacements: list[ReplacementEvent] | None = None,
    salvage_ratio: float = 0.0,
    discount_rate: float | None = None,   # 给定时计算 NPV/回收期，否则两者留 None
) -> ProjectCashflowResult
```

现金流组装：
```
cashflows = [-capex] + [(rev_y - opex_y - tax_y - replacement_y) for y in 1..N] + [+salvage]
IRR = compute_irr(cashflows)   # 复用 evaluation.metrics.compute_irr
```

- **逐年收入/OPEX 衰减**：由 `capacity_degradation` 曲线缩放（默认无衰减时退化为等额）。
- **税**：`tax_y = max(0, (rev_y - opex_y - depreciation_y) × tax_rate)`，直线折旧（`depreciation_years` 默认 = `life_years`）。简化口径，标注后续细化。
- **储能更换**：第 R 年注入 `replacement.cost_yuan`（负现金流）。
- **残值**：期末 `+salvage_ratio × capex`。
- **权益层**：`debt_service` / `equity_irr` 为 Optional，本轮不实现，仅占位。
- **NPV / 回收期**：仅当 `discount_rate` 给定时计算并填充 `npv_yuan` / `payback_year`，否则留 `None`（不阻塞 IRR 主链）。

### 5.2 与 `evaluate_degraded_irr` 的关系

`evaluate_degraded_irr`（当前仅 `multi_node_scanner` 调用）的线性容量衰减曲线，作为 `build_project_cashflows(capacity_degradation=...)` 的输入来源。主 IRR 链调用 `build_project_cashflows`，消除"已实现但未接入主链"的死代码风险。`evaluate_levelized_irr` 保留为"无税/无更换/无残值/无衰减"的扁平 baseline，供回归对比与旧测试。

### 5.3 `CostInputs` 共享合同（最小引入）

为给财务层干净输入，引入最小 `CostInputs` dataclass（CAPEX 单位因子 + OPEX 统一口径），先在 IRR planner 内部使用，不强制其他 planner 立即迁移。收敛目前 `annual_opex_ratio` / `o_and_m_per_kwh` / `opex_per_cycle_kwh` 三种散落口径的第一步。

---

## 6. Item 3：PPA 锁定→反求 IRR + 目标函数升级

### 6.1 `SearchObjective` 枚举

```python
class SearchObjective(str, Enum):
    MIN_CAPEX_AT_TARGET_IRR = "min_capex_at_target_irr"   # 现有行为
    MAXIMIZE_IRR = "maximize_irr"
    MAXIMIZE_SAVINGS_RATIO = "maximize_savings_ratio"
```

best 候选选择：
- `MIN_CAPEX_AT_TARGET_IRR`：`min(candidates, key=(capex, irr_gap))`（现有）。
- `MAXIMIZE_IRR`：`max(candidates, key=irr)`（受物理/PPA 约束）。
- `MAXIMIZE_SAVINGS_RATIO`：`max(candidates, key=savings_ratio)`。
诊断表对所有目标保留。

### 6.2 PPA 锁定→正向求 IRR

`WindPVBESSIRRPlanConfig` 新增 `ppa_price_locked: float | None = None`：
- 置位时**跳过 `backsolve_green_ppa_price`**，直接以锁定 PPA 价（+ `green_price_adder`）→ settlement → `build_project_cashflows` → **正向算 IRR 并报告**。
- 这正是目标中"PPA 价格锁定后反向求解 IRR"的缺口补齐：本质是"不再反推价格，改为给定价格正向算 IRR"。
- 与 `target_owner_price` 反推模式互斥（config 校验：二者不可同时显式指定）。

---

## 7. 合同演进与向后兼容

| 合同 | 变更 | 兼容策略 |
|---|---|---|
| `DispatchSimulationResult` | 新增 `grid_charge_kwh: np.ndarray` 字段 | `canonical_dispatch` 输出全零数组；现有测试断言不受影响（新增字段） |
| `MonthlySettlementResult` | 新增 `price_advantage_yuan`、`arbitrage_revenue_yuan`（可选，默认 0） | 旧消费方不读即不影响 |
| `settle_monthly` 签名 | 新增 `tariff: Tariff \| None = None` | 不传时走旧标量路径 |
| `WindPVBESSIRRPlanConfig` | 新增 `objective`、`ppa_price_locked`、`dispatch_mode`、`tax_rate`、`replacement`、`salvage_ratio` | 全部带默认值，旧 config 零改动 |
| `evaluate_levelized_irr` | 保留不动 | 作为扁平 baseline |
| 外部入口 / `__all__` / CSV 英文字段 / 中文表头 | 不动 | V1/V4 稳定性规则 |

新增对外导出：`Tariff`、`price_aware_dispatch`、`DispatchMode`、`build_project_cashflows`、`ProjectCashflowResult`、`SearchObjective`（经 `__init__` 且不触发 cvxpy）。

---

## 8. 测试策略（test-first，行为优先）

继承 V4 行为优先验证。每个新增能力先写失败测试：

| 测试 | 断言 |
|---|---|
| `Tariff.from_flat` 等价 | 扁平 tariff 结算 == 旧标量结算 |
| TOU 逐时计费 | `Σ(price[t]×kwh[t])` 与月度汇总一致；`年=Σ月` |
| SELF_CONSUMPTION 守恒 | 价格感知调度的 `generation=used+curtail+储能净变化`、`load=green+grid_buy`，与 canonical 同守恒 |
| SELF_CONSUMPTION 价值 | 放电集中在峰时；`price_advantage_yuan ≥ 0` |
| ARBITRAGE 套利 | `grid_charge_kwh>0`；套利收益在峰谷价差 × eta 内合理 |
| 现金流退化 | 无税/无更换/无残值/无衰减时 `build_project_cashflows.irr ≈ evaluate_levelized_irr.irr` |
| 更换事件 | 第 R 年更换使 IRR 下降方向正确 |
| 残值 | 期末残值使 IRR 上升方向正确 |
| 税 | 税率>0 使 IRR 下降 |
| 目标函数 | `MAXIMIZE_IRR` 选出最高 IRR 候选；`MAXIMIZE_SAVINGS_RATIO` 选出最高节费 |
| PPA 锁定正向 | `ppa_price_locked` 置位时不调用 backsolve，IRR 由正向现金流得出 |
| cvxpy 缺失 | 新增导出不触发 cvxpy 导入（继承 V4 P0-b） |
| golden 回归 | 现有 `test_wind_pv_bess_irr_planner` / `test_capacity_planning_v4_phase1` 通过（数值因 bug 修正变化时须解释） |

---

## 9. 建设序列

按依赖 + 价值排序，每步独立可验证：

| # | 步骤 | 产出 | 验证 |
|---|---|---|---|
| 1 | `Tariff` 合同 + `settle_monthly` 消费 TOU（§4.1/4.3） | `tariff.py` + settlement 分支 | 扁平等价 + TOU 计费 + `年=Σ月` |
| 2 | 价格感知调度 SELF_CONSUMPTION 模式（§4.2） | `price_aware_dispatch.py` | 守恒与 canonical 一致 + 放电集中峰时 |
| 3 | ARBITRAGE 模式 + 电网充电（§4.2） | 同上扩展 | `grid_charge_kwh` 追踪 + 套利收益合理 |
| 4 | "电价提升优势" KPI（§4.3） | settlement 新字段 | vs canonical 基线，数值可解释 |
| 5 | `ProjectCashflowResult` + 更换/残值/简税（§5） | `irr_finance.py` 扩展 | 退化/更换/残值/税 方向测试 |
| 6 | 主链切新现金流 + `CostInputs`（§5.3） | planner 内部接入 | 旧测试通过（数值变化解释） |
| 7 | `SearchObjective` 多目标（§6.1） | config + 选择逻辑 | 三目标各选出正确候选 |
| 8 | PPA 锁定→正向 IRR（§6.2） | config + 正向路径 | 锁定价正向 IRR，不反推 |
| 9 | MILP 单点精修接线（§4.4） | sizing 定型后精修 | 精修后结算 ≥ 启发式 |
| 10 | 端到端集成 + golden 输出 | 全链贯通 | 价值感知+TOU+富现金流+目标函数 |

> 步骤 1–4 为"价值半边"闭环（Item 1），可独立交付一个可用里程碑；5–6 为财务可信度（Item 2）；7–8 为倒推/反馈（Item 3）；9–10 收口。

---

## 10. 范围外 / 延后

- **权益 IRR / 融资 / 债务**：本轮仅预留接口（Q3），下一阶段实现。
- **省级真实分时电价表解析器**：本轮只建结构化 `Tariff` 合同，TOU 小时映射作为配置/数据由调用方提供；真实电价表解析后续独立做。
- **负荷电费单解析 / 生产 data_provider / PV 自动拉天气 / 特殊负荷分层 / 风光储比例与可开发容量约束**：属输入层与边界层，后续独立 spec。
- **`CostInputs` 全量铺开到所有 planner**：本轮仅 IRR planner 内部最小引入。
- **目录搬迁（`inputs/`、`dispatch/`、`planners/`）**：不做，继承 V4 规则。

---

## 11. 假设与待决

- **A1**：`price_advantage_yuan` 的精确定义（"vs canonical 同 TOU 结算"还是"vs 扁平均价结算"）在 writing-plans 阶段最终确定；本 spec 取前者（更纯，隔离调度策略价值）。
- **A2**：税模型为简化直线折旧 + 单一税率，不含增值税/所得税差异/加速折旧；标注为已知简化，后续细化。
- **A3**：ARBITRAGE 启发式的价格阈值规则（基于 `eta_roundtrip × 预期峰价`）在实现时定型；若启发式解质量不足，回退为"仅 MILP 精修"覆盖套利场景。
- **A4**：`ppa_price_locked` 与 `target_owner_price` 互斥校验在 config 层强制。
