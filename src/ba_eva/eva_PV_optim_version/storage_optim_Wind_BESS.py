# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim_Wind_BESS.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-28
# * Version     : 1.0.042816
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")
from typing import Optional, Dict, Any, Union

import numpy as np
import pandas as pd
from ba_eva.eva_PV_optim_version.storage_optim_common import (
    NUMBA_OK,
    UnitsConfig, PlanConfigFast,
    infer_dt_hours, normalize_time_and_load, as_time_series, align_to_time,
    evaluate,
)


# ============================================================
# 主规划函数
# 业务目标：
# 给定负荷曲线和风/光出力曲线，搜索满足“消纳率 + 负荷覆盖率”
# 约束的最小储能容量。
#
# 这个函数不是收益最大化模型，也不是完整的风光储联合投资优化。
# 它的核心是：
# 1. 把负荷、风电、光伏统一到同一时间轴和同一功率单位；
# 2. 枚举一批储能容量候选值；
# 3. 对每个候选值调用公共调度器做全年逐时能量平衡；
# 4. 选出满足约束且储能成本最低的方案。
# ============================================================
def plan_energy_system(
    df_load: pd.DataFrame,
    *,
    pv_unit_kw: Optional[Union[pd.Series, pd.DataFrame]] = None,
    wind_input: Optional[Union[pd.Series, pd.DataFrame]] = None,
    time_col: str = "Time",
    load_col: str = "P_kw",
    cfg: PlanConfigFast = PlanConfigFast(),
    units: UnitsConfig = UnitsConfig(),
) -> Dict[str, Any]:
    # ---------- 负荷 ----------
    # 统一提取时间轴和负荷功率序列，并转为内部统一口径：
    # - 时间：按升序排列
    # - 功率：统一转成 kW
    # - 输出：numpy 数组，便于后续年度仿真高效计算
    try:
        t, load_kw, load_warn = normalize_time_and_load(df_load, time_col, load_col, units)
        # 推断时间步长（小时），后续所有功率到电量的换算都依赖 dt。
        dt = infer_dt_hours(t)
    except Exception as e:
        return {
            "feasible": False,
            "diagnosis": {"stage": "load", "msg": str(e)},
        }

    # ---------- 风 ----------
    # wind_kw 表示与负荷时间轴完全对齐后的风电功率序列（单位 kW）。
    wind_kw = np.zeros_like(load_kw)
    if wind_input is not None:
        try:
            # 风电输入通常以 MW 给出，内部统一换算为 kW。
            scale = 1000.0 if units.wind_power.lower() == "mw" else 1.0
            w = as_time_series(wind_input, time_col, ("WindPower_MW", "wind_mw", "wind_kw"), scale)
            # 如果风电和负荷时间戳不完全一致，这里会插值并补齐到负荷时间轴。
            wind_kw = align_to_time(t, w)
        except Exception as e:
            return {
                "feasible": False,
                "diagnosis": {"stage": "wind", "msg": str(e)},
            }

    # ---------- PV ----------
    # 这里的 pv_unit_kw 实际被当作“可直接参与能量平衡的光伏功率曲线”使用，
    # 当前函数不会再额外搜索光伏装机容量。
    pv_kw = np.zeros_like(load_kw)
    if pv_unit_kw is not None:
        try:
            pv = as_time_series(pv_unit_kw, time_col, ("pv_unit_kw", "pv_kw", "value"), 1.0)
            pv_kw = align_to_time(t, pv)
        except Exception as e:
            return {
                "feasible": False,
                "diagnosis": {"stage": "pv", "msg": str(e)},
            }

    # ---------- 总新能源 ----------
    # 风和光在这里被合并成一条总新能源出力曲线，后续调度器不再区分来源。
    gen_kw = wind_kw + pv_kw

    # ---------- 仅储能场景 ----------
    # 如果既没有风也没有光，则当前模型无能量来源可供消纳。
    # 注意：这只是“新能源消纳”语境下不可行，不代表电价套利场景不可做。
    if pv_unit_kw is None and wind_input is None:
        return {
            "feasible": False,
            "diagnosis": {
                "reason": "NO_GENERATION",
                "msg": "无 PV / 无风，仅储能无法创造能量，仅可做移峰套利",
            }
        }

    # ---------- 储能搜索 ----------
    # 这里不是连续优化，而是把储能容量在 [0, batt_hi_max_kwh] 范围内均匀取 40 个点做穷举。
    # 每个候选容量都调用公共 evaluate() 进行一次全年逐时仿真。
    best = None
    for batt in np.linspace(0, cfg.batt_hi_max_kwh, 40):
        stats = evaluate(load_kw, gen_kw, dt, batt, cfg)
        # 只有当“新能源自消纳率”和“负荷覆盖率”都达标时，才认为该容量可行。
        if (stats["self_use_ratio"] >= cfg.self_use_ratio_min and stats["load_cover_ratio"] >= cfg.load_cover_ratio_min):
            # 当前成本口径非常简化，只按储能容量乘单位造价估算 CAPEX。
            cost = batt * cfg.bess_capex_yuan_per_kwh
            if best is None or cost < best["cost"]:
                best = {
                    "bess_kwh": batt, 
                    "metrics": stats, 
                    "cost": cost,
                }

    # 所有候选容量都不满足指标门槛时，返回不可行诊断。
    if best is None:
        return {
            "feasible": False,
            "diagnosis": {
                "reason": "NO_FEASIBLE_SOLUTION",
                "msg": "在当前约束和搜索上限下未找到可行储能容量。",
                "self_use_ratio_min": cfg.self_use_ratio_min,
                "load_cover_ratio_min": cfg.load_cover_ratio_min,
            }
        }
    else:
        # 返回最优可行解、输入规范化过程中的告警，以及这次仿真的上下文信息。
        return {
            "feasible": True,
            "solution": best,
            "warnings": load_warn,
            "context": {
                "dt_hours": dt,
                "engine": "numba" if (cfg.use_numba and NUMBA_OK) else "python",
            }
        }




def main():
    pass

if __name__ == "__main__":
    main()
