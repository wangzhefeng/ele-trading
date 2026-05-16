# -*- coding: utf-8 -*-

# ***************************************************
# * File        : ba_duli.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-11
# * Version     : 1.0.051115
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import os
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")

from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import pulp
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']   # 指定中文字体
plt.rcParams['axes.unicode_minus'] = False              # 解决负号显示问题


# ============================================================
# 0.多电价列自动分析（基础统计 + 波动 + 价差 + 峰谷曲线） + 储能潜力评分（0~100）+ 自动推荐储能规模（MW/MWh/C）
# ============================================================
class MultiPriceStorageFeasibilityFull:
    """
    多电价列自动分析（基础统计 + 波动 + 价差 + 峰谷曲线）
    + 储能潜力评分（0~100）
    + 自动推荐储能规模（MW/MWh/C）
    """
    def __init__(self, df, time_col="Time", config=None, output_dir="output_price_analysis", smooth_pattern=True):
        self.df = df.copy()
        self.time_col = time_col
        self.output_dir = output_dir
        self.smooth_pattern = smooth_pattern
        # 默认配置
        default_cfg = {
            "spread_levels": [200, 300, 400, 500, 600],
            "high_price_levels": [200, 400, 600, 800],
            "negative_price_levels": [0, -20],
            "suitable_threshold": 450,          # >=450 适合建储能
            "pattern_smooth_window": 5,
            "base_mw": 20,                      # 基准储能功率规模（可调）
            "min_hours": 2                      # 最小储能时长（h）
        }
        if config:
            default_cfg.update(config)
        self.cfg = default_cfg
        # 准备数据结构
        self._prepare()

    def _prepare(self):
        """
        准备数据结构
        """
        self.df[self.time_col] = pd.to_datetime(self.df[self.time_col])
        self.df = self.df.sort_values(self.time_col)

        # 自动识别电价列
        self.price_cols = [c for c in self.df.columns if c != self.time_col]

        # 时间字段
        self.df["Date"] = self.df[self.time_col].dt.date
        self.df["Hour"] = self.df[self.time_col].dt.hour
        self.df["Minute"] = self.df[self.time_col].dt.minute
        self.df["Timeslot"] = self.df["Hour"] * 60 + self.df["Minute"]

        os.makedirs(self.output_dir, exist_ok=True)

    def compute_score(self, spread_mean, spread_levels, extreme, std_price, pattern):
        """
        储能潜力评分
        """
        s1 = min(spread_mean / 6, 40)  # 日价差
        s2 = sum(v * 10 for v in spread_levels.values())  # 大价差占比
        s3 = sum(v * 15 for v in extreme.values())        # 高价事件
        s4 = min(std_price / 10, 10)                      # 波动性
        s5 = min((pattern.max() - pattern.min()) / 10, 10)  # 峰谷振幅

        return min(s1 + s2 + s3 + s4 + s5, 100)

    def score_to_level(self, score):
        """
        储能潜力评分
        """
        if score >= 80:
            return "非常适合"
        elif score >= 65:
            return "适合"
        elif score >= 50:
            return "一般"
        else:
            return "较弱"

    def analyze_single_price(self, col):
        """
        单列电价分析
        """
        df = self.df[[self.time_col, "Date", "Timeslot", col]].dropna()
        price = df[col]

        daily_group = df.groupby("Date")[col]
        daily_peak = daily_group.max()
        daily_valley = daily_group.min()
        daily_spread = daily_peak - daily_valley

        # ---------- 基础统计 ----------
        basic_stats = {
            "mean_price": price.mean(),
            "std_price": price.std(),
            "p95_price": np.percentile(price, 95),
            "max_price": price.max(),
            "min_price": price.min(),
        }

        # 负价比例
        for lv in self.cfg["negative_price_levels"]:
            basic_stats[f"ratio_price_below_{lv}"] = (price < lv).mean()

        # ---------- 日价差 ----------
        spread_stats = {
            "daily_spread_mean": daily_spread.mean(),
            "daily_spread_median": daily_spread.median(),
            "levels": {}
        }
        for lv in self.cfg["spread_levels"]:
            spread_stats["levels"][f"spread_gt_{lv}"] = (daily_spread > lv).mean()

        # ---------- 高价 ----------
        extreme_stats = {}
        for lv in self.cfg["high_price_levels"]:
            extreme_stats[f"price_gt_{lv}"] = (price > lv).mean()

        # ---------- 典型日 ----------
        pattern = df.groupby("Timeslot")[col].mean()
        if self.smooth_pattern:
            pattern = pattern.rolling(self.cfg["pattern_smooth_window"], min_periods=1).mean()

        # ---------- 是否适合储能 ----------
        spread_mean = spread_stats["daily_spread_mean"]
        threshold = self.cfg["suitable_threshold"]

        suitability = (
            f"适合建储能（日均价差 {spread_mean:.1f} ≥ {threshold}）"
            if spread_mean >= threshold else
            f"不太适合建储能（日均价差 {spread_mean:.1f} < {threshold})"
        )

        # ---------- 储能评分 ----------
        score = self.compute_score(spread_mean, spread_stats["levels"], extreme_stats, basic_stats["std_price"], pattern)
        level = self.score_to_level(score)

        return {
            "column": col,
            "basic": basic_stats,
            "spread": spread_stats,
            "extreme_price": extreme_stats,
            "pattern_curve": pattern,
            "suitability": suitability,
            "score": round(score, 2),
            "potential_level": level
        }

    def recommend_storage_size(self, result):
        """
        推荐储能规模（MW、MWh、C值）
        """
        pattern = result["pattern_curve"]
        spread_mean = result["spread"]["daily_spread_mean"]
        threshold = self.cfg["suitable_threshold"]

        # 电价波动强度
        strength = max(0.5, spread_mean / threshold)

        # 峰谷窗口（估算储能时长 h）
        valley_hours = (pattern < pattern.mean()).sum() * (15 / 60)
        peak_hours = (pattern > pattern.mean()).sum() * (15 / 60)

        usable_hours = max(self.cfg["min_hours"], min(valley_hours, peak_hours))

        # 推荐功率 MW
        MW = self.cfg["base_mw"] * strength

        # 推荐容量 MWh
        MWh = MW * usable_hours

        return {
            "recommended_MW": round(MW, 2),
            "recommended_MWh": round(MWh, 2),
            "usable_hours": round(usable_hours, 2)
        }

    def run(self):
        results = {}
        for col in self.price_cols:
            print(f"分析电价列：{col} ...")
            res = self.analyze_single_price(col)
            rec = self.recommend_storage_size(res)
            res["storage_recommendation"] = rec

            results[col] = res

        return results


