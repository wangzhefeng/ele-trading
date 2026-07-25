# app

`app` 存放可直接运行的测算入口脚本，并直接承载 MVP 与 v1 的运行编排流程。

当前入口：

```bash
PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_mvp_demo \
  --config src/investment_estimation/configs/mvp_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v1_capacity_search_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_capacity_search \
  --config src/investment_estimation/configs/v5_investor_irr_uplift_demo.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_pv_simulation_v1 \
  --config src/investment_estimation/configs/resource_pv_simulation_v1.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.run_wind_simulation_v1 \
  --config src/investment_estimation/configs/resource_wind_simulation_v1.yaml

PYTHONPATH=src ./.venv/bin/python -m investment_estimation.app.build_resource_profile \
  --config src/investment_estimation/configs/resource_profile_demo.yaml
```

脚本职责：

1. 读取 YAML 场景配置。
2. 可选生成模拟输入 CSV。
3. 直接运行最小可行版本或 v1 容量搜索测算流程。
4. 按配置写出逐时调度结果和月度结算结果。
5. 在终端打印项目税前 IRR 和目标 PPA 反求价格。
6. 资源仿真入口负责生成 `time,pv_kw`、`time,wind_kw` 和 `time,pv_kw,wind_kw`。

## 实现进度

MVP 版本已实现：

1. `run_mvp_demo.py` 命令行入口。
2. `--config` 指定 YAML 场景文件。
3. 终端打印核心财务结果。

v1 版本已实现：

1. `run_capacity_search.py` 容量搜索入口。
2. 终端打印候选方案数、可行方案数和最优方案摘要。

V2-V5 版本已实现：

1. 继续复用 `run_capacity_search.py`。
2. 支持通过不同 YAML 场景运行投资方优先、业主节费优先和 IRR uplift 模式。

资源仿真入口已实现：

1. `run_pv_simulation_v1.py`。
2. `run_pv_simulation_v2.py`。
3. `run_wind_simulation_v1.py`。
4. `run_wind_simulation_v2.py`。
5. `build_resource_profile.py`。

后续待扩展：

1. 多场景批量运行入口。
2. 结果摘要报告生成入口。
