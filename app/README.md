# 应用入口说明

`app/` 存放项目级可运行脚本。脚本职责是读取配置、构造样例输入、调用 `src/ele_trading/` 中的算法模块并输出日志；不要在本目录继续堆算法实现。

## 运行约定

从项目根目录运行，并使用项目 `.venv`：

```bash
uv run python app/<category>/<script_name>.py
```

依赖安装：

```bash
uv sync
```

求解器约定：

- PuLP/CBC 路径用于储能套利、MPC 等活动模型。
- Two-stage + CVaR 入口优先使用 `glpk`，不可用时尝试 `cbc`。
- 归档 CVXPY 入口仅在安装 `uv sync --extra archived-user-side` 后可运行，且不属于活动入口。

## 当前入口脚本

入口脚本按职责分为：

- `optimization/`（3 个）：储能套利、MPC、Two-stage demo。
- `capacity_planning/`（6 个）：BESS、Wind+BESS、Wind+PV+BESS、IRR 和分布式储能容量规划（实现位于 `investment_estimation.todo`）。
- `resource_simulation/`（4 个）：PV 和 Wind 物理仿真（实现位于 `investment_estimation.todo.resource_simulation`）。
- `trading/`（7 个）：蒙西统一单结算 pipeline、中长期、月度、日前运行计划、日内滚动、需求响应和 walk-forward 回测入口。

| 脚本 | 配置 | 作用 |
|------|------|------|
| `optimization/run_bess_arbitrage.py` | 默认样例数据（`data/trading/prices/`） | 单市场储能套利 demo，输出目标值、充放电功率和 SOC |
| `optimization/run_mpc_demo.py` | 默认样例数据（`data/trading/prices/`） | 储能 MPC 滚动优化 demo |
| `optimization/run_two_stage_skeleton.py` | `configs/market_mengxi.yaml` + 内置最小场景 | Two-stage + CVaR 4 时段、3 场景求解演示；场景偏差成本由 `MarketConfig` 注入 |
| `trading/run_pipeline.py` | `configs/market_mengxi.yaml` + data/forecast providers | 蒙西 position → forecast → scenario → 次日运行 → 日内 → 单结算统一入口 |
| `trading/run_day_ahead.py` | `configs/market_mengxi.yaml` + sample data provider | 次日运行计划摘要（资源计划/SOC/目标分项） |
| `trading/run_intraday.py` | `configs/market_mengxi.yaml` + sample data provider | 日内滚动计划摘要（已执行前缀/剩余/回退状态） |
| `trading/run_backtest.py` | `configs/market_mengxi.yaml` + 30 天样例 | walk-forward 回测，写出 `results/trading/backtest/<run_id>/` 报告与 manifest |
| `trading/run_mid_long.py` | `configs/market_mengxi.yaml` + sample data provider | 中长期覆盖和实时敞口 demo |
| `trading/run_monthly.py` | `configs/market_mengxi.yaml` + sample data provider | 月度阶梯、再平衡和缺少 orderbook 时的透明走廊 |
| `trading/run_dr.py` | `configs/market_mengxi.yaml` + sample/forecast providers | 配置驱动 DR 参与决策 |
| `capacity_planning/run_dist_bess_dispatch.py` | `configs/capacity_planning/dist_bess_dispatch.yaml` | 分布式储能多柜容量搜索、调度内核调用、收益汇总和 CSV 导出 |
| `capacity_planning/run_bess_capacity_planning.py` | `configs/capacity_planning/bess_capacity_planning.yaml` | 离网/绿电约束场景下 BESS 最小容量规划 |
| `capacity_planning/run_wind_bess_capacity_planning.py` | `configs/capacity_planning/wind_bess_capacity_planning.yaml` | Wind+BESS 容量规划和可行性诊断 |
| `capacity_planning/run_wind_pv_bess_capacity_planning_1.py` | `configs/capacity_planning/wind_pv_bess_capacity_planning.yaml` | Wind+PV+BESS 容量规划、配置驱动容量扫描 |
| `capacity_planning/run_wind_pv_bess_capacity_planning_2.py` | `configs/capacity_planning/capacity_planning.yaml` | Wind+PV+BESS 容量规划、三个应用场景（能量门槛/运行评估/组合） |
| `capacity_planning/run_wind_pv_bess_irr_planning.py` | `configs/capacity_planning/wind_pv_bess_irr_planning.yaml` | IRR 目标型 Wind+PV+BESS 容量规划、PPA 反推和综合电价约束 |
| `resource_simulation/run_pv_simulation_v1.py` | `configs/resource_simulation/pv_simulation_v1.yaml` | PV 物理仿真 v1（pvlib） |
| `resource_simulation/run_pv_simulation_v2.py` | `configs/resource_simulation/pv_simulation_v2.yaml` | PV 物理仿真 v2（pvlib） |
| `resource_simulation/run_wind_simulation_v1.py` | `configs/resource_simulation/wind_simulation_v1.yaml` | 风电物理仿真 v1（windpowerlib） |
| `resource_simulation/run_wind_simulation_v2.py` | `configs/resource_simulation/wind_simulation_v2.yaml` | 风电物理仿真 v2（windpowerlib） |

## 使用边界

用户侧、分布式和 CVXPY 入口已归档在 `app/optimization/todo/`，相应配置在 `configs/optimization/todo/`。它们不列入活动入口或常规入口冒烟测试；CVXPY 归档入口还需安装 `archived-user-side` extra。

- 新增入口时，应先确认对应算法已在 `src/ele_trading/` 或 `src/investment_estimation/` 中实现（容量规划/收益测算类算法在后者）。
- 入口脚本可以做格式化输出、配置解析和样例数据组装，不应新增核心约束、目标函数或业务规则。
- 重型链路如全年容量规划、分布式储能全量搜索适合人工验收；日常小改动优先运行相关单元测试和轻量入口。

## 验证

入口冒烟测试：

```bash
uv run python -m pytest -q tests/test_entry_scripts.py
```

完整测试：

```bash
uv run python -m pytest -q
```
