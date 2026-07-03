# 投资测算模型体系规划

## 原始需求描述

```text
投资测算模型体系介绍：
* 模型框架层次
    - 基础输入层：以风光资源建模、负荷模型及电价模型作为典型输入，其中风光资源和出力模型已可根据定位坐标建模，需合作方提供风电年发电利用小时数反向约束；负荷模型根据业主历史电费单和相关数据构建；电价模型目前基于电网代购电行政分时分类结合业主实际情况建立。
    - 运行仿真层：以 8760 点位或 15 分钟节点进行动态模拟平衡，对储能调度策略单独仿真模拟，每月电量结算和电价提升优势有单独算法或模型。
    - 财务测算层：以投资方的 IRR 整体性提升或业主的节费比例提升为关键效果点进行倒推和反馈。
* 输入边界条件
    - 利益方约束：能源投资方承担风光储全部投资，其 IRR 边界作为硬约束条件，也存在用电方 PPA 价格锁定后反向求解 IRR 的情况。
    - 其他约束：包括风资源可开发容量、风光储投建规模上限比例、政策性边界（硬性配储要求、自发自用比例、输配电价等）、特殊类型负荷相关约束（是否可中断、全年用电量特质、保电要求等），以及 EPC 造价、融资成本、运维费用、储能更换等前期输入条件。
```

## 总体判断

该体系不是单一的 IRR 计算器，而是一个“资源-负荷-价格-调度-结算-财务-反向优化”的组合算法体系。它至少需要四类模型共同闭环：

1. 输入生成模型：把位置、账单、价格规则、合同条件转换为可仿真的时间序列和约束。
2. 运行仿真模型：在 8760 小时或 15 分钟粒度下计算风、光、储、负荷、电网之间的能量流。
3. 结算收益模型：把时序仿真结果汇总为月度账单、PPA 收入、节费、电价优势和政策约束指标。
4. 投资反推优化模型：围绕投资方 IRR 与业主节费比例，反向搜索 PPA 价格、风光储容量和配置方案。

最核心的工程闭环是：

```text
输入边界条件
  -> 逐时或 15 分钟物理仿真
  -> 月度结算
  -> 生命周期现金流
  -> IRR / 节费比例
  -> 反向调整 PPA 或风光储配置
```

### 当前实现与原始需求的对应关系

当前 `src/invest_est_models/` 已按原始需求中的“模型框架层次”和“输入边界条件”建立了可运行的模块骨架。对应关系如下：

1. 基础输入层  
   原始需求中的风光资源建模、负荷模型和电价模型，当前分别由 `data_provider/`、`configs/` 和 `config_loader/` 承接。风光资源暂不在本模块内重新仿真，而是通过 `time,pv_kw,wind_kw` 资源 CSV 接入，后续补充的风光仿真脚本只需满足该输出合同。负荷模型当前按用户确认的 `time,value` 全年典型负荷 CSV 接入。电价模型当前按 `time,price,price_type` 全年分时电价 CSV 接入，其他不确定电价或政策参数先放入 YAML 配置。

2. 运行仿真层  
   原始需求中的 8760 点位或 15 分钟动态平衡、储能调度策略仿真，由 `data_provider/` 和 `dispatch/` 承接。`data_provider/` 自动识别 `dt_hours`，使小时级和 15 分钟级数据可共用同一仿真链路；`dispatch/` 负责风光、负荷、储能、电网购电和余电上网之间的能量流计算。当前实现为规则型储能调度，v1 已支持电网充电、按 `price_type` 控制充放电时段和 SOC 边界约束。

3. 月度结算和电价优势层  
   原始需求中“每月电量结算”和“电价提升优势”的算法，目前由 `settlement/` 承接。该模块把时序能量流汇总为月度电网购电成本、PPA 成本、余电上网收益、业主节费、业主节费比例和投资方收入。v1 已增加基本电费、需量电费、输配电价附加和偏差考核的占位配置，未确认口径仍以 YAML 参数表达。

4. 财务测算层  
   原始需求中以投资方 IRR 或业主节费比例为关键效果点的财务测算，由 `finance/` 承接。当前口径为项目税前 IRR，已实现 CAPEX、年度现金流、IRR、NPV、回收期、储能更换成本和固定 PPA 单价反求。税后 IRR、资本金 IRR、融资结构、税费和折旧仍属于后续扩展。

5. 反向优化和方案筛选层  
   原始需求中“以 IRR 边界作为硬约束”和“PPA 价格锁定后反向求解 IRR”的能力，由 `finance/`、`capacity_search/` 和 `app/` 共同承接。`finance/` 支持固定配置下反求目标 IRR 所需的 PPA 单价；`capacity_search/` 在 v1 中按风、光、储容量和 PPA 候选值做粗网格搜索，并输出可行性、不可行原因、业主节费比例、自发自用比例和余电上网比例；`app/` 下的运行脚本负责把输入、调度、结算、财务和搜索串成端到端流程。

6. 输入边界条件  
   原始需求中的投资方 IRR 约束、PPA 锁价、风资源可开发容量、风光储投建比例、政策边界、特殊负荷约束、EPC 造价、融资成本、运维费用和储能更换等输入边界，当前由 `configs/` 和 `config_loader/` 统一承接。已经实现的边界包括项目税前 IRR 目标、固定 PPA 价格、风光储候选容量、CAPEX、固定运维、可再生衰减、储能更换、余电上网价格和部分结算占位参数。尚未确认的政策约束、特殊负荷约束、融资税费口径不会硬编码，后续应继续以 YAML 配置和明确字段说明进入模型。

## 基础输入层

### 风光资源建模

目标是把地理位置、资源条件和设备参数转换成逐时或 15 分钟发电序列。

> 备注：用户已确认已有现成的风光资源仿真算法脚本，后续会补充到 `src/invest_est_models/` 内。本体系第一阶段不重写风光资源物理仿真算法，只分析补充进来的现有算法是否满足测算需求，重点检查输入参数、输出格式、时间粒度、容量口径和年利用小时反向约束是否可接入本测算闭环。最小可行版本先把风光资源视为已仿真的 CSV 输入，建议字段为 `time,pv_kw,wind_kw`。

