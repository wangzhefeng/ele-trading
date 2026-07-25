# investment_estimation

`investment_estimation` 是一个风光储投资测算算法原型包，用于把负荷、电价、风光资源、储能调度、月度结算、投资现金流和容量搜索串成一个可运行闭环。

> **包定位与历史**（2026-07 重组）
>
> - 本包与 `src/ele_trading/` **平级、完全自包含**（不 import `ele_trading`），专司**投资收益测算**。
> - 老版 `src/ele_trading/capacity_planning/`（本包的上一版，混合投资收益测算与极少调度内核）已**整体并入 `todo/` 迁移暂存区**，等待与本包新版模块（`dispatch/`、`settlement/`、`finance/`、`capacity_search/` 等）合并去重。`todo/` 非最终形态。
> - 真正的**电力市场交易算法**（套利/MPC/CVaR/用户侧调度）在 `src/ele_trading/optimization/`，与本包无关。
> - 自包含地基：`utils/`（迁移自 `ele_trading.utils` 的最小子集）+ `finance/metrics.compute_irr`（通用二分法 IRR）。

当前实现不是单一 IRR（Internal Rate of Return，内部收益率）计算器，而是围绕两个核心商业指标构建的测算体系：

1. 投资方指标：项目税前 IRR、NPV（Net Present Value，净现值）、回收期、IRR 相对基准提升。
2. 业主指标：业主节费额、业主节费比例。

两个指标可以互相作为目标和约束：

```text
投资方视角：
    maximize project_irr
    subject to owner_saving_pct >= min_owner_saving_pct

业主视角：
    maximize owner_saving_pct
    subject to project_irr >= min_project_irr

投资方提升视角：
    maximize irr_uplift = candidate_project_irr - baseline_project_irr
    subject to project_irr >= min_project_irr
               owner_saving_pct >= min_owner_saving_pct
```

## 开始阶段指标概念解释

原始需求中提到“投资方的 IRR 整体性提升”和“业主的节费比例提升”。这两个方向分别对应能源投资方和用电业主的核心评价口径。为了避免后续算法把不同口径混在一起，开始阶段先把关键指标定义清楚。

### 英文缩写对照

| 缩写 | 英文全称 | 中文含义 | 在本模块中的作用 |
|------|----------|----------|------------------|
| IRR | Internal Rate of Return | 内部收益率 | 衡量项目现金流自身能达到的收益率。 |
| NPV | Net Present Value | 净现值 | 衡量项目在给定折现率下能创造的现值。 |
| PPA | Power Purchase Agreement | 电力购买协议 | 表示业主向项目投资方购电的合同价格机制。 |
| CAPEX | Capital Expenditure | 资本性支出 | 表示风、光、储、并网等一次性建设投资。 |
| OPEX | Operating Expense | 运营性支出 | 表示项目投运后的运维、保险、租赁等持续性成本。 |
| CF | Cash Flow | 现金流 | 表示项目在某个年份流入或流出的净现金金额。 |
| SOC | State of Charge | 储能荷电状态 | 表示储能当前剩余电量占额定容量的比例或对应电量。 |
| PCS | Power Conversion System | 储能变流器 | 表示储能系统中负责交直流转换的核心设备。 |
| MVP | Minimum Viable Product | 最小可行版本 | 表示当前最小可运行、可验证的测算闭环。 |

### 投资方指标

投资方通常承担风电、光伏、储能、并网、开发和运维等全部或主要投资，因此投资方指标关注的是项目资产本身能否产生足够现金回报。当前最小可行版本优先采用“项目税前口径”，暂不引入所得税、增值税抵扣、折旧、贷款还本付息和资本金结构。

#### 项目税前 IRR（Internal Rate of Return，内部收益率）

项目税前 IRR 是指在不考虑所得税和融资结构影响时，使项目全生命周期税前净现金流折现值等于 0 的折现率。它衡量的是项目资产本身的收益率，而不是股东资本金收益率。

通俗理解：投资方先花一笔钱建设风光储项目，之后每年通过 PPA 售电、余电上网等方式回收现金。项目税前 IRR 就是在回答“这笔投资按项目自身现金流看，大约相当于每年赚多少收益率”。它更像收益率刻度，不是最终赚了多少钱。

基本现金流形式为：

```text
CF_0 = -CAPEX
CF_y = 税前收入_y - 运维成本_y - 更换成本_y - 其他税前成本_y
```

其中：

1. `CF_0` 是建设期初始投资现金流，通常为负值。
2. `CAPEX`（Capital Expenditure，资本性支出）包括风电、光伏、储能、并网、设计、开发、建设管理等一次性投资。
3. `税前收入_y` 包括 PPA（Power Purchase Agreement，电力购买协议）售电收入、余电上网收入、补贴、绿证或其他确认给投资方的收益。
4. `运维成本_y` 包括固定运维、保险、租赁、检修、管理等年度成本。
5. `更换成本_y` 主要包括储能电芯或 PCS（Power Conversion System，储能变流器）等设备在寿命期内的更换支出。
6. `y` 表示项目生命周期中的年份序号，`y=0` 通常表示建设期或初始投资时点，`y=1` 表示投运后第 1 年，依此类推。

IRR 的数学定义为：

```text
0 = sum(CF_y / (1 + IRR)^y), y = 0, 1, ..., N
```

公式变量说明：

1. `CF_y`：第 `y` 年的项目净现金流，单位通常为元。
2. `IRR`：使所有年份现金流折现后加总等于 0 的收益率。
3. `y`：年份序号，取值为 `0, 1, ..., N`。
4. `N`：项目测算总年限。例如项目测算 20 年，则 `N=20`。
5. `sum(...)`：表示把 `y=0` 到 `y=N` 每一年的折现现金流全部加总。

解释要点：

1. IRR 越高，说明同一投资额下项目回款能力越强。
2. IRR 不直接表示绝对赚钱金额，它是收益率，不是利润额。
3. 当现金流没有从负到正的稳定变化时，IRR 可能不存在或存在多个解，因此算法中需要同时输出 NPV 和现金流表辅助判断。
4. 当前模型中的 `project_irr` 默认指项目税前 IRR；后续如扩展税后 IRR 或资本金 IRR，必须新增明确字段，不应覆盖该字段含义。

