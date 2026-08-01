"""用户侧通用可再生能源无储能调度内核。

本模块是 PV / Wind 无储能场景的 canonical shared kernel。每个时刻按固定
优先级分配可再生出力：就地消纳本地负荷 → 盈余上网 → 弃电，负荷缺口由
电网补足。结果由规则唯一确定（无优化求解）。

运行 `python -m ele_trading.user_side_dispatch.user_side_renewable_dispatch_class`
可执行固定样例自检。
"""

from __future__ import annotations

from ele_trading.utils import clean_value
from ..interfaces import (
    UserSidePVExportParams,
    UserSideRenewableDispatchInput,
    UserSideRenewableDispatchResult,
)


class UserSideRenewableDispatcher:
    """确定性用户侧可再生能源调度（无储能）的 class 实现。

    每个时刻按优先级链分配可再生出力：
    ① 就地消纳本地负荷 → ② 盈余上网（受 export 规则限制）→ ③ 剩余弃电；
    负荷缺口由电网购电补足。不含储能，结果由规则唯一确定。
    """

    def __init__(self, dispatch_input: UserSideRenewableDispatchInput) -> None:
        # 只存输入，不做计算、不校验——构造永远成功，
        # 异常语义与原函数一致（都在调用入口 run() 抛）。
        self.dispatch_input = dispatch_input

    def run(self) -> UserSideRenewableDispatchResult:
        self._validate()
        renewable_to_load, renewable_to_grid, renewable_curtailment, grid_import = (
            self._allocate_power()
        )

        dispatch_input = self.dispatch_input
        # --- 经济性汇总（功率 × 时长换算为电量） ---
        # 周期内最大购电功率（尖峰需量）
        max_grid_import = clean_value(max(grid_import))
        # 电量电费：各时刻购电功率 × 时变购电价格 × 时长
        energy_cost = sum(
            dispatch_input.buy_price[t] * grid_import[t] * dispatch_input.step_hours
            for t in range(len(dispatch_input.timestamps))
        )
        # 需量（基本）电费：按最大需量计收
        demand_cost = dispatch_input.demand_charge_rate * max_grid_import
        # 售电价统一为等长列表：sell_price_list 提供则逐时段，否则广播标量 sell_price（支持现货逐时段）。
        _export = dispatch_input.export
        _sell_price_per_step = (
            list(_export.sell_price_list) if _export.sell_price_list is not None
            else [_export.sell_price] * len(dispatch_input.timestamps)
        )
        # 售电收入：上网电量 × 逐时段售电价 × 时长
        sell_revenue = sum(
            _sell_price_per_step[t] * renewable_to_grid[t] * dispatch_input.step_hours
            for t in range(len(dispatch_input.timestamps))
        )
        # 弃电机会成本：弃电量 × 弃电惩罚单价 × 时长
        curtailment_cost = sum(
            dispatch_input.export.curtailment_cost_rate
            * renewable_curtailment[t]
            * dispatch_input.step_hours
            for t in range(len(dispatch_input.timestamps))
        )

        return UserSideRenewableDispatchResult(
            renewable_to_load=renewable_to_load,
            renewable_to_grid=renewable_to_grid,
            renewable_curtailment=renewable_curtailment,
            grid_import=grid_import,
            max_grid_import=max_grid_import,
            energy_cost=energy_cost,
            demand_cost=demand_cost,
            sell_revenue=sell_revenue,
            curtailment_cost=curtailment_cost,
            # 总成本 = 电费 + 需量电费 + 弃电成本 - 售电收入
            total_cost=energy_cost + demand_cost + curtailment_cost - sell_revenue,
            constraint_violations=self._constraint_violations(
                renewable_to_load,
                renewable_to_grid,
                renewable_curtailment,
                grid_import,
                max_grid_import,
            ),
        )

    def _validate(self) -> None:
        """校验调度输入：序列等长、步长为正、费率非负、export 限制非负。"""
        dispatch_input = self.dispatch_input
        length = len(dispatch_input.timestamps)
        if length == 0:
            raise ValueError("dispatch horizon must not be empty")
        if not (
            len(dispatch_input.load_forecast)
            == len(dispatch_input.renewable_forecast)
            == len(dispatch_input.buy_price)
            == len(dispatch_input.price_type)
            == length
        ):
            raise ValueError(
                "timestamps, load_forecast, renewable_forecast, buy_price, "
                "and price_type must have the same length"
            )
        if dispatch_input.step_hours <= 0:
            raise ValueError("step_hours must be positive")
        if dispatch_input.demand_charge_rate < 0:
            raise ValueError("demand_charge_rate must be non-negative")
        if any(load < 0 for load in dispatch_input.load_forecast):
            raise ValueError("load_forecast must be non-negative")
        if any(renewable < 0 for renewable in dispatch_input.renewable_forecast):
            raise ValueError("renewable_forecast must be non-negative")
        export = dispatch_input.export
        # sell_price_list 提供时须与时段等长；buy_price / sell_price / sell_price_list 均允许负（现货市场可出现负电价）。
        if export.sell_price_list is not None and len(export.sell_price_list) != length:
            raise ValueError("export.sell_price_list length must match timestamps")
        if export.curtailment_cost_rate < 0:
            raise ValueError("export.curtailment_cost_rate must be non-negative")
        if export.export_limit is not None and export.export_limit < 0:
            raise ValueError("export.export_limit must be non-negative")

    def _allocate_power(
        self,
    ) -> tuple[list[float], list[float], list[float], list[float]]:
        """逐时刻按优先级链分配可再生出力，返回四路功率序列。"""
        dispatch_input = self.dispatch_input
        renewable_to_load: list[float] = []
        renewable_to_grid: list[float] = []
        renewable_curtailment: list[float] = []
        grid_import: list[float] = []

        # --- 逐时刻功率分配（优先级链） ---
        for load, renewable in zip(
            dispatch_input.load_forecast,
            dispatch_input.renewable_forecast,
        ):
            # ① 可再生优先消纳本地负荷，能消纳多少消纳多少
            local_renewable = min(load, renewable)
            # 扣除就地消纳后的盈余
            surplus = max(renewable - local_renewable, 0.0)
            # ② 盈余上网：受 allow_export / export_limit 限制
            export_power = self._export_power(surplus)
            # ③ 盈余中无法上网的部分被迫弃电
            curtailment = surplus - export_power

            renewable_to_load.append(clean_value(local_renewable))
            renewable_to_grid.append(clean_value(export_power))
            renewable_curtailment.append(clean_value(curtailment))
            # 负荷缺口向电网购买：可再生 + 电网 = 负荷（功率平衡）
            grid_import.append(clean_value(max(load - local_renewable, 0.0)))

        return renewable_to_load, renewable_to_grid, renewable_curtailment, grid_import

    def _export_power(self, surplus: float) -> float:
        """决定盈余中可上网的功率。

        不允许上网 → 0；无上限 → 全部盈余；有上限 → 不超过 export_limit。
        """
        export = self.dispatch_input.export
        if not export.allow_export:
            return 0.0
        if export.export_limit is None:
            return surplus
        return min(surplus, export.export_limit)

    def _constraint_violations(
        self,
        renewable_to_load: list[float],
        renewable_to_grid: list[float],
        renewable_curtailment: list[float],
        grid_import: list[float],
        max_grid_import: float,
    ) -> dict[str, float]:
        """校验功率守恒与边界约束，仅返回违反量超过容差的项。

        renewable_balance: 可再生去向之和（供负荷 + 上网 + 弃电）应等于出力；
        load_balance: 供负荷来源之和（可再生 + 电网）应等于负荷；
        grid_import_min: 电网购电不应为负；
        max_grid_import: 报告的最大需量应与实际最大值一致。
        """
        dispatch_input = self.dispatch_input
        tolerance = 1e-6
        violations = {
            "renewable_balance": max(
                abs(
                    renewable_to_load[t]
                    + renewable_to_grid[t]
                    + renewable_curtailment[t]
                    - renewable
                )
                for t, renewable in enumerate(dispatch_input.renewable_forecast)
            ),
            "load_balance": max(
                abs(renewable_to_load[t] + grid_import[t] - load)
                for t, load in enumerate(dispatch_input.load_forecast)
            ),
            "grid_import_min": max(-min(grid_import), 0.0),
            "max_grid_import": max(max(grid_import) - max_grid_import, 0.0),
        }
        # 仅保留违反量超过容差的约束项（确定性算法下理论上为空）
        return {
            name: amount for name, amount in violations.items() if amount > tolerance
        }


