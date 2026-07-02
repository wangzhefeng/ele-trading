# finance

`finance` 负责财务测算层的税前项目 IRR 和 PPA 反求。

当前口径：

1. 用户已确认最小可行版本采用项目税前 IRR。
2. 初始现金流为风、光、储 CAPEX。
3. 年度收入来自月度结算汇总的投资方收入。
4. 年度成本包括固定运维、可再生出力衰减影响和储能更换成本。
5. 不纳入融资结构、所得税、折旧和增值税。

使用方式：

```python
from invest_est_models.finance import backsolve_ppa_price, compute_project_irr

irr = compute_project_irr(monthly, case.project)
target_price = backsolve_ppa_price(dispatch, case.project)
```

扩展方向：

1. 若要计算税后 IRR，需要加入税费和折旧。
2. 若要计算资本金 IRR，需要加入贷款比例、利率、还款方式和还本付息计划。

## 实现进度

MVP 版本已实现：

1. 风、光、储 CAPEX 计算。
2. 税前项目年度现金流。
3. 固定运维、可再生出力衰减和储能更换成本。
4. 税前项目 IRR 求解。
5. 达到目标 IRR 的固定 PPA 单价反求。

v1 版本已实现：

1. NPV 计算。
2. 静态/动态回收期计算。
3. 年度现金流明细表。

后续待扩展：

1. 税后现金流。
2. 资本金现金流。
3. 融资还款计划。
4. 多情景敏感性分析。