光伏侧需要实现：

1. 太阳高度角和太阳方位角计算。
2. 水平辐照到倾斜面辐照转换。
3. 组件温度修正。
4. DC 出力计算。
5. 逆变器效率和限幅。
6. 系统损耗折减。
7. 年发电小时数或年发电量校准。

风电侧需要实现：

1. 风速高度外推。
2. 空气密度修正。
3. 风机功率曲线插值。
4. 切入、额定、切出风速处理。
5. 可用率、尾流或场站损耗折减。
6. 年利用小时数反向校准。

风电年利用小时数反向约束是关键输入。合作方给出的年发电利用小时数，本质上是一个校准约束：

```text
sum(wind_generation_t) / wind_capacity = target_full_load_hours
```

可选实现方式：

1. 简单比例缩放：保持原始时序形状，只调整总电量。
2. 分段缩放：对低风速、中风速、高风速区间分别修正。
3. 分位数映射：让模拟风速或发电量分布匹配目标利用小时。
4. 约束优化校准：最小化时序调整幅度，同时满足年利用小时目标。

建议第一版采用“分位数映射 + 年电量归一化”。它比单纯比例缩放更能保持资源分布形态，也不需要一开始引入复杂物理模型。

### 负荷模型

目标是把业主历史电费单和相关数据转换为未来逐时或 15 分钟负荷曲线。

> 备注：用户已确认当前阶段不从业主历史电费单反推负荷曲线。最小可行版本中，负荷作为每个场景的直接输入 CSV，字段固定为 `time,value`，表示该场景下一整年的典型负荷序列。`value` 在当前实现中按平均功率 `kW` 解释，并通过相邻时间戳自动推断 `dt_hours` 后转换为 `kWh`。

如果只有历史电费单，需要从以下账单信息反推负荷结构：

1. 月用电量。
2. 峰平谷电量。
3. 最大需量。
4. 基本电费。
5. 力调电费。
6. 偏差或考核费用。

负荷曲线重构需要实现：

1. 典型日模板生成。
2. 工作日、周末、节假日形态区分。
3. 峰平谷电量比例约束。
4. 月电量归一化。
5. 最大需量校准。
6. 异常点识别和修正。

如果存在历史 15 分钟或小时级负荷数据，还需要实现：

1. 趋势分解。
2. 季节性和周内周期识别。
3. 气温敏感性建模。
4. 负荷增长率情景。
5. 多情景负荷生成。

特殊类型负荷应被转换为可计算约束：

1. 不可中断负荷下限。
2. 可中断负荷容量。
3. 最大中断次数。
4. 最大连续中断时长。
5. 中断补偿成本。
6. 保电时段和关键负荷要求。

### 电价模型

目标是把行政分时分类电价、业主实际执行口径、PPA 价格、输配电价和上网电价转换为结算价格序列。

> 备注：用户已确认分时电价不需要通过账单反推。最小可行版本中，电价作为每个场景的一整年输入 CSV，字段固定为 `time,price,price_type`，其中 `price` 按 `元/kWh` 解释，`price_type` 用于储能规则调度。其他暂不确定的电价数据，如输配电价、政府性基金、绿证收益、偏差考核等，后续如果算法需要，统一用配置参数表达，并在样例数据或配置说明中标明含义，暂不硬编码业务规则。

需要实现：

1. 分时电价日历。
2. 尖峰、峰、平、谷时段映射。
3. 工作日、周末、节假日价格切换。
4. 电网购电价序列。
5. PPA 合同价格序列。
6. 余电上网价格序列。
7. 输配电价和政府性基金附加。
8. 未来电价增长情景。

业主实际电价校准需要从历史账单反推：

1. 实际平均电价。
2. 峰平谷加权价格。
3. 基本电费口径。
4. 力率调整影响。
5. 偏差考核影响。

PPA 合同价格模型至少应支持：

1. 固定 PPA 价格。
2. 按电网价格折扣。
3. 峰平谷差异化 PPA。
4. 阶梯价格。
5. 保底消纳价格。
6. 超额收益分享机制。

## 运行仿真层

### 动态能量平衡

目标是在每个时间节点模拟负荷、风光出力、储能充放电、电网购售电之间的能量流。

核心变量包括：

1. 负荷 `load_t`。
2. 光伏出力 `pv_t`。
3. 风电出力 `wind_t`。
4. 储能充电 `charge_t`。
5. 储能放电 `discharge_t`。
6. 储能 SOC `soc_t`。
7. 电网购电 `grid_buy_t`。
8. 余电上网 `grid_sell_t`。
9. 自发自用电量 `self_use_t`。
10. 弃电 `curtail_t`。

基本平衡关系：

```text
load_t = self_use_t + discharge_to_load_t + grid_buy_t
renewable_t = self_use_t + charge_from_renewable_t + grid_sell_t + curtail_t
soc_t = soc_{t-1} + charge_t * eta_ch * dt - discharge_t / eta_dis * dt
```

需要实现的算法：

1. 能量流分配。
2. 储能 SOC 状态转移。
3. 并网功率约束。
4. 可再生出力消纳优先级。
5. 弃电计算。
6. 自发自用比例计算。
7. 电网购电和余电上网分解。
8. 小时级与 15 分钟级时间尺度切换。

### 储能调度策略仿真

目标是决定储能什么时候充电、什么时候放电，以提升投资方收益、业主节费或系统消纳能力。

第一类是规则策略，适合最小可行版本：

1. 谷充峰放。
2. 光伏或风电余电优先充电。
3. 需量削峰。
4. 保电 SOC 预留。
5. 固定 SOC 上下限。

第二类是优化调度，适合正式测算：

1. 线性规划。
2. 混合整数规划。
3. 滚动时域优化。
4. 多目标优化，包括节费、削峰、消纳和寿命损耗。