#### NPV（Net Present Value，净现值）

NPV 是净现值，表示在给定折现率 `r` 下，项目全生命周期现金流折算到当前时点后的价值。它回答的问题是：如果投资方要求最低收益率为 `r`，该项目还能额外创造多少现值。

通俗理解：NPV 是把未来每年能赚到的钱都“折回今天”再加总。它回答的不是“收益率有多高”，而是“按投资方要求的收益率算完以后，今天看这个项目还多值多少钱”。因此 NPV 更适合看绝对价值。

计算公式为：

```text
NPV(r) = sum(CF_y / (1 + r)^y), y = 0, 1, ..., N
```

公式变量说明：

1. `NPV(r)`：在折现率为 `r` 时计算得到的净现值。
2. `r`：折现率，也可以理解为投资方要求的最低年化收益率或资金成本。
3. `CF_y`：第 `y` 年的项目净现金流。
4. `y`：年份序号，`y=0` 是初始投资时点，`y=1` 是第 1 个运营年。
5. `N`：项目测算总年限。

解释要点：

1. 当 `NPV(r) > 0` 时，说明项目收益超过折现率 `r` 对应的最低要求。
2. 当 `NPV(r) = 0` 时，说明项目刚好达到折现率 `r`。
3. 当 `NPV(r) < 0` 时，说明项目达不到折现率 `r` 的要求。
4. IRR 可以理解为使 `NPV(r) = 0` 的特殊折现率。

在投资测算中，NPV 适合用于比较绝对价值，IRR 适合用于比较收益率。两个指标可能给出不同排序：小项目可能 IRR 高但 NPV 小，大项目可能 IRR 略低但 NPV 更高。

#### 回收期

回收期表示项目累计现金流从负转正所需要的时间，衡量投资本金被项目现金流收回的速度。它更偏向资金安全性和流动性判断，不等同于项目总收益。

通俗理解：回收期就是“投出去的钱几年能收回来”。它适合快速判断资金占用时间，但不能说明项目后半段还能赚多少钱，所以不能只靠回收期判断项目好坏。

静态回收期不考虑折现：

```text
静态累计现金流_y = sum(CF_i), i = 0, 1, ..., y
```

公式变量说明：

1. `CF_i`：第 `i` 年的项目净现金流。
2. `i`：加总过程中的年份序号，从初始投资时点 `0` 一直加到当前年份 `y`。
3. `y`：正在判断的年份。如果第 `y` 年累计现金流首次大于等于 0，则回收期大约落在第 `y` 年附近。

动态回收期考虑折现：

```text
动态累计现金流_y = sum(CF_i / (1 + r)^i), i = 0, 1, ..., y
```

公式变量说明：

1. `r`：折现率，用来把未来现金流折算到当前价值。
2. `CF_i / (1 + r)^i`：第 `i` 年现金流折现到当前时点后的价值。
3. `动态累计现金流_y`：从第 0 年到第 `y` 年的折现现金流累计值。

解释要点：

1. 回收期越短，表示投资本金越早收回。
2. 回收期不能反映回收期之后的现金流价值，因此不能单独作为最优目标。
3. 在风光储项目中，储能更换成本可能导致某些年份现金流下降，因此需要查看年度现金流表，而不能只看首年回收速度。
4. 当前模型可把回收期作为辅助输出或约束，例如 `payback_year <= payback_max`。

#### IRR 相对基准提升（IRR Uplift）

IRR 相对基准提升用于衡量候选方案相对某个基准方案的收益率改善幅度。它不是一个独立的财务口径，而是两个项目税前 IRR 之间的差值。

通俗理解：如果已经有一个原方案，现在想比较“加储能”“调整 PPA 价格”“改变风光容量”之后有没有变好，IRR 相对基准提升就是看新方案比原方案的 IRR 高了几个百分点。它重点看改善幅度，不是只看新方案本身有多高。

计算公式为：

```text
baseline_irr = IRR(基准方案现金流)
candidate_irr = IRR(候选方案现金流)
irr_uplift = candidate_irr - baseline_irr
```

公式变量说明：

1. `baseline_irr`：基准方案的项目税前 IRR。
2. `candidate_irr`：候选方案的项目税前 IRR。
3. `irr_uplift`：候选方案相对基准方案的 IRR 提升值。
4. `基准方案现金流`：原方案或对照方案的全生命周期现金流。
5. `候选方案现金流`：待评估新方案的全生命周期现金流。

解释要点：

1. 基准方案可以是“无储能方案”“仅光伏方案”“当前已批准方案”或“上一轮设计方案”。
2. `irr_uplift` 是百分点差值。例如基准 IRR 为 8%，候选 IRR 为 10%，则提升为 2 个百分点，不是提升 25%。
3. 该指标适合回答“在已有方案基础上，新增风电、光伏、储能或调整 PPA 后，投资方收益率提升多少”。
4. 如果基准方案 IRR 不可求，IRR 相对提升也不可直接计算，应改用 NPV 提升或重新定义基准方案。
5. 当前 V5 版本对应 `investor_irr_uplift` 模式，按 `candidate_project_irr - baseline_project_irr` 计算提升值。

### 业主指标

业主是项目建成后的用电方。业主通常不承担风光储全部投资，核心关注点是项目后综合用能成本是否低于原有电网购电成本，以及节省幅度是否满足合同谈判目标。

#### 业主节费额

业主节费额表示项目建成后，业主相比“无项目情况下继续从电网购电”的成本减少金额。

通俗理解：业主节费额就是业主一年实际少花了多少钱。没有项目时，业主全靠电网购电；有项目后，业主一部分电从电网买，一部分按 PPA 合同价格向项目买。两种情况下总用能成本的差额，就是节费额。

计算公式为：

```text
业主节费额 = 无项目电费 - 有项目综合用能成本
```

公式变量说明：

1. `无项目电费`：如果不建设风光储项目，业主按原方式从电网购电的总成本。
2. `有项目综合用能成本`：项目建成后，业主剩余电网购电费、PPA 购电费和其他相关费用之和。
3. `业主节费额`：两个成本之间的差额，单位通常为元。