def build_result_dataframe(results, sort_by="score"):
    """
    将 results 转为 DataFrame（包含收益/C值）
    """
    rows = []
    for col, res in results.items():
        rec = res["storage_recommendation"]

        MW = rec["recommended_MW"]
        MWh = rec["recommended_MWh"]

        # 推荐倍率 C
        C = max(0.1, min(2.0, MW / MWh if MWh > 0 else 1.0))

        # 理论年收益（只考虑电价）
        p95 = res["basic"]["p95_price"]
        p5 = res["basic"]["min_price"]

        eff_d = 0.96
        eff_c = 0.96

        daily_income_per_mwh = max(0, p95 * eff_d - p5 / eff_c)
        annual_income_est = daily_income_per_mwh * 365 * MWh

        rows.append({
            "column": col,
            "score": res["score"],
            "potential_level": res["potential_level"],
            "recommended_MW": MW,
            "recommended_MWh": MWh,
            "recommended_C": round(C, 3),
            "annual_income_est": round(annual_income_est, 2),
            "spread_mean": res["spread"]["daily_spread_mean"],
            "mean_price": res["basic"]["mean_price"],
            "std_price": res["basic"]["std_price"],
            "p95_price": res["basic"]["p95_price"],
            "min_price": res["basic"]["min_price"],
        })

    df_out = pd.DataFrame(rows)

    # 排序
    if sort_by == "score":
        df_out = df_out.sort_values("score", ascending=False)
    elif sort_by == "MWh":
        df_out = df_out.sort_values("recommended_MWh", ascending=False)

    df_out.reset_index(drop=True, inplace=True)
    return df_out


