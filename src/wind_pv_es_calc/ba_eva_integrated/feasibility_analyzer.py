# -*- coding: utf-8 -*-
"""储能可行性评估模块。

分析电价、负荷和变压器数据，产出匹配性评分和策略建议。
从 ba_eva_optim_version/ba_eva_2.py 的两个分析器类合并整合。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ============================================================
# 配置数据类
# ============================================================
@dataclass(slots=True)
class FeasibilityAnalyzerConfig:
    """可行性分析配置。"""
    load_col: str = "Load"
    price_col: str = "Price"
    time_col: str = "Time"
    transformer_kva: float = 80000.0
    power_factor: float = 0.95
    power_unit: str = "kW"
    price_unit: str = "yuan_per_kwh"


# ============================================================
# 结果数据类
# ============================================================
@dataclass(slots=True)
class PriceAnalysis:
    """电价统计结果。"""
    mean: float = 0.0
    std: float = 0.0
    high_mean: float = 0.0
    low_mean: float = 0.0
    daily_spread: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 0.0


@dataclass(slots=True)
class LoadAnalysis:
    """负荷统计结果。"""
    max_kw: float = 0.0
    mean_kw: float = 0.0
    min_kw: float = 0.0
    peak_valley_ratio: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 0.0


@dataclass(slots=True)
class TransformerAnalysis:
    """变压器剩余容量分析。"""
    rated_kva: float = 0.0
    active_limit_kw: float = 0.0
    min_spare_kw: float = 0.0
    median_spare_kw: float = 0.0
    charge_window_hours_per_day: float = 0.0


@dataclass(slots=True)
class MatchingAnalysis:
    """电价-负荷-变压器匹配性分析。"""
    high_price_load_corr: float = 0.0
    low_price_load_corr: float = 0.0
    low_price_charge_feasibility: float = 0.0
    strategy_executability: float = 0.0
    score: float = 0.0
    recommendation: str = ""


@dataclass(slots=True)
class StorageStrategyRecommendation:
    """储能策略建议。"""
    category: str = ""
    power_range_kw: tuple[float, float] = (0.0, 0.0)
    capacity_range_kwh: tuple[float, float] = (0.0, 0.0)
    description: str = ""


@dataclass(slots=True)
class FeasibilityResult:
    """可行性分析完整结果。"""
    price: PriceAnalysis
    load: LoadAnalysis
    transformer: TransformerAnalysis
    matching: MatchingAnalysis
    strategy: StorageStrategyRecommendation
    monthly_stats: pd.DataFrame | None = None


# ============================================================
# 分析器主类
# ============================================================
class StorageFeasibilityAnalyzer:
    """储能项目可行性评估。

    在 MILP 优化之前，分析电价、负荷和变压器数据，
    产出匹配性评分 (0~1) 和策略建议。
    """

    def __init__(self, cfg: FeasibilityAnalyzerConfig):
        self.cfg = cfg

    # ----------------------------------------------------------
    # 单位归一化
    # ----------------------------------------------------------
    def _normalize_units(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        lc = self.cfg.load_col
        pc = self.cfg.price_col

        # 功率单位
        pu = self.cfg.power_unit.lower()
        if pu == "mw":
            df[lc] = df[lc] * 1000
        elif pu == "gw":
            df[lc] = df[lc] * 1_000_000
        elif pu == "w":
            df[lc] = df[lc] / 1000

        # 电价单位
        up = self.cfg.price_unit.lower()
        if up in ["yuan_per_mwh", "cny_per_mwh", "元_mwh"]:
            df[pc] = df[pc] / 1000

        df[self.cfg.time_col] = pd.to_datetime(df[self.cfg.time_col])
        df = df.sort_values(self.cfg.time_col).reset_index(drop=True)
        return df

    # ----------------------------------------------------------
    # 基础分析
    # ----------------------------------------------------------
    def _analyze_basic(self, df: pd.DataFrame):
        tc = self.cfg.time_col
        pc = self.cfg.price_col
        lc = self.cfg.load_col

        dt = (df[tc].iloc[1] - df[tc].iloc[0]).total_seconds() / 3600
        total_hours = len(df) * dt

        price = df[pc]
        load = df[lc]
        q30, q70 = price.quantile([0.3, 0.7])
        high_mask = price >= q70
        low_mask = price <= q30

        price_info = PriceAnalysis(
            mean=float(price.mean()),
            std=float(price.std()),
            high_mean=float(price[high_mask].mean()),
            low_mean=float(price[low_mask].mean()),
            daily_spread=float(price[high_mask].mean() - price[low_mask].mean()),
            skewness=float(price.skew()),
            kurtosis=float(price.kurtosis()),
        )

        load_info = LoadAnalysis(
            max_kw=float(load.max()),
            mean_kw=float(load.mean()),
            min_kw=float(load.min()),
            peak_valley_ratio=float(load.max() / load.mean()) if load.mean() > 0 else 0,
            skewness=float(load.skew()),
            kurtosis=float(load.kurtosis()),
        )

        tr_pmax = self.cfg.transformer_kva * self.cfg.power_factor
        df["变压器剩余容量_kW"] = tr_pmax - df[lc]
        spare = df["变压器剩余容量_kW"]

        transformer_info = TransformerAnalysis(
            rated_kva=self.cfg.transformer_kva,
            active_limit_kw=float(tr_pmax),
            min_spare_kw=float(spare.min()),
            median_spare_kw=float(spare.median()),
            charge_window_hours_per_day=float(
                (spare > 0).sum() * dt / (total_hours / 24)
            ),
        )

        return price_info, load_info, transformer_info, df

    # ----------------------------------------------------------
    # 匹配性分析
    # ----------------------------------------------------------
    def _analyze_matching(self, df: pd.DataFrame) -> MatchingAnalysis:
        pc = self.cfg.price_col
        lc = self.cfg.load_col

        p70 = df[pc].quantile(0.7)
        p30 = df[pc].quantile(0.3)
        high = df[pc] >= p70
        low = df[pc] <= p30

        corr_high = float(
            np.corrcoef(df.loc[high, pc], df.loc[high, lc])[0, 1]
        ) if high.sum() > 3 else 0.0

        corr_low = float(
            np.corrcoef(df.loc[low, pc], df.loc[low, lc])[0, 1]
        ) if low.sum() > 3 else 0.0

        charge_feas = float((df.loc[low, "变压器剩余容量_kW"] > 0).mean())

        # 策略可执行性
        df["可充标记"] = (
            (df[pc] <= p30)
            & (df[lc] <= df[lc].quantile(0.5))
            & (df["变压器剩余容量_kW"] > 0)
        )
        strategy_feas = float(df["可充标记"].mean())

        score = min(max(
            max(0, corr_high) * 0.4
            + max(0, -corr_low) * 0.2
            + charge_feas * 0.2
            + strategy_feas * 0.2,
            0), 1)

        if score >= 0.75:
            comment = "匹配性高，储能策略可有效执行。"
        elif score >= 0.55:
            comment = "匹配性中等，可部署储能但需优化策略。"
        elif score >= 0.35:
            comment = "匹配性偏弱，建议小容量高功率或策略优化。"
        else:
            comment = "匹配性不足，不建议建设储能。"

        return MatchingAnalysis(
            high_price_load_corr=corr_high,
            low_price_load_corr=corr_low,
            low_price_charge_feasibility=charge_feas,
            strategy_executability=strategy_feas,
            score=score,
            recommendation=comment,
        )

    # ----------------------------------------------------------
    # 策略推荐
    # ----------------------------------------------------------
    def _recommend_strategy(
        self, price_info: PriceAnalysis, load_info: LoadAnalysis,
        matching: MatchingAnalysis,
    ) -> StorageStrategyRecommendation:
        delta_p = price_info.daily_spread
        peak_load = load_info.max_kw
        score = matching.score

        if score < 0.35 or delta_p < 0.15:
            return StorageStrategyRecommendation(
                category="not_recommended",
                power_range_kw=(0, 0),
                capacity_range_kwh=(0, 0),
                description="电价差和可执行窗口不足",
            )

        if score < 0.7:
            p1, p2 = 0.2 * peak_load, 0.4 * peak_load
            c1, c2 = 0.5 * p1, 1.0 * p2
            return StorageStrategyRecommendation(
                category="small_high_power",
                power_range_kw=(round(p1), round(p2)),
                capacity_range_kwh=(round(c1), round(c2)),
                description="小容量高功率（削峰型）",
            )

        p1, p2 = 0.15 * peak_load, 0.3 * peak_load
        c1, c2 = 2 * p1, 4 * p2
        return StorageStrategyRecommendation(
            category="medium_large",
            power_range_kw=(round(p1), round(p2)),
            capacity_range_kwh=(round(c1), round(c2)),
            description="中大容量（削峰 + 套利）",
        )

    # ----------------------------------------------------------
    # 月度统计
    # ----------------------------------------------------------
    def _analyze_monthly(self, df: pd.DataFrame) -> pd.DataFrame:
        tc = self.cfg.time_col
        pc = self.cfg.price_col
        lc = self.cfg.load_col

        df = df.copy()
        df["month"] = df[tc].dt.month
        monthly = df.groupby("month").agg(
            月均价=(pc, "mean"),
            月均负荷=(lc, "mean"),
            月最大负荷=(lc, "max"),
        )
        return monthly

    # ----------------------------------------------------------
    # 对齐电价与负荷
    # ----------------------------------------------------------
    def _align(
        self, df_price: pd.DataFrame, df_load: pd.DataFrame | None, mode: str,
    ) -> pd.DataFrame:
        tc = self.cfg.time_col
        pc = self.cfg.price_col
        lc = self.cfg.load_col

        df_price = df_price.copy()
        df_price[tc] = pd.to_datetime(df_price[tc])

        if df_load is None:
            return df_price

        df_load = df_load.copy()
        df_load[tc] = pd.to_datetime(df_load[tc])

        if mode == "inner":
            df = pd.merge(df_price, df_load, on=tc, how="inner")
        elif mode == "left":
            df = pd.merge(df_load, df_price, on=tc, how="left")
        elif mode == "right":
            df = pd.merge(df_price, df_load, on=tc, how="right")
        elif mode == "nearest":
            df = pd.merge_asof(
                df_load.sort_values(tc),
                df_price.sort_values(tc),
                on=tc, direction="nearest",
                tolerance=pd.Timedelta("30min"),
            )
        else:
            raise ValueError("mode 必须为 inner / left / right / nearest")

        return df.dropna().reset_index(drop=True)

    # ----------------------------------------------------------
    # 主分析入口
    # ----------------------------------------------------------
    def analyze(
        self,
        df_price: pd.DataFrame,
        df_load: pd.DataFrame | None = None,
        align_mode: str = "inner",
    ) -> FeasibilityResult:
        """运行完整的可行性分析。

        Parameters
        ----------
        df_price : DataFrame
            电价数据，必须包含 time_col 和 price_col。
        df_load : DataFrame | None
            负荷数据，必须包含 time_col 和 load_col。为 None 时仅做电价分析。
        align_mode : str
            电价与负荷对齐方式：inner / left / right / nearest。

        Returns
        -------
        FeasibilityResult
        """
        df = self._align(df_price, df_load, align_mode)
        df = self._normalize_units(df)

        price_info, load_info, transformer_info, df2 = self._analyze_basic(df)
        matching = self._analyze_matching(df2)
        strategy = self._recommend_strategy(price_info, load_info, matching)
        monthly = self._analyze_monthly(df2)

        return FeasibilityResult(
            price=price_info,
            load=load_info,
            transformer=transformer_info,
            matching=matching,
            strategy=strategy,
            monthly_stats=monthly,
        )

    # ----------------------------------------------------------
    # 敏感性分析
    # ----------------------------------------------------------
    def analyze_price_sensitivity(
        self,
        df_price: pd.DataFrame,
        df_load: pd.DataFrame | None = None,
        changes: tuple[float, ...] = (-0.2, -0.1, 0, 0.1, 0.2),
    ) -> pd.DataFrame:
        """电价敏感性分析。"""
        rows = []
        for ch in changes:
            df_tmp = df_price.copy()
            df_tmp[self.cfg.price_col] = df_tmp[self.cfg.price_col] * (1 + ch)
            res = self.analyze(df_tmp, df_load)
            rows.append({
                "电价变化比例": ch,
                "匹配性评分": res.matching.score,
                "推荐类型": res.strategy.category,
            })
        return pd.DataFrame(rows)

    def analyze_load_sensitivity(
        self,
        df_price: pd.DataFrame,
        df_load: pd.DataFrame | None = None,
        changes: tuple[float, ...] = (-0.1, 0, 0.1),
    ) -> pd.DataFrame:
        """负荷敏感性分析。"""
        rows = []
        for ch in changes:
            if df_load is None:
                continue
            df_tmp = df_load.copy()
            df_tmp[self.cfg.load_col] = df_tmp[self.cfg.load_col] * (1 + ch)
            res = self.analyze(df_price, df_tmp)
            rows.append({
                "负荷变化比例": ch,
                "匹配性评分": res.matching.score,
                "推荐类型": res.strategy.category,
            })
        return pd.DataFrame(rows)

    def analyze_transformer_sensitivity(
        self,
        df_price: pd.DataFrame,
        df_load: pd.DataFrame | None = None,
        changes: tuple[float, ...] = (-0.1, 0, 0.1),
    ) -> pd.DataFrame:
        """变压器容量敏感性分析。"""
        base_kva = self.cfg.transformer_kva
        rows = []
        for ch in changes:
            self.cfg.transformer_kva = base_kva * (1 + ch)
            res = self.analyze(df_price, df_load)
            rows.append({
                "变压器容量变化比例": ch,
                "匹配性评分": res.matching.score,
                "推荐类型": res.strategy.category,
            })
        self.cfg.transformer_kva = base_kva
        return pd.DataFrame(rows)
