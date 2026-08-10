# ele_trading 算法架构图更新规划

> 文档定位：本文是 `docs/arch/ele_trading算法架构.tldraw` 与
> `docs/arch/蒙西电力交易算法业务架构.tldraw` 的唯一统一修改规划，不是当前实现说明书。
> 当前事实以 `src/ele_trading/`、`docs/策略算法框架详细设计-v6.md` §2/§3 审计结论和结构守卫测试为准；
> 业务约束以根目录 `AGENTS.md` 与 `docs/策略算法框架详细设计-v6.md` 为准。

## 1. 目标与范围

### 1.1 目标

当前 `src/ele_trading/` 已形成三类边界清晰、成熟度不同的算法能力：

1. **活动交易主链**：沿 `domain ← markets ← positions / operations ← trading ← backtest`
   分层运行默认单结算价格接受者资源优化闭环（plan-only，非真实申报执行系统）。
2. **离线市场数字孪生**：`market_simulation/` 独立承载 DC 网络、SCED/SCUC、
   后定价、N-1、报价行为、ABM 和 MARL，只作离线校准、反事实和规则推演，不在线依赖交易编排。
3. **用户侧调度领域**：`user_side_dispatch/` 独立承载用户侧、分布式和 CVXPY 调度，
   只依赖 `utils/`，不接市场交易主链。

本次更新应让两张图准确表达 v6 审计确认的当前工程实现：

- 技术图展示包职责、真实依赖、活动主链、研究链和独立领域边界。
- 技术图新增 V6 工程增量：市场 profile 与数据准入、资源级运行契约、多资源日内、场景准入门、隔离 synthetic 工程验证工具、用户侧落地电价合成。
- 业务图保留单结算七阶段主线，并补充 Bid→Award 履约工程链、执行反馈、账单对账和五门经济验收。
- 业务图以独立区域展示市场数字孪生和用户侧调度，不把两者误画成现役交易步骤。
- 两图共同区分“工程可运行基线”和“真实规则、真实账单与生产晋级尚未完成”。

### 1.2 覆盖文件

| 文件 | 修改动作 |
|------|----------|
| `docs/arch/ele_trading算法架构.tldraw` | 按当前包结构更新技术责任与独立域布局 |
| `docs/arch/蒙西电力交易算法业务架构.tldraw` | 更新七阶段内容并增加独立能力带 |
| `docs/arch/ele_trading算法架构图更新规划.md` | 维护事实基线、图示要求和验收标准 |

### 1.3 非目标

- 不在图中复制安装命令、完整 API、配置默认值或测试数量。
- 不展示本轮范围外的平级包 `src/investment_estimation/`。
- 不把未接主链的双结算插件、市场数字孪生或用户侧调度画成活动交易流程。
- 不把 synthetic fixture、模拟账单、治理 dry-run 画成正式结算、正式验收或生产晋级能力。
- 不把“已有代码和测试”夸大为“真实市场校准完成”或“具备生产切换资格”。
- 不改变源码、配置或市场规则，仅同步规划和图示。

---

## 2. 当前架构事实基线

### 2.1 能力边界与成熟度

| 能力域 | 当前定位 | 真实依赖边界 | 成熟度表达 |
|--------|----------|--------------|------------|
| 活动交易主链 | 价格接受者资源优化：预测、场景、头寸、日前/日内、单结算、对账、回测验收 | 模式接口化分层 + V6 profile/资源契约 | 工程级主链；plan-only，无真实申报接口 |
| `market_simulation/` | 离线市场数字孪生与策略实验 | 当前实际只 import 共享 `optimization`；不得依赖交易、回测或具体市场插件 | 离线物理/行为原型；N-1 未入 SCUC 可行域，生产晋级未完成 |
| `user_side_dispatch/` | 用户侧风光储、分布式和 CVXPY 调度 | 只依赖 `utils/`，与活动市场主链零代码依赖 | 独立可运行领域能力；落地电价合成已实现，需量经济规则待确认 |

`src/investment_estimation/` 不属于本轮 `src/ele_trading/` 架构图范围，技术图和业务图均不展示。

### 2.2 包职责与 V6 增量

