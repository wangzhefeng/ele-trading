# config_loader

`config_loader` 定义测算场景的结构化配置，并负责从 YAML 文件加载参数。

核心作用：

1. 用 `ProjectConfig` 表示项目级参数，如风光容量、PPA 固定单价、余电上网价格和目标 IRR。
2. 用 `BESSConfig` 表示储能容量、效率、SOC 边界、电网充电开关和充放电电价类型。
3. 用 `FinanceConfig` 表示税前项目 IRR 测算所需的 CAPEX、运维、衰减和储能更换假设。
4. 用 `CaseConfig` 汇总场景名称、输入输出路径、项目参数和模拟数据生成参数。
5. 用 `CapacitySearchConfig` 表示容量搜索候选值、约束阈值和目标模式。
6. 用 `BaselineProjectConfig` 表示 V5 的基准方案，用于计算投资方 IRR 相对提升。

使用方式：

```python
from investment_estimation.config_loader import load_case_config

case = load_case_config("src/investment_estimation/configs/mvp_demo.yaml")
```

路径规则：

1. YAML 放在 `configs/` 下时，相对路径按 `src/investment_estimation/` 解析。
2. 不确定但算法需要的输入，应先放入 YAML 配置，并在字段名和 README 中说明含义。
3. 当前最小可行版本采用项目税前 IRR、固定单价 PPA、允许储能从电网充电、允许余电上网。

## 实现进度

MVP 版本已实现：

1. 项目、储能、财务、路径、样例数据配置 dataclass。
2. YAML 场景配置加载。
3. 相对路径按 `src/investment_estimation/` 解析。

v1 版本已实现：

1. 容量搜索配置 `CapacitySearchConfig`。
2. 结算扩展配置 `SettlementConfig`。
3. v1 搜索结果输出路径配置。
4. 从 `v1_capacity_search_demo.yaml` 加载候选容量、PPA 单价和约束阈值。

V2-V5 版本已实现：

1. `objective_mode` 配置字段。
2. `investor_irr_first`、`owner_saving_first`、`investor_irr_uplift` 三种目标模式。
3. `baseline_project` 基准方案配置。
4. 从 V2-V5 YAML 场景加载目标模式和基准方案。

后续待扩展：

1. 配置字段级校验和错误提示。
2. 税后 IRR、资本金 IRR 所需的融资、税费、折旧配置。
3. 更完整的政策约束和特殊负荷约束配置。
