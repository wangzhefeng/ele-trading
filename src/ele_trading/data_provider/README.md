# 数据模块说明

数据模块负责把样例数据、配置参数和场景文件读取成统一的数据结构。

## 当前文件

- `schemas.py`：定义价格序列、储能参数和场景样本结构。
- `loader.py`：读取 CSV / YAML 样例文件。
- `sample_data.py`：提供内置样例路径和快捷加载函数。
- `user_side_storage_sample.py`：读取用户侧储能调度 demo 配置，并生成确定性的负荷 / 电价 / 电价类型模拟数据。
- `user_side_pv_dispatch_sample.py`：读取用户侧光伏调度 demo 配置，并生成确定性的负荷 / 光伏 / 电价模拟数据。
- `user_side_pv_storage_dispatch_sample.py`：读取用户侧光伏+储能调度 demo 配置，并生成确定性的负荷 / 光伏 / 电价模拟数据。

## 用户侧储能调度模拟数据

`user_side_storage_sample.py` 服务 `app/run_user_side_storage_dispatch.py` 和相关测试。它根据 `configs/user_side_storage_dispatch.yaml` 生成一个确定性的调度窗口：

- `timestamp`：调度时段时间戳。
- `load_forecast`：未来负荷预测样例。
- `buy_price`：购电价格样例。
- `price_type`：分时电价类型样例。

这组数据只用于 demo 和回归测试，不代表真实负荷预测模型。真实项目接入时应由预测模块或外部数据源提供 `load_forecast`，再构造 `UserSideStorageDispatchInput`。

## 用户侧光伏调度模拟数据

`user_side_pv_dispatch_sample.py` 服务 `app/run_user_side_pv_dispatch.py`，根据 `configs/user_side_pv_dispatch.yaml` 生成 PV-only 调度输入。

`user_side_pv_storage_dispatch_sample.py` 服务 `app/run_user_side_pv_storage_dispatch.py`，根据 `configs/user_side_pv_storage_dispatch.yaml` 生成 PV+storage 调度输入。

两个脚本各自维护模拟数据构造逻辑，不互相导入 builder 函数。它们生成的表字段一致：

- `timestamp`：调度时段时间戳。
- `load_forecast`：未来负荷预测样例。
- `pv_forecast`：未来光伏功率预测样例。
- `buy_price`：购电价格样例。
- `price_type`：分时电价类型样例。

这两组数据只用于 demo 和测试，不代表真实光伏预测模型。真实项目接入时应由预测模块或外部数据源提供 `load_forecast` 和 `pv_forecast`。

## 上下游关系

- 上游来自 `data/` 目录中的原始样例数据与配置。
- 下游被 `forecasting`、`scenario`、`optimization`、`evaluation` 调用。

## 扩展建议

后续可增加数据库读取、接口拉取、市场数据版本管理与设备状态快照加载能力。