# ============================================================
# 1.配置
# ============================================================
@dataclass
class StorageConfig:
    # 寿命与循环
    life_years: int = 10                 # 经济寿命（年）
    life_cycles: int = 4000              # 设计寿命总等效循环次数（用于“利用率”统计）

    # 🔥 最大年循环约束（等效完整循环/年，可不填 = 不约束）
    max_cycles_per_year: Optional[float] = None  # 例如 365 表示最多 1 次/天

    # 🔥 最大日循环约束（等效完整循环/日，可不填 = 不约束）
    max_daily_cycles: Optional[float] = 1.0      # 例如 1.0 表示最多 1 次/日

    # 电池物理参数
    dod: float = 0.9
    eta_charge: float = 0.98
    eta_discharge: float = 0.98
    soc_init: float = 0.5
    soc_min: float = 0.1
    soc_max: float = 1.0

    # 经济参数
    capex_per_kwh: float = 1500          # 元/kWh
    opex_per_kwh_year: float = 30        # 元/kWh·年

    # 容量扫描
    cap_min_mwh: float = 50
    cap_max_mwh: float = 200
    cap_step_mwh: float = 10

    # 电价/放电阈值
    discharge_price_threshold: float = 300
    allow_negative_price: bool = True

    # 🔥 衰减参数：寿命末期剩余容量比例（SOH）
    capacity_end_ratio: float = 0.7      # 例如 0.7 = 10 年后剩余 70%

    # 并网参数
    line_limit_mw: float = 400           # 线路/并网点最大功率限制（MW）
    c_rate: float = 0.5                  # 统一 C 倍率（功率 = c_rate * 容量）

    # 🔥 网侧充电附加成本（元/MWh）
    # 充电成本 = (电价 + grid_charge_fee) / η_charge
    grid_charge_fee: float = 14.5

# ============================================================
# 2. IRR 计算（用二分法，避免 NaN）
# ============================================================
def compute_irr_bisect(cash_flows: List[float], tol: float = 1e-6, max_iter: int = 100) -> float:
    """
    用二分法算 IRR，避免 np.irr 出 NaN。
    返回值为小数（0.2 = 20%），若无解返回 0.0。
    """
    if all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        return 0.0

    def npv(rate: float) -> float:
        return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cash_flows))

    low, high = -0.99, 1.0
    npv_low, npv_high = npv(low), npv(high)
    if npv_low * npv_high > 0:
        return 0.0

    for _ in range(max_iter):
        mid = (low + high) / 2
        npv_mid = npv(mid)
        if abs(npv_mid) < tol:
            return mid
        if npv_mid * npv_low < 0:
            high, npv_high = mid, npv_mid
        else:
            low, npv_low = mid, npv_mid
    return mid

