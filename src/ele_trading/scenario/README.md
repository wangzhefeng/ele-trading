# scenario — 场景生成与缩减模块

本模块把点预测扩展为多场景输入，服务 Two-stage、CVaR、回测和风险分析。

## 当前文件

| 文件 | 职责 |
|------|------|
| `contracts.py` | `Scenario` / `ScenarioSet` 对齐、概率与来源版本契约 |
| `joint_builder.py` | 从 price/load/wind/PV `ForecastResult` 构建联合场景 |
| `sampler.py` | 兼容价格场景 wrapper，支持 LHS 和 Monte Carlo |
| `reduction.py` | 联合场景的 Kantorovich/Wasserstein L1 后向缩减与诊断 |

## 采样逻辑

v2 主入口 `build_joint_scenarios()` 默认使用 Latin Hypercube Sampling
（LHS）；也保留 `method="mc"` 的 Monte Carlo 路径用于对比。它从四类
`ForecastResult` 的分位与残差尺度构造边际分布，并通过经过校验的
Gaussian copula 保留 target 或 target-time 相关矩阵。

当前能力：

- 支持随机种子，便于测试和复现实验。
- 强制四类来源使用相同 issue time、tz-aware valid-time index 与 horizon。
- 入口重新校验 `feature_as_of`；显式 q0.5 优先于 point，point 只补缺失 median。
- 输出共同单位、概率、seed、模型版本和 feature-as-of 元数据。
- `generate_price_scenarios()` / `PriceScenario` 仅作为活动 v1 兼容面。

## 缩减逻辑

`reduce_scenarios()` 的 v2 输入/输出为 `ScenarioSet`：

- 用按 target 尺度归一的联合 L1 距离衡量路径差异。
- 每轮对候选保留集重新计算全部原始概率的最小运输代价。
- 最终把每个移除场景的原始概率转移到最近的保留场景并归一化。
- retained 场景始终保留自己的原始概率；重复路径与 `top_k == N` 合法。
- 默认保留关键净负荷峰值/爬坡场景，并报告 Wasserstein 代价、概率转移、
  均值与分位漂移；可用阈值让漂移超限显式失败。

## 上下游关系

- 上游：`forecasting` 提供严格 `ForecastResult`。
- 下游：`optimization.two_stage_cvar`、回测流程和风险评估使用缩减后的场景集合。

## 使用边界

- Copula 与分位边际是透明工程基线，不替代真实市场误差校准。
- v2 扩展应保持 `Scenario` / `ScenarioSet` 契约稳定；`PriceScenario` 仅保留
  窄兼容用途。
