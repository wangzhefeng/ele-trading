# optimization — 优化调度模块

本模块承接价格、负荷、光伏、风电、储能参数和场景输入，输出申报、调度、容量搜索或成本收益结果。当前覆盖市场储能、用户侧、CVXPY、分布式储能和 Two-stage 风险优化多条链路。

## 当前文件

| 文件 | 职责 |
|------|------|
| `interfaces.py` | 统一 dataclass 和枚举，包括储能、用户侧、CVXPY、分布式储能输入输出 |
| `bess_arbitrage.py` | 单市场储能套利和容量 sizing |
| `mpc_storage.py` | 单窗口 MPC 和滚动 MPC |
| `two_stage_cvar.py` | Two-stage + CVaR 场景优化模型 |
| `user_side_bess_dispatch.py` | 用户侧储能调度，最小化购电、需量和循环成本 |
| `user_side_renewable_dispatch.py` | 用户侧通用可再生能源无储能调度内核 |
| `user_side_renewable_bess_dispatch.py` | 用户侧通用可再生能源+BESS 调度内核 |
| `user_side_pv_dispatch.py` | 用户侧 PV-only 场景适配入口 |
| `user_side_pv_bess_dispatch.py` | 用户侧 PV+BESS 场景适配入口 |
| `user_side_wind_dispatch.py` | 用户侧 Wind-only 场景适配入口 |
| `user_side_wind_bess_dispatch.py` | 用户侧 Wind+BESS 场景适配入口 |
| `user_side_wind_pv_bess_dispatch.py` | 用户侧 Wind+PV+BESS 场景适配入口 |
| `user_side_bess_dispatch_cvxpy.py` | 用户侧 BESS 调度的 CVXPY 版本 profile |
| `dist_ess_dispatch.py` | 分布式储能多柜容量搜索、调度模拟和结果输出 |

## 市场储能套利

`solve_bess_arbitrage()` 把储能视为独立市场资产，在已知价格序列下最大化：

```text
放电卖电收入 - 充电买电成本 - 线性退化成本
```

核心约束包括 SOC 动态、功率上限、充放电互斥和可选末端 SOC 约束。该模型不使用负荷预测，适合做独立储能套利基准和收益上限评估。

## MPC 滚动调度

`solve_one_mpc_window()` 求解单个预测窗口，`run_bess_mpc()` 在价格序列上滚动执行。当前支持 `terminal_soc_fraction` 终端 SOC 下界，避免窗口末端过度放电。

## Two-stage + CVaR

`build_two_stage_cvar_model()` 构造日前申报 + 实时场景调节模型：

- 第一阶段：日前申报量。
- 第二阶段：各场景下充放电、SOC、偏差和收益。
- 风险项：CVaR 线性化，目标兼顾期望收益和尾部风险。

演示入口为 `app/run_two_stage_skeleton.py`。

## 用户侧模型

用户侧模型是电表后视角，目标通常是最小化综合用能成本。

- `run_user_side_bess_dispatch()`：负荷 + 购电价格 + 储能，考虑需量电费、循环成本、终端 SOC 和禁止反送电。
- `run_user_side_pv_dispatch()`：负荷 + PV + 购售电规则，计算 PV 自用、上网、弃光和购电。
- `run_user_side_pv_bess_dispatch()`：负荷 + PV + 储能 + 可选策略偏好，联合决定 PV 分流、储能动作、购电和上网。
- `run_user_side_wind_dispatch()`：负荷 + Wind + 购售电规则，计算风电自用、上网、弃风和购电。
- `run_user_side_wind_bess_dispatch()`：负荷 + Wind + 储能，联合决定风电分流、储能动作、购电和上网。
- `run_user_side_wind_pv_bess_dispatch()`：负荷 + Wind + PV + 储能，把风光总出力作为 renewable 统一调度，并保留 PV/Wind 原始预测。

`user_side_renewable_dispatch.py` 和 `user_side_renewable_bess_dispatch.py` 是共享内核；PV/Wind 场景入口只负责字段映射和兼容输出。PV/BESS 链路的样例输入由 `data_provider/*_sample.py` 和 `configs/user_side_*.yaml` 提供；Wind 相关链路当前先提供算法层 API。

## CVXPY 储能调度

`run_cvxp_bess_dispatch()` 提供 CVXPY 版本储能调度实现，用 `CvxpBESSDispatchInput` 和 `CvxpBESSDispatchResult` 明确输入输出。入口为 `app/run_cvxp_bess_dispatch.py`。

## 分布式储能调度

`dist_ess_dispatch.py` 面向多变压器、多储能柜组合的容量搜索和运行模拟。当前支持：

- preset 配置和系统选择。
- 坐标搜索、全网格搜索、最大容量组合。
- 组合可行性检查、调度优化、收益评估。
- `combo_key`、schedule、summary 输出之间的映射。

入口为 `app/run_dist_bess_dispatch.py`，配置为 `configs/dist_ess_dispatch.yaml`。

## 与 legacy 的关系

当前主线没有原样搬迁历史 `es_rolling_schedule` 的所有站点策略和数据预处理逻辑。已迁入的是稳定的优化核心、输入输出 dataclass、用户侧调度和项目级兼容工具；未迁入内容包括多设备实时策略后处理、平台数据预处理细节、特定站点规则和部分 legacy import 路径。

如需补齐 legacy 兼容，应单独做接口和数据契约设计，不要直接把历史脚本复制进本目录。
