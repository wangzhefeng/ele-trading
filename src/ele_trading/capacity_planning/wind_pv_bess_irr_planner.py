"""IRR 目标型 Wind+PV+BESS 容量规划。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ele_trading.evaluation.metrics import compute_irr
from ele_trading.utils.data_alignment import as_time_series, align_to_time
from ele_trading.utils.num_utils import inclusive_float_range
from ele_trading.utils.time_index import infer_dt_hours
from ele_trading.utils.log_util import logger
from .dispatch_algo import dispatch_annual


@dataclass(slots=True)
class WindPVBESSIRRPlanConfig:
    """
    IRR 目标型 Wind+PV+BESS 容量规划配置。
    """
    target_owner_price_yuan_per_kwh: float = 0.32  # 业主综合电价
    grid_buy_price_yuan_per_kwh: float = 0.36  # 电网购电电价
    green_price_adder_yuan_per_kwh: float = 0.074  # 绿电电价=PPA价格+0.074元
    target_irr: float = 0.08  # 测算IRR=8%的最佳风光储配比
    irr_tolerance: float = 0.002  # IRR 容差
    irr_constraint_mode: str = "range"  # IRR 约束模式：range 或 minimum

    wind_min_mw: float = 0.0  # 风电搜索下限 MW
    pv_min_mw: float = 0.0  # 光伏搜索下限 MW
    bess_min_mwh: float = 0.0  # BESS 搜索下限 MWh；设为正数时强制配置储能
    wind_max_mw: float = 280.0  # 绿电规模上限：风电：280MW
    pv_max_mw: float = 140.0  # 绿电规模上限：光伏：140MW
    bess_max_mwh: float = 2000.0  # 储能规模上限：1000MWh
    wind_step_mw: float = 10.0  # 风光储容量步进：风电：10MW
    pv_step_mw: float = 10.0  # 风光储容量步进：光伏：10MW
    bess_step_mwh: float = 10.0  # 储能容量步进：20MWh

    self_use_ratio_min: float = 0.60  # 绿电消纳最低要求：至少60%自用
    load_cover_ratio_min: float = 0.35  # 绿电消纳最低要求：占业主用电量最低35%

    wind_capex_yuan_per_kw: float = 5000.0  # 风电单位投资，元/kW
    pv_capex_yuan_per_kwp: float = 3500.0  # 光伏单位投资，元/kWp
    bess_capex_yuan_per_kwh: float = 800.0  # BESS 单位投资，元/kWh
    annual_opex_ratio: float = 0.02  # 年运维费用占总投资比例
    life_years: int = 15  # IRR 现金流测算年限

    eta_roundtrip: float = 0.92  # BESS 往返效率
    c_rate: float = 0.5  # BESS 功率倍率，MW/MWh
    soc_init_frac: float = 0.1  # 初始 SOC
    soc_min_frac: float = 0.1  # 最小 SOC
    soc_max_frac: float = 1.0  # 最大 SOC
    switch_gap_hours: float = 1.0  # 充放电状态切换的最小间隔小时数
    use_numba: bool = True  # 是否使用 numba 加速


@dataclass(slots=True)
class WindPVBESSIRRResult:
    """
    IRR 目标型 Wind+PV+BESS 容量规划结果。
    """
    status: str  # "ok" or "no_solution"
    wind_mw: float = 0.0  # 风电装机 MW
    pv_mw: float = 0.0  # 光伏装机 MW
    bess_mwh: float = 0.0  # BESS 容量 MWh
    green_price: float = 0.0  # 反推的绿电结算价格，元/kWh
    ppa_price: float = 0.0  # 扣除绿电附加价后的 PPA 价格，元/kWh
    owner_avg_price: float = 0.0  # 业主综合电价，理论上应回到 target_owner_price_yuan_per_kwh
    irr: float | None = None  # 项目 IRR
    total_capex_yuan: float = 0.0  # 总投资
    annual_revenue_yuan: float = 0.0  # PPA 口径年收入
    annual_opex_yuan: float = 0.0  # 年运维
    annual_cashflow_yuan: float = 0.0  # 年净现金流
    annual_green_used_kwh: float = 0.0  # 年度绿电消纳量
    annual_grid_buy_kwh: float = 0.0  # 年度电网购电量
    self_use_ratio: float = 0.0  # 新能源自用率
    load_cover_ratio: float = 0.0  # 负荷覆盖率
    curtail_kwh: float = 0.0  # 弃电量
    diagnostics: pd.DataFrame | None = None  # 候选诊断表；成功时是所有 ok 候选，失败时是 PPA/IRR 不满足的候选
    diagnostic_summary: dict[str, Any] | None = None  # 搜索失败分布、最优诊断候选和 IRR 达标缺口
    best_solution: dict[str, Any] | None = None  # 与对象字段一致的最优解摘要
    message: str | None = None  # 失败原因说明


def _prepare_arrays(df_load: pd.DataFrame, wind_unit_kw: pd.Series | pd.DataFrame, pv_unit_kw: pd.Series | pd.DataFrame, load_col: str, time_col: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """
    数据预处理：将负荷、风电、光伏数据对齐到统一时间轴并转为连续数组。

    Args:
        df_load (pd.DataFrame): 负荷数据 DataFrame
        wind_unit_kw (pd.Series | pd.DataFrame): 风电单位出力曲线（每 MW 对应的 kW 出力）
        pv_unit_kw (pd.Series | pd.DataFrame): 光伏单位出力曲线（每 kWp 对应的 kW 出力）
        load_col (str): 负荷列名
        time_col (str): 时间列名

    Returns:
        tuple[np.ndarray, np.ndarray, np.ndarray, float]: (负荷数组, 风电单位出力数组, 光伏单位出力数组, 时间步长小时数)
    """
    # 取出 Time 和 P_kw
    df = df_load[[time_col, load_col]].copy()
    # 时间转 datetime 并排序
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    # 负荷转成连续 float64 数组，缺失值按 0.0
    load_kw_arr = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    load_kw_arr = np.ascontiguousarray(load_kw_arr, dtype=np.float64)
    # 将风、光单位出力曲线对齐到负荷时间轴
    wind_s = as_time_series(
        wind_unit_kw,
        time_col=time_col,
        value_cols=("wind_unit_kw", "WindPower_MW", "wind_mw", "wind_kw", "WindPower_kW", "WindPower", "value"),
        scale=1.0,
    )
    wind_unit_arr = align_to_time(df[time_col], wind_s)
    # 将光伏单位出力曲线对齐到负荷时间轴
    pv_s = as_time_series(
        pv_unit_kw,
        time_col=time_col,
        value_cols=("pv_unit_kw", "pv_kw", "u", "value"),
        scale=1.0,
    )
    pv_unit_arr = align_to_time(df[time_col], pv_s)
    # 推断时间步长 dt_hours
    dt_hours = infer_dt_hours(df[time_col])
    
    return load_kw_arr, wind_unit_arr, pv_unit_arr, dt_hours


def _evaluate_candidate(wind_mw: float, pv_mw: float, bess_mwh: float, st: dict[str, float], cfg: WindPVBESSIRRPlanConfig) -> dict[str, Any]:
    """
    评估单个风光储容量组合的可行性与经济性。

    依次检查：物理约束（发电/消纳/负荷非零）、绿电消纳率与负荷覆盖率、
    绿电/PPA 价格为正、IRR 是否在目标容差内。
    """
    # 绿电发电量
    gen = st["ren_gen_kwh"]
    # 绿电消纳量
    used = st["ren_used_kwh"]
    # 用电负荷
    load = st["load_kwh"]
    # ------------------------------
    # 1.物理约束：先排除无发电、无消纳、无负荷(不会进入诊断表(diagnostics)
    # ------------------------------
    if gen <= 1e-9 or used <= 1e-9 or load <= 1e-9:
        return {"reason": "no_generation", "irr_gap": np.inf}
    # ------------------------------
    # 2.物理约束：绿电消纳率、绿电覆盖负荷率
    # ------------------------------
    # 计算新能源自用率
    self_use = used / gen
    # 计算负荷覆盖率
    cover = used / load
    # 不满足绿电消纳率、绿电覆盖负荷率的组合直接标记为 physical_infeasible，且不会进入诊断表(diagnostics)
    if self_use < cfg.self_use_ratio_min or cover < cfg.load_cover_ratio_min:
        return {"reason": "physical_infeasible", "irr_gap": np.inf}
    # ------------------------------
    # 3.绿电价格/PPA 价格约束
    # ------------------------------
    # 电网购电量
    grid_buy_kwh = max(load - used, 0.0)
    # 反推绿电价格
    green_price = (
        cfg.target_owner_price_yuan_per_kwh * load
        - cfg.grid_buy_price_yuan_per_kwh * grid_buy_kwh
    ) / used
    # PPA 价格再扣除绿电附加价
    ppa_price = green_price - cfg.green_price_adder_yuan_per_kwh
    # 业主综合电价 = (绿电用电量×绿电价 + 电网购电量×电网电价) / 总用电量
    owner_avg_price = (
        green_price * used + cfg.grid_buy_price_yuan_per_kwh * grid_buy_kwh
    ) / load

    row = {
        "wind_mw": wind_mw,
        "pv_mw": pv_mw,
        "bess_mwh": bess_mwh,
        "green_price": float(green_price),
        "ppa_price": float(ppa_price),
        "owner_avg_price": float(owner_avg_price),
        "annual_green_used_kwh": float(used),
        "annual_grid_buy_kwh": float(grid_buy_kwh),
        "self_use_ratio": float(self_use),
        "load_cover_ratio": float(cover),
        "curtail_kwh": float(st.get("curtail_kwh", 0.0)),
    }
    # 候选组合被标记为 non_positive_ppa，不会作为可行解，但会进入 diagnostics
    if green_price <= 0.0 or ppa_price <= 0.0:
        return {**row, "reason": "non_positive_ppa", "irr": np.nan, "irr_gap": np.inf}
    # ------------------------------
    # 5.经济模型和 IRR
    # ------------------------------
    # CAPEX
    total_capex = (
        wind_mw * 1000.0 * cfg.wind_capex_yuan_per_kw
        + pv_mw * 1000.0 * cfg.pv_capex_yuan_per_kwp
        + bess_mwh * 1000.0 * cfg.bess_capex_yuan_per_kwh
    )
    # PPA 口径年收入
    annual_revenue = ppa_price * used
    # 年运维
    annual_opex = total_capex * cfg.annual_opex_ratio
    # 年现金流
    annual_cf = annual_revenue - annual_opex
    # 输入现金流序列，计算 IRR
    irr = compute_irr([-total_capex] + [annual_cf] * int(cfg.life_years))
    irr_gap = abs(irr - cfg.target_irr)

    row.update({
        "total_capex_yuan": float(total_capex),
        "annual_revenue_yuan": float(annual_revenue),
        "annual_opex_yuan": float(annual_opex),
        "annual_cashflow_yuan": float(annual_cf),
        "irr": float(irr),
        "irr_gap": float(irr_gap),
    })
    if cfg.irr_constraint_mode not in {"range", "minimum"}:
        raise ValueError("irr_constraint_mode must be 'range' or 'minimum'")

    if cfg.irr_constraint_mode == "minimum":
        irr_out_of_bounds = irr < cfg.target_irr
    else:
        irr_out_of_bounds = irr < cfg.target_irr or irr > cfg.target_irr + cfg.irr_tolerance

    # 候选组合不满足 IRR 约束时进入 diagnostics。
    if irr_out_of_bounds:
        return {**row, "reason": "irr_out_of_tolerance"}
    # ------------------------------
    # 物理约束满足、PPA 为正、IRR 达到目标且在上侧容差内
    # ------------------------------
    return {**row, "reason": "ok"}


def _capacity_candidates(min_value: float, max_value: float, step: float) -> list[float]:
    """生成容量候选，默认将 0 容量纳入合法扫描点。"""
    if step <= 0:
        raise ValueError("capacity search step must be positive")
    if min_value < 0 or max_value < 0:
        raise ValueError("capacity search bounds must be non-negative")
    if min_value > max_value:
        raise ValueError("capacity search min must be <= max")
    if max_value <= 1e-9:
        return [0.0]
    return [float(x) for x in inclusive_float_range(float(min_value), float(max_value), float(step))]


def _required_level_cashflow(total_capex_yuan: float, target_irr: float, life_years: int) -> float:
    """给定初始投资和目标 IRR，反推等额年度净现金流。"""
    if total_capex_yuan <= 0 or life_years <= 0:
        return 0.0
    if abs(target_irr) <= 1e-12:
        return total_capex_yuan / life_years
    factor = target_irr / (1.0 - (1.0 + target_irr) ** (-life_years))
    return total_capex_yuan * factor


def _summary_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = (
        "wind_mw",
        "pv_mw",
        "bess_mwh",
        "irr",
        "irr_gap",
        "green_price",
        "ppa_price",
        "owner_avg_price",
        "annual_green_used_kwh",
        "annual_grid_buy_kwh",
        "self_use_ratio",
        "load_cover_ratio",
        "curtail_kwh",
        "total_capex_yuan",
        "annual_revenue_yuan",
        "annual_opex_yuan",
        "annual_cashflow_yuan",
        "reason",
    )
    cleaned: dict[str, Any] = {}
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if pd.isna(value):
            cleaned[key] = None
        elif isinstance(value, (np.floating, np.integer)):
            cleaned[key] = float(value)
        else:
            cleaned[key] = value
    return cleaned


def _target_gap_metrics(row: dict[str, Any] | None, cfg: WindPVBESSIRRPlanConfig) -> dict[str, float] | None:
    if row is None:
        return None

    total_capex = float(row.get("total_capex_yuan", 0.0) or 0.0)
    annual_cf = float(row.get("annual_cashflow_yuan", 0.0) or 0.0)
    annual_opex = float(row.get("annual_opex_yuan", 0.0) or 0.0)
    used = float(row.get("annual_green_used_kwh", 0.0) or 0.0)
    grid_buy = float(row.get("annual_grid_buy_kwh", 0.0) or 0.0)
    load = used + grid_buy

    required_cf = _required_level_cashflow(total_capex, cfg.target_irr, int(cfg.life_years))
    required_green_price = (required_cf + annual_opex) / used if used > 1e-9 else np.nan
    required_ppa_price = required_green_price - cfg.green_price_adder_yuan_per_kwh if used > 1e-9 else np.nan
    required_owner_avg_price = (
        (required_green_price * used + cfg.grid_buy_price_yuan_per_kwh * grid_buy) / load
        if used > 1e-9 and load > 1e-9
        else np.nan
    )
    max_capex_for_target = (
        annual_cf / _required_level_cashflow(1.0, cfg.target_irr, int(cfg.life_years))
        if annual_cf > 0
        else 0.0
    )
    capex_reduction = max(total_capex - max_capex_for_target, 0.0)

    return {
        "target_irr": float(cfg.target_irr),
        "required_annual_cashflow_yuan": float(required_cf),
        "actual_annual_cashflow_yuan": float(annual_cf),
        "annual_cashflow_gap_yuan": float(required_cf - annual_cf),
        "required_green_price_yuan_per_kwh": float(required_green_price),
        "required_ppa_price_yuan_per_kwh": float(required_ppa_price),
        "required_owner_avg_price_yuan_per_kwh": float(required_owner_avg_price),
        "owner_avg_price_delta_yuan_per_kwh": float(required_owner_avg_price - cfg.target_owner_price_yuan_per_kwh),
        "max_capex_for_target_irr_yuan": float(max_capex_for_target),
        "capex_reduction_needed_yuan": float(capex_reduction),
        "capex_reduction_needed_ratio": float(capex_reduction / total_capex) if total_capex > 1e-9 else 0.0,
    }


def _build_diagnostic_summary(total_combinations: int, reason_counts: dict[str, int], evaluated_rows: list[dict[str, Any]], cfg: WindPVBESSIRRPlanConfig) -> dict[str, Any]:
    finite_irr_rows = [
        row for row in evaluated_rows
        if row.get("irr") is not None and np.isfinite(float(row.get("irr", np.nan)))
    ]
    max_irr_row = max(finite_irr_rows, key=lambda row: row["irr"], default=None)
    nearest_irr_row = min(finite_irr_rows, key=lambda row: row["irr_gap"], default=None)
    return {
        "total_combinations": int(total_combinations),
        "reason_counts": {key: int(value) for key, value in reason_counts.items()},
        "max_irr_candidate": _summary_row(max_irr_row),
        "nearest_irr_candidate": _summary_row(nearest_irr_row),
        "target_gap_metrics": _target_gap_metrics(max_irr_row, cfg),
    }


def _result_from_row(status: str, row: dict[str, Any], diagnostics: pd.DataFrame | None, diagnostic_summary: dict[str, Any] | None = None) -> WindPVBESSIRRResult:
    """
    从评估结果字典构造 WindPVBESSIRRResult 对象。

    Args:
        status (str): 结果状态，"ok" 或 "no_solution"
        row (dict[str, Any]): 候选组合的评估结果字典
        diagnostics (pd.DataFrame | None): 候选诊断表

    Returns:
        WindPVBESSIRRResult: 容量规划结果对象
    """
    return WindPVBESSIRRResult(
        status=status,
        wind_mw=float(row["wind_mw"]),
        pv_mw=float(row["pv_mw"]),
        bess_mwh=float(row["bess_mwh"]),
        green_price=float(row["green_price"]),
        ppa_price=float(row["ppa_price"]),
        owner_avg_price=float(row["owner_avg_price"]),
        irr=float(row["irr"]),
        total_capex_yuan=float(row["total_capex_yuan"]),
        annual_revenue_yuan=float(row["annual_revenue_yuan"]),
        annual_opex_yuan=float(row["annual_opex_yuan"]),
        annual_cashflow_yuan=float(row["annual_cashflow_yuan"]),
        annual_green_used_kwh=float(row["annual_green_used_kwh"]),
        annual_grid_buy_kwh=float(row["annual_grid_buy_kwh"]),
        self_use_ratio=float(row["self_use_ratio"]),
        load_cover_ratio=float(row["load_cover_ratio"]),
        curtail_kwh=float(row["curtail_kwh"]),
        diagnostics=diagnostics,
        diagnostic_summary=diagnostic_summary,
        best_solution=_summary_row(row),
    )


def plan_wind_pv_bess_for_target_irr(
    df_load: pd.DataFrame,
    wind_unit_kw: pd.Series | pd.DataFrame,
    pv_unit_kw: pd.Series | pd.DataFrame,
    *,
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: WindPVBESSIRRPlanConfig = WindPVBESSIRRPlanConfig(),
) -> WindPVBESSIRRResult:
    """
    扫描风光储容量组合，寻找满足 IRR 目标的最低投资方案。
    """
    # 数据预处理
    load_kw_arr, wind_unit_arr, pv_unit_arr, dt_hours = _prepare_arrays(
        df_load, wind_unit_kw, pv_unit_kw, load_col, time_col
    )
    logger.info(f"dt_hours: {dt_hours}")

    # 将充放切换间隔（小时）转换为时间步数
    switch_gap_steps = int(round(cfg.switch_gap_hours / dt_hours)) if cfg.switch_gap_hours > 0 else 0
    logger.info(f"switch_gap_steps: {switch_gap_steps}")

    # 风光储容量组合搜索算法
    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    evaluated_rows: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {
        "ok": 0,
        "no_generation": 0,
        "physical_infeasible": 0,
        "non_positive_ppa": 0,
        "irr_out_of_tolerance": 0,
    }
    total_combinations = 0
    
    wind_candidates = _capacity_candidates(cfg.wind_min_mw, cfg.wind_max_mw, cfg.wind_step_mw)
    pv_candidates = _capacity_candidates(cfg.pv_min_mw, cfg.pv_max_mw, cfg.pv_step_mw)
    bess_candidates = _capacity_candidates(cfg.bess_min_mwh, cfg.bess_max_mwh, cfg.bess_step_mwh)
    for wind_mw in wind_candidates:
        wind_kw_arr = wind_unit_arr * float(wind_mw)
        for pv_mw in pv_candidates:
            pv_kw_arr = pv_unit_arr * float(pv_mw) * 1000.0
            for bess_mwh in bess_candidates:
                total_combinations += 1
                # ------------------------------
                # 年度调度模型
                # ------------------------------
                st = dispatch_annual(
                    load_kw = load_kw_arr,
                    wind_kw = wind_kw_arr,
                    pv_kw = pv_kw_arr,
                    other_kw = np.zeros_like(load_kw_arr, dtype=np.float64),
                    batt_kwh = float(bess_mwh) * 1000.0,
                    dt_hours = dt_hours,
                    cfg = cfg,
                    switch_gap_steps = switch_gap_steps,
                )
                # ------------------------------
                # 对当前风光储组合进行物理约束、价格约束和 IRR 约束的综合评估
                # ------------------------------
                evaluated = _evaluate_candidate(
                    wind_mw = float(wind_mw), 
                    pv_mw = float(pv_mw), 
                    bess_mwh = float(bess_mwh), 
                    st = st, 
                    cfg = cfg,
                )
                logger.debug("evaluated=%s", evaluated)
                # ------------------------------
                # 结果解析
                # ------------------------------
                reason = evaluated.pop("reason")
                # 统计结果
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                # 物理约束满足、PPA 为正、IRR 在目标容差内
                if reason == "ok":
                    row = {**evaluated, "reason": reason}
                    candidates.append(row)
                    evaluated_rows.append(row)
                # 物理约束不满足, 不会作为可行解，但会进入 diagnostics
                elif reason in {"non_positive_ppa", "irr_out_of_tolerance"}:
                    row = {**evaluated, "reason": reason}
                    diagnostics.append(row)
                    evaluated_rows.append(row)
    diagnostic_summary = _build_diagnostic_summary(total_combinations, reason_counts, evaluated_rows, cfg)
    # 存在满足所有约束的候选组合，选取总投资最低且 IRR 偏差最小的方案
    if candidates:
        best = min(candidates, key=lambda row: (row["total_capex_yuan"], row["irr_gap"]))
        return _result_from_row(
            status="ok", 
            row=best, 
            diagnostics=pd.DataFrame(candidates), 
            diagnostic_summary=diagnostic_summary, 
        )
    # 无满足所有约束的候选，返回 PPA/IRR 不满足的诊断信息供参考
    if diagnostics:
        diag_df = pd.DataFrame(diagnostics).sort_values("irr_gap", na_position="last").reset_index(drop=True)
        return WindPVBESSIRRResult(
            status="no_solution",
            diagnostics=diag_df,
            diagnostic_summary=diagnostic_summary,
            message="未找到满足 PPA/IRR 约束的风光储组合。",
        )
    # 所有候选均不满足物理消纳约束，返回空诊断信息
    return WindPVBESSIRRResult(
        status="no_solution",
        diagnostics=pd.DataFrame(),
        diagnostic_summary=diagnostic_summary,
        message="未找到满足物理消纳约束的风光储组合。",
    )
