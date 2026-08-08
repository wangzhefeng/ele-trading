# 配置目录说明

`configs/` 存放 `src/ele_trading/` 项目级入口使用的 YAML。配置描述参数、路径和运行开关；算法约束、目标函数、市场计算和数据处理逻辑只能位于相应业务包中，不能放在 YAML 或 `app/`。

## 活动配置

| 文件 | 当前消费者 | 用途 |
|---|---|---|
| `optimization/bess.yaml` | BESS 套利和 MPC 入口 | SOC、功率、效率、退化成本和时间步长样例 |
| `markets/single_settlement.yaml` | 单结算加载器和默认交易链 | 市场、场景、BESS、DR、月度与求解参数 |
| `markets/dual_settlement.yaml` | 双结算加载器和结算规则测试 | 结算时段、偏差带和中长期回收参数 |

当前市场 YAML 由相应 `config_loader` 加载，并对 typed config 执行未知字段、缺失字段和取值校验。单结算配置为 schema v1 六区段组合式 typed config（market/scenario/bess/dr/monthly/solver + `schema_version`，v3 D-003）；旧扁平格式已由 `scripts/migrate_market_config_v3.py` 一次性迁移，不再被 loader 接受。

## 用户侧配置（独立领域能力，v3 M6 恢复）

`user_side_dispatch/` 下 4 个 YAML 只服务 `app/user_side_dispatch/` 用户侧入口：

- `user_side_bess_dispatch.yaml`
- `user_side_pv_dispatch.yaml`
- `user_side_pv_bess_dispatch.yaml`
- `cvxp_bess_dispatch.yaml`

用户侧为独立领域能力，这些配置不进入市场交易链。

## 平级包边界

`src/investment_estimation/` 使用自己的包内配置和文档。本目录不复制其配置清单，也不把它纳入 `src/ele_trading/` 的配置契约。

## 当前配置纪律

- YAML 读取统一通过项目的 `read_yaml` 边界。
- 市场规则参数通过相应市场配置和加载器注入，不隐藏在通用算法中。
- 设备参数放入对应设备或调度配置，不混入无关入口。
- 路径参数以项目根目录为解析基准，由入口或加载器解析。
- 新增或修改配置字段时，同步更新 typed config、加载校验、消费者测试和本 README。
- 15 分钟链路中的 `dt` 当前使用 `0.25` 小时，并在配置中显式注明。

## 运行与验证

```bash
uv run python app/optimization/run_bess_arbitrage.py
uv run python app/trading/run_pipeline.py
uv run python -m pytest -q tests/test_yaml_config_loading.py tests/trading tests/markets
```

已批准的配置边界见 [v3](../docs/策略算法框架详细设计-v3.md)，当前配置缺口、报价 capability 和后续 schema 影响见 [v6 §13、§14](../docs/策略算法框架详细设计-v6.md)。
