# `eva_Pv.ipynb` 代码功能分析

## 整体概览

`src/ba_eva/eva_Pv.ipynb` 虽然文件名看起来像“光伏测算脚本”，但实际不是单一的光伏分析 notebook，而是一个把多类试验性代码堆叠在一起的“风光储测算工作台”。从单元内容看，它至少包含以下 5 类逻辑：

1. 负荷原始数据读取、清洗、补点与 2025 年序列构造。
2. 光伏出力模拟与等效小时校验。
3. 风电出力模拟与风场年能量校准。
4. 风光固定条件下的储能容量寻优，以及风光储联合容量规划。
5. 月度统计、结果导出与绘图分析。

这个 notebook 的特点不是“结构化实现”，而是“围绕同一业务场景持续叠加试验代码”。因此它更适合作为分析素材和算法草稿来源，而不适合作为稳定模块直接复用。

## 分模块分析

### 1. 负荷数据处理

这部分代码的目标是从原始 Excel 负荷曲线出发，构造一个可供后续风光储测算使用的 2025 年负荷时序。

核心函数包括：

- `read_power_folder_raw`
- `build_daily_energy_2025`
- `fill_2025_power_by_daily_energy`
- `smooth_2024_shape`
- `shift_2024_to_2025`
- `fill_missing_days_by_nearest`

功能分工如下：

- `read_power_folder_raw` 负责批量读取目录下的 Excel 文件，统一生成 `Time` 和 `P_kw` 字段，并保留来源文件名。它只做最基础的时间列拼接和数值规范化，不处理插值、补点或异常值修正。
- `build_daily_energy_2025` 根据手工给定的月电量字典，展开成 2025 年每日电量目标，用于后续对负荷曲线进行“总量回填”。
- `fill_2025_power_by_daily_energy` 用日总电量约束来回填 2025 年负荷曲线中的缺失点。其核心思路是：先计算当天已知点电量，再把剩余日电量按照时间插值后的权重分配到缺失点。
- `smooth_2024_shape` 和 `shift_2024_to_2025` 的作用是保留历史形状特征，并将其迁移到 2025 年序列中，属于“用历史曲线形状辅助构造未来负荷”的方法。
- `fill_missing_days_by_nearest` 则进一步处理整天缺失的问题，按邻近日期的曲线形状去补齐缺口。

从数据流看，这一段的产物是一个具备 `Time` 和 `P_kw` 的年度负荷数据表，后续光伏、风电、储能寻优都围绕它展开。

### 2. 光伏出力模拟

这部分围绕 pvlib 建立了一个相对完整的光伏出力模拟和容量搜索流程。

核心函数包括：

- `simulate_pv_output`
- `validate_equivalent_hours`
- `plot_daily_pv_shape`
- `plan_pv_bess_min_capex_fast`

功能分工如下：

- `simulate_pv_output` 是核心建模入口。它基于时间索引、经纬度、装机容量、倾角、方位角、系统损耗、温度系数和云量折减等参数，通过 `pvlib` 计算光伏 AC 输出功率序列。输出是以时间为索引的 `pv_kw` 序列。
- `validate_equivalent_hours` 用于校验光伏序列的年等效利用小时数，判断模拟结果是否落在合理范围。
- `plot_daily_pv_shape` 用于抽取某一天的光伏出力曲线并作图，主要服务于结果可视化和形状检查。
- `plan_pv_bess_min_capex_fast` 则把单机光伏出力曲线和负荷数据结合起来，在给定约束下搜索最小化投资的光伏+储能方案。约束主要围绕自用率、负荷覆盖率、储能参数和投资成本展开。

这一段说明 notebook 不只是“模拟一条光伏曲线”，而是把光伏曲线直接作为容量规划的输入，服务后面的投资测算。

### 3. 风电出力模拟

这部分代码的目标是根据 ERA5-Land 气象数据和风机功率曲线，生成风电场的时序出力，并通过能量约束对结果进行回标。

核心函数/类包括：

- `fetch_era5_land_open_meteo`
- `resample_hourly_to_15min`
- `calibrate_energy_with_cap`
- `WindFarmConfig`
- `WindFarmPowerModelERA5Land`

功能分工如下：

- `fetch_era5_land_open_meteo` 通过 Open-Meteo 接口获取 ERA5-Land 小时级天气数据，主要使用风速和气温作为风电建模输入。
- `resample_hourly_to_15min` 负责把小时级气象数据插值或重采样到更细粒度，便于与 15 分钟负荷曲线对齐。
- `calibrate_energy_with_cap` 负责对原始风功率序列做年发电量校准，并施加装机上限约束。
- `WindFarmConfig` 定义风场级别的关键参数，如装机规模、轮毂高度、功率曲线参数、等效利用小时数目标、峰值比例限制等。
- `WindFarmPowerModelERA5Land` 是这部分的主模型。它通过风速高度换算、自定义功率曲线、风机配置和年能量回补逻辑，把气象输入变成风电功率序列。