| 包 | 当前职责 | 图中必须体现的 V6 增量 |
|----|----------|------------------------|
| `domain/` | 共享交易契约、事件链、决策追踪 | 价格角色、Bid/Award/履约事件契约、输入版本推导 |
| `data_provider/` | 市场、资产、气象数据、版本快照和质量处理 | `DataCatalog`、来源/质量/修订证据 |
| `forecasting/` | 统一预测协议和价格、负荷、天气、风光模型 | forecast vintage、价格角色、市场状态概率 |
| `scenario/` | 联合场景、采样、缩减和诊断 | 状态条件 t-Copula、极端模板、诊断（接入 admission 门） |
| `optimization/` | BESS 物理核、目标组件、求解出口、套利、MPC、Two-stage | Level 2 温度退化、VaR/CVaR/EVaR/最坏情形风险菜单 |
| `positions/` | 中长期覆盖、月度报价、缺口再平衡 | 可选 CVaR 头寸优化；报价概率仍为启发式 |
| `operations/` | 日前、日内、DR 联合优化和回退 | 统一资源运行契约 `resource_runtime`；多资源日前/日内 `multi_resource(_intraday)`；执行偏差收紧 |
| `markets/` | MarketMode 协议、配置区段、单/双结算插件 | V6-0 `profile.py`：市场阶段时序、输入准入、规则缺失拒绝；单结算账单对账 |
| `demand_response/` | 独立 DR 机会成本和参与判定 | 保持启发式旁路，不替代主链 DR |
| `trading/` | 编排、事件链、场景准入门与隔离 synthetic 工具 | `scenario_admission.py`（诊断→准入层级）；`synthetic/`（fixture/回放/模拟账单/治理 dry-run，永久非正式）；Bid→Award→履约工程链 |
| `backtest/` | walk-forward、四组对照、指标和事件断言 | 反例库、block bootstrap、风险/对账/不变量五门验收；`InvariantEvidence` 缺失即失败 |
| `market_simulation/` | 网架、SCED/SCUC、后定价、N-1、报价、ABM、MARL | 独立研究链和 policy guard；物理约束与校准未闭合 |
| `user_side_dispatch/` | 用户侧 PV/BESS/联合/分布式调度 | `landed_price.py`：目录/市场落地电价与版本化输配电费合成 |
| `utils/` | 时间、对齐、IO、数值和日志工具 | 无业务语义 |

### 2.3 真实依赖拓扑

活动交易主分层仍为：

```text
domain ← markets ← positions / operations ← trading ← backtest
```

当前 import 事实为：

```text
data_provider → utils
forecasting → domain
scenario → domain / forecasting
optimization → scenario
operations → domain / markets / scenario / optimization
markets → domain / utils / data_provider（profile 数据准入消费 DataCatalog 契约）
trading → domain / forecasting / markets / operations / scenario
backtest → domain / markets / operations / optimization / trading

market_simulation → optimization
user_side_dispatch → utils
```

图中不得为了表现“可能使用”而画出源码中不存在的直接依赖：

- `market_simulation` 不得连接到 `trading`、`backtest` 或具体市场插件。
- `user_side_dispatch` 不得连接到活动数据、预测、场景、市场或回测包。
- 主链包只依赖 `MarketMode`、`SettlementEngine` 协议和共享配置，不直接 import 具体模式。
- `demand_response` 是独立机会成本评估能力，不替代 `operations` 内主链 DR。
- `trading/synthetic/` 是隔离工程验证工具，不产生正式账单、正式验收或生产晋级资格。

### 2.4 市场规则模式与 V6 profile

| 模式 | 当前状态 | 图中表达 |
|------|----------|----------|
| `single_settlement` | 默认活动主线；plan-only，正式申报被显式拒绝 | 实线接入交易编排和业务结算 |
| `dual_settlement` | 规则库；输入契约与主编排不兼容，无产品/运行/回测用例 | 虚线旁路插件，标明“未接活动主线、需重构输入契约” |
| `profile.py`（V6-0） | 市场阶段时序、输入准入（来源/质量/修订/权限/cutoff）、规则缺失结构化拒绝 | markets 容器中的正式路径骨架 |
| `shared` / `protocol` / `sections` | 模式协议、共享配置词汇、时段聚合和对账底座 | markets 容器中的共享底座 |