# ============================================================
# 3. 主 MILP 类：给定一个电价序列，做容量扫描
# ============================================================
class PriceBasedStorageMILP:
    """
    只知道电价（元/MWh）情况下：
    - 对每个容量 E（MWh）建立 MILP 模型
    - 决策：每个时刻充电/放电功率、SOC
    - 目标：最大化日内套利收益（年化）
    - 再叠加寿命衰减，算 IRR / 全寿命收益 / 利用率 等
    """
    def __init__(self, df_price: pd.DataFrame, time_col: str, price_col: str, config: StorageConfig):
        self.df = df_price.copy()
        self.time_col = time_col
        self.price_col = price_col
        self.cfg = config
        self._prepare()

    # --------------------------------------------------------
    def _prepare(self):
        # 时间、电价清洗
        self.df[self.time_col] = pd.to_datetime(self.df[self.time_col], errors="coerce")
        self.df[self.price_col] = pd.to_numeric(self.df[self.price_col], errors="coerce")
        self.df = self.df.dropna(subset=[self.time_col, self.price_col])
        self.df = self.df.sort_values(self.time_col).reset_index(drop=True)

        if self.df.empty:
            raise ValueError("电价数据为空或时间/价格列全部无效。")

        # 推断时间步长（小时）
        diff = self.df[self.time_col].diff().dropna().dt.total_seconds() / 3600.0
        if diff.empty:
            raise ValueError("时间步长无法推断，请检查 Time 列。")
        self.dt = diff.median()

        # 价格序列覆盖的总天数（用于年化）
        hours = (self.df[self.time_col].iloc[-1] - self.df[self.time_col].iloc[0]).total_seconds() / 3600.0
        self.days = max(hours / 24.0, 1.0)

    # --------------------------------------------------------
    def _get_pmax(self, cap_mwh: float) -> float:
        """
        根据容量和 C 值，以及线路容量限制，计算功率上限（MW）
        """
        cfg = self.cfg
        p_c = cfg.c_rate * cap_mwh        # 按 C 值得到的功率上限
        p_lim = cfg.line_limit_mw         # 线路极限
        return float(min(p_c, p_lim))

    # --------------------------------------------------------
    def solve_single_capacity(self, cap_mwh: float) -> Optional[Dict[str, Any]]:
        """
        针对单一容量 cap_mwh（MWh）求解 MILP：
        返回首年满容量的年化收益 & 充放电曲线
        """
        cfg = self.cfg
        prices = self.df[self.price_col].values
        T = len(prices)
        dt = self.dt

        p_max = self._get_pmax(cap_mwh)

        # 若 p_max 很小或为 0，直接认为无收益
        if p_max <= 1e-6:
            return None

        discharge_allowed = (prices >= cfg.discharge_price_threshold).astype(float)

        model = pulp.LpProblem("storage_arbitrage", sense=pulp.LpMaximize)

        # 变量（功率单位 MW，SOC 单位 MWh）
        ch = pulp.LpVariable.dicts("ch", range(T), lowBound=0, upBound=p_max)
        dis = pulp.LpVariable.dicts("dis", range(T), lowBound=0, upBound=p_max)
        soc = pulp.LpVariable.dicts(
            "soc", range(T),
            lowBound=cfg.soc_min * cap_mwh,
            upBound=cfg.soc_max * cap_mwh
        )

        # 目标函数：∑(放电收益 - 充电成本)
        # 充电成本中增加网侧费用 cfg.grid_charge_fee（元/MWh）
        model += pulp.lpSum(
            (prices[t] * dis[t] * cfg.eta_discharge
             - (prices[t] + cfg.grid_charge_fee) * ch[t] / cfg.eta_charge) * dt
            for t in range(T)
        )

        # SOC 动力学
        for t in range(T):
            if t == 0:
                soc_prev = cfg.soc_init * cap_mwh
            else:
                soc_prev = soc[t - 1]
            model += soc[t] == soc_prev + (ch[t] * cfg.eta_charge - dis[t] / cfg.eta_discharge) * dt

        # 不允许放电的时段：放电功率 = 0
        for t in range(T):
            if discharge_allowed[t] < 0.5:
                model += dis[t] <= 0.0

        # 若不允许负价充电，则负价时段 ch = 0
        if not cfg.allow_negative_price:
            for t in range(T):
                if prices[t] < 0:
                    model += ch[t] <= 0.0

        # 🔥 最大年循环约束（等效完整循环/年）
        # 样本期放电电量（MWh）
        total_discharge_energy = pulp.lpSum(dis[t] * dt for t in range(T))
        # 若给了 max_cycles_per_year，则：
        # 年放电电量 / 容量 ≤ max_cycles_per_year
        # => 样本期∑(dis*dt) ≤ max_cycles_per_year * cap_mwh * (self.days / 365)
        if cfg.max_cycles_per_year is not None:
            model += total_discharge_energy <= cfg.max_cycles_per_year * cap_mwh * (self.days / 365.0)

        # 🔥 最大日循环约束（等效完整循环/日）
        # 年等效完整循环数 = 年放电电量 / cap_mwh
        # 日等效完整循环数 = 年等效 / 365
        # 样本期放电电量 ≤ max_daily_cycles * cap_mwh * self.days
        if cfg.max_daily_cycles is not None:
            model += total_discharge_energy <= cfg.max_daily_cycles * cap_mwh * self.days

        solver = pulp.PULP_CBC_CMD(msg=False)
        model.solve(solver)

        status = pulp.LpStatus[model.status]
        if status != "Optimal":
            return None

        ch_v = np.array([pulp.value(ch[t]) for t in range(T)])
        dis_v = np.array([pulp.value(dis[t]) for t in range(T)])

        # 首年满容量收益（元/年）
        revenue_series = (
            prices * dis_v * cfg.eta_discharge
            - (prices + cfg.grid_charge_fee) * ch_v / cfg.eta_charge
        ) * dt
        total_revenue = revenue_series.sum()
        annual_revenue_1 = total_revenue * 365.0 / self.days

        return {
            "status": status,
            "annual_revenue_1": annual_revenue_1,  # 元/年（首年，已包含网侧费用、未扣 OPEX）
            "ch": ch_v,
            "dis": dis_v,
        }

    # --------------------------------------------------------
    def evaluate_with_degradation(self, cap_mwh: float, sol: Dict[str, Any]) -> Dict[str, Any]:
        """
        把首年的收益结果，叠加衰减 + CAPEX/OPEX，计算 IRR 等指标。
        输出字段全部按中文 & 万元。
        """
        cfg = self.cfg

        annual_revenue_1 = sol["annual_revenue_1"]   # 元/年（首年，套利净收入，含网侧费，未扣 OPEX）
        ch_period = sol["ch"].sum() * self.dt       # 样本期充电电量（MWh）
        dis_period = sol["dis"].sum() * self.dt     # 样本期放电电量（MWh）

        # 年化因子
        factor_year = 365.0 / self.days

        # 首年充放电量（MWh）
        charge_mwh_year1 = ch_period * factor_year
        discharge_mwh_year1 = dis_period * factor_year

        # 投资成本 CAPEX（元）
        capex = cap_mwh * 1000.0 * cfg.capex_per_kwh
        # 首年 OPEX（元/年）
        opex_year_1 = cap_mwh * 1000.0 * cfg.opex_per_kwh_year

        # 容量衰减（线性）：year 1 = 1.0, year N = capacity_end_ratio
        Y = cfg.life_years
        if Y > 1:
            step = (1.0 - cfg.capacity_end_ratio) / (Y - 1)
        else:
            step = 0.0
        year_ratios = [max(cfg.capacity_end_ratio, 1.0 - step * y) for y in range(Y)]

        # 每年收入 & OPEX
        revenues = [annual_revenue_1 * r for r in year_ratios]     # 元/年，含网侧费，未扣 OPEX
        opexes = [opex_year_1 * r for r in year_ratios]            # 元/年

        # 现金流列表（元）：CF0 为 -CAPEX，之后为每年净现金流
        cash_flows = [-capex] + [revenues[y] - opexes[y] for y in range(Y)]

        # IRR
        irr = compute_irr_bisect(cash_flows)
        irr_percent = round(irr * 100, 2)

        # 全寿命总收入 & 净收益（元）
        life_revenue = sum(revenues)
        life_net_cash = sum(revenues[y] - opexes[y] for y in range(Y))

        # 首年净收益（元/年）
        annual_net_cash_1 = revenues[0] - opexes[0]
        daily_net_cash_1 = annual_net_cash_1 / 365.0

        # 全寿命充放电量（MWh）：按容量比例缩放
        life_charge = sum(charge_mwh_year1 * r for r in year_ratios)
        life_discharge = sum(discharge_mwh_year1 * r for r in year_ratios)

        # 利用率：全寿命放电 / (life_cycles * 初始容量)
        if cfg.life_cycles > 0 and cap_mwh > 0:
            utilization = life_discharge / (cfg.life_cycles * cap_mwh)
        else:
            utilization = 0.0

        # 首年日平均充放次数（等效完整循环次数/日，用放电量算）
        daily_cycles_year1 = discharge_mwh_year1 / cap_mwh / 365.0 if cap_mwh > 0 else 0.0

        # 金额统一折算为万元
        wan = lambda x: round(x / 1e4, 2)

        p_max = self._get_pmax(cap_mwh)
        c_rate_effective = p_max / cap_mwh if cap_mwh > 0 else 0.0

        # ⚠ 输出字段全部中文，后续表格/Excel 直接用
        return {
            "推荐容量(MWh)": cap_mwh,
            "推荐功率(MW)": round(p_max, 2),
            "等效C倍率": round(c_rate_effective, 3),

            "IRR(%)": irr_percent,

            # 收入/收益（万元）
            "首年收入(万元)": wan(revenues[0]),              # 首年总收入（含网侧费，未扣 OPEX）
            "全寿命总收入(万元)": wan(life_revenue),         # 全寿命总收入（未扣 OPEX）
            "全寿命净收益(万元)": wan(life_net_cash),        # 全寿命净收益（收入 - OPEX）

            # 充放电量（MWh）
            "首年充电量(MWh)": round(charge_mwh_year1, 2),
            "首年放电量(MWh)": round(discharge_mwh_year1, 2),
            "全寿命充电量(MWh)": round(life_charge, 2),
            "全寿命放电量(MWh)": round(life_discharge, 2),

            # 单位收益 & 日均收益（万元）
            "首年单位收益(万元/MWh)": round(wan(annual_net_cash_1) / cap_mwh, 4) if cap_mwh > 0 else 0.0,
            "首年日均收益(万元/日)": round(wan(annual_net_cash_1) / 365.0, 4),
            "全寿命单位收益(万元/MWh)": round(wan(life_net_cash) / cap_mwh, 4) if cap_mwh > 0 else 0.0,

            # 利用率 & 日平均充放次数
            "利用率": round(utilization, 4),
            "首年日平均充放次数(次/日)": round(daily_cycles_year1, 4),
        }

    # --------------------------------------------------------
    def sweep_capacities(self) -> pd.DataFrame:
        """
        按 config 中的容量范围做轮巡，返回结果 DataFrame（列名中文）
        """
        cfg = self.cfg
        caps = np.arange(cfg.cap_min_mwh, cfg.cap_max_mwh + 1e-9, cfg.cap_step_mwh)
        rows: List[Dict[str, Any]] = []

        for cap in caps:
            print(f"→ 优化 {self.price_col}, 容量 {cap} MWh ...")
            sol = self.solve_single_capacity(cap)
            if sol is None:
                continue
            rows.append(self.evaluate_with_degradation(cap, sol))

        if not rows:
            return pd.DataFrame()

        df_res = pd.DataFrame(rows)
        df_res = df_res.sort_values("IRR(%)", ascending=False).reset_index(drop=True)
        return df_res

