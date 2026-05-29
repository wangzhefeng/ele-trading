# scenario — 场景生成与缩减模块

本模块把点预测扩展为多场景输入，服务 Two-stage、CVaR、回测和风险分析。

## 当前文件

| 文件 | 职责 |
|------|------|
| `sampler.py` | 生成价格场景，支持 LHS 和 Monte Carlo |
| `reduction.py` | 权重归一化和 Kantorovich/Wasserstein L1 后向缩减 |

## 采样逻辑

`generate_price_scenarios()` 默认使用 Latin Hypercube Sampling（LHS）生成扰动样本；也保留 `method="mc"` 的 Monte Carlo 路径用于兼容和对比。

当前能力：

- 按 `base_prices` 和 `noise_scale` 构造价格扰动。
- 支持随机种子，便于测试和复现实验。
- 可通过 Cholesky 分解引入跨时段相关性。
- 输出 `PriceScenario`，包含 `name`、`prices`、`weight`。

## 缩减逻辑

`reduce_scenarios()` 使用后向缩减思路：

- 先对场景权重归一化。
- 用 L1 距离衡量场景路径差异。
- 迭代删除代价较小的场景，并把权重转移给最近保留场景。
- 最终输出 `top_k` 个代表性场景。

## 上下游关系

- 上游：`forecasting.price_forecast` 或外部预测系统提供点预测、历史误差或价格路径。
- 下游：`optimization.two_stage_cvar`、回测流程和风险评估使用缩减后的场景集合。

## 使用边界

- 当前价格扰动是工程样例，不等价于真实现货价格概率模型。
- 新增高级方法如 Copula、Bootstrap、误差重采样时，应保持 `PriceScenario` 数据结构稳定。