规则插件按结算模式命名，不按地区命名。业务图可以保留“蒙西”标题和研究背景，但内部活动节点必须使用
“单结算规则口径”。

### 2.5 活动交易链（V6 审计确认）

```text
版本化证据与当前持仓
  → 多价格角色预测 + forecast vintage + 市场状态概率
  → 状态条件联合场景 + 极端模板 + 诊断/准入门
  → 中长期/月度头寸（启发式 + 可选 CVaR 优化）
  → 多资源日前计划 + DR + 风险/退化
  → Bid → Award 匹配 → 受承诺约束的重求解（工程链）
  → 日内新 vintage 滚动 + 资源级实测 SOC 重解 + 执行偏差收紧 + 物理回退
  → 单结算 + 可选账单对账（confirmed=False 永不通过）
  → walk-forward + 反例 + 五门统一经济验收（缺失证据即失败）
```

关键事实：

1. `TradingOrchestrator` 编排预测、场景、日前、日内、结算和 Bid/Award 履约工程链；
   中长期/月度策略有独立入口；多资源日内已接线但仍是并列结果，不是唯一可结算主路径。
2. 市场状态、状态条件场景、多资源优化和统一验收均已工程实现，但真实规则、真实账单校准和生产晋级未闭合。
3. 日前价格的业务角色由 `PriceRole` 区分；活动单结算中只作运行参考，不进入财务结算。
4. 需求响应主链逻辑位于日前联合优化、日内履约约束和单结算履约核算；独立 `demand_response` 只做机会成本评估。
5. 真值只在决策完成后进入结算、回测和明确标识的 oracle 对照；履约事实必须来自版本化外部计量，禁止计划代替实测。
6. 15 分钟颗粒度使用 `dt = 0.25`；风光功率使用 MW，电量由下游按 `dt` 换算。
7. synthetic 工具链（fixture/回放/模拟账单/治理 dry-run）固定 `formal=False / production=False`，永久与正式路径隔离。

### 2.6 两条非主链能力

市场数字孪生研究链：

```text
网架 / 机组 / 负荷快照
  → SCED / SCUC
  → LMP / uplift / 阻塞与备用
  → N-1 安全校验（独立筛查，未入 SCUC 可行域）
  → 分段报价 / ABM / MARL
  → policy guard
```

当前没有 `app/market_simulation/` 项目入口；能力通过公开 API 和测试直接运行。
SCUC 未消费最小停机/停机成本/可再生/备用输入，SCED 未消费分段报价；它们不能被画成
“安全约束市场出清已完成”，更不能作为活动交易的实时依赖。

用户侧调度通过 `app/user_side_dispatch/` 的四个入口运行，覆盖 PV、BESS、PV+BESS 和 CVXPY 样例；
`landed_price.py` 把落地电价从外生序列升级为可审计合成（目录价透传；市场价 = 中长期×覆盖率 +
现货×(1−覆盖率) + 输配电量部分 + 基金附加），两部制需量电费走 `demand_charge_rate` 字段。
业务口径与现货交易、MarketMode 和活动回测互不依赖。

---

## 3. 当前两图差异分析

### 3.1 技术图差异

上一版技术图已表达活动分层、market_simulation 与 user_side_dispatch，但相对当前源码仍有以下过期项：

| 当前图示 | 当前源码事实 | 修改要求 |
|----------|--------------|----------|
| `trading` 只写“仅编排” | 新增 `scenario_admission.py` 与隔离 `synthetic/`；Bid→Award 履约工程链已进主链 | 更正为“编排 + 场景准入门 + 隔离 synthetic 工程验证” |
| `operations` 未列资源运行契约 | `resource_runtime.py` 是 V6-1 统一资源运行/实测/履约上层语义 | 增加资源级计划—实测—履约契约与多资源日内 fallback |
| `markets` 未列 profile | `profile.py` 是 V6-0 正式路径骨架（阶段时序、数据准入、缺失拒绝） | 增加 profile 卡 |
| `user_side_dispatch` 未列落地电价 | `landed_price.py` 已实现目录/市场价合成与版本化费率 | 增加落地电价合成 |
| 双结算只写“未接主链” | v6 确认输入契约与主编排不兼容，需先重构 | 状态文案更新为“需重构输入契约后接入” |
| market_simulation 未标注物理缺口 | N-1 未入 SCUC 可行域、分段报价未入 SCED | 研究链状态文案补充缺口 |