其中：

```text
无项目电费 = 无项目电网电度电费 + 无项目基本电费 + 无项目其他费用
有项目综合用能成本 = 剩余电网购电费 + PPA 购电费 + 项目后基本电费 + 项目后其他费用
```

解释要点：

1. `无项目电费` 是业主的基准成本，表示不建设风光储项目时的全年用能成本。
2. `有项目综合用能成本` 是项目投运后业主实际承担的综合成本，包括向电网购电和向投资方按 PPA 购电。
3. 如果储能削峰降低了基本电费，应体现在项目后基本电费下降中。
4. 如果合同约定业主承担储能服务费、管理费或其他固定费用，也应计入项目后综合用能成本。
5. 当前最小可行版本先按年度汇总口径计算节费额，月度表用于解释节费来源。

#### 业主节费比例

业主节费比例表示节费额占无项目电费的比例，是业主视角最直观的相对收益指标。

通俗理解：业主节费比例就是“原来每花 100 元电费，现在能省多少元”。例如无项目电费为 1000 万元，项目后节省 100 万元，则节费比例为 10%。它比节费额更方便比较不同规模业主的节费效果。

计算公式为：

```text
业主节费比例 = 业主节费额 / 无项目电费
```

公式变量说明：

1. `业主节费额`：业主因项目减少的总用能成本。
2. `无项目电费`：业主原本的基准用能成本。
3. `业主节费比例`：节费额占基准成本的比例，通常以百分比展示。

解释要点：

1. 节费比例越高，说明业主从项目中获得的相对成本下降越明显。
2. 节费比例依赖基准电费口径。如果无项目电费只包含电度电费，结果会不同于包含基本电费、偏差费用和附加费用的口径。
3. 当 PPA 价格升高时，投资方收入通常增加，但业主节费比例通常下降。
4. 当储能调度减少高价时段电网购电或降低需量电费时，业主节费比例可能提升。
5. 当前模型中的 `owner_saving_pct` 默认按首年或典型年结算结果计算；如后续改为全生命周期累计折现节费比例，应新增字段并单独说明。

### 双方指标的关系

投资方指标和业主指标不是孤立的，它们共同决定项目是否可交易、可落地。

通俗理解：投资方和业主是在同一个项目里分配收益。PPA 价格高一点，投资方卖电收入通常更好，但业主买电成本也更高；PPA 价格低一点，业主更省钱，但投资方收益可能不达标。因此模型不能只看一方，而要同时看双方能不能接受。

典型关系如下：

```text
PPA 价格上升
  -> 投资方 PPA 收入上升
  -> 项目税前 IRR / NPV 上升
  -> 业主 PPA 购电成本上升
  -> 业主节费额 / 节费比例下降
```

因此，原始需求中的“投资方 IRR 整体性提升”和“业主节费比例提升”可以形成双目标与约束切换：

1. 投资方优先：最大化项目税前 IRR 或 IRR 相对基准提升，同时约束业主节费比例不低于底线。
2. 业主优先：最大化业主节费比例，同时约束项目税前 IRR 不低于投资方底线。
3. 双边可行域：寻找同时满足 `project_irr >= min_project_irr` 和 `owner_saving_pct >= min_owner_saving_pct` 的 PPA 与容量组合。

开始阶段建议所有场景至少同时输出以下指标：

1. `project_irr`：项目税前 IRR。
2. `npv`：按目标折现率计算的净现值。
3. `payback_year`：回收期。
4. `owner_saving`：业主节费额。
5. `owner_saving_pct`：业主节费比例。
6. `baseline_project_irr`：如存在基准方案，输出基准项目税前 IRR。
7. `irr_uplift`：如存在基准方案，输出 IRR 相对基准提升。

## 当前算法目标

本目录内算法要实现的目标是：

1. 接入一年或典型年的负荷、电价、风光资源时序数据。
2. 在小时级或 15 分钟级时间尺度上模拟风、光、储、负荷、电网之间的能量平衡。
3. 对储能进行规则型调度，当前支持风光余电优先充电、低价电网充电、高价放电供负荷。
4. 将逐时或 15 分钟调度结果汇总为月度结算口径。
5. 计算业主无项目成本、有项目综合用能成本、业主节费额和节费比例。
6. 计算投资方 PPA 收入、余电上网收入、CAPEX、年度现金流、项目税前 IRR、NPV 和回收期。
7. 在风、光、储容量和 PPA 价格候选组合中搜索可行方案。
8. 支持不同目标模式下的最优方案排序：
   - 投资方 IRR 优先。
   - 业主节费比例优先。
   - 投资方 IRR 相对基准提升优先。
9. 输出候选方案表、最优方案摘要、不可行原因表和年度现金流表。

## 目录结构

```text
investment_estimation/
  app/              运行入口脚本
  capacity_search/  风光储容量和 PPA 价格粗网格搜索
  config_loader/    YAML 配置加载和强类型配置对象
  configs/          MVP、V1-V5 示例场景配置
  data_provider/    CSV 读取、样例数据生成和时序校验
  dataset/          样例输入数据
  dispatch/         规则型储能调度和能量平衡
  finance/          CAPEX、现金流、IRR、NPV、回收期和 PPA 反求
  resource_simulation/ 独立风光资源仿真
  results/          示例运行输出
  settlement/       月度结算和业主/投资方收益汇总
  PLAN.md           需求拆解和版本规划
```

## 数据输入口径

当前实现要求每个场景通过 YAML 配置指定输入路径。YAML 位于 `configs/` 下时，相对路径按 `src/investment_estimation/` 解析。

### 负荷 CSV

字段：

```text
time,value
```

含义：

1. `time`：时间戳。
2. `value`：负荷平均功率，单位 kW。读取后会重命名为 `load_kw`。

### 电价 CSV

字段：

```text
time,price,price_type
```

含义：

1. `price`：电网购电价，单位元/kWh。
2. `price_type`：电价类型，用于储能规则调度。标准值统一使用英文编码：`deep_valley`、`valley`、`flat`、`peak`、`sharp_peak`。

`price_type` 允许在原始 CSV 中使用常见中文别名，读取后会统一转换为英文编码：

