# 应用入口说明

`app/` 存放项目级可运行脚本。脚本职责是读取配置、构造样例输入、调用 `src/ele_trading/` 中的算法模块并输出日志；不要在本目录继续堆算法实现。

## 运行约定

从项目根目录运行，并使用项目 `.venv`：

```bash
uv run python app/<script_name>.py
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

| 脚本 | 配置 | 作用 |
|------|------|------|
| `run_storage_arbitrage.py` | `configs/storage.yaml` | 单市场储能套利 demo，输出目标值、充放电功率和 SOC |
| `run_mpc_demo.py` | `configs/storage.yaml` | 储能 MPC 滚动优化 demo |
| `run_two_stage_skeleton.py` | 内置最小场景 | Two-stage + CVaR 4 时段、3 场景求解演示 |
| `run_backtest.py` | 默认样例数据 | 最小回测，串联滚动调度、收益结算和指标汇总 |
| `run_user_side_storage_dispatch.py` | `configs/user_side_storage_dispatch.yaml` | 用户侧储能成本优化，含能量电费和需量电费 |
| `run_user_side_pv_dispatch.py` | `configs/user_side_pv_dispatch.yaml` | 用户侧 PV-only 自用、上网、弃光和购电测算 |
| `run_user_side_pv_storage_dispatch.py` | `configs/user_side_pv_storage_dispatch.yaml` | 用户侧 PV+storage 联合调度 |
| `run_cvxp_storage_dispatch.py` | `configs/cvxp_storage_dispatch.yaml` | CVXPY 储能调度 demo，支持 profile 版本切换 |
| `run_dist_ess_dispatch.py` | `configs/dist_ess_dispatch.yaml` | 分布式储能多柜容量搜索、调度模拟和收益汇总 |
| `run_wind_solar_storage.py` | `configs/capacity_planning.yaml` | 风光储一体化容量规划与全年运行测算 |
| `run_bess_capacity_planning.py` | `configs/bess_capacity_planning.yaml` | 离网/绿电约束场景下 BESS 最小容量规划 |
| `run_wind_bess_capacity_planning.py` | `configs/wind_bess_capacity_planning.yaml` | Wind+BESS 容量规划和可行性诊断 |
| `run_wind_pv_bess_capacity_planning.py` | `configs/wind_pv_bess_capacity_planning.yaml` | Wind+PV+BESS 容量规划、能量门槛检查和运行评估 |
| `run_wind_pv_bess_irr_planning.py` | `configs/wind_pv_bess_irr_planning.yaml` | IRR 目标型 Wind+PV+BESS 容量规划、PPA 反推和综合电价约束 |
| `run_legacy_data_preparation.py` | `configs/wind_pv_es_calc_data_bridge.yaml` | legacy 风光储测算数据桥接，生成负荷/PV/风电兼容 CSV |
| `run_wind_pv_legacy_profit_eval.py` | `configs/wind_pv_legacy_profit_eval.yaml` | 基于 legacy 兼容数据做年度收益拆分 |
| `run_wind_pv_legacy_market_trading.py` | `configs/wind_pv_legacy_market_trading.yaml` | 基于 legacy 兼容数据运行用户侧风光储交易调度 |

## 使用边界

- 新增入口时，应先确认对应算法已在 `src/ele_trading/` 中实现。
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