技术图需原位更新这些卡片文案；主体结构（三列 + 底部用户域 + 入口层）可保留。

### 3.2 业务图保留内容

以下业务结构继续有效：

- 七阶段活动主链、单结算模式、15 分钟颗粒度、日前价格不参与活动财务结算。
- 左侧无未来信息、交易风险、多资源物理寿命边界。
- 右侧需求响应三段闭环、单结算对账、双结算旁路、五门验收。
- 底部市场数字孪生、用户侧调度两条独立能力带。

### 3.3 业务图新增与修正

| 位置 | 当前缺口 | 修改要求 |
|------|----------|----------|
| 市场数据准备 | 未表达正式路径输入准入 | 增加来源/质量/修订/权限/时点准入与缺失拒绝 |
| 不确定性量化 | 未表达场景准入门 | 增加诊断未过不得进入正式候选优化 |
| 日前运行 | 未表达 Bid→Award 履约工程链 | 增加报价—回执—承诺约束（工程链，非真实申报） |
| 日内滚动 | 未表达资源级实测 SOC 重解 | 增加资源级实测驱动与安全裁剪 fallback |
| 经营验收 | 未表达 synthetic 工具边界 | 明确合成账单/治理 dry-run 只是工程验证证据 |
| 用户侧能力带 | 未表达落地电价合成 | 增加目录/市场落地电价与版本化费率 |

---

## 4. 技术架构图修改规划

### 4.1 标题与总体版式

- 标题：`ele_trading 电力交易算法架构`
- 副标题：`单结算价格接受者主链（plan-only）｜离线市场数字孪生｜独立用户侧调度`

保留既有“四区布局”（入口层 / 数据支撑列 / 活动责任分层列 / 算法与研究域列 / 底部用户侧域），
仅更新卡片内容与依赖箭头。

### 4.2 必须出现或更新的节点

#### 入口层

- `app/trading/`：pipeline、中长期、月度、日前、日内、DR、回测入口。
- `app/optimization/`：套利、MPC、Two-stage + CVaR 示例。
- `app/user_side_dispatch/`：PV、BESS、PV+BESS、CVXPY 四个独立入口。
- 明确标注“当前无 `app/market_simulation/` 项目入口”。

#### 活动交易责任层

- `backtest/`：walk-forward、四组对照、指标、反例和五门验收（InvariantEvidence 缺失即失败）。
- `trading/`：`TradingOrchestrator`、事件链、forecast vintage、`scenario_admission` 场景准入门、
  Bid→Award→履约工程链、`demo_fixtures`、隔离 `synthetic/`（fixture/回放/模拟账单/治理 dry-run，永久非正式）。
- `positions/`：中长期、月度、量价走廊和可选 CVaR 头寸；报价概率启发式。
- `operations/`：日前、日内、DR、`resource_runtime` 统一资源运行契约、
  `multi_resource`/`multi_resource_intraday` 多资源日前/日内、执行偏差收紧、物理回退。
- `markets/`：协议、共享配置、`profile`（V6-0 阶段时序 + 数据准入 + 缺失拒绝）、
  单结算活动插件（plan-only + 对账）、双结算旁路插件（需重构输入契约）。
- `domain/`：领域契约、价格角色、Bid/Award/履约事件与追溯。

#### 数据、概率和优化支撑

- `data_provider/`：版本化快照、质量、资产、气象、`DataCatalog` 证据。
- `forecasting/`：价格/负荷/风电/光伏/天气、分位预测、市场状态、forecast vintage。
- `scenario/`：联合场景、状态条件 t-Copula、极端模板、诊断（接 admission 门）和缩减。
- `optimization/`：共享 BESS 核、MPC、Two-stage、风险菜单、Level 1/2 退化和 typed 求解状态。
- `demand_response/`：独立机会成本评估，不等于主链 DR。

#### 市场数字孪生研究域

在独立容器中展示：

