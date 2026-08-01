# markets — 市场规则插件层

市场规则按**结算模式**（而非地区名）组织为 `markets/<模式>/` 插件。
每个插件自包含：配置契约 + 加载校验 + 结算引擎；规则参数全部经
`configs/markets/<模式>.yaml` 注入，代码不得硬编码。

## 当前插件

### `single_settlement/` — 单结算模式（规则研究参考蒙西市场规则）

| 模块 | 职责 |
|------|------|
| `contracts.py` | `MarketConfig`（市场/运行/求解规则，字段与 YAML 严格一一对应）、`SettlementReport`（分项结算报告） |
| `config_loader.py` | `load_market_config`：YAML 一一映射加载与校验（未知/缺失字段均拒绝） |
| `settlement.py` | 单结算引擎：`build_settlement_report`（实时电能 `Q_real*p_real` + 中长期差价 `Q_long*(p_long-p_ref)` + 月度回收 + DR/退化/执行分项，不重复计费）、`aggregate_to_settle_periods`、`compute_dr_settlement` |

### `dual_settlement/` — 双结算（偏差带考核）模式（规则研究参考蒙西 v1.3 双结算设计）

| 模块 | 职责 |
|------|------|
| `contracts.py` | `MarketConfig`（偏差带/中长期回收/结算时段）、`SettlementReport`（双结算口径报告） |
| `config_loader.py` | `load_market_config`：YAML 加载与校验（`band_deviation` 模式、带序、结算时段） |
| `settlement.py` | 双结算引擎：`compute_settlement_C`（量价结算）、`compute_settlement_C2`（差价结算，与 C 代数恒等）、`compute_cpen_dayah`（日前偏差考核）、`compute_cpen_long`（中长期月度回收） |

当前为带测试的规则引擎库（`tests/markets/` 18 项），未接入主链编排——
v1 报量报价日前属参与者角色差异，待报价契约设计时另行实现。

### `shared.py` — 跨模式共享工具

`aggregate_to_settle_periods`（结算时段能量守恒聚合）统一实现于此，
两个插件共用。

## 接口接缝记录（第二模式接入时发现）

1. `aggregate_to_settle_periods` 原先在两个结算实现中各有一份（语义等价、
   校验严格度不同）——已上提 `shared.py`，统一采用单结算现役实现
   （含 ndim 校验）。
2. 两种模式的 `SettlementReport` 字段结构不同（分项口径差异）——模式接口
   允许各插件自定义报告契约，`domain` 不强求统一报告结构。
3. 双结算插件配置字段仅为结算子集——v1 完整配置中的报量报价/风控/策略
   权重属决策层而非规则层，未随插件移植。

配置：`configs/markets/single_settlement.yaml`、`configs/markets/dual_settlement.yaml`；
标 `TODO(rule-confirm)` 的参数为待书面规则确认的默认值。第二个市场进入时
新建 `markets/<新模式>/`，不从现有插件复制地区命名。