| 中文输入 | 标准英文编码 | 含义 |
| --- | --- | --- |
| `深谷` | `deep_valley` | 深谷电价时段 |
| `谷`、`低谷` | `valley` | 谷电价时段 |
| `平` | `flat` | 平电价时段 |
| `峰`、`高峰` | `peak` | 峰电价时段 |
| `尖峰` | `sharp_peak` | 尖峰电价时段 |

建议外部电价 CSV 直接使用英文标准值；如果上游数据来自中文行政分时表，也可以保留中文，系统会在 `data_provider` 读取阶段标准化。后续调度配置中的 `charge_price_types` 和 `discharge_price_types` 也统一按英文标准值匹配。

### 风光资源 CSV

字段：

```text
time,pv_kw,wind_kw
```

含义：

1. `pv_kw`：光伏平均出力，单位 kW。
2. `wind_kw`：风电平均出力，单位 kW。

当前模块已内置独立的 `resource_simulation/` 风光资源仿真能力，也仍支持外部风光资源 CSV 直接接入。容量搜索时会按候选容量相对于配置中的基准容量比例缩放 `pv_kw` 和 `wind_kw`。

资源仿真入口可以先分别生成单资源 CSV：

```text
time,pv_kw
time,wind_kw
```

再通过 `app/build_resource_profile.py` 合并为测算链路需要的资源 CSV：

```text
time,pv_kw,wind_kw
```

相关配置文件：

```text
configs/resource_pv_simulation_v1.yaml
configs/resource_pv_simulation_v2.yaml
configs/resource_wind_simulation_v1.yaml
configs/resource_wind_simulation_v2.yaml
configs/resource_profile_demo.yaml
```

### 时间步长

`data_provider.build_timeseries()` 会按相邻时间戳自动推断 `dt_hours`。后续所有能量计算均使用：

```text
energy_kwh = power_kw * dt_hours
```

因此小时级和 15 分钟级数据可以共用同一套计算逻辑。

## 核心算法链路

### 1. 数据接入与校验

实现位置：

```text
data_provider/data_loader.py
```

运行逻辑：

1. 读取负荷、电价、资源三个 CSV。
2. 按 `time` 做内连接，得到统一时序主表。
3. 按时间戳排序。
4. 推断 `dt_hours`。
5. 校验关键数据质量：
   - 必需字段是否存在。
   - 时间戳是否重复。
   - 是否存在缺失值。
   - `dt_hours` 是否为正。
   - 负荷、电价、风光出力是否非负。

该层的输出是调度模型的统一输入表：

```text
time, load_kw, price, price_type, pv_kw, wind_kw, dt_hours
```

### 2. 规则型储能调度

实现位置：

```text
dispatch/rule_based.py
```

当前调度是可解释的规则策略，不是优化调度。每个时间步按以下顺序分配能量：

1. 风光优先直接供负荷。

   ```text
   renewable_to_load_kwh = min(load_kwh, renewable_kwh)
   ```

2. 风光余电优先给储能充电。

   ```text
   charge_from_renewable_kwh <= bess_power_kw * dt_hours
   charge_from_renewable_kwh <= (soc_max - soc) / charge_efficiency
   ```

3. 在放电电价类型内，储能对剩余负荷放电。

   ```text
   discharge_to_load_kwh <= bess_power_kw * dt_hours
   discharge_to_load_kwh <= (soc - soc_min) * discharge_efficiency
   ```

4. 如果允许电网充电，并且当前标准化后的 `price_type` 属于充电电价类型，则用电网补充储能。

5. 剩余负荷由电网购电。

6. 剩余风光电量作为余电上网。

储能 SOC 更新关系：

```text
soc_t = soc_{t-1}
        + charge_kwh * charge_efficiency
        - discharge_kwh / discharge_efficiency
```

SOC 边界：

```text
soc_min = energy_kwh * soc_min_pct
soc_max = energy_kwh * soc_max_pct
```

无储能或储能容量为 0 时，算法退化为：

```text
风光先供负荷
负荷缺口由电网购电
风光超出负荷部分余电上网
```

### 3. 月度结算

实现位置：

```text
settlement/monthly.py
```

结算模型把时序调度结果按月聚合，形成业主侧和投资方侧指标。

无项目基准成本：

```text
baseline_grid_cost_t = load_kw_t * dt_hours_t * price_t
```

有项目后电网购电成本：

```text
grid_purchase_cost_t = grid_buy_kwh_t * price_t
```

PPA 结算电量当前口径：

```text
ppa_energy_kwh_t = renewable_to_load_kwh_t + charge_from_renewable_kwh_t
```

PPA 收入或业主 PPA 成本：

```text
ppa_revenue_t = ppa_energy_kwh_t * ppa_price
```

余电上网收入：

```text
export_revenue_t = grid_sell_kwh_t * export_price
```

业主有项目综合成本：

```text
with_project_owner_cost =
    grid_purchase_cost
  + transmission_adder_cost
  + deviation_penalty_cost
  + basic_charge
  + demand_charge
  + ppa_cost_to_owner
```

业主节费：

```text
owner_saving = baseline_grid_cost - with_project_owner_cost
owner_saving_pct = owner_saving / baseline_grid_cost
```

投资方收入：

```text
investor_revenue = ppa_cost_to_owner + export_revenue
```

当前已经支持的结算扩展参数：

1. `basic_charge_per_month`：每月固定基本电费。
2. `demand_charge_per_kw_month`：需量电费，按月最大电网购电功率估算。
3. `transmission_price_adder`：输配电价附加。
4. `deviation_penalty_per_kwh`：偏差考核费用率。

这些参数目前是占位口径，用于在业务规则未完全确认时保持算法接口稳定。

### 4. 财务测算

实现位置：

```text
finance/irr.py
```

CAPEX 当前口径：

```text
capex =
    wind_capacity_kw * capex_wind_per_kw
  + pv_capacity_kw * capex_pv_per_kw
  + bess_power_kw * capex_bess_power_per_kw
  + bess_energy_kwh * capex_bess_energy_per_kwh
```

年度现金流：

