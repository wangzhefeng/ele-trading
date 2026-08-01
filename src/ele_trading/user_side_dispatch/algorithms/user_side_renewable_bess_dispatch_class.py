"""用户侧通用可再生能源 + BESS 调度内核。

本模块是 PV+BESS / Wind+BESS / Wind+PV+BESS 场景的 canonical shared
kernel。它用 MILP 联合决定可再生能源分流、储能充放电、购电、上网、
弃电和 SOC，并通过 run_* 外壳保持包级函数入口稳定。

运行
`python -m ele_trading.user_side_dispatch.user_side_renewable_bess_dispatch_class`
可执行固定样例自检。
"""

from __future__ import annotations

from typing import Any

from pulp import (
    LpBinary,
    LpMinimize,
    LpProblem,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
    value,
)

from ele_trading.utils import check_pulp_status, clean_value, extract_timestamp_hours
from ..interfaces import (
    UserSideBESSParams,
    UserSideDispatchPolicy,
    UserSidePVExportParams,
    UserSideRenewableBESSDispatchInput,
    UserSideRenewableBESSDispatchResult,
)


class UserSideRenewableBESSDispatcher:
    """用户侧可再生能源 + BESS 联合调度的 MILP class 实现。

    在给定时段内，决策新能源（光伏/风电）、储能、电网与负荷之间的能量分流，
    最小化用户用电总成本（电度费 + 需量费 + 弃电罚 + 循环成本 − 上网收益）。
    """

    def __init__(self, dispatch_input: UserSideRenewableBESSDispatchInput) -> None:
        # 构造只保存输入，不校验、不建模；异常语义集中在 solve() 阶段。
        self.dispatch_input = dispatch_input

    def solve(self) -> UserSideRenewableBESSDispatchResult:
        self._validate_input()
        self._init_model()
        self._create_variables()
        self._add_balance_constraints()
        self._add_bess_constraints()
        self._add_export_constraints()
        self._add_policy_constraints()
        self._add_terminal_soc_constraint()
        self._set_objective()
        self._solve_model()
        values = self._extract_solution()
        return self._build_result(values)

    def _validate_input(self) -> None:
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
        if dispatch_input.cycle_cost_rate < 0:
            raise ValueError("cycle_cost_rate must be non-negative")
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

        bess = dispatch_input.bess
        if bess.capacity <= 0:
            raise ValueError("bess.capacity must be positive")
        if bess.soc_min < 0 or bess.soc_max > bess.capacity:
            raise ValueError("bess SOC bounds must be within bess capacity")
        if bess.soc_min > bess.soc_max:
            raise ValueError("bess.soc_min must be less than or equal to bess.soc_max")
        if not bess.soc_min <= dispatch_input.initial_soc <= bess.soc_max:
            raise ValueError("initial_soc must be within bess SOC bounds")
        if dispatch_input.terminal_soc_target is not None and not (
            bess.soc_min <= dispatch_input.terminal_soc_target <= bess.soc_max
        ):
            raise ValueError("terminal_soc_target must be within bess SOC bounds")
        if bess.p_ch_max < 0 or bess.p_dis_max < 0:
            raise ValueError("bess power limits must be non-negative")
        if bess.eta_ch <= 0 or bess.eta_dis <= 0:
            raise ValueError("bess efficiencies must be positive")
        self._validate_policy(dispatch_input.policy)

    def _validate_policy(self, policy: UserSideDispatchPolicy | None) -> None:
        if policy is None:
            return
        for name, hours in (
            ("charge_allowed_hours", policy.charge_allowed_hours),
            ("discharge_allowed_hours", policy.discharge_allowed_hours),
        ):
            if hours is None:
                continue
            if any(hour < 0 or hour > 23 for hour in hours):
                raise ValueError(f"policy.{name} must contain hours in [0, 23]")
        if policy.pv_to_bess_reward_rate < 0:
            raise ValueError("policy.pv_to_bess_reward_rate must be non-negative")
        if policy.pv_to_load_reward_rate < 0:
            raise ValueError("policy.pv_to_load_reward_rate must be non-negative")
        if policy.pv_export_penalty_rate < 0:
            raise ValueError("policy.pv_export_penalty_rate must be non-negative")

    def _init_model(self) -> None:
        self.bess = self.dispatch_input.bess
        self.T = range(len(self.dispatch_input.timestamps))
        self.model = LpProblem("user_side_renewable_bess_dispatch", LpMinimize)
        # 售电价统一为等长列表：sell_price_list 提供则用它，否则广播标量 sell_price（支持现货逐时段 + 标量回退）。
        export = self.dispatch_input.export
        if export.sell_price_list is not None:
            self._sell_price_per_step = list(export.sell_price_list)
        else:
            self._sell_price_per_step = [export.sell_price] * len(self.T)

    def _create_variables(self) -> None:
        bess = self.bess
        # 新能源四路分流：就地消纳 / 充储能 / 上网 / 弃电（均为功率，单位与 forecast 一致）。
        self.renewable_to_load = {
            t: LpVariable(f"renewable_to_load_{t}", lowBound=0) for t in self.T
        }
        self.renewable_to_bess = {
            t: LpVariable(f"renewable_to_bess_{t}", lowBound=0) for t in self.T
        }
        self.renewable_to_grid = {
            t: LpVariable(f"renewable_to_grid_{t}", lowBound=0) for t in self.T
        }
        self.renewable_curtailment = {
            t: LpVariable(f"renewable_curtailment_{t}", lowBound=0) for t in self.T
        }
        # 电网供电两路去向：直供负荷 / 充储能。
        self.grid_to_load = {
            t: LpVariable(f"grid_to_load_{t}", lowBound=0) for t in self.T
        }
        self.grid_to_bess = {
            t: LpVariable(f"grid_to_bess_{t}", lowBound=0) for t in self.T
        }
        # 储能：充/放电功率（受功率上限约束）、荷电状态（受 SOC 上下界约束）。
        self.charge = {
            t: LpVariable(f"charge_{t}", lowBound=0, upBound=bess.p_ch_max)
            for t in self.T
        }
        self.discharge = {
            t: LpVariable(f"discharge_{t}", lowBound=0, upBound=bess.p_dis_max)
            for t in self.T
        }
        self.soc = {
            t: LpVariable(f"soc_{t}", lowBound=bess.soc_min, upBound=bess.soc_max)
            for t in self.T
        }
        # 电网总取电 = grid_to_load + grid_to_bess；max_grid_import 为全周期峰值需量（需量电费基准）。
        self.grid_import = {
            t: LpVariable(f"grid_import_{t}", lowBound=0) for t in self.T
        }
        # 充/放电状态二元变量：与功率上限联动，禁止同时充放电。
        self.is_charging = {
            t: LpVariable(f"is_charging_{t}", cat=LpBinary) for t in self.T
        }
        self.is_discharging = {
            t: LpVariable(f"is_discharging_{t}", cat=LpBinary) for t in self.T
        }
        self.max_grid_import = LpVariable("max_grid_import", lowBound=0)

    def _add_balance_constraints(self) -> None:
        dispatch_input = self.dispatch_input
        for t in self.T:
            # 新能源四路分流守恒：本地消纳 + 充储能 + 上网 + 弃电 = 新能源预测出力。
            self.model += (
                self.renewable_to_load[t]
                + self.renewable_to_bess[t]
                + self.renewable_to_grid[t]
                + self.renewable_curtailment[t]
                == dispatch_input.renewable_forecast[t]
            )
            # 负荷平衡：负荷必须被完全满足（新能源直供 + 储能放电 + 电网供电）。
            self.model += (
                self.renewable_to_load[t]
                + self.discharge[t]
                + self.grid_to_load[t]
                == dispatch_input.load_forecast[t]
            )
            # 充电来源 = 新能源充储能 + 电网充储能。
            self.model += self.charge[t] == self.renewable_to_bess[t] + self.grid_to_bess[t]
            # 电网总取电 = 电网供负荷 + 电网充储能。
            self.model += self.grid_import[t] == self.grid_to_load[t] + self.grid_to_bess[t]
            # max_grid_import 追踪全周期电网峰值取电，作为需量电费（基本电费）计费基准。
            self.model += self.max_grid_import >= self.grid_import[t]

    def _add_bess_constraints(self) -> None:
        dispatch_input = self.dispatch_input
        bess = self.bess
        for t in self.T:
            # 禁止同时充放电，否则会因往返损耗产生无意义的循环套利。
            self.model += self.is_charging[t] + self.is_discharging[t] <= 1
            # 功率上限与充/放电状态联动：仅在该状态为 1 时才允许对应方向的功率。
            self.model += self.charge[t] <= bess.p_ch_max * self.is_charging[t]
            self.model += self.discharge[t] <= bess.p_dis_max * self.is_discharging[t]
            # 储能放电不得超过负荷，即禁止储能向电网卖电（本模型上网仅走 renewable_to_grid）。
            self.model += self.discharge[t] <= dispatch_input.load_forecast[t]

            previous_soc = dispatch_input.initial_soc if t == 0 else self.soc[t - 1]
            # SOC 递推：充电入库乘 η_ch（充电损耗）、放电出库除以 η_dis（放电损耗），
            # step_hours 把功率换算为电量，构成标准往返效率建模。
            self.model += (
                self.soc[t]
                == previous_soc
                + bess.eta_ch * self.charge[t] * dispatch_input.step_hours
                - self.discharge[t] * dispatch_input.step_hours / bess.eta_dis
            )

    def _add_export_constraints(self) -> None:
        export = self.dispatch_input.export
        for t in self.T:
            # 禁止上网时，新能源余电只能就地消纳或弃电。
            if not export.allow_export:
                self.model += self.renewable_to_grid[t] == 0
            # 上网功率上限（如并网容量、政策限发）。
            if export.export_limit is not None:
                self.model += self.renewable_to_grid[t] <= export.export_limit

    def _add_policy_constraints(self) -> None:
        policy = self.dispatch_input.policy
        if policy is None:
            return

        # 按时间戳小时数筛选充/放电允许时段（硬约束），典型用于"谷时段充电、峰时段放电"。
        timestamp_hours = extract_timestamp_hours(self.dispatch_input.timestamps)
        for t in self.T:
            hour = timestamp_hours[t]
            # 非允许充电时段：新能源与电网均不得充储能。
            if policy.charge_allowed_hours is not None and hour not in policy.charge_allowed_hours:
                self.model += self.renewable_to_bess[t] == 0
                self.model += self.grid_to_bess[t] == 0
            # 非允许放电时段：储能不得放电。
            if policy.discharge_allowed_hours is not None and hour not in policy.discharge_allowed_hours:
                self.model += self.discharge[t] == 0

    def _add_terminal_soc_constraint(self) -> None:
        terminal_soc_target = self.dispatch_input.terminal_soc_target
        if terminal_soc_target is not None:
            # 锁定末时刻 SOC，保证次日初始电量，防止优化把电池"榨干"。
            self.model += self.soc[len(self.dispatch_input.timestamps) - 1] == terminal_soc_target

    def _set_objective(self) -> None:
        dispatch_input = self.dispatch_input
        # 最小化用户用电成本 = 真实成本项 − 上网收益 + policy 调度偏置项（见 _policy_objective）。
        # 电度电费：各时段电网取电量 × 购电价。
        energy_cost_expr = lpSum(
            dispatch_input.buy_price[t]
            * self.grid_import[t]
            * dispatch_input.step_hours
            for t in self.T
        )
        # 需量电费（基本电费）：按全周期电网峰值取电量计费。
        demand_cost_expr = dispatch_input.demand_charge_rate * self.max_grid_import
        # 上网收益：新能源上网电量 × 逐时段上网电价（成本项中作减项）。
        sell_revenue_expr = lpSum(
            self._sell_price_per_step[t]
            * self.renewable_to_grid[t]
            * dispatch_input.step_hours
            for t in self.T
        )
        # 弃电惩罚：抑制无谓弃风弃光。
        curtailment_cost_expr = lpSum(
            dispatch_input.export.curtailment_cost_rate
            * self.renewable_curtailment[t]
            * dispatch_input.step_hours
            for t in self.T
        )
        # 循环成本：按充放电总量计，抑制无谓充放、保护电池寿命。
        cycle_cost_expr = lpSum(
            dispatch_input.cycle_cost_rate
            * (self.charge[t] + self.discharge[t])
            * dispatch_input.step_hours
            for t in self.T
        )
        self.model += (
            energy_cost_expr
            + demand_cost_expr
            + curtailment_cost_expr
            + cycle_cost_expr
            - sell_revenue_expr
            + self._policy_objective()
        )

    def _policy_objective(self):
        policy = self.dispatch_input.policy
        if policy is None:
            return 0.0

        dispatch_input = self.dispatch_input
        # policy 的 reward/penalty 仅作"软偏置/平局裁决"：在真实成本相等的次优解之间，
        # 优先选光伏充储能、光伏供负荷、少上网。注意：它只影响求解偏好，_build_result 返回的
        # total_cost 不计入这些项；reward/penalty 量级不宜过大，否则会盖过真实经济性、破坏最优。
        return (
            -policy.pv_to_bess_reward_rate
            * lpSum(self.renewable_to_bess[t] * dispatch_input.step_hours for t in self.T)
            - policy.pv_to_load_reward_rate
            * lpSum(self.renewable_to_load[t] * dispatch_input.step_hours for t in self.T)
            + policy.pv_export_penalty_rate
            * lpSum(self.renewable_to_grid[t] * dispatch_input.step_hours for t in self.T)
        )

    def _solve_model(self) -> None:
        self.model.solve(PULP_CBC_CMD(msg=False))
        check_pulp_status(self.model, "user-side renewable bess dispatch")

    def _extract_solution(self) -> dict[str, Any]:
        values = {
            "renewable_to_load": [
                clean_value(value(self.renewable_to_load[t])) for t in self.T
            ],
            "renewable_to_bess": [
                clean_value(value(self.renewable_to_bess[t])) for t in self.T
            ],
            "renewable_to_grid": [
                clean_value(value(self.renewable_to_grid[t])) for t in self.T
            ],
            "renewable_curtailment": [
                clean_value(value(self.renewable_curtailment[t])) for t in self.T
            ],
            "grid_to_load": [clean_value(value(self.grid_to_load[t])) for t in self.T],
            "grid_to_bess": [clean_value(value(self.grid_to_bess[t])) for t in self.T],
            "charge": [clean_value(value(self.charge[t])) for t in self.T],
            "discharge": [clean_value(value(self.discharge[t])) for t in self.T],
            "soc": [clean_value(value(self.soc[t])) for t in self.T],
            "grid_import": [clean_value(value(self.grid_import[t])) for t in self.T],
            "max_grid_import": clean_value(value(self.max_grid_import)),
        }

        # 用解出的实际值重算各成本项（而非复用目标表达式），天然避免策略偏置项被误计入。
        dispatch_input = self.dispatch_input
        values["energy_cost"] = sum(
            dispatch_input.buy_price[t] * values["grid_import"][t] * dispatch_input.step_hours
            for t in self.T
        )
        values["demand_cost"] = dispatch_input.demand_charge_rate * values["max_grid_import"]
        values["sell_revenue"] = sum(
            self._sell_price_per_step[t]
            * values["renewable_to_grid"][t]
            * dispatch_input.step_hours
            for t in self.T
        )
        values["curtailment_cost"] = sum(
            dispatch_input.export.curtailment_cost_rate
            * values["renewable_curtailment"][t]
            * dispatch_input.step_hours
            for t in self.T
        )
        values["cycle_cost"] = sum(
            dispatch_input.cycle_cost_rate
            * (values["charge"][t] + values["discharge"][t])
            * dispatch_input.step_hours
            for t in self.T
        )
        return values

    def _build_result(self, values: dict[str, Any]) -> UserSideRenewableBESSDispatchResult:
        return UserSideRenewableBESSDispatchResult(
            renewable_to_load=values["renewable_to_load"],
            renewable_to_bess=values["renewable_to_bess"],
            renewable_to_grid=values["renewable_to_grid"],
            renewable_curtailment=values["renewable_curtailment"],
            grid_to_load=values["grid_to_load"],
            grid_to_bess=values["grid_to_bess"],
            charge_power=values["charge"],
            discharge_power=values["discharge"],
            net_bess_power=[
                clean_value(values["charge"][t] - values["discharge"][t]) for t in self.T
            ],
            soc=values["soc"],
            grid_import=values["grid_import"],
            max_grid_import=values["max_grid_import"],
            energy_cost=values["energy_cost"],
            demand_cost=values["demand_cost"],
            sell_revenue=values["sell_revenue"],
            curtailment_cost=values["curtailment_cost"],
            cycle_cost=values["cycle_cost"],
            # total_cost 仅含真实经济性项，不含 policy 的 reward/penalty（见 _policy_objective）。
            total_cost=(
                values["energy_cost"]
                + values["demand_cost"]
                + values["curtailment_cost"]
                + values["cycle_cost"]
                - values["sell_revenue"]
            ),
            constraint_violations=self._constraint_violations(values),
        )

    def _constraint_violations(self, values: dict[str, Any]) -> dict[str, float]:
        # 对求解结果做防御性健康检查：复核各约束的最大违背量，仅返回超过容差的项，
        # 便于上层判断解是否可信（浮点松弛残差、SOC 越界等）。
        dispatch_input = self.dispatch_input
        bess = self.bess
        tolerance = 1e-6
        violations = {
            "renewable_balance": max(
                abs(
                    values["renewable_to_load"][t]
                    + values["renewable_to_bess"][t]
                    + values["renewable_to_grid"][t]
                    + values["renewable_curtailment"][t]
                    - dispatch_input.renewable_forecast[t]
                )
                for t in self.T
            ),
            "load_balance": max(
                abs(
                    values["renewable_to_load"][t]
                    + values["discharge"][t]
                    + values["grid_to_load"][t]
                    - dispatch_input.load_forecast[t]
                )
                for t in self.T
            ),
            "grid_import": max(
                abs(
                    values["grid_import"][t]
                    - values["grid_to_load"][t]
                    - values["grid_to_bess"][t]
                )
                for t in self.T
            ),
            "charge_balance": max(
                abs(
                    values["charge"][t]
                    - values["renewable_to_bess"][t]
                    - values["grid_to_bess"][t]
                )
                for t in self.T
            ),
            "soc_min": max(bess.soc_min - min(values["soc"]), 0.0),
            "soc_max": max(max(values["soc"]) - bess.soc_max, 0.0),
            "charge_max": max(max(values["charge"]) - bess.p_ch_max, 0.0),
            "discharge_max": max(max(values["discharge"]) - bess.p_dis_max, 0.0),
            "grid_import_min": max(-min(values["grid_import"]), 0.0),
            "max_grid_import": max(
                max(values["grid_import"]) - values["max_grid_import"], 0.0
            ),
        }
        return {
            name: amount for name, amount in violations.items() if amount > tolerance
        }


