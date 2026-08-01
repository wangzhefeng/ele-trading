# scenario — 场景生成与缩减

本模块把价格、负荷、风电和光伏预测扩展为概率场景，服务风险优化、回测和诊断。

## 当前 API

| 文件 | 当前职责 | 状态 |
|---|---|---|
| `contracts.py` | `Scenario` / `ScenarioSet` 对齐、概率和来源版本契约 | canonical |
| `joint_builder.py` | 从四类 `ForecastResult` 构建联合场景 | canonical |
| `reduction.py` | 联合 Wasserstein L1 后向缩减与兼容 wrapper | canonical + compatibility |
| `sampler.py` | `PriceScenario` 和独立价格扰动采样 | legacy compatibility |

## 当前生成逻辑

`build_joint_scenarios()` 默认支持 LHS，并保留 `method="mc"` 对照路径。当前实现：

- 强制四类预测使用同一 issue time、horizon 和 tz-aware valid-time index；
- 重新校验 `feature_as_of`；显式 q0.5 优先，point 只补缺失 median；
- 从分位与残差尺度构造边际分布；
- 使用经过校验的 Gaussian copula 相关矩阵；
- 输出共同单位、概率、随机种子、模型版本和来源时间元数据。

## 当前缩减逻辑

`reduce_scenarios()` 的 canonical 输入输出是 `ScenarioSet`：

- 使用按 target 尺度归一的联合 L1 距离；
- 逐轮重新计算候选保留集的最小运输代价；
- 将移除场景概率转移到最近保留场景并归一化；
- 支持关键净负荷峰值/爬坡保护与分布漂移诊断；
- `PriceScenario` wrapper 仅服务现有兼容测试和窄调用面。

## 成熟度边界

当前场景生成和缩减是可运行工程基线。Gaussian copula、边际分布、相关矩阵和缩减阈值尚未通过真实市场历史误差完成生产标定。canonical API、兼容期限和未来算法选择由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#73-场景与风险)决定。
