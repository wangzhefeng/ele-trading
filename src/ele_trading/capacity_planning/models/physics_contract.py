"""容量规划调度路径共享的 BESS 物理参数合同。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BESSPhysicsContract:
    """容量规划内部统一使用的储能物理合同。

    设计目标：
    1. 把充电效率、放电效率、SOC 单位和 C-rate 语义集中到一个对象；
    2. 避免不同 planner 在本地写 0.92、0.95 或 sqrt(eta) 等散落默认值；
    3. 让 canonical dispatch、旧 wrapper 和后续结算层都能追溯同一组物理参数。

    V4 第一阶段保留 Wind/PV/BESS IRR 主链旧口径：往返效率 0.92 对称拆成
    充电/放电单边效率 sqrt(0.92), SOC 的内部单位统一为 kWh。
    """

    # 单边充电效率：进入电池 SOC 的能量 = AC 侧充电电量 * eta_charge。
    eta_charge: float = 0.92 ** 0.5
    # 单边放电效率：AC 侧放电电量 = 电池 SOC 减少量 * eta_discharge。
    eta_discharge: float = 0.92 ** 0.5
    # SOC 三个比例字段仍沿用既有 public config 的语义，adapter 只做集中转换。
    soc_init_frac: float = 0.1
    soc_min_frac: float = 0.1
    soc_max_frac: float = 1.0
    # c_rate 表示最大充/放电功率 kW = c_rate * 容量 kWh。
    c_rate: float = 0.5
    # V4 canonical 核内部只接受 kWh，避免 fraction/kWh 混用。
    soc_unit: str = "kwh"
    c_rate_definition: str = "power_kw_per_capacity_kwh"

    @classmethod
    def from_roundtrip(
        cls,
        roundtrip_efficiency: float,
        *,
        soc_init_frac: float = 0.1,
        soc_min_frac: float = 0.1,
        soc_max_frac: float = 1.0,
        c_rate: float = 0.5,
    ) -> "BESSPhysicsContract":
        """
        由旧 public config 的往返效率构造 V4 物理合同。
        """
        if roundtrip_efficiency <= 0:
            raise ValueError("roundtrip_efficiency must be positive")
        eta = float(roundtrip_efficiency) ** 0.5
        return cls(
            eta_charge=eta,
            eta_discharge=eta,
            soc_init_frac=float(soc_init_frac),
            soc_min_frac=float(soc_min_frac),
            soc_max_frac=float(soc_max_frac),
            c_rate=float(c_rate),
        )

    def validate(self) -> None:
        """
        在进入调度前校验物理参数，尽早暴露错误配置。
        """
        if self.eta_charge <= 0 or self.eta_discharge <= 0:
            raise ValueError("BESS efficiencies must be positive")
        if not 0 <= self.soc_min_frac <= self.soc_max_frac <= 1:
            raise ValueError("SOC fractions must satisfy 0 <= min <= max <= 1")
        if not self.soc_min_frac <= self.soc_init_frac <= self.soc_max_frac:
            raise ValueError("initial SOC must be within SOC bounds")
        if self.c_rate < 0:
            raise ValueError("c_rate must be non-negative")
        if self.soc_unit != "kwh":
            raise ValueError("V4 canonical dispatch requires soc_unit='kwh'")