这部分不仅做“风速转功率”，还显式引入了年满发小时数、峰值不超装机等工程约束，因此更接近测算模型而不是简单物理仿真。

### 4. 风光固定条件下的储能寻优

这部分在风电和光伏装机已基本确定的前提下，评估储能是否需要配置、以及需要多大容量才能满足覆盖率或消纳率目标。

核心函数/类包括：

- `BESSRuleConfig`
- `align_curves`
- `energy_gate_check`
- `simulate_bess_for_coverage`
- `estimate_min_bess_capacity`
- `evaluate_50wind_50pv_with_bess`
- `plan_wind_fixed_pv_bess_fast`
- `plan_wind_fixed_pv_bess_fast_full`

功能分工如下：

- `BESSRuleConfig` 描述储能调度规则和设备参数，如充放电效率、SOC 范围、充满时长、充放切换间隔等。
- `align_curves` 用于把负荷、光伏、风电三类曲线对齐成统一表结构，这是储能模拟前的标准化入口。
- `energy_gate_check` 做快速能量层面的可行性筛选，避免对明显不满足约束的组合做更重的仿真。
- `simulate_bess_for_coverage` 基于规则模拟储能参与后的供电过程，评估系统覆盖率或新能源消纳效果。
- `estimate_min_bess_capacity` 在满足目标的前提下估算最小储能容量。
- `evaluate_50wind_50pv_with_bess` 更像一个针对固定风电/光伏规模的专项评估入口。
- `plan_wind_fixed_pv_bess_fast` 与 `plan_wind_fixed_pv_bess_fast_full` 则进一步把固定风电、可搜索光伏和储能规模串起来，形成一个“在给定约束下寻找最低投资方案”的快速规划器。

这一段的实质，是把“新能源发电曲线”和“用电负荷曲线”通过规则型储能调度拼接起来，寻找满足指标约束的最小配置。

### 5. 风光储联合规划与最小投资测算

这是 notebook 中最接近“整体规划器”的一部分，目标是同时考虑负荷、风电、光伏、储能，做统一容量规划和投资测算。

核心函数/类包括：

- `UnitsConfig`
- `plan_energy_system`
- `run_planning_min_investment`
- `calc_monthly_wind_metrics`
- `run_wind_bess_planning_min_cap`

功能分工如下：

- `UnitsConfig` 用来统一内部单位体系，处理 `kW`、`MW`、`kWh` 等量纲问题。
- `plan_energy_system` 是这一层的核心规划函数，负责在给定负荷、风电、光伏曲线和约束条件时，搜索满足自用率、覆盖率等要求的系统配置。
- `run_planning_min_investment` 更偏“从输入到结果”的封装入口，允许传入 DataFrame 或文件路径，执行对齐、快速可行性诊断、储能调度模拟、结果指标汇总等流程。
- `calc_monthly_wind_metrics` 用于按月统计风电发电量、消纳电量、弃电量、负荷覆盖率等指标。
- `run_wind_bess_planning_min_cap` 更偏“风电+储能”专项最小容量/最小投资测算，说明 notebook 中同时存在多个相互重叠但目标略有差异的规划入口。

这一部分和前面的“固定风光条件下储能寻优”相比，更接近一个完整的系统级投资分析工作流。

### 6. 绘图与结果导出

除了建模和规划，notebook 中还穿插了大量结果导出和可视化代码，例如：

- 导出月度发电量 CSV。
- 导出中间对齐结果，如总表或调度表。
- 绘制日曲线、净负荷曲线、容量曲线。
- 生成用于后续 Excel 或图表汇总的中间结果。

这说明 notebook 不只是做算法验证，也承担了“生成汇报素材”和“沉淀中间数据”的角色。

## 数据与文件依赖

### 1. 代码中的主要输入依赖

从 notebook 内容看，输入来源主要有三类：

- 本地负荷 Excel 文件目录，用于读取原始负荷曲线。
- 手工指定的月度电量参数，用于构造 2025 年每日目标电量。
- 外部气象数据接口 Open-Meteo，用于获取 ERA5-Land 风速、气温数据。

其中负荷原始目录在 notebook 中仍写成了 Windows 本地绝对路径：

- `D:\\测算工作\\负荷曲线`

这意味着当前 notebook 的负荷数据读取仍依赖原作者本地目录，而不是仓库内的相对路径。

### 2. 代码中的相对输出文件

notebook 中存在一批相对路径输出。这些文件在当前 `src/ba_eva/` 或 `src/ba_eva/dataset/` 下已经可以看到对应结果或同类文件，说明这部分逻辑至少曾在本地运行过：

- `pv_monthly_kwh.csv`
- `pv_gen_kwh_monthly.csv`
- `wind_gen_kwh_monthly.csv`
- `df_total.csv`
- `bess_schedule.csv`
- `bess_monthly_metrics.csv`

结合当前目录结构，`src/ba_eva/dataset/` 下已有如下相关结果文件：