- `grid/contracts`：版本化节点、支路、机组和备用需求。
- `SCED / SCUC`：DC 出清、固定 commitment 后定价；缺口：N-1 未入可行域、分段报价未入 SCED、
  SCUC 未消费最小停机/可再生/备用。
- `LMP / uplift / N-1`：节点电价、补偿分离、事故重调度筛查。
- `bidding / behavior`：分段报价、经验加成、logit 与 ABM。
- `marl`：多智能体报价环境、独立 Q 基线和 policy guard。
- 状态：“离线研究基线｜物理约束与市场校准未闭合｜真实数据生产晋级未完成”。

#### 独立用户侧调度域

- `interfaces`：用户侧输入输出契约。
- `adapters`：PV、风电、储能和分布式适配。
- `algorithms`：规则、PuLP、CVXPY 和分布式调度。
- `landed_price`：目录/市场落地电价合成、版本化输配电费与基金附加、需量电费字段。
- 状态：“独立可运行｜只依赖 utils｜不接活动市场主链｜需量经济规则待确认”。

### 4.3 箭头与边界规则

- 实线表示运行数据或控制流；虚线表示配置注入或静态 import 依赖。
- 活动主链依赖箭头统一指向被依赖层。
- `market_simulation → optimization` 使用虚线；不得画到 `trading`、`backtest` 或具体市场插件。
- `user_side_dispatch → utils` 使用虚线；不得与活动主链建立箭头。
- `markets → data_provider`（profile 消费 DataCatalog）可用虚线表达，不得反向。
- `app` 入口只连接其真实领域，不能画一个“总入口”统管三个独立域。
- `trading/synthetic` 不得向 `markets`/`backtest` 画“正式证据”箭头；其隔离性写在卡片内。
- 箭头不带标签，输入、输出、成熟度和禁止关系写在卡片内部。
- 使用显式边界坐标，不使用默认中心绑定 elbow 箭头。

### 4.4 技术图禁止表达

```text
investment_estimation
src/ele_trading/capacity_planning
trading/contracts
trading/config_loader
trading/settlement_mengxi
trading/backtest
market_simulation → trading
market_simulation → backtest
user_side_dispatch → markets
user_side_dispatch → trading
synthetic → 正式结算 / 正式验收 / 生产晋级
```

---

## 5. 业务架构图修改规划

### 5.1 标题层

- 主标题：`蒙西电力交易算法 · 业务架构`
- 副标题：`单结算价格接受者主线（规则研究参考蒙西）｜决策—执行—结算—验收闭环｜15 分钟颗粒度`

### 5.2 七阶段主链

| 阶段 | 输入依据 | 核心动作 | 业务输出 |
|------|----------|----------|----------|
| 01 市场数据准备 | 市场实绩、资产与气象、合约成交、规则与账单证据 | 按可用时点完成质量校验、版本固化；正式路径需通过来源/质量/修订/权限/时点准入，缺失即拒绝 | 版本化市场快照、当前持仓和证据版本 |
| 02 市场预测 | 决策时点可用数据和历史 forecast vintage | 区分日前参考、实时结算、价差和中长期价格角色；形成电价、负荷、风电、光伏及市场状态概率 | 点预测、分位区间、状态概率和模型版本 |
| 03 不确定性量化 | 预测残差、状态概率、相关关系和极端事件证据 | 状态条件 t-Copula 联合采样，注入有证据极端模板，诊断并经准入门分级 | 代表场景、概率、状态标签、尾部与诊断报告 |
| 04 中长期与月度决策 | 持仓缺口、预算、价格走廊和风险边界 | 年度覆盖、月度分解、竞价阶梯和可选 CVaR 头寸优化；报价概率为启发式建议 | 目标仓位、竞价阶梯和再平衡建议 |
| 05 现货日前运行计划 | 当前持仓、场景、BESS 群、DR、新能源和风险/退化参数 | 联合安排多资源、响应和限电；报价—回执—承诺约束重求解（工程链） | 日前物理计划、风险敞口、响应承诺和求解状态 |
| 06 日内滚动调整 | 最新预测、已执行段、资源级实测功率/SOC、剩余承诺 | 冻结前缀、按资源级实测 SOC 重解剩余窗口；执行偏差形成降额和 SOC 储备，样本不足时不生效 | 最新计划、约束收紧、调整原因和安全裁剪回退标记 |
| 07 结算与经营复盘 | 实际电量、实时价格、履约、正式账单和规则版本 | 单结算分项核算；提供正式账单时逐项对账，未确认账单永不通过 | 结算报告、对账报告、策略档案和验收输入 |