```text
cashflow_0 = -capex

cashflow_year =
    base_investor_revenue * (1 - renewable_degradation_pct)^(year - 1)
  - capex * fixed_om_pct_of_capex
  - bess_replacement_cost_if_any
```

储能更换成本：

```text
bess_replacement_cost =
    (bess_power_kw * capex_bess_power_per_kw
   + bess_energy_kwh * capex_bess_energy_per_kwh)
  * bess_replacement_cost_pct
```

项目税前 IRR：

```text
NPV(r) = sum(cashflow_y / (1 + r)^y)
IRR = r where NPV(r) = 0
```

当前用 `scipy.optimize.brentq` 在 `[-0.95, 1.0]` 区间求根。若现金流没有同时出现正负值，或区间内无根，则返回 `None`。

NPV：

```text
NPV(discount_rate) = sum(cashflow_y / (1 + discount_rate)^y)
```

回收期：

```text
payback_year = 上一年度 + abs(上一年度累计现金流) / 当年现金流
```

固定 PPA 单价反求：

```text
find ppa_price
subject to compute_project_irr(ppa_price) = target_irr
```

反求同样使用二分/Brent 求根思想。若最低价已满足目标 IRR，返回下界；若最高价仍不满足，返回 `None`。

### 5. 容量搜索

实现位置：

```text
capacity_search/grid_search.py
```

容量搜索采用粗网格枚举，优点是可解释、容易回溯不可行原因，适合当前阶段和业务人员讨论方案。

搜索变量：

```text
wind_capacity_kw
pv_capacity_kw
bess_power_kw
bess_energy_kwh
ppa_price
```

算法流程：

1. 读取 YAML 中的候选数组。
2. 对候选数组做笛卡尔积枚举。
3. 对每个候选方案生成新的 `ProjectConfig`。
4. 按候选容量比例缩放风光资源曲线：

   ```text
   scaled_pv_kw = base_pv_kw * candidate_pv_capacity / base_pv_capacity
   scaled_wind_kw = base_wind_kw * candidate_wind_capacity / base_wind_capacity
   ```

5. 对候选方案运行规则调度。
6. 对调度结果做月度结算。
7. 计算候选方案财务指标：
   - `project_irr`
   - `npv_at_target_irr`
   - `payback_years`
8. 计算候选方案业主侧和政策侧指标：
   - `owner_saving`
   - `owner_saving_pct`
   - `self_use_ratio`
   - `export_ratio`
9. 判断可行性。
10. 对可行方案按 `objective_mode` 排序，选出最优方案。
11. 输出候选结果、不可行结果、最优方案和最优方案年度现金流。

当前可行性约束：

```text
project_irr >= min_project_irr
owner_saving_pct >= min_owner_saving_pct
self_use_ratio >= min_self_use_ratio, if configured
export_ratio <= max_export_ratio, if configured
```

不可行原因字段：

```text
project_irr_unavailable
project_irr_below_min
owner_saving_pct_below_min
self_use_ratio_below_min
export_ratio_above_max
```

## 已实现的 6 个版本

当前已经实现 MVP、V1、V2、V3、V4、V5 共 6 个版本。它们复用同一套输入、调度、结算和财务模块，区别主要在是否做容量搜索，以及容量搜索的最优排序目标。

### 统一数学符号和运筹优化视角

为了理解 MVP 到 V5 的差异，先定义统一符号。

时间集合：

```text
T = {1, 2, ..., n}
```

月份集合：

```text
M = {1, 2, ..., 12}
```

容量和价格决策变量：

```text
x = (W, P, Bp, Be, q)

W  = wind_capacity_kw
P  = pv_capacity_kw
Bp = bess_power_kw
Be = bess_energy_kwh
q  = ppa_price
```

时序输入：

```text
L_t       = load_kw at time t
C_t       = grid price at time t
R_t(W,P)  = pv_kw(P) + wind_kw(W)
Delta_t   = dt_hours
```

调度输出：

```text
G_t       = grid_buy_kwh
E_t       = grid_sell_kwh
U_t       = renewable_to_load_kwh
S_t       = state of charge
Ch_t      = total charge_kwh
Dis_t     = discharge_to_load_kwh
```

投资方收益函数：

```text
Revenue_inv(x) =
    sum_t ppa_energy_t(x) * q
  + sum_t grid_sell_t(x) * export_price
```

业主节费函数：

```text
Saving_owner(x) =
    BaselineCost - WithProjectOwnerCost(x)

SavingRatio_owner(x) =
    Saving_owner(x) / BaselineCost
```

投资方 IRR 函数：

```text
IRR(x) = r

where:
    sum_y Cashflow_y(x) / (1 + r)^y = 0
```

当前实现可以从运筹优化角度理解为一个“外层离散搜索 + 内层确定性仿真 + 财务评价”的模型：

```mermaid
flowchart LR
    A["候选集合 S: 风/光/储/PPA"] --> B["逐候选枚举 x in S"]
    B --> C["资源曲线容量缩放 R_t(x)"]
    C --> D["规则调度仿真"]
    D --> E["月度结算"]
    E --> F["现金流与 IRR/NPV"]
    F --> G["约束过滤"]
    G --> H["按 objective_mode 字典序排序"]
```

运筹建模形式可以写成：

```text
choose x in S

subject to:
    IRR(x) >= IRR_min
    SavingRatio_owner(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured

maximize:
    objective_mode dependent objective
```

其中 `S` 是 YAML 中候选容量和 PPA 单价的笛卡尔积：

```text
S =
    W_candidates
  x P_candidates
  x Bp_candidates
  x Be_candidates
  x q_candidates
```

当前没有把储能调度写成 LP/MILP 求解器，而是用规则策略生成调度结果。因此当前运筹结构是：

```text
外层：离散组合优化
内层：规则仿真，不求解连续调度优化
```

后续如果将储能调度升级为优化调度，可将内层替换为线性规划或混合整数规划，例如：

```text
minimize or maximize dispatch objective

subject to:
    load balance
    renewable allocation balance
    SOC transition
    charge/discharge power limits
    SOC lower/upper bounds
    optional no-simultaneous-charge-discharge constraints
```

### MVP：固定方案测算与 PPA 反求

配置文件：

```text
configs/mvp_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_mvp_demo \
  --config src/investment_estimation/configs/mvp_demo.yaml
```

