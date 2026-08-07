# scenario — 场景生成与缩减

本模块把价格、负荷、风电和光伏预测扩展为概率场景，服务风险优化、回测和诊断。

## 当前 API

| 文件 | 当前职责 | 状态 |
|---|---|---|
| `contracts.py` | `Scenario` / `ScenarioSet` 对齐、概率和来源版本契约 | canonical |
| `joint_builder.py` | 从四类 `ForecastResult` 构建联合场景 | canonical |
| `reduction.py` | 联合 Wasserstein L1 后向缩减 | canonical |
| `diagnostics.py` | 场景集质量诊断（权重守恒、边际一致、相关保持、极端覆盖、复现性，v4 P0） | 可选增强 |

## 当前生成逻辑

`build_joint_scenarios()` 默认支持 LHS，并保留 `method="mc"` 对照路径。当前实现：

- 强制四类预测使用同一 issue time、horizon 和 tz-aware valid-time index；
- 重新校验 `feature_as_of`；显式 q0.5 优先，point 只补缺失 median；
- 从分位与残差尺度构造边际分布；
- 使用经过校验的 Gaussian copula 相关矩阵；
- 输出共同单位、概率、随机种子、模型版本和来源时间元数据。

`diagnostics.diagnose_scenario_set`（v4 P0）对活动场景集执行五项诊断（权重守恒、边际一致、相关保持、极端覆盖、复现性），不修改场景集，只报告；无历史参考时相关/极端覆盖显式 skipped。

## 当前缩减逻辑

`reduce_scenarios()` 的 canonical 输入输出是 `ScenarioSet`：

- 使用按 target 尺度归一的联合 L1 距离；
- 逐轮重新计算候选保留集的最小运输代价；
- 将移除场景概率转移到最近保留场景并归一化；
- 支持关键净负荷峰值/爬坡保护与分布漂移诊断。

## 成熟度边界

当前场景生成和缩减是可运行工程基线；状态条件 Student-t Copula、极端模板和缩减保护已实现。Gaussian 是退化比较路径，边际/相关/尾部的真实市场校准、重要性采样、对抗压力和物理投影仍未完成，见 [v5 §8、V5-10](../../../docs/策略算法框架详细设计-v5.md#8-状态条件场景与风险)。
