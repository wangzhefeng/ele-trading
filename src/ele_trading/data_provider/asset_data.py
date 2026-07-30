"""活动资产配置接入：新能源与 BESS 的物理/效率参数。

当前仅含 BESS；配置统一从 YAML 读取（经 ``ele_trading.utils.io.read_yaml``），
字段与 dataclass 一一对应，缺字段或多余字段都会在构造时显式报错。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ele_trading.utils.io import read_yaml


@dataclass(slots=True)
class BESSConfig:
    """储能物理约束与效率参数。

    字段约定：
    - ``soc0`` / ``soc_min`` / ``soc_max``：初始及上下限荷电状态（MWh）；
    - ``p_ch_max`` / ``p_dis_max``：最大充/放电功率（MW）；
    - ``eta_ch`` / ``eta_dis``：充/放电效率（0–1）；
    - ``deg_cost``：单位吞吐退化成本（元/MWh）；
    - ``dt``：时间步长（小时）。15 分钟颗粒度必须为 0.25（AGENTS.md 约束）。
    """

    asset_name: str
    soc0: float
    soc_min: float
    soc_max: float
    p_ch_max: float
    p_dis_max: float
    eta_ch: float
    eta_dis: float
    deg_cost: float
    dt: float


def load_bess_config(path: str | Path) -> BESSConfig:
    """从 YAML 加载活动 BESS 参数，字段与 ``BESSConfig`` 严格一一对应。"""
    return BESSConfig(**read_yaml(path))