### 5.3 左侧决策边界

1. **无未来信息与证据纪律**
   - 特征、预测、场景和规则均有 `as_of` 与版本。
   - forecast vintage 和历史残差不得越过决策时点；迟到市场回执不污染已执行前缀。
   - 履约事实必须来自版本化外部计量，禁止计划代替实测。
   - 仅 oracle 对照可使用未来真值。

2. **交易、价格角色与风险边界**
   - 日前参考价、实时结算价、价差和中长期价格不得混用。
   - 持仓、价格、预算、VaR/CVaR/EVaR 和最坏情形边界清晰。
   - 求解失败返回结构化状态，不伪造零计划成功。
   - 正式申报前需规则化信用与敞口约束（当前未闭合，不得暗示已具备）。

3. **多资源物理与寿命边界**
   - 多 BESS、DR、新能源限电和购电满足统一能量平衡。
   - SOC、功率、效率、同充同放、吞吐量和末端状态受约束。
   - Level 1/2 退化和温度影响分级使用，缺参数不得伪装精确模型。
   - 样本不足时偏差收紧不生效。

### 5.4 右侧闭环能力

#### 需求响应闭环

- 日前申报：基线、增量响应、补偿、机会成本共同判断。
- 日内履约：已执行量与剩余窗口共同约束。
- 结算核算：实际增量补偿和未履约罚金分项列示。

#### 单结算规则与账单对账

- 实时电能、中长期差价、长协回收、DR、退化和执行调整不重复计费。
- 日前价格只作运行参考，不参与活动财务结算。
- 建模分项与正式账单逐项比较；未确认账单永不通过；未知差异不得隐藏或提前归因。

#### 双结算旁路

- 双结算与偏差带规则引擎已有规则库。
- 输入契约与主编排不兼容，无产品/运行/回测用例；需先统一产品与实际量契约再接入。
- 使用虚线旁路卡，不连接七阶段主链。

#### 回测与统一经济验收

- walk-forward 逐日重放完整决策链。
- 对照：无储能、确定性、风险、oracle。
- 五门同时通过：统计显著性、尾部风险、账单对账、HARD 反例、无前瞻与零硬约束违约。
- 任一门失败均不得晋级；缺失证据（含 InvariantEvidence）按失败处理。
- synthetic 回放与模拟账单仅作工程验证证据，不构成正式验收。

### 5.5 两块独立业务能力带

#### 市场数字孪生与策略实验

使用纯业务语言展示：

```text
网架 / 机组 / 负荷快照
  → 经济调度 / 机组组合
  → 节点电价 / 补偿 / 阻塞 / 备用
  → N-1 安全校验
  → 分段报价 / 参与者行为 / 多主体学习
  → 策略安全闸
```

标注“离线研究链｜不在线接入活动交易和回测｜物理约束与市场校准未闭合｜真实数据晋级未完成”。

#### 用户侧风光储调度

展示目录/市场落地电价合成（版本化输配电费、基金附加、需量电费）下的 PV、风电、BESS、
需量和分布式调度；标注“独立领域能力｜规则/PuLP/CVXPY/分布式｜不接现货交易主链｜需量经济规则待确认”。

两块能力带不得使用箭头连接七阶段主链。

### 5.6 底部经营验收

将原四组经营指标升级为五门验收：

- 统计门：block bootstrap 节省置信区间显著为正。
- 风险门：CVaR 与尾部损失不超容差恶化。
- 对账门：正式账单已确认且差异归零或有明确归因。
- 反例门：全部 HARD 业务反例通过。
- 不变量门：无前瞻、零硬约束违约、回退物理可行。

资产指标继续保留等效循环、完整 SOC 序列雨流退化和寿命成本，但作为验收证据而非单独晋级门。

---

## 6. 两图一致性矩阵

