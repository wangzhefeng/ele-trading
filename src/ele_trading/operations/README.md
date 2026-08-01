# operations — 资源运行层：日前计划与日内滚动

## 模块

| 模块 | 职责 |
|------|------|
| `day_ahead_coupled.py` | 日前运行计划 `solve_day_ahead_operational`：共享 BESS 物理内核 + 可选联合场景 CVaR + `dr_enabled=True` 时 DR 两阶段联合优化；日前价仅作解释性信号，不进结算 |
| `intraday_rolling.py` | 日内滚动 `solve_intraday_rolling`：冻结已执行前缀 + 剩余窗口重优化 + 求解失败物理裁剪回退 + DR 履约硬约束 |
