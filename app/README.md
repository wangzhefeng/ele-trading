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
- `trading/`（7 个）：蒙西统一单结算 pipeline、中长期、月度、日前运行计划、日内滚动、需求响应和 walk-forward 回测入口。

> 容量规划入口（6 个）位于 `src/investment_estimation/app/capacity_planning/`，配置在 `src/investment_estimation/configs/capacity_planning/`（指向 `investment_estimation.todo`）。
> PV/Wind 物理仿真入口位于 `src/investment_estimation/app/`（指向 `investment_estimation.resource_simulation`）。

| 脚本 | 配置 | 作用 |
|------|------|------|
| `optimization/run_bess_arbitrage.py` | `configs/optimization/bess.yaml` + `data/trading/prices/` | 单市场储能套利 demo，输出目标值、充放电功率和 SOC |
| `optimization/run_mpc_demo.py` | `configs/optimization/bess.yaml` + `data/trading/prices/` | 储能 MPC 滚动优化 demo |
| `optimization/run_two_stage_skeleton.py` | `configs/markets/single_settlement.yaml` + 内置最小场景 | Two-stage + CVaR 4 时段、3 场景求解演示；场景偏差成本由 `MarketConfig` 注入 |
| `trading/run_pipeline.py` | `configs/markets/single_settlement.yaml` + data/forecast providers | 单结算 position → forecast → scenario → 次日运行 → 日内 → 结算统一入口 |
| `trading/run_day_ahead.py` | `configs/markets/single_settlement.yaml` + sample data provider | 次日运行计划摘要（资源计划/SOC/目标分项） |
| `trading/run_intraday.py` | `configs/markets/single_settlement.yaml` + sample data provider | 日内滚动计划摘要（已执行前缀/剩余/回退状态） |
| `trading/run_backtest.py` | `configs/markets/single_settlement.yaml` + 30 天样例 | walk-forward 回测，写出 `results/trading/backtest/<run_id>/` 报告与 manifest |
| `trading/run_mid_long.py` | `configs/markets/single_settlement.yaml` + sample data provider | 中长期覆盖和实时敞口 demo |
| `trading/run_monthly.py` | `configs/markets/single_settlement.yaml` + sample data provider | 月度阶梯、再平衡和缺少 orderbook 时的透明走廊 |
| `trading/run_dr.py` | `configs/markets/single_settlement.yaml` + sample/forecast providers | 配置驱动 DR 参与决策 |

## 使用边界

用户侧、分布式和 CVXPY 入口已归档在 `app/user_side_dispatch/`，相应配置在 `configs/user_side_dispatch/`。它们不列入活动入口或常规入口冒烟测试；CVXPY 归档入口还需安装 `archived-user-side` extra。

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
