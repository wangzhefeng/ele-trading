# 系统架构说明

项目围绕「数据 → 预测 → 场景 → 优化 → 控制 → 结算 → 回测」搭建。

## 模块职责

| 模块 | 职责 |
|------|------|
| `data_provider` | 加载样例数据、配置、负荷曲线、气象数据，提供统一输入接口 |
| `forecasting` | 价格预测、PV/风电功率预测、天气特征工程 |
| `scenario` | 价格场景采样（LHS/MC）与缩减（Kantorovich 后向缩减） |
| `optimization` | 储能套利、MPC、Two-stage+CVaR、用户侧调度、分布式储能等优化建模（CVXPY 路径为延迟导入可选依赖，缺失时不影响 PuLP/Pyomo 主链路） |
| `capacity_planning` | PV/风电/BESS/风光储容量规划、可行性分析、IRR 测算、多节点扫描 |
| `resource_simulation` | 风光资源物理仿真与 profile 构造 |
| `control` | 基于 MPC 的滚动调度封装 |
| `evaluation` | 收益结算、偏差考核、IRR、回测、仿真评估 |
| `demand` | 固定窗口/滑动窗口最大需量和需量电费计算 |
| `utils` | IO、日志、时间处理、数据对齐、绘图等通用工具 |

## 主链路

1. `data_provider` 提供配置、样例数据、负荷与气象输入。
2. `forecasting` 产出价格预测、PV/风电功率预测及天气特征。
3. `scenario` 将预测扩展为多场景样本供鲁棒优化使用。
4. `optimization` / `capacity_planning` 执行调度优化或容量规划建模。
5. `control` 封装滚动执行逻辑。
6. `evaluation` / `demand` 输出收益、回测指标及需量电费核算。
7. `resource_simulation` 为风光资源提供物理仿真支撑。

## 设计原则

- 先打通闭环，再逐步增加市场复杂度。
- 先支持单资产储能，再扩展风光储联合调度。
- 先用统一接口，避免 app 脚本直接依赖底层求解细节。
- 新算法实现放入 `src/ele_trading/`，入口脚本只负责组装配置、数据和日志输出。
- 重型可选依赖（cvxpy）通过延迟导入（`__getattr__`）降低耦合，缺失时主链路不受阻。