| 业务图内容 | 技术图责任节点 | 一致性要求 |
|------------|----------------|------------|
| 数据证据准备与准入 | `data_provider` + `markets/profile` | 版本、as-of、质量、来源、权限与时点准入 |
| 价格角色与市场状态 | `domain.price_roles` + `forecasting.market_state` | 价格用途不混用，状态输出概率而非伪真值 |
| 概率预测 | `forecasting` | 活动四路预测与 weather 支撑分开；保留 forecast vintage |
| 状态条件场景与准入 | `scenario.state_conditioned` + `trading.scenario_admission` | 诊断未过不得进入正式候选优化 |
| 中长期与月度头寸 | `positions` | 独立策略入口，启发式报价概率 + 可选 CVaR |
| 多资源日前/日内运行 | `operations.multi_resource(_intraday)` + `resource_runtime` + `optimization` | 资源级计划—实测—履约契约；多资源仍为并列结果 |
| 报价履约工程链 | `trading` + `domain` 事件 | Bid→Award→承诺约束是工程链，非真实申报接口 |
| 单结算和对账 | `markets/single_settlement` + `markets.shared` | 日前价不结算；未确认账单不通过 |
| 双结算扩展 | `markets/dual_settlement` | 规则库；需重构输入契约后接入 |
| 回测与经济验收 | `backtest` | 四组对照、反例、五门；InvariantEvidence 缺失即失败 |
| synthetic 工程验证 | `trading/synthetic` | 永久非正式，不进入正式证据链 |
| 市场数字孪生 | `market_simulation` | 独立研究链，只画真实 `→ optimization` 依赖 |
| 用户侧调度 | `user_side_dispatch`（含 `landed_price`） | 只依赖 utils，不接市场主链 |
| 全链追溯 | `domain.DecisionTrace` + 事件链 | 预测、报价、履约、求解、回退、结算和验收可追溯 |

---

## 7. 绘图实施顺序

### 阶段 A：基线

1. 读取两张图的形状、文字、箭头和绑定。
2. 保存修改前截图、文件大小和 SQLite 记录数。
3. 确认 app 内存状态无未落盘修改。

### 阶段 B：技术图

1. 更新 `trading` 卡：编排 + 场景准入门 + Bid/Award 工程链 + 隔离 synthetic。
2. 更新 `operations` 卡：资源运行契约 + 多资源日内 + 执行偏差。
3. 更新 `markets` 卡：profile 正式路径骨架 + 双结算需重构输入契约。
4. 更新 `user_side_dispatch` 卡：落地电价合成。
5. 更新 `market_simulation` 状态卡：物理约束与校准缺口。
6. 必要时新增 `markets → data_provider` 虚线依赖。
7. 保持既有几何结构，避免大规模重排。

### 阶段 C：业务图

1. 更新阶段 01/03/05/06 文案：准入、准入门、Bid→Award 工程链、资源级实测重解。
2. 更新右侧双结算旁路与回测验收文案。
3. 更新用户侧能力带：落地电价合成。
4. 更新市场仿真能力带状态：物理约束与市场校准未闭合。
5. 独立能力带不连接七阶段主链。

### 阶段 D：联合验收

1. 按 §6 逐项核对业务与技术责任。
2. 检索必需和禁止文本。
3. 程序化检查箭头穿盒、交叉、贴边和端点。
4. 截图检查文字、层级、对齐和可读性。
5. 调用 `helpers.saveDoc()`，再解包验证 SQLite 持久化。

---

## 8. 视觉与几何规范

- 不使用圆形节点；阶段编号写在标题条中。
- 大模块使用“外框 + 标题条 + 内容卡”，避免一个大文本框承载全部信息。
- 同类模块统一宽高、内边距和间距。
- 主列与侧栏保留至少 `120 px` 专用走廊。
- 容器内边距统一 `16 px`，同级卡片间距统一 `12 px`。
- 活动主链、独立研究链和独立用户侧域使用不同背景色和边界说明。
- 独立域之间没有业务流时不画箭头；禁止用虚线暗示未来集成。
- 箭头使用直线或正交折线，端点落在边界，不穿卡片、不交叉、不沿盒边长距离运行。
- 不使用默认中心锚点的 elbow 箭头；复杂路径使用显式坐标分段，仅末段带箭头。
- 业务图只使用业务语言；技术图允许包名、契约名和成熟度状态。
- 所有文字完整显示，无裁切、溢出或豆腐字符。

