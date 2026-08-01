# 配置目录说明

`configs/` 存放项目级 YAML 配置样例（仅服务 `src/ele_trading/`）。配置只描述参数、路径和运行开关；算法约束、目标函数和数据处理逻辑应放在 `src/ele_trading/` 或对应 `app/` 入口中。

## 当前配置清单

配置按入口职责分目录：

- `optimization/`：储能套利/MPC 的 BESS 物理参数配置。
- `trading/`：蒙西交易主线配置。
- `user_side_dispatch/`：归档用户侧/CVXPY 配置。

> 容量规划配置（6 个）位于 `src/investment_estimation/configs/capacity_planning/`。

| 文件 | 对应入口 / 模块 | 用途 |
|------|------------------|------|
| `optimization/bess.yaml` | `optimization/run_bess_arbitrage.py`、`optimization/run_mpc_demo.py` | 基础储能 SOC、功率、效率、退化成本、时间步长 |
| `trading/market_mengxi.yaml` | `trading/config_loader.load_market_config()` → 蒙西单结算交易链 | 字段与 `MarketConfig` 严格一一对应：`market` 单结算与 `dt=0.25`、`long_recovery` 月度回收、`scenario` LHS/MC 与 CVaR、`bess` 物理参数、`dr` 补偿/违约/最低裕度/最小响应量/窗口/开关/基线模式、`monthly` 交易边界和 `solver`；待确认规则均标 `TODO(rule-confirm)` |

## 配置边界

- 市场规则参数放入 `trading/market_mengxi.yaml`（经 `trading/config_loader` 加载，YAML 叶字段与 `MarketConfig` 严格一一对应，未知或缺失字段均拒绝）。
- 设备物理参数放入对应设备或调度配置，例如 `*_dispatch.yaml`、`*_capacity_planning.yaml`。
- 路径类参数使用相对项目根目录的路径，入口脚本负责解析为绝对路径。
- 新增配置文件时，应同步补充对应入口、读取逻辑、测试和本 README。
- `user_side_dispatch/` 下的用户侧、分布式和 CVXPY 配置仅服务归档入口，不得由活动代码加载。

## 运行示例

```bash
uv run python app/optimization/run_bess_arbitrage.py
uv run python src/investment_estimation/app/capacity_planning/run_dist_bess_dispatch.py
uv run python src/investment_estimation/app/capacity_planning/run_wind_pv_bess_capacity_planning_1.py
uv run python src/investment_estimation/app/capacity_planning/run_wind_pv_bess_capacity_planning_2.py
uv run python src/investment_estimation/app/capacity_planning/run_wind_pv_bess_irr_planning.py
```

完整验证：

```bash
uv run python -m pytest -q
```
