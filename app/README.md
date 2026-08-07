# 应用入口说明

`app/` 存放 `src/ele_trading/` 的项目级可运行脚本。入口只负责配置解析、依赖装配、样例输入构造和输出展示；核心约束、目标函数、市场规则和数据处理算法必须位于业务包中。

平级包 `src/investment_estimation/` 使用自己的包内入口和文档，不在本清单中展开。

## 运行约定

从项目根目录运行：

```bash
uv sync
uv run python app/<category>/<script_name>.py
```

活动 PuLP 模型使用项目已安装的 CBC 求解路径。用户侧 CVXPY 入口需要额外安装：

```bash
uv sync --extra archived-user-side
```

## 活动入口

### `optimization/`：3 个

| 脚本 | 配置/数据 | 作用 |
|---|---|---|
| `run_bess_arbitrage.py` | `configs/optimization/bess.yaml`、样例日前价格 | 单市场 BESS 套利基线 |
| `run_mpc_demo.py` | `configs/optimization/bess.yaml`、样例日内价格 | BESS 滚动 MPC 基线 |
| `run_two_stage_skeleton.py` | `configs/markets/single_settlement.yaml`、内置最小场景 | Two-stage + CVaR 兼容示例 |

### `trading/`：7 个

| 脚本 | 配置/数据 | 作用 |
|---|---|---|
| `run_pipeline.py` | 单结算配置、样例 data/forecast provider | 默认单结算完整链路 |
| `run_mid_long.py` | 单结算配置、样例 position provider | 中长期覆盖与敞口基线 |
| `run_monthly.py` | 单结算配置、样例 position provider | 月度阶梯、缺口再平衡和透明走廊 |
| `run_day_ahead.py` | 单结算配置、样例 provider | 日前运行计划摘要 |
| `run_intraday.py` | 单结算配置、样例 provider | 日内滚动与回退状态摘要 |
| `run_dr.py` | 单结算配置、样例/预测 provider | 独立 DR 参与评估 |
| `run_backtest.py` | 单结算配置、30 天样例 | walk-forward 回测与结果清单 |

## 用户侧入口（独立领域能力，v3 M6 恢复）

`app/user_side_dispatch/` 保留 4 个用户侧入口；用户侧是独立领域能力，与市场主链互不依赖：

- `run_user_side_bess_dispatch.py`
- `run_user_side_pv_dispatch.py`
- `run_user_side_pv_bess_dispatch.py`
- `run_cvxp_bess_dispatch.py`

用户侧能力的业务定位见 `src/ele_trading/user_side_dispatch/README.md`。

## 使用边界

- 入口不得新增或复制核心算法逻辑。
- 生产数据必须通过业务包定义的数据或 provider 边界传入，入口不得硬编码生产路径。
- 新入口必须有对应算法实现、配置读取、轻量冒烟测试和本 README 记录。
- 重型、需要外部数据或可选依赖的入口应明确标记为手动验收，不得伪装成常规自动测试。

## 验证

活动入口测试由 `tests/test_entry_scripts.py` 覆盖。当前入口边界见 [tests/README.md](../tests/README.md)，验证快照和下一开发路线见 [v5 §3、§15](../docs/策略算法框架详细设计-v5.md#15-验证与性能预算)。

```bash
uv run python -m pytest -q tests/test_entry_scripts.py
```

该命令还会收集平级包入口用例；需要严格排除投资测算时，应按当前任务选择活动节点，并以 v5 §15 的验证口径记录结果。
