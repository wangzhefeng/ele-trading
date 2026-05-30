# control — 滚动控制模块

控制模块负责把优化模型包装成可逐时滚动执行的调度流程。

## 当前文件

| 文件 | 职责 |
|------|------|
| `rolling_dispatch.py` | `run_bess_rolling_dispatch()`，复用 `optimization.mpc_storage.run_bess_mpc()` 生成滚动储能调度 |

## 上下游关系

- 上游：价格预测、储能参数和当前 SOC。
- 中游：调用 `optimization` 中的 MPC 求解逻辑。
- 下游：`evaluation` 用滚动调度结果做结算和回测。

## 使用边界

- 当前控制层是轻量包装，不包含 BMS 通信、实时状态反馈、策略下发或异常纠偏。
- 后续新增实时控制时，应把设备状态、执行反馈和预测刷新逻辑放在控制层，不要塞进底层优化模型。
