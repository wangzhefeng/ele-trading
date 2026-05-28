"""最大需量计算使用示例。

演示完整流程: 模拟数据 -> 需量计算 -> 电费计算 -> 可视化。
"""
from __future__ import annotations

from .calc import calc_demand, calc_demand_charge
from .config import DemandConfig
from .data import generate_simulated_load
from .plot import plot_load_with_demand, plot_monthly_demand


def main() -> None:
    # 1. 生成模拟负荷数据（30天，15分钟采样）
    df = generate_simulated_load(n_days=30, freq="15min")
    power = df.set_index("timestamp")["power_kw"]

    # 2. 配置: 15分钟滑动窗口，需量电价 40 元/kW/月
    cfg = DemandConfig(window_minutes=15, window_type="sliding", demand_price=40.0)

    # 3. 计算最大需量
    result = calc_demand(power, cfg)

    print("=" * 50)
    print("最大需量计算结果")
    print("=" * 50)
    print(f"最大需量:    {result.max_demand:.1f} kW")
    print(f"发生时刻:    {result.peak_timestamp}")
    print()
    print("月度最大需量:")
    for period, val in result.monthly_max.items():
        print(f"  {period}:  {val:.1f} kW")
    print()

    # 4. 计算需量电费
    charge = calc_demand_charge(result, demand_price=40.0)
    print(f"需量电价:    {charge['demand_price']:.1f} 元/kW/月")
    print(f"月基本电费:  {charge['demand_charge']:.1f} 元")
    print()

    # 5. 固定窗口对比
    cfg_fixed = DemandConfig(window_minutes=15, window_type="fixed", demand_price=40.0)
    result_fixed = calc_demand(power, cfg_fixed)
    print(f"固定窗口最大需量: {result_fixed.max_demand:.1f} kW")
    print(f"滑动窗口最大需量: {result.max_demand:.1f} kW")
    print()

    # 6. 可视化
    plot_load_with_demand(power, result)
    plot_monthly_demand(result)


if __name__ == "__main__":
    main()