第三类是带退化成本的调度：

1. 等效循环次数计算。
2. 雨流计数。
3. 每次充放电退化成本估计。
4. 储能更换年份推算。

### 月度电量结算

目标是把逐时或 15 分钟仿真结果汇总成月度账单口径。

需要实现：

1. 月度尖峰、峰、平、谷电量统计。
2. 月最大需量计算。
3. 基本电费计算。
4. 电度电费计算。
5. PPA 结算电费计算。
6. 余电上网收益计算。
7. 储能套利收益计算。
8. 风光自发自用节费计算。
9. 偏差或考核费用计算。
10. 业主节费比例计算。

该层最容易出错的是“仿真口径”和“账单口径”不一致。每个时间点的能量流最终归属到哪一类结算项，必须在算法中明确定义。

### 电价提升优势算法

“电价提升优势”可以理解为两类效果：

1. 对投资方：PPA 价格或未来电价越高，项目收入越高，IRR 越好。
2. 对业主：锁定价格低于电网购电价格，节费越多。

需要实现：

1. 电网价格基准模型。
2. PPA 合同价格模型。
3. 价差收益计算。
4. 未来电价增长情景。
5. 电价敏感性分析。
6. PPA 价格反向求解。

基本收益关系：

```text
业主节费 = 原始电网购电成本 - 项目后综合用能成本
投资方收入 = PPA 售电收入 + 余电上网收入 + 其他收益
```

## 财务测算层

### 投资方 IRR 模型

目标是从项目全生命周期现金流计算投资方收益率，并把 IRR 作为硬约束或目标。

CAPEX 模型应包括：

1. 风电 EPC。
2. 光伏 EPC。
3. 储能 EPC。
4. 并网费用。
5. 土地或屋顶费用。
6. 设计、管理、开发费用。
7. 预备费。

年度收入模型应包括：

1. PPA 售电收入。
2. 余电上网收入。
3. 容量收益。
4. 补贴或绿证收益。
5. 需量管理收益分成。

年度成本模型应包括：

1. 运维费用。
2. 保险。
3. 租赁。
4. 融资利息。
5. 税费。
6. 储能更换。
7. 组件或风机衰减。
8. 逆变器更换。

现金流基本形式：

```text
净现金流 = 收入 - 运维成本 - 税费 - 还本付息 - 更换成本
```

IRR 是使 NPV 为 0 的折现率：

```text
NPV = sum(CF_y / (1 + r)^y) = 0
```

求解算法可采用二分法、牛顿法或 Brent 方法。优先采用二分法或 Brent 方法，因为对异常现金流更稳定。

### 业主节费比例模型

目标是衡量项目后业主相比原用电方案节省多少。

```text
业主节费额 = 无项目电费 - 有项目综合用能成本
业主节费比例 = 业主节费额 / 无项目电费
```

有项目综合用能成本应包括：

1. 剩余电网购电费。
2. PPA 购电费。
3. 基本电费变化。
4. 力调电费变化。
5. 储能服务费。
6. 其他合同费用。

## 反向求解与配置优化

原始需求中存在两个关键反向问题：

1. 给定投资方 IRR 下限，反求 PPA 价格、装机容量或配置方案。
2. 给定 PPA 价格，反求投资方 IRR。

### 单变量反求

固定装机规模和调度策略时，可以反求最低 PPA 价格，使投资方 IRR 达标。

适用算法：

1. 二分法。
2. 单调搜索。
3. Brent 求根。

该类问题的关键前提是目标函数近似单调：

```text
PPA 价格上升 -> 投资方收入上升 -> IRR 上升
```

### 多变量配置优化

当风、光、储容量同时可变时，需要搜索满足约束的配置方案：

```text
IRR >= IRR_min
业主节费比例 >= saving_min
自发自用比例 >= policy_min
wind_capacity <= wind_capacity_max
pv_capacity <= pv_capacity_max
bess_power <= bess_power_max
bess_energy <= bess_energy_max
```

适用算法：

1. 网格搜索。
2. 分层粗细搜索。
3. 启发式搜索。
4. 线性或混合整数规划。
5. 非线性优化。
6. 多目标 Pareto 搜索。

建议第一版采用“粗网格搜索 + 局部细化搜索”。它可解释性强，便于和业务人员讨论，也容易定位不可行原因。

### 双边可行域分析

该体系本质上涉及投资方和业主两个利益主体，需要找到双方都可接受的区间：

```text
投资方要求：IRR >= IRR_min
业主要求：节费比例 >= saving_min
```

由此可得：

1. PPA 价格下限：投资方可接受的最低价格。
2. PPA 价格上限：业主仍有节费的最高价格。
3. 若下限小于等于上限，项目存在商业可行区间。
4. 若下限大于上限，项目经济性不可行。

这是整个体系中最重要的商业判断算法之一。

## 输入边界条件和约束体系

### 利益方约束

投资方约束：

```text
IRR >= IRR_min
NPV >= 0
回收期 <= payback_max
DSCR >= DSCR_min
```

业主约束：

```text
节费比例 >= saving_min
PPA 价格 <= 可接受上限
供电可靠性 >= 要求
```

合同约束：

```text
PPA 电量 <= 实际消纳电量
保底电量 >= 合同下限
合同期限 = N 年
价格调整规则满足合同定义
```

### 资源与容量约束

```text
0 <= wind_capacity <= wind_capacity_max
0 <= pv_capacity <= pv_capacity_max
0 <= bess_power <= bess_power_max
0 <= bess_energy <= bess_energy_max
```

比例和接入约束：

```text
bess_power >= renewable_capacity * 配储功率比例
bess_energy >= renewable_capacity * 配储时长比例
wind_capacity + pv_capacity <= 接入容量上限
```

### 政策性边界约束

需要算法化的政策约束包括：

1. 硬性配储要求。
2. 自发自用比例要求。
3. 余电上网限制。
4. 输配电价规则。
5. 分布式接入容量限制。
6. 电价执行规则。
7. 绿电或绿证收益规则。

