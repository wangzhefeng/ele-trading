# 应用入口说明

本目录存放可以直接运行的项目级 demo 脚本。入口脚本只负责串联样例数据、配置、核心模块和日志输出；算法实现应放在 `src/ele_trading/`，不要在 `app/` 中堆业务逻辑。

## 运行约定

从项目根目录运行脚本，并使用项目 `.venv`：

```bash
uv run python app/<script_name>.py
```

依赖安装使用：

```bash
uv sync
```

储能套利和 MPC 入口依赖 PuLP 的 CBC 求解器。当前代码使用 `PULP_CBC_CMD`，优先使用 `.venv` 中 PuLP 自带的 CBC；Two-stage + CVaR 入口优先使用 `glpk`，不可用时尝试 `cbc`。

## 当前脚本

| 脚本 | 作用 | 备注 |
|------|------|------|
| `run_storage_arbitrage.py` | 运行单市场储能套利 | 读取默认日前价格和储能参数，输出目标值、充放电功率和 SOC 序列 |
| `run_mpc_demo.py` | 运行储能 MPC 滚动优化 demo | 读取默认日内价格和储能参数，输出逐步调度 DataFrame |
| `run_two_stage_skeleton.py` | 运行 Two-stage + CVaR 最小场景演示 | 构造 4 个时段、3 个价格场景，求解申报量和场景收益 |
| `run_backtest.py` | 运行最小回测流程 | 串联滚动调度、收益结算和基础指标汇总 |
| `run_wind_solar_storage.py` | 运行风光储一体化测算演示 | 合成全年 8760 小时气象和负荷，完成风光模拟、容量规划、运行测算和短期预测 |

## 验证建议

快速验证入口脚本：

```bash
uv run python -m pytest -q tests/test_entry_scripts.py
```

完整验证：

```bash
uv run python -m pytest -q
```

`run_wind_solar_storage.py` 比其它 demo 更重，适合链路演示和人工验收；日常小改动优先跑测试或轻量入口。
