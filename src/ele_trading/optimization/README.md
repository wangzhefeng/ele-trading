# optimization — 活动优化内核

本模块承接价格或 `ScenarioSet` 输入，提供活动 BESS 套利、MPC 和 Two-stage + CVaR 基线。当前活动模型使用 PuLP/CBC；用户侧、分布式和 CVXPY 实现位于平级独立活动领域 `user_side_dispatch`，本目录不转出其 API。

## 当前文件

| 文件 | 当前职责 |
|---|---|
| `contracts.py` | `BESSArbitrageResult`、`MPCStepResult` |
| `bess_model.py` | 可复用的 SOC、效率、功率、terminal、throughput 和 no-export 约束 |
| `objectives.py` | 目标组件层（v3 D-005）：毛收益、吞吐退化、套利净收益组件 |
| `extraction.py` | 统一结果提取（v3 D-005） |
| `solver.py` | PuLP/CBC typed 求解状态、`solve_pulp_model` 统一出口和失败边界 |
| `risk.py` | CVaR 辅助变量、风险目标和事后 VaR/CVaR |
| `degradation.py` | 日历+循环退化分离（Level 1，v4 P0，可选） |
| `bess_arbitrage.py` | 复用共享 BESS kernel 的确定性套利，支持 Level 0/1 退化选择 |
| `mpc_bess.py` | 单窗口和滚动 MPC，复用共享 `add_bess_constraints` 核（v3 D-004） |
| `two_stage_cvar.py` | 消费 `ScenarioSet` 的 Two-stage + CVaR |

## 当前算法边界

### BESS 套利

在已知价格序列下优化放电收入减充电成本和退化成本，包含 SOC、功率、互斥和可选终端约束。退化模型支持 Level 0（线性吞吐，默认）和 Level 1（日历+循环分离，v4 P0 可选）。它是独立储能基准，不使用负荷预测。

### MPC

`solve_one_mpc_window()` 求解单窗口，`run_bess_mpc()` 滚动执行首步。MPC 已复用共享 `add_bess_constraints` 核（v3 D-004），`dt` 统一 0.25，不再维护私有约束实现。

### Two-stage + CVaR

`solve_two_stage_cvar()` 使用 PuLP/CBC 求解第一阶段 bid 和场景 recourse，返回求解状态、期望成本、VaR、CVaR 和 trace metadata。市场偏差成本必须由调用方显式传入。

`build_two_stage_cvar_model()` 是为现有示例保留的 compatibility adapter，不是未来扩展入口。

## 独立领域边界

用户侧实现、样例和接口位于 `src/ele_trading/user_side_dispatch/`，对应入口和配置位于 `app/user_side_dispatch/` 与 `configs/user_side_dispatch/`。它与市场交易主链隔离，活动 optimization 不转出其 API。

共享物理核、目标策略、求解器 adapter 和结果提取的已批准边界见 [v3](../../../docs/策略算法框架详细设计-v3.md)；温度退化、扩展风险度量和未接线的多资源优化路线见 [v5 §8、§11](../../../docs/策略算法框架详细设计-v5.md#11-多资源优化头寸与结算)。