def run_user_side_renewable_dispatch(
    dispatch_input: UserSideRenewableDispatchInput,
) -> UserSideRenewableDispatchResult:
    """确定性用户侧可再生能源调度（无储能）。ele_trading 标准函数式 API。

    内部委托给 UserSideRenewableDispatcher，保持包级函数入口稳定。
    """
    return UserSideRenewableDispatcher(dispatch_input).run()


if __name__ == "__main__":
    # 自检：覆盖「就地消纳 + 上网 + 弃电 + 电网补缺」的固定样例。
    sample = UserSideRenewableDispatchInput(
        timestamps=[0, 1, 2],
        load_forecast=[5.0, 3.0, 8.0],
        renewable_forecast=[3.0, 7.0, 10.0],
        buy_price=[1.0, 1.0, 1.0],
        price_type=["flat", "flat", "flat"],
        export=UserSidePVExportParams(
            allow_export=True, sell_price=0.2, export_limit=2.0
        ),
        demand_charge_rate=10.0,
        step_hours=1.0,
    )

    result = run_user_side_renewable_dispatch(sample)

    assert result.renewable_to_load == [3.0, 3.0, 8.0]
    assert result.renewable_to_grid == [0.0, 2.0, 2.0]
    assert result.renewable_curtailment == [0.0, 2.0, 0.0]
    assert result.grid_import == [2.0, 0.0, 0.0]
    assert result.max_grid_import == 2.0
    assert result.energy_cost == 2.0
    assert result.demand_cost == 20.0
    assert result.sell_revenue == 0.8
    assert result.curtailment_cost == 0.0
    assert result.total_cost == 21.2
    assert result.constraint_violations == {}

    print("可再生能源无储能共享内核自检通过")
    print(f"  renewable_to_load     = {result.renewable_to_load}")
    print(f"  renewable_to_grid     = {result.renewable_to_grid}")
    print(f"  renewable_curtailment = {result.renewable_curtailment}")
    print(f"  grid_import           = {result.grid_import}")
    print(f"  total_cost            = {result.total_cost}")

    # --- 现货逐时段售电价场景：验证 sell_price_list per-step 生效 ---
    spot = UserSideRenewableDispatchInput(
        timestamps=[0, 1],
        load_forecast=[0.0, 0.0],
        renewable_forecast=[2.0, 2.0],
        buy_price=[1.0, 1.0],
        price_type=["spot", "spot"],
        export=UserSidePVExportParams(allow_export=True, sell_price_list=[0.5, 0.2]),
        demand_charge_rate=0.0,
        step_hours=1.0,
    )
    spot_result = run_user_side_renewable_dispatch(spot)
    assert spot_result.renewable_to_grid == [2.0, 2.0]
    assert spot_result.sell_revenue == 1.4  # 0.5*2 + 0.2*2，逐时段而非标量
    print("现货逐时段售电价场景自检通过")