这些规则不应硬编码在算法中，应作为可配置边界条件进入模型。

### 特殊负荷约束

不可中断负荷要求：

```text
served_load_t >= critical_load_t
```

可中断负荷约束：

```text
interrupt_power_t <= interrupt_power_max
interrupt_count <= interrupt_count_max
continuous_interrupt_duration <= duration_max
```

保电约束：

```text
soc_t >= emergency_soc_min
```

或：

```text
available_backup_energy >= critical_load * backup_duration
```

## 算法模块清单

从零构建时，建议拆成以下模块：

> 备注：用户要求在 `src/invest_est_models/` 内制定每个模块的模型脚本或文件夹命名和设计方案。当前最小可行版本采用轻量平铺模块，后续风光仿真脚本补充进来后再按复杂度决定是否拆成子目录。

当前命名和职责设计：

1. `config_loader/`  
   存放 `ProjectConfig`、`BESSConfig`、`FinanceConfig` 和 YAML 加载逻辑。不确定的输入先作为配置参数进入模型，并在字段名中保留业务含义。

2. `data_provider/`  
   统一负责数据接入和模拟数据生成。读取并校验 CSV 输入，负荷 CSV 为 `time,value`，电价 CSV 为 `time,price,price_type`，资源 CSV 为 `time,pv_kw,wind_kw`。同时负责时间对齐、`dt_hours` 自动识别和样例 CSV 生成。不使用 `io/` 或 `sample_data/` 目录命名。

3. `dispatch/`  
   实现最小可行版本的规则储能调度和能量平衡。当前口径允许储能从电网充电，具体充放电时段由 `price_type` 和配置参数控制。

4. `settlement/`  
   实现月度结算，将时序调度结果汇总为电网购电成本、PPA 成本、余电上网收益、业主节费和投资方收入。

5. `finance/`  
   实现税前项目 IRR、CAPEX、年度现金流和固定 PPA 价格反求。

6. `app/`  
   存放可运行入口脚本，替代原 `examples/` 命名。当前入口从 YAML 读取测算场景配置，并直接承载 MVP 与 v1 的运行编排流程，不再保留独立 `pipeline/` 模块。

7. `configs/`  
   存放每个测算场景的 YAML 配置文件，场景参数不再硬编码在 app 脚本中。

8. `dataset/`  
   存放样例或临时输入 CSV。真实项目数据接入前，先使用 `data_provider/` 生成可复现实验数据。

9. `results/`  
    存放端到端示例输出，如月度结算表和逐时调度表。

1. 数据标准化模块  
   时间索引统一、小时和 15 分钟转换、缺失值处理、异常值处理、单位转换、月度账单到时序数据映射。

2. 风光资源模块  
   光伏出力模型、风电出力模型、年利用小时校准、出力衰减模型、资源情景生成。

3. 负荷模块  
   账单解析、负荷曲线重构、负荷预测、特殊负荷约束建模。

4. 电价模块  
   分时电价日历、电网购电价、PPA 价格、上网电价、输配电价、未来电价情景。

5. 运行仿真模块  
   能量平衡、风光消纳、弃电、购售电、储能 SOC 演化、保电约束。

6. 储能调度模块  
   规则调度、优化调度、削峰填谷、需量控制、退化成本、更换周期。

7. 月度结算模块  
   电度电费、基本电费、PPA 结算、余电上网、偏差费用、业主节费。

8. 财务模块  
   CAPEX、OPEX、融资、税费、折旧、设备更换、现金流、IRR、NPV、回收期。

9. 反向优化模块  
   给定 IRR 求 PPA、给定 PPA 求 IRR、给定节费比例求配置、风光储容量搜索、双边可行域分析、敏感性分析。

10. 风险与情景模块  
    电价变化情景、负荷增长情景、风光资源偏差情景、投资成本变化情景、融资成本变化情景、设备衰减情景、蒙特卡洛或情景树分析。

## 最小可行版本实现计划

最小可行版本的目标是先打通一个可运行、可解释、可验证的投资测算闭环。它回答的核心问题是：

```text
在给定一年负荷、风光出力、电价、投资成本和风光储容量后，
当前固定 PPA 价格下项目税前 IRR 是多少，
以及达到目标 IRR 所需的最低固定 PPA 单价是多少？
```

### 口径约定

1. 投资方收益口径：项目税前 IRR。
2. PPA 价格模式：固定单价 PPA，用 `ppa_price` 表示，单位元/kWh。
3. 储能充电：允许从电网充电，由 `charge_price_types` 控制充电时段。
4. 余电处理：允许余电上网，由 `export_price` 控制上网价格。
5. 时间粒度：按 CSV 相邻时间戳自动识别 `dt_hours`，兼容小时级和 15 分钟级。
6. 不确定数据：如算法需要但业务口径未确认，先进入 YAML 配置或模拟数据，并标明含义。

### 当前实现进度

当前最小可执行版本已落地在 `src/invest_est_models/`，并完成以下模块：

1. `configs/`  
   已实现 `mvp_demo.yaml`，集中配置场景名称、输入输出路径、模拟数据、项目参数、储能参数和财务参数。每个变量已添加中文注释。

2. `config_loader/`  
   已实现配置 dataclass 和 YAML 加载逻辑，包括 `ProjectConfig`、`BESSConfig`、`FinanceConfig`、`PathConfig`、`SampleDataConfig`、`CaseConfig`。每个配置字段已添加中文注释。

3. `data_provider/`  
   已实现 `data_loader.py` 和 `sample_generator.py`。支持读取 `time,value` 负荷 CSV、`time,price,price_type` 电价 CSV、`time,pv_kw,wind_kw` 风光资源 CSV，并能生成模拟数据。

4. `dispatch/`  
   已实现规则型储能调度。当前逻辑为风光优先供负荷，风光余电优先充储能，低价时段允许电网充电，高价时段储能放电供负荷，剩余风光按余电上网处理。

