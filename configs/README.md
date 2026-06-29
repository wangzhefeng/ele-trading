# 配置目录说明

`configs/` 存放项目级 YAML 配置样例。配置只描述参数、路径和运行开关；算法约束、目标函数和数据处理逻辑应放在 `src/ele_trading/` 或对应 `app/` 入口中。

## 当前配置清单

配置按入口职责分目录：

- `optimization/`：优化和调度入口配置。
- `capacity_planning/`：容量规划、IRR 和分布式储能搜索配置。
- `resource_simulation/`：PV/Wind 物理仿真配置。
- `market/`：市场、场景和结算规则样例。
- `legacy/`：旧风光储兼容链路配置。

| 文件 | 对应入口 / 模块 | 用途 |
|------|------------------|------|
| `optimization/bess.yaml` | `optimization/run_bess_arbitrage.py`、`optimization/run_mpc_demo.py` | 基础储能 SOC、功率、效率、退化成本、时间步长 |
| `market/market.yaml` | 数据/市场样例 | 基础日前市场元信息 |
| `market/market_guangdong.yaml` | `evaluation.settlement`、Two-stage 规则参考 | 广东现货市场 15 分钟颗粒度、价格限幅、偏差考核分层参数 |
| `market/scenario.yaml` | `scenario` 模块 | 价格场景数量、噪声、随机种子和权重样例 |
| `capacity_planning/capacity_planning.yaml` | `capacity_planning/run_wind_pv_bess_capacity_planning_2.py` | 风光储联合容量规划三场景演示、约束、搜索步长和成本参数 |
| `capacity_planning/bess_capacity_planning.yaml` | `capacity_planning/run_bess_capacity_planning.py` | 固定风光容量下 BESS 最小容量规划 |
| `capacity_planning/wind_bess_capacity_planning.yaml` | `capacity_planning/run_wind_bess_capacity_planning.py` | Wind+BESS 容量规划、平移充电策略、二分搜索参数 |
| `capacity_planning/wind_pv_bess_capacity_planning.yaml` | `capacity_planning/run_wind_pv_bess_capacity_planning_1.py` | Wind+PV+BESS 容量规划、PV 搜索、BESS 搜索和能量门槛检查 |
| `capacity_planning/wind_pv_bess_irr_planning.yaml` | `capacity_planning/run_wind_pv_bess_irr_planning.py` | IRR 目标型 Wind+PV+BESS 容量规划、PPA 反推和综合电价约束 |
| `optimization/user_side_bess_dispatch.yaml` | `optimization/run_user_side_bess_dispatch.py` | 用户侧储能调度、需量电费、终端 SOC、合成负荷/电价 |
| `optimization/user_side_pv_dispatch.yaml` | `optimization/run_user_side_pv_dispatch.py` | 用户侧 PV-only 调度、上网/弃光规则、合成负荷/PV/电价 |
| `optimization/user_side_pv_bess_dispatch.yaml` | `optimization/run_user_side_pv_bess_dispatch.py` | 用户侧 PV+storage 联合调度、储能、上网、策略偏好 |
| `optimization/cvxp_bess_dispatch.yaml` | `optimization/run_cvxp_bess_dispatch.py` | CVXPY 储能调度 profile、需量价格、合成负荷/电价 |
| `capacity_planning/dist_bess_dispatch.yaml` | `capacity_planning/run_dist_bess_dispatch.py` | 分布式储能数据目录、时间范围、preset、系统、搜索模式 |
| `legacy/wind_pv_es_calc_data_bridge.yaml` | `legacy/run_legacy_data_preparation.py` | legacy 风光储数据桥接，生成 `df_2025`、PV、风电和总表 |
| `legacy/wind_pv_legacy_profit_eval.yaml` | `legacy/run_wind_pv_legacy_profit_eval.py` | legacy 风光数据年度收益测算、成本年化和结果输出 |
| `legacy/wind_pv_legacy_market_trading.yaml` | `legacy/run_wind_pv_legacy_market_trading.py` | legacy 风光数据交易调度窗口、储能、上网、价格和输出 |

## 配置边界

- 市场规则参数放入 `market/market_*.yaml`，例如偏差死区、分层阈值、价格上下限。
- 设备物理参数放入对应设备或调度配置，例如 `optimization/bess.yaml`、`*_dispatch.yaml`、`*_capacity_planning.yaml`。
- 路径类参数使用相对项目根目录的路径，入口脚本负责解析为绝对路径。
- 新增配置文件时，应同步补充对应入口、读取逻辑、测试和本 README。

## 运行示例

```bash
uv run python app/optimization/run_bess_arbitrage.py
uv run python app/optimization/run_user_side_pv_bess_dispatch.py
uv run python app/capacity_planning/run_dist_bess_dispatch.py
uv run python app/capacity_planning/run_wind_pv_bess_capacity_planning_1.py
uv run python app/capacity_planning/run_wind_pv_bess_capacity_planning_2.py
uv run python app/capacity_planning/run_wind_pv_bess_irr_planning.py
```

完整验证：

```bash
uv run python -m pytest -q
```
