# eva_PV_optim_version

该目录保留为 legacy reference。

用途边界：

- 作为历史负荷重建、PV 仿真、风电资源回标和样例数据拼装的参考实现。
- 作为主线重构后的回归对照来源。

不再承担的职责：

- 不再作为 `ele_trading` 主线的数据入口。
- 不再继续扩展新的主业务功能。
- 不再作为收益测算和电力市场交易的数据层标准实现。

当前主线对应能力已迁入或正在迁入：

- `ele_trading.data_provider.load_profile`
- `ele_trading.data_provider.resource_weather`
- `ele_trading.data_provider.case_dataset`
- `ele_trading.capacity_planning.pv_profile`
- `ele_trading.capacity_planning.wind_profile`

若需要新增主线功能，应优先修改 `src/ele_trading/` 下对应模块，而不是在本目录追加新脚本。
