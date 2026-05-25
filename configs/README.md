# 配置目录说明

本目录存放 `src/ele_trading/` 主线使用的项目级配置样例。配置文件描述市场规则、储能设备、场景生成和容量规划参数；算法实现仍放在 `src/ele_trading/`，不要把业务逻辑写进 YAML。

## 当前文件

| 文件 | 用途 | 主要字段 |
|------|------|----------|
| `market.yaml` | 基础日前市场样例配置 | `market_name`、`time_step_hours`、`currency`、`price_column`、`settlement_mode` |
| `market_guangdong.yaml` | 广东现货市场样例配置 | 15 分钟颗粒度、96 时段/日、价格上下限、偏差考核分层参数 |
| `storage.yaml` | 储能设备约束与效率参数 | 初始 SOC、SOC 上下界、充放电功率、效率、退化成本、`dt` |
| `scenario.yaml` | 价格场景生成参数 | 场景数量、扰动幅度、随机种子、场景权重 |
| `capacity_planning.yaml` | 风光储容量规划参数 | 粗/精搜索步长、储能默认参数、风光储单位成本 |

## 使用边界

- 本目录服务 `src/ele_trading/` 核心包和 `app/` demo 入口。
- `src/wind_pv_es_calc/config/` 是历史风光储测算链路的预留配置目录；只服务该历史链路的配置不要混入本目录。
- 市场规则参数应优先放入 `market_*.yaml`，例如偏差考核死区、分层阈值、价格上下限。
- 设备物理参数应优先放入 `storage.yaml` 或容量规划配置，不要在 app 入口脚本里硬编码新默认值。

## 运行与验证

从项目根目录使用 `uv run` 运行脚本和测试：

```bash
uv run python app/run_storage_arbitrage.py
uv run python app/run_mpc_demo.py
uv run python -m pytest -q
```

新增或修改配置后，至少运行与配置相关的测试切片；如果不确定影响范围，运行完整测试。

## 扩展建议

- 新增省份市场时使用 `market_<province>.yaml` 命名。
- 新增资产组或多市场配置时先明确下游读取入口，再补 README 说明。
- 配置字段变更需要同步更新读取逻辑、测试和相关文档，避免 YAML 与代码默认值漂移。
