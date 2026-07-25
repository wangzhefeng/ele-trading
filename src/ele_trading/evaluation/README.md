# evaluation — 评估、结算与回测模块

本模块把优化或控制结果转换为收益、成本、风险指标和回测输出。

## 当前文件

| 文件 | 职责 |
|------|------|
| `settlement.py` | 调度收益计算 `compute_dispatch_revenue()`（广东分层偏差考核已移除，见下） |
| `metrics.py` | IRR、基础储能指标、Sharpe、MDD、EFC、RTE、利用率等扩展指标 |
| `backtest.py` | 最小回测闭环 `run_simple_backtest()` + 蒙西 forecast-aware 回测 `run_mengxi_backtest()` |
| `simulation.py` | `BESSSimulationModel`，储能仿真模型 |

## 当前能力

- `compute_dispatch_revenue()`：按充放电、价格、退化成本计算逐时收益。
- `compute_irr()`：计算投资收益率。
- `summarize_bess_metrics()`：汇总基础储能调度指标。
- `compute_extended_metrics()`：输出年化 Sharpe、最大回撤、等效循环次数、单 EFC 收益、往返效率和利用率。
- `run_simple_backtest()`：串起样例价格、滚动调度、结算和指标。
- `run_mengxi_backtest()`：蒙西 forecast-aware 两阶段回测（DayAhead→Intraday，输出 ΔCost、机会损失 Top-K、oracle upside），结算公式在 `trading/settlement_mengxi.py`。

## 上下游关系

- 上游：`optimization`、`control` 输出调度功率、SOC、申报量等结果。
- 下游：`app/evaluation/run_backtest.py`、测试、收益测算脚本和后续报告消费评估结果。

## 使用边界

- 指标解释必须带上时间步长、储能容量、收益口径和电价单位。
- **结算口径**：蒙西带状结算为唯一实现（`trading/settlement_mengxi.py`，v1.3 §5）。广东式分层偏差考核 `compute_deviation_penalty()` 已从 `settlement.py` 移除，仅保留与结算无关的 `compute_dispatch_revenue()`。
- 当前未实现离线雨流退化核算；如补充，应与线性吞吐量退化成本并列展示。