# ============================================================
# run all nodes
# ============================================================
def run_all_nodes(
    df_use: pd.DataFrame,
    time_col: str = "Time",
    price_cols: Optional[List[str]] = None,
    config: Optional[StorageConfig] = None,
    excel_path: str = "src/ba_eva/results/output_excel/storage_analysis_with_degradation.xlsx",
    fig_dir: str = "src/ba_eva/results/output_fig",
) -> pd.DataFrame:
    """
    自动对多个电价列（节点）进行 MILP 轮巡：
        - df_use: 原始数据，至少包含 time_col + 若干电价列
        - time_col: 时间列名，比如 "Time"
        - price_cols: 要分析的电价列列表；为 None 时自动识别所有数值列
        - config: StorageConfig（可以用默认，也可以传入覆盖）
        - 返回：summary DataFrame（每个节点的“最优一行”结果，列名中文）
    """
    # 默认配置
    if config is None:
        config = StorageConfig()  # 有默认值，但你可以在调用时传入自己的参数
    # 路径处理
    os.makedirs(os.path.dirname(excel_path), exist_ok=True)
    os.makedirs(fig_dir, exist_ok=True)
    # 自动识别电价列
    if price_cols is None:
        price_cols = [
            c for c in df_use.columns
            if c != time_col and np.issubdtype(df_use[c].dtype, np.number)
        ]
    if not price_cols:
        raise ValueError("未找到可用于电价分析的列，请检查输入数据。")
    # ------------------------------
    # 轮巡
    # ------------------------------
    writer = pd.ExcelWriter(excel_path, engine="xlsxwriter")
    summary_rows: List[Dict[str, Any]] = []
    for col in price_cols:
        print(f"\n================  节点: {col}  ================")
        df_price = df_use[[time_col, col]].rename(columns={col: "Price"})
        df_price = df_price.dropna(subset=[time_col, "Price"])

        if df_price.empty:
            print(f"⚠ 节点 {col} 数据为空，跳过。")
            continue

        evaluator = PriceBasedStorageMILP(df_price, time_col=time_col, price_col="Price", config=config)
        df_res = evaluator.sweep_capacities()

        if df_res.empty:
            print(f"⚠ 节点 {col} 没有可行解，跳过。")
            continue

        # 写入单独 sheet（表头已经是中文）
        sheet_name = col[:31]  # Excel sheet 名最长 31
        df_res.to_excel(writer, sheet_name=sheet_name, index=False)

        # 取 IRR 最大的那一行做汇总
        best_row = df_res.iloc[0].to_dict()
        summary_rows.append({"节点": col, **best_row})

        # -------- 画图：IRR vs 推荐容量 --------
        plt.figure(figsize=(8, 5))
        plt.plot(df_res["推荐容量(MWh)"], df_res["IRR(%)"], marker="o")
        plt.title(f"IRR vs 容量 - {col}")
        plt.xlabel("推荐容量(MWh)")
        plt.ylabel("IRR(%)")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(os.path.join(fig_dir, f"IRR_{col}.png"), dpi=200)
        plt.close()

        # -------- 画图：首年收入 vs 容量 --------
        plt.figure(figsize=(8, 5))
        plt.plot(df_res["推荐容量(MWh)"], df_res["首年收入(万元)"], marker="o")
        plt.title(f"首年收入 vs 容量 - {col}")
        plt.xlabel("推荐容量(MWh)")
        plt.ylabel("首年收入(万元)")
        plt.grid(True)
        plt.tight_layout()
        plt.savefig(os.path.join(fig_dir, f"AnnualIncome_{col}.png"), dpi=200)
        plt.close()

    # 写 summary sheet（中文表头）
    df_summary = pd.DataFrame(summary_rows)
    if not df_summary.empty:
        df_summary.to_excel(writer, sheet_name="汇总", index=False)
    writer.close()
    print(f"\n🎉 全部节点分析完成，结果已保存到：{excel_path}")
    print(f"📈 单节点图表已保存到目录：{fig_dir}")
    # ------------------------------
    # 🔥 汇总对比图：所有节点的最优容量 & IRR
    # ------------------------------
    if not df_summary.empty:
        # 按 IRR 从高到低排序
        df_plot = df_summary.sort_values("IRR(%)", ascending=False).reset_index(drop=True)

        nodes = df_plot["节点"].tolist()
        x = np.arange(len(nodes))

        fig, ax1 = plt.subplots(figsize=(10, 6))

        # 柱状：最优容量
        ax1.bar(x, df_plot["推荐容量(MWh)"], width=0.4, label="推荐容量(MWh)")
        ax1.set_xlabel("节点")
        ax1.set_ylabel("推荐容量(MWh)")
        ax1.set_xticks(x)
        ax1.set_xticklabels(nodes, rotation=45, ha="right")

        # 折线：最优 IRR
        ax2 = ax1.twinx()
        ax2.plot(x, df_plot["IRR(%)"], marker="o", linestyle="-", label="IRR(%)", color="tab:red")
        ax2.set_ylabel("IRR(%)")

        handles1, labels1 = ax1.get_legend_handles_labels()
        handles2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left")

        plt.title("各节点最优容量 & IRR 对比")
        plt.tight_layout()
        summary_fig_path = os.path.join(fig_dir, "汇总_最优容量_IRR对比.png")
        plt.savefig(summary_fig_path, dpi=200)
        plt.close()

        print(f"📊 汇总对比图已保存：{summary_fig_path}")

    return df_summary