5. `settlement/`  
   已实现月度结算。当前输出月度负荷电量、电网购电量、PPA 电量、余电上网量、无项目电网购电成本、有项目业主成本、业主节费、业主节费比例和投资方收入。

6. `finance/`  
   已实现 CAPEX、年度税前现金流、税前项目 IRR 和固定 PPA 价格反求。当前现金流考虑固定运维、可再生收入衰减和储能更换成本。

7. `app/`  
   已实现 `run_mvp_demo.py`，支持通过 `--config` 指定 YAML 场景并写出结果 CSV。MVP 的数据读取、调度、结算、财务和 PPA 反求流程直接写在该脚本中。

### 当前输出

1. `results/mvp_dispatch_timeseries.csv`  
   逐时间步输出负荷、风光、电价、储能 SOC、电网购电、余电上网、PPA 相关电量等调度结果。

2. `results/mvp_monthly_settlement.csv`  
   按月输出结算指标和收益指标。

3. 命令行摘要  
   输出 `project_irr` 和 `target_ppa_price`。

### 当前验证记录

已使用以下命令验证当前最小可执行版本：

```bash
PYTHONPATH=src ./.venv/bin/python -m compileall src/invest_est_models
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_mvp_demo --config src/invest_est_models/configs/mvp_demo.yaml
```

当前示例输出为：

```text
project_irr=0.20825878685240767
target_ppa_price=0.29445747461867944
```

### 当前未覆盖内容

1. 未实现真实风光资源仿真脚本接入，只要求后续脚本输出 `time,pv_kw,wind_kw`。
2. 未实现容量搜索，当前只支持固定容量下测算和 PPA 反求。
3. 未实现基本电费、需量电费、输配电价拆分、偏差考核、绿证和补贴。
4. 未实现优化调度、保电约束、储能退化进入调度目标。
5. 未实现税后 IRR、资本金 IRR、融资还款、折旧和税费。
6. 未实现多场景批量运行、敏感性分析和不可行诊断报告。

## v1版本

v1 版本应在最小可行版本的基础上，把当前“可运行闭环”升级为“可用于项目方案筛选的测算工具”。核心目标是：

```text
在多个测算场景下，
基于真实或模拟的负荷、电价、风光资源输入，
搜索风光储容量和 PPA 价格的可行区间，
输出投资方 IRR、业主节费、关键政策指标和不可行原因。
```

v1 当前保持为基础 `capacity_search` 能力版本。它的核心不是引入复杂优化器，而是先把候选容量、PPA 价格、调度、结算、财务测算和不可行诊断串成稳定闭环。当前最优方案排序规则为：先比较 `project_irr`，再比较 `owner_saving_pct`。这使 v1 的默认结果更接近投资方收益优先口径，但 v1 尚未显式引入 `objective_mode`，因此不把 v1 定义为最终的“投资方 IRR 目标场景”。

### v1 开发原则

1. 保持 YAML 驱动：新增业务参数必须进入 `configs/`，app 脚本不得硬编码场景参数。
2. 保持模块边界：数据接入归 `data_provider/`，调度归 `dispatch/`，结算归 `settlement/`，财务归 `finance/`，容量搜索归新增 `capacity_search/`。
3. 保持可解释：v1 优先采用规则调度、网格搜索和清晰的不可行诊断，不直接引入复杂黑箱优化。
4. 保持向后兼容：当前 `mvp_demo.yaml`、`app/run_mvp_demo.py`、结果 CSV 字段应继续可用。

### 模块开发方案

1. `data_provider/` 数据接入增强  
   继续保留当前 CSV 合同，并新增数据校验函数。校验内容包括全年完整性、时间戳重复、时间间隔一致性、缺失值、负负荷、负电价、风光出力负值。对真实风光仿真脚本的接入要求是输出统一资源 CSV：`time,pv_kw,wind_kw`。

2. `config_loader/` 配置体系增强  
   将当前配置扩展为 v1 场景配置。新增容量搜索配置、结算扩展配置、政策约束配置和敏感性分析配置。所有新增字段必须有中文注释、README 说明和默认示例。

3. `dispatch/` 调度增强  
   在当前规则调度基础上新增策略参数：是否允许电网充电、是否保留应急 SOC、是否优先削峰、是否限制余电上网。v1 仍以规则调度为主，优化调度作为后续版本预留。

4. `settlement/` 结算增强  
   新增基本电费、需量电费、输配电价附加、偏差考核占位配置和结算列。对业务口径未确认的项目，先使用配置参数表示，并在输出字段中标明是模拟口径。

5. `finance/` 财务增强  
   新增 NPV、静态回收期、动态回收期、年度现金流表输出。税前项目 IRR 保持为默认口径；税后 IRR 和资本金 IRR 先做配置和接口预留，等税费和融资口径确认后再实现。

6. 新增 `capacity_search/` 容量搜索模块  
   实现粗网格搜索，搜索变量包括 `wind_capacity_kw`、`pv_capacity_kw`、`bess_power_kw`、`bess_energy_kwh`、`ppa_price`。每组候选配置由 `app/run_capacity_search.py` 编排调用调度、结算和财务模块，输出 IRR、业主节费比例、自发自用比例、余电上网比例和约束满足状态。

7. `app/` 入口增强  
   新增 `run_capacity_search.py`，通过 `--config` 指向 v1 搜索场景 YAML。该脚本直接读取搜索配置、生成候选方案、调用 dispatch/settlement/finance、收集结果并输出排序后的候选表。

8. `configs/` 场景模板增强  
   新增 `v1_capacity_search_demo.yaml`，包含搜索范围、步长、约束阈值、结算扩展配置和输出路径。该文件应作为后续真实项目配置的模板。

9. `results/` 输出增强  
    新增候选方案表、最优方案摘要、不可行原因表和年度现金流表。建议文件包括 `v1_candidate_results.csv`、`v1_best_summary.csv`、`v1_infeasible_reasons.csv`、`v1_annual_cashflows.csv`。