算法目标：

```text
在给定固定风光储容量、固定 PPA 价格、负荷、电价和风光资源后，
计算项目税前 IRR，并反求达到目标 IRR 所需的最低固定 PPA 单价。
```

详细运行逻辑：

1. 从 YAML 读取输入路径、项目参数、储能参数和财务参数。
2. 如 `sample_data.enabled=true`，生成模拟负荷、电价和风光资源 CSV。
3. 读取三类 CSV 并构建统一时序主表。
4. 运行规则型储能调度。
5. 汇总月度结算。
6. 基于月度投资方收入构造年度现金流。
7. 计算 `project_irr`。
8. 在 `[0, 2]` 元/kWh 区间内反求达到 `target_irr` 的 PPA 单价。
9. 输出：
   - `results/mvp_dispatch_timeseries.csv`
   - `results/mvp_monthly_settlement.csv`
   - 命令行摘要 `project_irr` 和 `target_ppa_price`

算法原理：

MVP 是单方案评价模型。它不搜索容量，只回答当前配置是否有经济性，以及如果投资方要求目标 IRR，固定 PPA 单价至少需要多少。

数学模型：

MVP 中容量和 PPA 初始价格都是给定参数，不把 `x` 作为搜索变量：

```text
x_fixed = (W0, P0, Bp0, Be0, q0)
```

第一步是固定方案评价：

```text
Evaluate:
    IRR(x_fixed)
    SavingRatio_owner(x_fixed)
    NPV(x_fixed)
```

第二步是单变量反求 PPA 价格。此时容量固定，只把 `q` 作为未知数：

```text
find q*

subject to:
    IRR(W0, P0, Bp0, Be0, q*) = target_irr
    q_low <= q* <= q_high
```

等价求根问题：

```text
f(q) = IRR(W0, P0, Bp0, Be0, q) - target_irr
find q* where f(q*) = 0
```

当前实现使用 Brent 求根。该方法依赖 `q` 与投资方收入近似单调：

```text
q increases
  -> PPA revenue increases
  -> annual cashflow increases
  -> IRR increases
```

运筹视角：

MVP 不是组合优化模型，而是“固定方案仿真评价 + 单变量非线性方程求解”。它适合回答单个项目配置是否达标，以及目标 IRR 对应的 PPA 价格边界。

### V1：基础 capacity_search

配置文件：

```text
configs/v1_capacity_search_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v1_capacity_search_demo.yaml
```

算法目标：

```text
在风、光、储和 PPA 候选组合中做粗网格搜索，
筛选同时满足投资方 IRR 和业主节费比例约束的方案，
并按投资方 IRR 优先规则选出最优方案。
```

V1 显式配置：

```yaml
search:
  objective_mode: investor_irr_first
```

详细运行逻辑：

1. 读取 v1 YAML 搜索配置。
2. 枚举：
   - 风电容量候选值。
   - 光伏容量候选值。
   - 储能功率候选值。
   - 储能容量候选值。
   - 固定 PPA 单价候选值。
3. 对每个候选方案缩放风光资源曲线。
4. 运行调度、月度结算和财务测算。
5. 计算 `project_irr`、`owner_saving_pct`、`self_use_ratio`、`export_ratio`。
6. 按约束判断可行性。
7. 对可行候选按以下规则排序：

   ```text
   先 project_irr
   再 owner_saving_pct
   再 npv_at_target_irr
   ```

8. 输出候选表、不可行表、最优摘要和年度现金流。

算法原理：

V1 是“约束过滤 + 投资方收益优先排序”的离散搜索模型。它不是连续优化器，而是在有限候选集合中选出最优可行方案。

数学模型：

V1 的候选集合为：

```text
S_v1 =
    W_candidates
  x P_candidates
  x Bp_candidates
  x Be_candidates
  x q_candidates
```

对每个候选 `x in S_v1`，先通过调度和结算得到：

```text
IRR(x)
SavingRatio_owner(x)
SelfUseRatio(x)
ExportRatio(x)
NPV(x)
Payback(x)
```

可行域：

```text
F_v1 = {
    x in S_v1 |
        IRR(x) >= IRR_min
        SavingRatio_owner(x) >= Saving_min
        SelfUseRatio(x) >= SelfUse_min, if configured
        ExportRatio(x) <= Export_max, if configured
}
```

目标函数采用字典序最大化，而不是单一加权求和：

```text
maximize lexicographic key:
    K_v1(x) = (IRR(x), SavingRatio_owner(x), NPV(x))

subject to:
    x in F_v1
```

字典序含义是：先比较第一项；第一项相同或非常接近时，再比较第二项；之后再比较第三项。

运筹视角：

V1 是有限集合上的离散可行性筛选与字典序优化：

```text
argmax_{x in F_v1} K_v1(x)
```

这种方法的优点是可解释、易调试、能直接输出不可行原因；缺点是精度受候选网格影响，不能保证连续变量意义下的全局最优。

### V2：目标模式切换 capacity_search

配置文件：

```text
configs/v2_owner_saving_first_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v2_owner_saving_first_demo.yaml
```

算法目标：

```text
保留 V1 的 capacity_search 流程，
通过 objective_mode 改变最优方案排序规则，
使同一套搜索算法支持业主节费比例优先。
```

V2 显式配置：

```yaml
search:
  objective_mode: owner_saving_first
```

详细运行逻辑：

1. 复用 V1 的候选枚举、调度、结算、财务测算和可行性约束。
2. 可行性仍要求：

   ```text
   project_irr >= min_project_irr
   owner_saving_pct >= min_owner_saving_pct
   ```

3. 最优排序切换为：

   ```text
   先 owner_saving_pct
   再 project_irr
   再 npv_at_target_irr
   ```

4. 输出中写入：
   - `objective_mode`
   - `objective_value`
   - `ranking_primary_metric`
   - `ranking_secondary_metric`

算法原理：

V2 体现“双目标 + 约束切换”的第一步：两个核心指标都计算，但通过配置决定哪一个作为主排序目标。它没有复制新的搜索模块，而是把排序规则参数化。

数学模型：

V2 仍使用与 V1 相同的候选集合和可行域：