# 测试代码 main 函数
def main():
    # ------------------------------
    # 电价数据
    # ------------------------------
    # 数据读取
    df_hqb = pd.read_csv('D:\\内蒙项目\\resources\\node-price-by-node4\\内蒙_黄旗海站 内蒙_黄旗海站_220kV_1M__合并2.csv')
    df_sh  = pd.read_csv('D:\\内蒙项目\\resources\\node-price-by-node4\\内蒙_赛罕站 内蒙_赛罕站_500kV_1M__合并2.csv')
    df_qxy  = pd.read_csv('D:\\内蒙项目\\resources\\node-price-by-node4\\内蒙_旗下营站 内蒙_旗下营站_500kV_1M__合并2.csv')
    df_kz  = pd.read_csv('D:\\内蒙项目\\resources\\node-price-by-node4\\内蒙_可镇站 内蒙_可镇站_220kV_1M__合并2.csv')
    # 数据合并
    df_all = pd.DataFrame(columns=['Time', 'price_hqb', 'price_sh', 'price_qxy', 'price_kz'])
    df_all['Time'] = df_hqb['datetime']
    df_all['Time'] = pd.to_datetime(df_all['Time'])
    df_all['price_hqb'] = df_hqb['Node pricing(yuan/MWh)']
    df_all['price_sh'] = df_sh['Node pricing(yuan/MWh)']
    df_all['price_qxy'] = df_qxy['Node pricing(yuan/MWh)']
    df_all['price_kz'] = df_kz['Node pricing(yuan/MWh)']
    # 数据保存
    df_all.to_csv('src/ba_eva/results/df_all.csv', index=False)
    # 选时间范围
    df_use = df_all[df_all["Time"] >= "2025-01-01"]
    # ------------------------------
    # 多电价列自动分析（基础统计 + 波动 + 价差 + 峰谷曲线）+ 储能潜力评分（0~100）+ 自动推荐储能规模（MW/MWh/C）
    # ------------------------------
    # config
    config = {
        "spread_levels": [300, 400, 500, 600, 700],
        "high_price_levels": [400, 500, 600, 800],
        "negative_price_levels": [0, -20],
        "suitable_threshold": 450,
        "base_mw": 50
    }
    # analyzer
    analyzer = MultiPriceStorageFeasibilityFull(df_use, config=config)
    # run
    results = analyzer.run()
    # result
    df_final = build_result_dataframe(results, sort_by="score")
    df_final.to_csv("src/ba_eva/results/output_price_analysis/final_storage_suggestion.csv", index=False)
    print(df_final)
    # ------------------------------
    # 
    # ------------------------------
    config1 = StorageConfig(
        life_years=15,
        life_cycles=6500,

        max_cycles_per_year=None,    # 不用年约束就设 None
        max_daily_cycles=1.5,        # 日等效完整循环次数上限（可配）

        dod=0.9,
        eta_charge=0.92,
        eta_discharge=0.95,
        soc_init=0.5,
        soc_min=0.1,
        soc_max=1.0,

        capex_per_kwh=1000,
        opex_per_kwh_year=30,

        cap_min_mwh=200,
        cap_max_mwh=800,
        cap_step_mwh=50,

        discharge_price_threshold=300,
        allow_negative_price=True,

        capacity_end_ratio=0.7,

        line_limit_mw=100,
        c_rate=0.25,

        grid_charge_fee=1.45  # 🔥 网侧充电附加成本（元/MWh）
    )
    summary = run_all_nodes(
        df_use=df_use,
        time_col="Time",
        price_cols=None, # None = 自动识别所有数值电价列
        config=config1,
        excel_path="src/ba_eva/results/output_excel/storage_analysis_final.xlsx",
        fig_dir="src/ba_eva/results/output_fig"
    )
    print(summary)
    # ------------------------------
    # 
    # ------------------------------
    config2 = StorageConfig(
        life_years=10,
        life_cycles=6500,

        max_cycles_per_year=None,    # 不用年约束就设 None
        max_daily_cycles=2,        # 日等效完整循环次数上限（可配）

        dod=0.9,
        eta_charge=0.92,
        eta_discharge=0.95,
        soc_init=0.5,
        soc_min=0.1,
        soc_max=1.0,

        capex_per_kwh=1200,
        opex_per_kwh_year=20,

        cap_min_mwh=1,
        cap_max_mwh=1,
        cap_step_mwh=1,

        discharge_price_threshold=300,
        allow_negative_price=True,

        capacity_end_ratio=0.5,

        line_limit_mw=100,
        c_rate=1,

        grid_charge_fee=0  # 🔥 网侧充电附加成本（元/MWh）
    )
    summary = run_all_nodes(
        df_use=df_use,
        time_col="Time",
        price_cols=None, # None = 自动识别所有数值电价列
        config=config2,
        excel_path="output_excel/storage_analysis_final_1mwh.xlsx",
        fig_dir="src/ba_eva/results/output_fig"
    )
    print(summary)
    # ------------------------------
    # TODO 1月
    # ------------------------------
    df_month_1 = (0.596597 - 0.230117)*1000*2*31
    df_month_2 = (0.661913 - 0.257993)*1000*2*28
    df_month_3 = (0.565685 - 0.227285)*1000*2*31
    df_month_4 = (0.634741 - 0.249301)*1000*2*30
    df_month_5 = (0.662933 - 0.260213)*1000*2*31
    df_month_6 = (0.721895 - 0.216695 + 0.617883 - 0.246413)*1000*30
    df_month_7 = (0.673078 - 0.200192 + 0.575719 - 0.228009)*1000*31
    df_month_8 = (0.701972 - 0.202458 + 0.599131 - 0.231841)*1000*31
    df_month_9 = (0.639553 - 0.253033)*1000*2*30
    df_month_10 = (0.651373 - 0.257053)*1000*2*31
    df_month_11 = (0.625957 - 0.248077)*1000*2*30
    df_month_12 = (0.280209 - 0.224649)*1000*2*31
    df_sum = sum([
        df_month_1, df_month_2, df_month_3, df_month_4, df_month_5, df_month_6, 
        df_month_7, df_month_8, df_month_9, df_month_10, df_month_11, df_month_12
    ]) * 0.92 * 0.92
    print(df_sum)

if __name__ == "__main__":
    main()