### v1 里程碑

1. v1.1 数据校验和配置扩展  
   已完成。实现了数据质量校验、搜索配置 dataclass、结算扩展配置、v1 YAML 模板和 README 更新。验证标准：错误输入能给出明确异常，`mvp_demo.yaml` 仍可运行。

2. v1.2 财务和结算指标扩展  
   已完成。实现了 NPV、回收期、年度现金流表、基本电费、需量电费、输配电价附加和偏差考核占位口径。验证标准：月度结算表和年度现金流表字段稳定。

3. v1.3 容量搜索  
   已完成。实现了 `capacity_search/` 粗网格搜索和 `run_capacity_search.py`。验证标准：能输出候选方案排序表，并区分可行与不可行方案。

4. v1.4 不可行诊断和结果摘要  
   已完成。实现了不可行原因表、最优方案摘要和 README/PLAN 同步。验证标准：当 IRR、节费比例或容量边界不满足时，输出具体失败原因。

### v1 当前实现进度

当前已新增和更新以下内容：

1. `configs/v1_capacity_search_demo.yaml`  
   新增 v1 搜索场景配置，不修改 `mvp_demo.yaml`。配置包含候选容量、PPA 单价、约束阈值、结算扩展参数和 v1 输出路径。

2. `config_loader/`  
   新增 `CapacitySearchConfig` 和 `SettlementConfig`，并扩展 `PathConfig` 与 `CaseConfig` 以支持 v1 输出和搜索配置。

3. `data_provider/`  
   新增 `validate_timeseries()`，校验缺失字段、重复时间戳、缺失值、非正时间步、负负荷、负电价和负风光出力。

4. `settlement/`  
   新增基本电费、需量电费、输配电价附加和偏差考核占位列。默认值为 0 时不改变 MVP 口径。

5. `finance/`  
   新增 `compute_npv()`、`compute_payback_years()`、`annual_cashflow_table()`。

6. `capacity_search/`  
   新增粗网格搜索模块，输出候选方案表、可行状态、自发自用比例、余电上网比例和不可行原因。

7. `app/`  
   新增 `run_capacity_search.py`，通过 `--config` 运行 v1 搜索场景。MVP 与 v1 的运行流程均已直接写入 `app/` 下对应脚本，不再使用独立 `pipeline/` 模块。

8. `tests/test_invest_est_models_v1.py`  
   新增 v1 测试，覆盖配置加载、数据校验、财务指标和容量搜索。

当前 v1 示例运行输出：

```text
candidate_count=32
feasible_count=32
```

当前 v1 输出文件：

1. `results/v1_candidate_results.csv`
2. `results/v1_best_summary.csv`
3. `results/v1_infeasible_reasons.csv`
4. `results/v1_annual_cashflows.csv`

### v1 验收标准

1. `mvp_demo.yaml` 继续可运行，结果口径不破坏。
2. `v1_capacity_search_demo.yaml` 能完成一轮容量搜索。
3. 每个新增模块都有 README，并记录 MVP 已实现和 v1 进度。
4. 每个新增 YAML 字段和配置类字段都有中文注释。
5. 所有输出 CSV 字段稳定，并在对应 README 中说明业务含义。
6. 至少验证以下命令：

```bash
PYTHONPATH=src ./.venv/bin/python -m compileall src/invest_est_models
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_mvp_demo --config src/invest_est_models/configs/mvp_demo.yaml
PYTHONPATH=src ./.venv/bin/python -m invest_est_models.app.run_capacity_search --config src/invest_est_models/configs/v1_capacity_search_demo.yaml
```

## V2版本

V2 版本继续保留 `capacity_search` 模式，不新建一套重复搜索模块。V2 的目标是在 v1 基础上把“最优排序规则”显式配置化，使同一套容量搜索算法可以支持投资方优先和业主优先两种排序口径。

### V2 设计判断

V2 不需要单独构建和开发一套独立算法。原因是 V2 与 v1 的算法流程完全一致：

```text
候选容量和 PPA 枚举
  -> 规则调度
  -> 月度结算
  -> 财务测算
  -> 约束过滤
  -> 最优排序
```

V2 只改变最后一步“最优排序”的主次指标，因此应通过配置参数在 v1 的 `capacity_search/` 内切换，而不是复制出新的搜索流程。

### V2 新增配置

建议在 `CapacitySearchConfig` 中新增：

```python
objective_mode: str = "investor_irr_first"
```

建议支持的取值：

```text
investor_irr_first
owner_saving_first
```

对应 YAML 示例：

```yaml
search:
  # 目标模式：investor_irr_first 表示投资方 IRR 优先；
  # owner_saving_first 表示业主节费比例优先。
  objective_mode: owner_saving_first
```

### V2 排序规则

1. `investor_irr_first`

   ```text
   可行性约束：
       project_irr >= min_project_irr
       owner_saving_pct >= min_owner_saving_pct

   排序规则：
       先比较 project_irr
       再比较 owner_saving_pct
   ```

2. `owner_saving_first`

   ```text
   可行性约束：
       project_irr >= min_project_irr
       owner_saving_pct >= min_owner_saving_pct

   排序规则：
       先比较 owner_saving_pct
       再比较 project_irr
   ```

### V2 输出增强

建议在候选结果表和最优摘要中新增以下字段：

1. `objective_mode`：当前目标模式。
2. `objective_value`：当前模式下用于主排序的目标值。
3. `ranking_primary_metric`：主排序指标名。
4. `ranking_secondary_metric`：次排序指标名。
5. `constraint_min_project_irr`：投资方最低 IRR 约束。
6. `constraint_min_owner_saving_pct`：业主最低节费比例约束。

### V2 配置文件

新增配置文件，不修改 `mvp_demo.yaml`：

```text
configs/v2_owner_saving_first_demo.yaml
```

该文件用于演示 `owner_saving_first` 排序模式。v1 配置可继续保留当前排序结果，用于回归对照。

