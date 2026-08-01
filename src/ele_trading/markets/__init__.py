"""markets — 市场规则插件层。

按结算模式（而非地区名）组织子包；每个子包是一个自包含的规则插件
（配置契约 + 加载校验 + 结算引擎）。``shared`` 提供跨模式通用结算工具。
当前插件：

- ``single_settlement``：单结算模式（实时电能 + 中长期差价 + 回收及逐项
  调整），规则研究参考蒙西市场规则。
- ``dual_settlement``：双结算（偏差带考核）模式（量价结算 C/差价结算 C2 +
  日前偏差考核 + 中长期回收），规则研究参考蒙西 v1.3 双结算设计；
  当前为带测试的规则引擎库，未接入主链编排。
"""

from . import dual_settlement, single_settlement

__all__ = ["dual_settlement", "single_settlement"]
