from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SettlementConfig:
    """结算扩展配置，用于 v1 阶段引入账单类占位口径。"""

    # 每月固定基本电费，单位元/月；业务口径未确认时用于模拟。
    basic_charge_per_month: float = 0.0
    # 最大需量电费单价，单位元/kW/月；按月最大电网购电功率估算。
    demand_charge_per_kw_month: float = 0.0
    # 输配电价附加，单位元/kWh；按电网购电量叠加。
    transmission_price_adder: float = 0.0
    # 偏差考核费用率，单位元/kWh；MVP/v1 暂按电网购电量占位估算。
    deviation_penalty_per_kwh: float = 0.0


@dataclass(frozen=True)
class BESSConfig:
    """储能系统配置，用于 MVP 规则调度和储能投资测算。"""

    # 储能额定功率，单位 kW；限制单个时间步的最大充放电功率。
    power_kw: float = 0.0
    # 储能额定容量，单位 kWh；决定 SOC 上下限和容量侧投资。
    energy_kwh: float = 0.0
    # 充电效率；外部充入电量转换为 SOC 的比例。
    charge_efficiency: float = 0.95
    # 放电效率；SOC 转换为负荷侧可用电量的比例。
    discharge_efficiency: float = 0.95
    # 最小 SOC 比例；SOC 不低于 energy_kwh * soc_min_pct。
    soc_min_pct: float = 0.1
    # 最大 SOC 比例；SOC 不高于 energy_kwh * soc_max_pct。
    soc_max_pct: float = 0.9
    # 初始 SOC 比例；仿真首个时间步开始前的储能电量。
    initial_soc_pct: float = 0.5
    # 是否允许电网充电；MVP 口径已确认允许。
    allow_grid_charge: bool = True
    # 允许从电网充电的标准 price_type 集合；内部统一使用英文编码。
    charge_price_types: tuple[str, ...] = ("deep_valley", "valley", "flat")
    # 允许放电供负荷的标准 price_type 集合；内部统一使用英文编码。
    discharge_price_types: tuple[str, ...] = ("peak", "sharp_peak")


@dataclass(frozen=True)
class FinanceConfig:
    """税前项目 IRR 测算配置。"""

    # 项目测算年限，单位年。
    project_years: int = 20
    # 风电单位投资，单位元/kW。
    capex_wind_per_kw: float = 5500.0
    # 光伏单位投资，单位元/kW。
    capex_pv_per_kw: float = 3500.0
    # 储能功率侧单位投资，单位元/kW。
    capex_bess_power_per_kw: float = 500.0
    # 储能容量侧单位投资，单位元/kWh。
    capex_bess_energy_per_kwh: float = 900.0
    # 固定运维费比例，按初始 CAPEX 每年扣除。
    fixed_om_pct_of_capex: float = 0.02
    # 可再生资源年收入衰减率，用于近似表达风光出力衰减。
    renewable_degradation_pct: float = 0.005
    # 储能更换年份；None 表示不计入更换成本。
    bess_replacement_year: int | None = 10
    # 储能更换成本比例，按储能初始投资乘以该比例。
    bess_replacement_cost_pct: float = 0.35


@dataclass(frozen=True)
class ProjectConfig:
    """项目级配置，汇总容量、电价、目标收益和嵌套配置。"""

    # 风电装机容量，单位 kW；当前用于 CAPEX，不缩放资源 CSV。
    wind_capacity_kw: float = 0.0
    # 光伏装机容量，单位 kW；当前用于 CAPEX，不缩放资源 CSV。
    pv_capacity_kw: float = 0.0
    # 固定 PPA 单价，单位元/kWh。
    ppa_price: float = 0.45
    # 余电上网价格，单位元/kWh。
    export_price: float = 0.25
    # 目标税前项目 IRR，用于 PPA 价格反求。
    target_irr: float = 0.08
    # 电价单位说明，仅作为配置元数据。
    price_unit: str = "CNY_per_kWh"
    # 时区说明，当前 MVP 暂不主动转换时区。
    time_zone: str | None = None
    # 储能配置。
    bess: BESSConfig = BESSConfig()
    # 财务测算配置。
    finance: FinanceConfig = FinanceConfig()
    # 结算扩展配置。
    settlement: SettlementConfig = SettlementConfig()


