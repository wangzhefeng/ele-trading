# configs

`configs` 存放每个测算场景的 YAML 配置文件。

当前示例：

1. `mvp_demo.yaml`：最小可行版本演示场景。
2. `v1_capacity_search_demo.yaml`：v1 容量搜索演示场景。

YAML 结构：

1. `scenario`：场景名称。
2. `sample_data`：是否生成模拟 CSV、年份和时间频率。
3. `paths`：负荷、电价、资源输入 CSV，以及结果输出 CSV。
4. `project`：风光容量、固定 PPA 单价、余电上网价格、目标 IRR。
5. `bess`：储能容量、效率、SOC 边界和充放电电价类型。
6. `finance`：税前项目 IRR 所需的投资、运维、衰减和更换假设。

规则：

1. 配置文件位于 `configs/` 下时，相对路径按 `src/invest_est_models/` 解析。
2. 暂不确定但算法需要的数据，先用 YAML 参数表示，并在对应 README 或 PLAN 中说明含义。
3. 不在代码中硬编码具体场景参数。

## 实现进度

MVP 版本已实现：

1. `mvp_demo.yaml` 单场景配置。
2. 输入输出路径配置。
3. 项目、储能、财务和模拟数据参数配置。

v1 版本已实现：

1. `v1_capacity_search_demo.yaml` 容量搜索配置。
2. 搜索候选容量、PPA 单价和约束阈值配置。
3. 候选结果、最优摘要、不可行原因和年度现金流输出路径配置。

后续待扩展：

1. 真实项目场景配置模板。
2. 15 分钟场景配置模板。
3. 不同 PPA 价格策略配置。
4. 政策约束配置。