### V2 验收标准

1. `v1_capacity_search_demo.yaml` 仍可运行，默认结果不破坏。
2. `v2_owner_saving_first_demo.yaml` 可运行，并选择 `owner_saving_pct` 更高的可行方案。
3. 测试覆盖两个排序模式，确认同一批候选结果在不同 `objective_mode` 下可选出不同最优方案。
4. 输出 CSV 能明确说明本次运行采用的目标模式和排序依据。

### V2 当前实现进度

已完成。当前在 `CapacitySearchConfig` 中新增 `objective_mode`，并在 `capacity_search/` 中实现 `investor_irr_first` 和 `owner_saving_first` 两种排序规则。已新增 `configs/v2_owner_saving_first_demo.yaml`，候选结果表和最优摘要中已输出 `objective_mode`、`objective_value`、主次排序指标和约束阈值。

## V3版本

V3 版本定义为“以投资方 IRR 为目标，业主节费比例为约束”的模型场景。V3 不需要新建算法模块，而是在 V2 的 `objective_mode` 机制上形成明确的投资方视角配置模板和验收口径。

### V3 模型形式

V3 采用绝对 IRR 目标，即当前讨论中的方案 A：

```text
maximize project_irr(x)

subject to:
    owner_saving_pct(x) >= min_owner_saving_pct
    project_irr(x) >= min_project_irr
    self_use_ratio(x) >= min_self_use_ratio，可选
    export_ratio(x) <= max_export_ratio，可选
    风光储容量满足候选边界
```

其中 `x` 表示候选风电容量、光伏容量、储能功率、储能容量和 PPA 价格组合。

### V3 业务含义

V3 回答的问题是：

```text
在保证业主至少达到约定节费比例的前提下，
投资方能获得的最高项目 IRR 是多少，
对应的风光储容量和 PPA 价格是什么？
```

### V3 配置方案

建议新增：

```text
configs/v3_investor_irr_target_demo.yaml
```

关键配置：

```yaml
search:
  # 目标模式：投资方 IRR 优先。
  objective_mode: investor_irr_first
  # 投资方最低税前项目 IRR，作为准入约束。
  min_project_irr: 0.08
  # 业主最低节费比例，作为硬约束。
  min_owner_saving_pct: 0.05
```

### V3 开发内容

1. 复用 V2 的 `objective_mode=investor_irr_first`。
2. 在 README 和输出摘要中明确 V3 是投资方收益优先场景。
3. 在结果中突出 `project_irr`、`owner_saving_pct`、`ppa_price`、CAPEX、NPV 和回收期。
4. 增加测试：构造多个可行候选方案，确认 V3 选择 `project_irr` 最高的方案。

### V3 不包含内容

V3 不计算“IRR 提升值”。如果要计算相对基准方案的提升，应进入 V5 的 `investor_irr_uplift` 模式。

### V3 当前实现进度

已完成。V3 复用 V2 的 `objective_mode=investor_irr_first`，并新增 `configs/v3_investor_irr_target_demo.yaml` 作为投资方 IRR 优先场景配置。当前输出会把 `project_irr` 作为主目标值，并保留业主节费比例约束字段。

## V4版本

V4 版本定义为“以业主节费比例为目标，投资方 IRR 为约束”的模型场景。V4 同样不新建算法模块，而是在 V2 的 `objective_mode` 机制上形成业主视角配置模板和验收口径。

### V4 模型形式

```text
maximize owner_saving_pct(x)

subject to:
    project_irr(x) >= min_project_irr
    owner_saving_pct(x) >= min_owner_saving_pct
    self_use_ratio(x) >= min_self_use_ratio，可选
    export_ratio(x) <= max_export_ratio，可选
    风光储容量满足候选边界
```

其中 `x` 表示候选风电容量、光伏容量、储能功率、储能容量和 PPA 价格组合。

### V4 业务含义

V4 回答的问题是：

```text
在投资方收益不低于最低 IRR 要求的前提下，
最多能给业主做到多高的节费比例，
对应的风光储容量和 PPA 价格是什么？
```

### V4 配置方案

建议新增：

```text
configs/v4_owner_saving_target_demo.yaml
```

关键配置：

```yaml
search:
  # 目标模式：业主节费比例优先。
  objective_mode: owner_saving_first
  # 投资方最低税前项目 IRR，作为硬约束。
  min_project_irr: 0.08
  # 业主最低节费比例，作为准入约束或谈判底线。
  min_owner_saving_pct: 0.05
```

### V4 开发内容

1. 复用 V2 的 `objective_mode=owner_saving_first`。
2. 在 README 和输出摘要中明确 V4 是业主节费优先场景。
3. 在结果中突出 `owner_saving_pct`、`owner_saving`、`project_irr`、`ppa_price` 和业主综合用能成本。
4. 增加测试：构造多个可行候选方案，确认 V4 选择 `owner_saving_pct` 最高的方案。

### V4 当前实现进度

已完成。V4 复用 V2 的 `objective_mode=owner_saving_first`，并新增 `configs/v4_owner_saving_target_demo.yaml` 作为业主节费比例优先场景配置。当前输出会把 `owner_saving_pct` 作为主目标值，并保留投资方最低 IRR 约束字段。

## V5版本

V5 版本记录为 `investor_irr_uplift` 模式，即“以投资方 IRR 提升为目标，业主节费比例为约束”的相对提升场景。该模式对应当前讨论中的方案 B。

V5 与 V3 的区别是：

```text
V3：看候选方案自身 project_irr 是否最高。
V5：看候选方案相对基准方案的 irr_uplift 是否最高。
```

### V5 模型形式

```text
baseline_irr = IRR(基准方案)
candidate_irr(x) = IRR(候选方案 x)
irr_uplift(x) = candidate_irr(x) - baseline_irr

maximize irr_uplift(x)

subject to:
    candidate_irr(x) >= min_project_irr
    owner_saving_pct(x) >= min_owner_saving_pct
    self_use_ratio(x) >= min_self_use_ratio，可选
    export_ratio(x) <= max_export_ratio，可选
    风光储容量满足候选边界
```