---

## 9. 验收标准

### 9.1 内容验收

- [ ] 技术图完整展示活动主分层 `domain / markets / positions / operations / trading / backtest`。
- [ ] `trading` 展示编排、事件链、forecast vintage、场景准入门、Bid/Award 工程链和隔离 synthetic。
- [ ] 技术图展示状态预测、状态条件场景、多资源日前/日内、资源运行契约、执行偏差、对账和五门验收。
- [ ] `markets` 展示 V6-0 profile 正式路径骨架。
- [ ] `market_simulation` 独立展示 SCED/SCUC、LMP/uplift、N-1、报价/ABM/MARL 和 policy guard，并标注物理/校准缺口。
- [ ] `market_simulation` 只连接真实共享依赖，不连接活动交易或回测。
- [ ] `user_side_dispatch` 独立展示（含落地电价合成），并明确只依赖 `utils`。
- [ ] 两图不展示范围外 `investment_estimation`。
- [ ] 业务图保留七阶段、15 分钟和单结算活动主线。
- [ ] 业务图包含输入准入、场景准入门、Bid→Award 工程链、资源级实测重解、对账和五门验收。
- [ ] 业务图以无连接独立能力带展示市场仿真和用户侧调度。
- [ ] 双结算明确输入契约未兼容、未接活动主线。
- [ ] 日前价格只作活动单结算运行参考，不参与财务结算。

### 9.2 禁止旧表达验收

两图中以下内容应为零命中：

```text
investment_estimation
trading/contracts
trading/config_loader
settlement_mengxi
market_mengxi.yaml
configs/trading
trading/backtest
trading/metrics
src/ele_trading/capacity_planning
蒙西单结算口径
```

同时禁止出现表示虚假集成的箭头：

```text
market_simulation → trading
market_simulation → backtest
user_side_dispatch → markets
user_side_dispatch → trading
synthetic → 正式结算 / 正式验收 / 生产晋级
```

“蒙西”仅允许出现在业务图标题和规则研究背景中。

### 9.3 成熟度验收

- [ ] V6 新能力标为工程可运行基线或正式路径骨架，不写“生产已验证”。
- [ ] 主链明确为价格接受者 plan-only 优化器，非真实申报执行系统。
- [ ] 市场数字孪生明确物理约束、市场校准与生产晋级未闭合。
- [ ] 用户侧调度明确需量经济规则待确认。
- [ ] 双结算明确输入契约与主编排不兼容。
- [ ] synthetic 明确固定 formal=False / production=False。
- [ ] 缺失数据、参数或验收证据不以默认成功表达。

### 9.4 几何验收

- [ ] 箭头不穿过非端点矩形或文字。
- [ ] 箭头不沿矩形边界长距离重叠。
- [ ] 不同流程箭头无非预期交叉或共用走廊重叠。
- [ ] 箭头端点落在目标模块边界。
- [ ] 同级模块坐标、尺寸、内边距和间距遵守统一公式。
- [ ] 全图缩放后仍能区分活动主链、研究链和独立用户侧域。

### 9.5 文件与代码验收

- [ ] 每张图修改后调用 `helpers.saveDoc()`。
- [ ] app 报告 `unsavedChanges=false`。
- [ ] 两个 `.tldraw` 的 `db.sqlite` 均包含完整形状记录和更新后必需文本。
- [ ] 权威副本只位于 `docs/arch/`，无临时截图或工作副本进入 Git diff。
- [ ] `tests/test_structure_layers.py` 通过，包含 `market_simulation` 和 `user_side_dispatch` 独立性守卫。
- [ ] `git diff --check` 通过。

---

## 10. 预期改动清单

本轮只修改：

```text
docs/arch/ele_trading算法架构图更新规划.md
docs/arch/ele_trading算法架构.tldraw
docs/arch/蒙西电力交易算法业务架构.tldraw
```

当前工作区已有待提交源码、测试和设计文档改动，本规划只读取它们作为事实基线，不修改、不整理、不提交这些用户改动。
如源码与结构守卫冲突，应停止改图并先报告冲突，不能通过图示掩盖实际依赖。