def run_user_side_renewable_bess_dispatch(
    dispatch_input: UserSideRenewableBESSDispatchInput,
) -> UserSideRenewableBESSDispatchResult:
    """用户侧可再生能源 + BESS 联合调度。class 版本的函数式兼容入口。"""
    return UserSideRenewableBESSDispatcher(dispatch_input).solve()


if __name__ == "__main__":
    sample = UserSideRenewableBESSDispatchInput(
        timestamps=[0, 1, 2, 3],
        load_forecast=[1.0, 5.0, 5.0, 2.0],
        renewable_forecast=[6.0, 0.0, 0.0, 2.0],
        buy_price=[0.1, 1.0, 1.0, 0.2],
        price_type=["valley", "peak", "peak", "flat"],
        export=UserSidePVExportParams(allow_export=False, sell_price=0.0),
        demand_charge_rate=0.0,
        step_hours=1.0,
        bess=UserSideBESSParams(
            capacity=10.0,
            soc_min=0.0,
            soc_max=10.0,
            p_ch_max=5.0,
            p_dis_max=5.0,
            eta_ch=1.0,
            eta_dis=1.0,
        ),
        initial_soc=0.0,
        terminal_soc_target=0.0,
    )

    result = run_user_side_renewable_bess_dispatch(sample)

    assert result.renewable_to_load == [1.0, 0.0, 0.0, 2.0]
    assert result.renewable_to_bess == [5.0, 0.0, 0.0, 0.0]
    assert result.renewable_to_grid == [0.0, 0.0, 0.0, 0.0]
    assert result.renewable_curtailment == [0.0, 0.0, 0.0, 0.0]
    assert result.grid_to_load == [0.0, 0.0, 5.0, 0.0]
    assert result.grid_to_bess == [0.0, 0.0, 0.0, 0.0]
    assert result.charge_power == [5.0, 0.0, 0.0, 0.0]
    assert result.discharge_power == [0.0, 5.0, 0.0, 0.0]
    assert result.net_bess_power == [5.0, -5.0, 0.0, 0.0]
    assert result.soc == [5.0, 0.0, 0.0, 0.0]
    assert result.grid_import == [0.0, 0.0, 5.0, 0.0]
    assert result.max_grid_import == 5.0
    assert result.energy_cost == 5.0
    assert result.demand_cost == 0.0
    assert result.sell_revenue == 0.0
    assert result.curtailment_cost == 0.0
    assert result.cycle_cost == 0.0
    assert result.total_cost == 5.0
    assert result.constraint_violations == {}

    print("可再生能源 + BESS 共享内核自检通过")
    print(f"  renewable_to_bess = {result.renewable_to_bess}")
    print(f"  discharge_power   = {result.discharge_power}")
    print(f"  soc               = {result.soc}")
    print(f"  grid_import       = {result.grid_import}")
    print(f"  total_cost        = {result.total_cost}")

    # --- 现货 + 负电价场景：验证 sell_price_list 逐时段生效、负电价行为正确 ---
    # 场景 A：p_ch_max=0 使盈余只能上网，sell_price_list 逐时段不同 → 精确验证 per-step 售电收入。
    spot_a = UserSideRenewableBESSDispatchInput(
        timestamps=[0, 1],
        load_forecast=[0.0, 0.0],
        renewable_forecast=[2.0, 2.0],
        buy_price=[1.0, 1.0],
        price_type=["spot", "spot"],
        export=UserSidePVExportParams(allow_export=True, sell_price_list=[0.5, 0.2]),
        demand_charge_rate=0.0,
        step_hours=1.0,
        bess=UserSideBESSParams(
            capacity=10.0, soc_min=0.0, soc_max=10.0,
            p_ch_max=0.0, p_dis_max=0.0, eta_ch=1.0, eta_dis=1.0,
        ),
        initial_soc=0.0,
        terminal_soc_target=0.0,
    )
    res_a = run_user_side_renewable_bess_dispatch(spot_a)
    assert res_a.renewable_to_grid == [2.0, 2.0]
    assert res_a.sell_revenue == 1.4  # 0.5*2 + 0.2*2，证明按逐时段而非标量计

    # 场景 B：负购电价（t=0）+ 负售电价（t=1），验证负电价不上网且不发散。
    spot_b = UserSideRenewableBESSDispatchInput(
        timestamps=[0, 1],
        load_forecast=[1.0, 1.0],
        renewable_forecast=[2.0, 2.0],
        buy_price=[-0.1, 1.0],
        price_type=["spot", "spot"],
        export=UserSidePVExportParams(allow_export=True, sell_price_list=[0.5, -0.1]),
        demand_charge_rate=0.0,
        step_hours=1.0,
        bess=UserSideBESSParams(
            capacity=10.0, soc_min=0.0, soc_max=10.0,
            p_ch_max=5.0, p_dis_max=5.0, eta_ch=1.0, eta_dis=1.0,
        ),
        initial_soc=0.0,
        terminal_soc_target=0.0,
    )
    res_b = run_user_side_renewable_bess_dispatch(spot_b)
    # 负售电价时段（t=1）有盈余却不上网（理性弃电/充储能，因上网会倒贴）。
    assert res_b.renewable_to_grid[1] == 0.0
    # 售电收入按逐时段 sell_price_list 精确计算（验证 per-step 而非标量 sell_price=0）。
    expected_sell = sum(
        spot_b.export.sell_price_list[t] * res_b.renewable_to_grid[t] for t in range(2)
    )
    assert abs(res_b.sell_revenue - expected_sell) < 1e-9
    # 负购电价不发散：SOC 在界、无约束违背（求解成功即说明 well-posed）。
    assert all(0.0 <= s <= 10.0 for s in res_b.soc)
    assert res_b.constraint_violations == {}

    print("现货 + 负电价场景自检通过")