@dataclass(frozen=True)
class CapacitySearchConfig:
    """v1 容量搜索配置，使用粗网格枚举保持结果可解释。"""

    # 是否启用容量搜索。
    enabled: bool = False
    # 风电容量候选值，单位 kW。
    wind_capacity_kw: tuple[float, ...] = ()
    # 光伏容量候选值，单位 kW。
    pv_capacity_kw: tuple[float, ...] = ()
    # 储能功率候选值，单位 kW。
    bess_power_kw: tuple[float, ...] = ()
    # 储能容量候选值，单位 kWh。
    bess_energy_kwh: tuple[float, ...] = ()
    # 固定 PPA 单价候选值，单位元/kWh。
    ppa_price: tuple[float, ...] = ()
    # 目标模式；控制可行候选方案的最优排序规则。
    objective_mode: str = "investor_irr_first"
    # 投资方最低税前项目 IRR 约束。
    min_project_irr: float = 0.0
    # 业主最低节费比例约束。
    min_owner_saving_pct: float = 0.0
    # 最低自发自用比例约束；None 表示不启用。
    min_self_use_ratio: float | None = None
    # 最大余电上网比例约束；None 表示不启用。
    max_export_ratio: float | None = None


@dataclass(frozen=True)
class BaselineProjectConfig:
    """V5 基准方案配置，用于计算投资方 IRR 相对提升。"""

    # 基准方案风电容量，单位 kW；None 表示沿用 project.wind_capacity_kw。
    wind_capacity_kw: float | None = None
    # 基准方案光伏容量，单位 kW；None 表示沿用 project.pv_capacity_kw。
    pv_capacity_kw: float | None = None
    # 基准方案固定 PPA 单价，单位元/kWh；None 表示沿用 project.ppa_price。
    ppa_price: float | None = None
    # 基准方案储能功率，单位 kW；None 表示沿用 project.bess.power_kw。
    bess_power_kw: float | None = None
    # 基准方案储能容量，单位 kWh；None 表示沿用 project.bess.energy_kwh。
    bess_energy_kwh: float | None = None


@dataclass(frozen=True)
class PathConfig:
    """场景输入和输出路径配置。"""

    # 负荷 CSV 路径，字段为 time,value。
    load_csv: Path
    # 电价 CSV 路径，字段为 time,price,price_type；price_type 读取后统一为英文编码。
    price_csv: Path
    # 风光资源 CSV 路径，字段为 time,pv_kw,wind_kw。
    resource_csv: Path
    # 月度结算结果输出路径。
    monthly_output_csv: Path
    # 时序调度结果输出路径。
    dispatch_output_csv: Path
    # v1 候选方案搜索结果输出路径。
    candidate_output_csv: Path | None = None
    # v1 最优方案摘要输出路径。
    best_summary_csv: Path | None = None
    # v1 不可行原因输出路径。
    infeasible_reasons_csv: Path | None = None
    # v1 年度现金流输出路径。
    annual_cashflows_csv: Path | None = None


@dataclass(frozen=True)
class SampleDataConfig:
    """模拟数据生成配置，用于真实输入尚不完整的 MVP 验证。"""

    # 是否在运行前生成模拟 CSV。
    enabled: bool = False
    # 模拟数据年份。
    year: int = 2026
    # 模拟数据频率，如 1h 或 15min。
    freq: str = "1h"


@dataclass(frozen=True)
class CaseConfig:
    """完整测算场景配置，由 YAML 加载后传入 app 运行脚本。"""

    # 场景名称。
    name: str
    # 输入输出路径配置。
    paths: PathConfig
    # 项目测算配置。
    project: ProjectConfig
    # 模拟数据生成配置。
    sample_data: SampleDataConfig = SampleDataConfig()
    # v1 容量搜索配置。
    search: CapacitySearchConfig = CapacitySearchConfig()
    # V5 基准方案配置；仅 investor_irr_uplift 模式需要。
    baseline_project: BaselineProjectConfig | None = None