```text
S_v2 = S_v1
F_v2 = F_v1
```

变化只发生在目标排序函数。V2 的 `owner_saving_first` 模式为：

```text
maximize lexicographic key:
    K_v2(x) = (SavingRatio_owner(x), IRR(x), NPV(x))

subject to:
    x in F_v2
```

与 V1 的差异：

```text
V1: K(x) = (IRR, SavingRatio, NPV)
V2: K(x) = (SavingRatio, IRR, NPV)
```

运筹视角：

V2 将双目标问题转换为可配置的字典序多目标优化。它没有使用加权目标：

```text
alpha * IRR + beta * SavingRatio
```

原因是当前阶段 `alpha` 和 `beta` 很难用业务口径校准。字典序排序更符合谈判逻辑：先明确主目标，再把另一个目标作为约束或次级偏好。

可解释为：

```text
argmax_{x in F_v2} K_v2(x)
```

该模式适合业主优先初筛：先找节费比例最高的方案，再检查投资方收益表现。

### V3：以投资方 IRR 为目标，业主节费比例为约束

配置文件：

```text
configs/v3_investor_irr_target_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v3_investor_irr_target_demo.yaml
```

算法目标：

```text
在业主节费比例不低于底线的前提下，
选择投资方项目税前 IRR 最高的风光储和 PPA 方案。
```

数学形式：

```text
maximize project_irr(x)

subject to:
    project_irr(x) >= min_project_irr
    owner_saving_pct(x) >= min_owner_saving_pct
    optional policy constraints
```

其中 `x` 表示：

```text
wind_capacity_kw
pv_capacity_kw
bess_power_kw
bess_energy_kwh
ppa_price
```

详细运行逻辑：

1. 读取 V3 YAML。
2. 使用 `objective_mode=investor_irr_first`。
3. 枚举候选方案并计算所有指标。
4. 用业主节费比例作为硬约束之一。
5. 在可行方案中选择 `project_irr` 最大的方案。

算法原理：

V3 是投资方视角的绝对收益优化场景。它看的是候选方案自身 IRR 是否最高，不计算相对某个基准方案的提升幅度。

数学模型：

V3 是对 V1 投资方视角的场景化表达：

```text
maximize IRR(x)

subject to:
    x in S_v3
    IRR(x) >= IRR_min
    SavingRatio_owner(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured
```

当前实现中，为了保持排序稳定，实际采用字典序：

```text
K_v3(x) = (IRR(x), SavingRatio_owner(x), NPV(x))

argmax_{x in F_v3} K_v3(x)
```

其中：

```text
F_v3 = feasible candidate set under V3 constraints
```

经济含义：

```text
PPA price q higher
  -> investor revenue tends to increase
  -> IRR tends to increase
  -> owner saving tends to decrease
```

因此 V3 必须保留业主节费比例约束，否则模型可能倾向选择高 PPA 价格，使投资方收益最大但业主不可接受。

运筹视角：

V3 是投资方收益最大化的离散约束优化问题。它适合投资方测算或谈判底价分析：先确保业主仍有最低节费，再看投资收益能做到多高。

### V4：以业主节费比例为目标，投资方 IRR 为约束

配置文件：

```text
configs/v4_owner_saving_target_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v4_owner_saving_target_demo.yaml
```

算法目标：

```text
在投资方 IRR 不低于最低收益要求的前提下，
选择业主节费比例最高的风光储和 PPA 方案。
```

数学形式：

```text
maximize owner_saving_pct(x)

subject to:
    project_irr(x) >= min_project_irr
    owner_saving_pct(x) >= min_owner_saving_pct
    optional policy constraints
```

详细运行逻辑：

1. 读取 V4 YAML。
2. 使用 `objective_mode=owner_saving_first`。
3. 枚举候选方案并计算所有指标。
4. 用投资方最低 IRR 作为硬约束之一。
5. 在可行方案中选择 `owner_saving_pct` 最大的方案。

算法原理：

V4 是业主视角的节费优化场景。它利用 PPA 价格、容量组合和储能调度结果，在保证投资方最低收益的前提下，寻找业主成本下降最多的候选方案。

数学模型：

V4 是 V3 的角色切换：

```text
maximize SavingRatio_owner(x)

subject to:
    x in S_v4
    IRR(x) >= IRR_min
    SavingRatio_owner(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured
```

当前实现排序键：

```text
K_v4(x) = (SavingRatio_owner(x), IRR(x), NPV(x))

argmax_{x in F_v4} K_v4(x)
```

经济含义：

```text
PPA price q lower
  -> owner cost tends to decrease
  -> owner saving tends to increase
  -> investor IRR tends to decrease
```

因此 V4 必须保留投资方最低 IRR 约束，否则模型可能倾向选择低 PPA 价格，使业主节费最大但投资方不可接受。

运筹视角：

V4 是业主收益最大化的离散约束优化问题。它适合业主侧报价方案评估：在投资方收益底线之上，选择业主综合用能成本最低的方案。

### V5：投资方 IRR uplift 模式

配置文件：

```text
configs/v5_investor_irr_uplift_demo.yaml
```

入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v5_investor_irr_uplift_demo.yaml
```

算法目标：

```text
相对于一个明确的基准方案，
选择投资方 IRR 提升幅度最大的候选方案，
同时保证候选方案满足投资方 IRR 和业主节费比例约束。
```

数学形式：

```text
baseline_project_irr = IRR(baseline_project)
candidate_project_irr(x) = IRR(candidate_project_x)
irr_uplift(x) = candidate_project_irr(x) - baseline_project_irr

maximize irr_uplift(x)

subject to:
    candidate_project_irr(x) >= min_project_irr
    candidate_owner_saving_pct(x) >= min_owner_saving_pct
    optional policy constraints
