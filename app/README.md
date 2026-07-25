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

- PuLP/CBC 路径用于储能套利、MPC、用户侧 MILP、分布式储能等模型。
- Two-stage + CVaR 入口优先使用 `glpk`，不可用时尝试 `cbc`。
- CVXPY 入口依赖 `cvxpy` 已在项目依赖中声明。

## 当前入口脚本

入口脚本按职责分为（共 20 个）：

- `optimization/`（7 个）：储能套利、MPC、Two-stage、用户侧调度和 CVXPY 调度 demo。
- `capacity_planning/`（6 个）：BESS、Wind+BESS、Wind+PV+BESS、IRR 和分布式储能容量规划（实现位于 `investment_estimation.todo`）。
- `resource_simulation/`（4 个）：PV 和 Wind 物理仿真（实现位于 `investment_estimation.todo.resource_simulation`）。
- `evaluation/`（1 个）：回测和评估入口。
- `legacy/`（2 个）：旧风光储兼容数据链路入口。

| 脚本 | 配置 | 作用 |
|------|------|------|
| `optimization/run_bess_arbitrage.py` | 默认样例数据（`data/raw/`） | 单市场储能套利 demo，输出目标值、充放电功率和 SOC |
| `optimization/run_mpc_demo.py` | 默认样例数据（`data/raw/`） | 储能 MPC 滚动优化 demo |
| `optimization/run_two_stage_skeleton.py` | 内置最小场景 | Two-stage + CVaR 4 时段、3 场景求解演示 |
| `evaluation/run_backtest.py` | 默认样例数据 | 最小回测，串联滚动调度、收益结算和指标汇总 |
| `optimization/run_user_side_bess_dispatch.py` | `configs/optimization/user_side_bess_dispatch.yaml` | 用户侧储能成本优化，含能量电费和需量电费 |
| `optimization/run_user_side_pv_dispatch.py` | `configs/optimization/user_side_pv_dispatch.yaml` | 用户侧 PV-only 自用、上网、弃光和购电测算 |
| `optimization/run_user_side_pv_bess_dispatch.py` | `configs/optimization/user_side_pv_bess_dispatch.yaml` | 用户侧 PV+storage 联合调度 |
| `optimization/run_cvxp_bess_dispatch.py` | `configs/optimization/cvxp_bess_dispatch.yaml` | CVXPY 储能调度 demo，支持 profile 版本切换 |
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
| `legacy/run_wind_pv_legacy_profit_eval.py` | `configs/legacy/wind_pv_legacy_profit_eval.yaml` | 基于 legacy 兼容数据做年度收益拆分 |
| `legacy/run_wind_pv_legacy_market_trading.py` | `configs/legacy/wind_pv_legacy_market_trading.yaml` | 基于 legacy 兼容数据运行用户侧风光储交易调度 |

## 使用边界

- 新增入口时，应先确认对应算法已在 `src/ele_trading/` 或 `src/investment_estimation/` 中实现（容量规划/收益测算类算法在后者）。
- 入口脚本可以做格式化输出、配置解析和样例数据组装，不应新增核心约束、目标函数或业务规则。
- 重型链路如全年容量规划、分布式储能全量搜索适合人工验收；日常小改动优先运行相关单元测试和轻量入口。

> **已知缺口**：`legacy/run_wind_pv_legacy_*.py` 两个入口 `import run_legacy_data_preparation`，该模块文件当前不在仓库中（pre-existing，见 LOG.md），legacy 链路暂无法端到端运行。

## 验证

入口冒烟测试：

```bash
uv run python -m pytest -q tests/test_entry_scripts.py
```

完整测试：

```bash
uv run python -m pytest -q
```
