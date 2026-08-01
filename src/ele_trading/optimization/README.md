# optimization — 活动优化内核

本模块承接价格或 `ScenarioSet` 输入，提供活动 BESS 套利、MPC 和 Two-stage + CVaR 基线。当前活动模型使用 PuLP/CBC；用户侧、分布式和 CVXPY 实现位于平级归档包 `user_side_dispatch`，本目录不存在 `todo/`。

## 当前文件

| 文件 | 当前职责 |
|---|---|
| `contracts.py` | `BESSArbitrageResult`、`MPCStepResult` |
| `bess_model.py` | 可复用的 SOC、效率、功率、terminal、throughput 和 no-export 约束 |
| `risk.py` | CVaR 辅助变量、风险目标和事后 VaR/CVaR |
| `solver.py` | PuLP/CBC typed 求解状态和失败边界 |
| `bess_arbitrage.py` | 复用共享 BESS kernel 的确定性套利 |
| `mpc_bess.py` | 单窗口和滚动 MPC |
| `two_stage_cvar.py` | 消费 `ScenarioSet` 的 Two-stage + CVaR |

## 当前算法边界

### BESS 套利

在已知价格序列下优化放电收入减充电成本和线性退化成本，包含 SOC、功率、互斥和可选终端约束。它是独立储能基准，不使用负荷预测。

### MPC

`solve_one_mpc_window()` 求解单窗口，`run_bess_mpc()` 滚动执行首步。当前 MPC 自己维护 SOC、功率和互斥约束，尚未复用 `bess_model.py`，是 v3 的明确收敛对象。

### Two-stage + CVaR

`solve_two_stage_cvar()` 使用 PuLP/CBC 求解第一阶段 bid 和场景 recourse，返回求解状态、期望成本、VaR、CVaR 和 trace metadata。市场偏差成本必须由调用方显式传入。

`build_two_stage_cvar_model()` 是为现有示例保留的 compatibility adapter，不是未来扩展入口。

## 归档边界

归档实现、样例和接口位于 `src/ele_trading/user_side_dispatch/`，对应入口和配置位于 `app/user_side_dispatch/` 与 `configs/user_side_dispatch/`。活动 optimization 不转出归档 API。

共享物理核、目标策略、求解器 adapter 和结果提取的目标边界由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#74-通用物理与求解内核)决定。