### V5 业务含义

V5 回答的问题是：

```text
相对于一个已定义的基准方案，
通过调整风光储容量、PPA 价格或调度策略，
投资方 IRR 能提升多少，
同时业主节费比例是否仍满足约束？
```

### V5 必须新增的基准方案定义

V5 不能只靠当前 v1/v2 的候选搜索完成，必须先定义基准方案。基准方案可选口径包括：

1. 不配置储能，只投风光。
2. 固定当前人工经验容量和 PPA 价格。
3. 只投光伏，不投风电和储能。
4. 使用已谈判或已立项的原始方案。
5. 使用当前 v1/v3 的最优方案作为后续策略改进的基准。

未确认基准方案前，不应实现 `irr_uplift` 排序，否则“提升”没有业务含义。

### V5 配置方案

建议新增：

```text
configs/v5_investor_irr_uplift_demo.yaml
```

建议新增配置结构：

```yaml
search:
  # 目标模式：投资方 IRR 相对基准提升优先。
  objective_mode: investor_irr_uplift
  # 投资方最低税前项目 IRR，约束候选方案本身必须达标。
  min_project_irr: 0.08
  # 业主最低节费比例，作为硬约束。
  min_owner_saving_pct: 0.05

baseline_project:
  # 基准方案风电容量，单位 kW。
  wind_capacity_kw: 1000.0
  # 基准方案光伏容量，单位 kW。
  pv_capacity_kw: 800.0
  # 基准方案 PPA 价格，单位元/kWh。
  ppa_price: 0.55
  # 基准方案储能功率，单位 kW。
  bess_power_kw: 0.0
  # 基准方案储能容量，单位 kWh。
  bess_energy_kwh: 0.0
```

### V5 开发内容

1. 在配置类中新增 `baseline_project` 或等价基准方案配置。
2. 在 `capacity_search/` 中先运行基准方案，计算 `baseline_irr`。
3. 对每个候选方案计算 `candidate_irr` 和 `irr_uplift`。
4. 新增 `objective_mode=investor_irr_uplift` 排序：

   ```text
   先比较 irr_uplift
   再比较 candidate_irr
   再比较 owner_saving_pct
   ```

5. 输出新增字段：

   ```text
   baseline_project_irr
   candidate_project_irr
   irr_uplift
   baseline_owner_saving_pct
   candidate_owner_saving_pct
   ```

6. 增加测试：同一批候选方案中，确认 V5 选择 `irr_uplift` 最大的方案，而不是绝对 `project_irr` 最大的方案。

### V5 待澄清问题

1. 基准方案到底采用哪一种业务口径。
2. `irr_uplift` 是否按百分点差值计算，例如 `12% - 8% = 4pct`，还是按相对增长率计算，例如 `(12%-8%)/8%=50%`。建议优先采用百分点差值。
3. 基准方案是否也必须满足业主节费比例约束。建议默认不强制，但输出基准方案的业主节费比例用于解释。
4. 如果基准方案 IRR 不可求，应判定 V5 场景不可运行，还是允许以 NPV 提升替代。建议第一版直接报错，避免混淆口径。

### V5 当前实现进度

已完成第一版。当前新增 `BaselineProjectConfig` 和 `baseline_project` YAML 配置，并在 `capacity_search/` 中支持 `objective_mode=investor_irr_uplift`。运行时会先计算基准方案 `baseline_project_irr` 和 `baseline_owner_saving_pct`，再对候选方案计算 `candidate_project_irr`、`candidate_owner_saving_pct` 和 `irr_uplift`。第一版按百分点差值计算 `irr_uplift = candidate_project_irr - baseline_project_irr`，基准方案 IRR 不可求时直接报错。

## 后续必须澄清的问题

继续深化 V2-V5 前，需要补齐以下定义：

> 已确认口径：最小可行版本采用项目税前 IRR、固定单价 PPA、允许储能从电网充电、允许余电上网、时间粒度由 CSV 自动识别。未确认的数据如有需要先使用配置参数表示，并通过模拟数据注明含义。

1. 投资方 IRR 口径  
   已确认最小可行版本采用项目税前 IRR。后续仍需确认是否要扩展为项目税后 IRR 或资本金 IRR。税后 IRR 需要补充所得税、增值税、折旧、可抵扣项等口径；资本金 IRR 需要补充贷款比例、贷款利率、还款方式、建设期利息和还本付息计划。

2. 业主节费比例口径  
   需要确认节费比例按首年、年度平均、逐年展示，还是全生命周期累计折现节费计算。当前最小可行版本先按首年月度结算汇总计算：`节费 = 无项目电网购电成本 - 有项目综合用能成本`。

3. PPA 价格模式  
   已确认最小可行版本采用固定单价 PPA，即用配置参数 `ppa_price` 表示 `元/kWh`。后续可扩展为分时 PPA、电网电价折扣、阶梯 PPA 或保底消纳价格。

4. 储能收益归属  
   需要确认储能削峰、峰谷套利、提高自发自用比例等收益在投资方和业主之间如何分配。当前最小可行版本将 PPA 售电和余电上网计为投资方收入，将电网购电成本和 PPA 购电成本计为业主成本。

5. 基本电费口径  
   需要确认基本电费按变压器容量、合同容量还是最大需量计费。v1 可先通过配置参数模拟，字段名和 README 必须标明口径。

6. 政策约束  
   需要确认强制配储、自发自用比例、输配电价、余电上网限制、绿证收益等是否进入硬约束。当前不确定项先通过配置参数或模拟数据表示，不硬编码政策规则。

7. 风光资源脚本接入  
   用户后续会补充现有风光资源仿真算法脚本。接入前需要确认脚本输入参数、容量缩放口径、年利用小时反向约束方式和输出字段是否满足 `time,pv_kw,wind_kw` 合同。