```

详细运行逻辑：

1. 读取 V5 YAML。
2. 使用 `objective_mode=investor_irr_uplift`。
3. 读取 `baseline_project`。
4. 用 `baseline_project` 覆盖基准风光储容量和 PPA 价格。
5. 对基准方案运行资源缩放、调度、结算和财务测算。
6. 得到：
   - `baseline_project_irr`
   - `baseline_owner_saving_pct`
7. 枚举候选方案。
8. 对每个候选方案计算：
   - `candidate_project_irr`
   - `candidate_owner_saving_pct`
   - `irr_uplift`
9. 按约束筛选可行方案。
10. 对可行方案按以下规则排序：

    ```text
    先 irr_uplift
    再 candidate_project_irr
    再 candidate_owner_saving_pct
    ```

11. 输出基准指标、候选指标和 uplift 指标。

算法原理：

V5 与 V3 的差异是：

```text
V3 看绝对 IRR：哪个候选方案自身 project_irr 最高。
V5 看相对提升：哪个候选方案相对于 baseline_project 的 IRR 增量最大。
```

当前 V5 第一版采用百分点差值：

```text
irr_uplift = candidate_project_irr - baseline_project_irr
```

如果基准方案 IRR 不可求，当前实现会直接报错，避免在缺少基准收益率时输出没有业务含义的提升值。

数学模型：

V5 引入基准方案：

```text
x_base = (W_base, P_base, Bp_base, Be_base, q_base)
```

先计算基准指标：

```text
IRR_base = IRR(x_base)
SavingRatio_base = SavingRatio_owner(x_base)
```

再对每个候选方案计算：

```text
IRR_candidate(x) = IRR(x)
SavingRatio_candidate(x) = SavingRatio_owner(x)
IRR_uplift(x) = IRR_candidate(x) - IRR_base
```

V5 优化模型：

```text
maximize IRR_uplift(x)

subject to:
    x in S_v5
    IRR_candidate(x) >= IRR_min
    SavingRatio_candidate(x) >= Saving_min
    SelfUseRatio(x) >= SelfUse_min, if configured
    ExportRatio(x) <= Export_max, if configured
    IRR_base is available
```

当前实现排序键：

```text
K_v5(x) = (IRR_uplift(x), IRR_candidate(x), SavingRatio_candidate(x))

argmax_{x in F_v5} K_v5(x)
```

V5 与 V3 的结果可能不同。示例：

```text
方案 A: IRR_base = 8%,  IRR_candidate = 12%, IRR_uplift = 4pct
方案 B: IRR_base = 11%, IRR_candidate = 13%, IRR_uplift = 2pct
```

V3 会偏向方案 B，因为候选方案绝对 IRR 更高；V5 会偏向方案 A，因为相对基准提升更大。

运筹视角：

V5 是带基准方案的相对收益优化问题。它适合回答“优化策略相对原方案到底提升了多少”，而不只是“当前方案收益率是多少”。

当前实现采用百分点差值作为提升：

```text
IRR_uplift = IRR_candidate - IRR_base
```

暂未采用相对增长率：

```text
(IRR_candidate - IRR_base) / abs(IRR_base)
```

原因是百分点差值更符合项目 IRR 谈判表达，也避免基准 IRR 接近 0 时相对增长率失真。

## objective_mode 对照表

| 模式 | 目标含义 | 主排序指标 | 次排序指标 | 典型版本 |
|---|---|---|---|---|
| `investor_irr_first` | 投资方 IRR 优先 | `project_irr` | `owner_saving_pct` | V1、V3 |
| `owner_saving_first` | 业主节费比例优先 | `owner_saving_pct` | `project_irr` | V2、V4 |
| `investor_irr_uplift` | 投资方 IRR 相对基准提升优先 | `irr_uplift` | `candidate_project_irr` | V5 |

## 输出文件

MVP 输出：

```text
results/mvp_dispatch_timeseries.csv
results/mvp_monthly_settlement.csv
```

V1-V5 输出：

```text
results/v*_candidate_results.csv
results/v*_best_summary.csv
results/v*_infeasible_reasons.csv
results/v*_annual_cashflows.csv
```

候选结果关键字段：

1. 容量与价格：
   - `wind_capacity_kw`
   - `pv_capacity_kw`
   - `bess_power_kw`
   - `bess_energy_kwh`
   - `ppa_price`
2. 投资方指标：
   - `project_irr`
   - `candidate_project_irr`
   - `npv_at_target_irr`
   - `payback_years`
   - `baseline_project_irr`
   - `irr_uplift`
3. 业主指标：
   - `owner_saving`
   - `owner_saving_pct`
   - `candidate_owner_saving_pct`
   - `baseline_owner_saving_pct`
4. 电量和政策指标：
   - `renewable_generation_kwh`
   - `ppa_energy_kwh`
   - `export_energy_kwh`
   - `self_use_ratio`
   - `export_ratio`
5. 可行性和目标模式：
   - `is_feasible`
   - `infeasible_reasons`
   - `objective_mode`
   - `objective_value`
   - `ranking_primary_metric`
   - `ranking_secondary_metric`
   - `constraint_min_project_irr`
   - `constraint_min_owner_saving_pct`

## 已验证命令

```bash
PYTHONPATH=src ./.venv/bin/python -m compileall src/investment_estimation
PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_investment_estimation_v1.py -q

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_mvp_demo \
  --config src/investment_estimation/configs/mvp_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v1_capacity_search_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v2_owner_saving_first_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v3_investor_irr_target_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v4_owner_saving_target_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v5_investor_irr_uplift_demo.yaml
```

## 当前边界和后续扩展

当前已经实现的是可运行、可解释的算法闭环，但仍有明确边界：

1. 风光资源物理仿真未在本目录内重写，当前通过资源 CSV 接入。
2. 负荷模型当前直接读取典型年 CSV，尚未从历史电费单反推负荷曲线。
3. 电价模型当前直接读取全年分时电价 CSV，尚未实现行政日历自动生成。
4. 储能调度当前是规则策略，尚未引入线性规划、混合整数规划或滚动优化。
5. 财务口径当前是项目税前 IRR，尚未实现税后 IRR、资本金 IRR、融资还款、折旧和税费。
6. 结算中的基本电费、需量电费、输配电价和偏差考核仍是占位口径，需要后续根据真实业务规则细化。
7. V5 的 `baseline_project` 需要业务侧明确基准方案定义，当前示例使用模拟基准。
8. 当前搜索是粗网格枚举，后续可扩展为分层粗细搜索、Pareto 前沿或优化模型。