- `src/ba_eva/dataset/pv_monthly_kwh.csv`
- `src/ba_eva/dataset/pv_gen_kwh_monthly.csv`
- `src/ba_eva/dataset/wind_gen_kwh_monthly.csv`
- `src/ba_eva/dataset/df_total.csv`
- `src/ba_eva/dataset/bess_schedule.csv`
- `src/ba_eva/dataset/bess_monthly_metrics.csv`

这说明你提到的“很多数据文件已移动到 `dataset` 中”这一变化，和 notebook 原始写法之间已经出现了路径层面的脱节。

### 3. 仍残留的 Windows 绝对路径

notebook 中仍然保留了多处硬编码 Windows 路径，包括：

- `D:\\228-售前测算\\乌兰察布\\df_wind_2026.csv`
- `D:\\228-售前测算\\乌兰察布\\df_2025.csv`
- `D:\\228-售前测算\\乌兰察布\\pv_kw_100.csv`

这些路径一方面用于导出中间结果，另一方面也被后续单元直接重新读入。这种写法带来的影响是：

- notebook 单元之间不只依赖内存变量，也依赖本机磁盘上的中间文件。
- 如果这些文件不存在，后续单元即使语法正确也无法复现。
- 当前仓库中虽然已有 `dataset/` 目录，但 notebook 内部尚未统一切换到该目录。

### 4. 与 `dataset/` 迁移的关系

从当前仓库结构看，`src/ba_eva/dataset/` 已经收纳了大量测算结果和样例数据文件，但 `eva_Pv.ipynb` 仍保留原作者早期的本地路径引用。两者之间的关系可以概括为：

- 仓库层面已经开始把数据收口到 `dataset/`。
- notebook 层面的路径治理还没有完成。
- 因此当前 notebook 的“结构分析”是成立的，但“直接运行复现”并不稳定。

换句话说，这个 notebook 目前更适合当作算法逻辑参考，不适合直接视为“仓库内开箱即跑的标准入口”。

## 主要问题与风险

### 1. 同一 notebook 中存在重复定义

这是 `eva_Pv.ipynb` 最明显的结构问题。多个配置类和函数在不同单元中被重复实现或二次改写，例如：

- `PlanConfigFast`
- `PlanConfigFastWind`
- `plan_energy_system`
- `align_and_merge`
- `BESSConfig`

这类重复定义意味着：

- 后定义会覆盖前定义。
- 阅读者必须知道每个单元的执行顺序，才能判断当前内存中到底使用的是哪一版实现。
- 后续若把代码从 notebook 抽到 `.py` 文件中，必须先做去重和职责划分。

### 2. 代码以“试验叠加”方式组织

整个 notebook 不是从上到下精心设计的模块化实现，而是边试验、边复制、边扩展的研究过程产物。因此它的优点是灵活，缺点是结构不稳定：

- 单元之间存在强状态依赖。
- 中途会插入单次导出、单次画图、单次调参代码。
- 一些代码块更像实验记录，而不是长期复用接口。

这也解释了为什么同一主题会出现多套相似函数和不同版本的规划器。

### 3. 执行顺序敏感，不适合作为模块直接导入

由于 notebook 中存在：

- 重复定义；
- 依赖前面单元产出的内存变量；
- 依赖本地绝对路径上的中间文件；

所以它不能被简单理解为“一个可以 import 的脚本”。如果不按原作者执行顺序逐段运行，很多单元都可能失效。

### 4. 当前主要可运行性风险来自路径不一致

你这次提到“原先在 `src/ba_eva/` 目录下的很多数据文件移动到了 `dataset` 中，其他内容没动过”，这正是当前最大的可运行性风险来源：

- notebook 代码仍引用旧位置或本地绝对路径。
- 仓库内数据已经部分迁移到 `src/ba_eva/dataset/`。
- 因此 notebook 中的文件读写路径与当前仓库布局不一致。

这意味着现在最容易出问题的不是算法逻辑本身，而是路径找不到、单元依赖断裂、导出的中间文件与后续读取位置不一致。

## 结论

`eva_Pv.ipynb` 实际上是一个围绕“负荷 + 光伏 + 风电 + 储能 + 投资测算”逐步扩展出来的综合测算 notebook，而不是单一的光伏分析脚本。它的价值主要体现在：

- 汇总了风光储测算的关键原型逻辑。
- 给出了从负荷构造到新能源出力模拟，再到储能与投资规划的完整试验链路。
- 保留了多个版本的快速规划思路和月度统计方法。

但从工程角度看，它当前更适合作为“研究草稿 + 逻辑来源”，而不是稳定入口。你已经把很多数据迁移到了 `src/ba_eva/dataset/`，而 notebook 里仍残留旧路径和本地绝对路径，所以后续如果要继续使用这份 notebook，最有价值的下一步不是继续叠加功能，而是先统一路径、清理重复定义，再按模块拆分。
