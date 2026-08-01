"""用户侧调度场景适配层。

单节点适配器(``dispatch_adapters``)只依赖 PuLP 内核,可无 CVXPY 导入;
分布式适配器(``distributed_dispatch_adapters``)依赖 CVXPY 内核,经包级
lazy 属性访问。
"""
