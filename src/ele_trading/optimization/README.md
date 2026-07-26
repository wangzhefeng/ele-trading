# optimization — 优化调度模块

本模块承接活动市场储能和场景输入，输出储能套利、MPC 和 Two-stage 风险优化结果。用户侧、分布式和 CVXPY 路径已归档，不属于活动 API。

## 当前文件

| 文件 | 职责 |
|------|------|
| `contracts.py` | 活动通用结果契约：`BESSArbitrageResult`、`MPCStepResult` |
| `bess_model.py` | PuLP 共享 SOC、效率、功率、terminal、throughput、no-export 约束 |
| `risk.py` | CVaR 辅助变量与风险目标 helper |
| `solver.py` | typed PuLP/CBC 求解状态边界 |
| `bess_arbitrage.py` | 复用共享 BESS kernel 的确定性单市场储能套利 |
| `mpc_bess.py` | 单窗口 MPC 和滚动 MPC |
| `two_stage_cvar.py` | 消费 `ScenarioSet` 的可运行 Two-stage + CVaR 优化 |
| `todo/` | 归档用户侧、分布式和 CVXPY 模块；显式导入，且 CVXPY 需要 `archived-user-side` extra |

## 市场储能套利

`solve_bess_arbitrage()` 把储能视为独立市场资产，在已知价格序列下最大化：

```text
放电卖电收入 - 充电买电成本 - 线性退化成本
```

核心约束包括 SOC 动态、功率上限、充放电互斥和可选末端 SOC 约束。该模型不使用负荷预测，适合做独立储能套利基准和收益上限评估。

## MPC 滚动调度

`solve_one_mpc_window()` 求解单个预测窗口，`run_bess_mpc()` 在价格序列上滚动执行。当前支持 `terminal_soc_fraction` 终端 SOC 下界，避免窗口末端过度放电。

## Two-stage + CVaR

`solve_two_stage_cvar()` 通过 PuLP+CBC 构造并求解日前申报 + 实时场景调节：

- 第一阶段：日前申报量。
- 第二阶段：各场景下充放电、SOC、偏差和收益。
- 风险项：以场景成本为 loss 的加权 CVaR 线性化。
- 正负偏差考核系数必须由上层显式传入，optimization 不提供市场默认值。
- 返回 typed solve status、第一阶段 bid、场景 recourse、期望成本、VaR、
  CVaR 和来源 trace metadata；失败时不返回伪造的零计划。

`build_two_stage_cvar_model()` 仅为当前旧示例入口保留未求解 PuLP model
adapter，不是 v2 主 API；其 `kappa_pos` / `kappa_neg` 同样必须显式传入。

演示入口为 `app/optimization/run_two_stage_skeleton.py`。

## 归档用户侧、分布式与 CVXPY 模块

归档实现位于 `optimization/todo/`，样例输入位于
`data_provider/todo/`，入口和配置分别位于 `app/optimization/todo/` 与
`configs/optimization/todo/`。活动代码不得导入它们；需要运行 CVXPY
归档模块时先执行 `uv sync --extra archived-user-side`。归档详情、依赖和恢复
条件见各自 `todo/README.md`。
