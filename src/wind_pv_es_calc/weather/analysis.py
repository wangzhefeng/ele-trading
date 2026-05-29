# -*- coding: utf-8 -*-

# ***************************************************
# * File        : analysis.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-28
# * Version     : 2.0.052814
# * Description : 气象-负荷相关性分析管线：
# *               相关性分析 → 空间插值 → 聚类 → 权重计算
# * Link        : link
# * Requirement : numpy, pandas, scipy, scikit-learn, matplotlib; 可选: pykrige
# ***************************************************

# python libraries
import os
import sys
import warnings
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Union

ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)

warnings.filterwarnings("ignore")

# 核心依赖
import numpy as np
import pandas as pd

from scipy.interpolate import Rbf

from sklearn.cluster import KMeans, DBSCAN
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from sklearn.linear_model import LinearRegression, Ridge

import matplotlib.pyplot as plt
import seaborn as sns

# 可选依赖：克里金插值
try:
    from pykrige.ok import OrdinaryKriging
    HAS_PYKRIGE = True
except ImportError:
    HAS_PYKRIGE = False

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

WEATHER_VARS: List[str] = ["t_avg", "rh", "ssrd", "ws", "wd"]
DEFAULT_LAG: int = 6
# 相关性→综合得分的默认权重（基于领域经验）
DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {
    "t_avg": 0.40,
    "ws": 0.25,
    "rh": 0.20,
    "ssrd": 0.15,
}


# ===========================================================================
# Section 1: Correlation Analysis
# ===========================================================================

def _safe_corr(series_a: pd.Series, series_b: pd.Series, method: str) -> float:
    """安全计算相关系数，处理零方差和 NaN 情况。"""
    mask = series_a.notna() & series_b.notna()
    if mask.sum() < 3:
        return 0.0
    a, b = series_a[mask], series_b[mask]
    if a.std() == 0 or b.std() == 0:
        return 0.0
    result = a.corr(b, method=method)
    return 0.0 if pd.isna(result) else float(result)


def compute_correlations(
    df_weather: pd.DataFrame,
    df_load: pd.DataFrame,
    weather_cols: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    对每个格点 (lat, lon) 计算所有气象变量与负荷的相关系数。

    Parameters
    ----------
    df_weather : pd.DataFrame
        需包含列: ['datetime', 'lat', 'lon'] + 气象变量列
    df_load : pd.DataFrame
        需包含列: ['datetime', 'load']
    weather_cols : list of str, optional
        要分析的气象变量，默认 WEATHER_VARS

    Returns
    -------
    pd.DataFrame
        每行一个格点，列为 lat, lon, {var}_pearson, {var}_spearman, {var}_kendall
    """
    if weather_cols is None:
        weather_cols = WEATHER_VARS

    # 单次 merge，避免逐行 join
    df_merge = df_weather.merge(df_load, on="datetime", how="inner")
    if df_merge.empty:
        return pd.DataFrame()

    results = []
    for (lat, lon), group in df_merge.groupby(["lat", "lon"]):
        item: Dict[str, Any] = {"lat": float(lat), "lon": float(lon)}
        for col in weather_cols:
            if col not in group.columns:
                item[f"{col}_pearson"] = 0.0
                item[f"{col}_spearman"] = 0.0
                item[f"{col}_kendall"] = 0.0
                continue
            item[f"{col}_pearson"] = _safe_corr(group[col], group["load"], "pearson")
            item[f"{col}_spearman"] = _safe_corr(group[col], group["load"], "spearman")
            item[f"{col}_kendall"] = _safe_corr(group[col], group["load"], "kendall")
        results.append(item)

    return pd.DataFrame(results)


def compute_multi_lag_correlations(
    df_weather: pd.DataFrame,
    df_load: pd.DataFrame,
    max_lag: int = DEFAULT_LAG,
    weather_vars: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    对每个格点和气象变量做滞后相关性分析（lag 0 ~ max_lag）。

    Parameters
    ----------
    df_weather : pd.DataFrame
        需包含列: ['datetime', 'lat', 'lon'] + 气象变量列
    df_load : pd.DataFrame
        需包含列: ['datetime', 'load']
    max_lag : int
        最大滞后小时数，默认 6
    weather_vars : list of str, optional
        要分析的气象变量，默认 WEATHER_VARS

    Returns
    -------
    pd.DataFrame
        列: lat, lon, variable, lag, correlation
    """
    if weather_vars is None:
        weather_vars = WEATHER_VARS

    results: List[Dict[str, Any]] = []

    for (lat, lon), df_sub in df_weather.groupby(["lat", "lon"]):
        df_merge = df_sub.merge(df_load, on="datetime", how="inner")
        if df_merge.empty:
            continue

        for var in weather_vars:
            if var not in df_merge.columns:
                continue

            # 预计算所有滞后值，避免多次 shift + 列覆盖
            for lag in range(max_lag + 1):
                shifted = df_merge[var].shift(lag)
                corr = _safe_corr(shifted, df_merge["load"], "pearson")
                results.append({
                    "lat": lat,
                    "lon": lon,
                    "variable": var,
                    "lag": lag,
                    "correlation": abs(corr),
                })

    return pd.DataFrame(results)


def pick_best_correlation(df_corr: pd.DataFrame) -> pd.DataFrame:
    """
    从滞后分析结果中挑选每个格点最相关的气象指标及滞后时间。

    Parameters
    ----------
    df_corr : pd.DataFrame
        compute_multi_lag_correlations 的输出

    Returns
    -------
    pd.DataFrame
        每行一个格点，包含最佳 variable, lag, correlation
    """
    idx = df_corr.groupby(["lat", "lon"])["correlation"].idxmax()
    return (
        df_corr.loc[idx]
        .reset_index(drop=True)
        .sort_values("correlation", ascending=False)
    )


# ===========================================================================
# Section 2: Sensitivity & Factor Engineering
# ===========================================================================

def build_sensitivity_score(
    df_corr: pd.DataFrame,
    factors: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    综合 Pearson / Spearman / Kendall 三种相关系数，
    为每个格点和每个气象因子计算敏感性得分。

    Parameters
    ----------
    df_corr : pd.DataFrame
        compute_correlations 的输出
    factors : list of str, optional
        气象因子名，默认 WEATHER_VARS

    Returns
    -------
    pd.DataFrame
        添加了 {factor}_score 和 total_score 列
    """
    if factors is None:
        factors = WEATHER_VARS

    df = df_corr.copy()
    for f in factors:
        cols = [f"{f}_{m}" for m in ("pearson", "spearman", "kendall")]
        available = [c for c in cols if c in df.columns]
        if available:
            df[f"{f}_score"] = df[available].abs().mean(axis=1)
        else:
            df[f"{f}_score"] = 0.0

    score_cols = [f"{f}_score" for f in factors if f"{f}_score" in df.columns]
    df["total_score"] = df[score_cols].mean(axis=1) if score_cols else 0.0
    return df


def cluster_and_select(
    df_top: pd.DataFrame,
    k: int = 8,
    random_state: int = 42,
) -> pd.DataFrame:
    """
    对高敏感性格点做空间聚类，每类选 total_score 最高的代表。

    Parameters
    ----------
    df_top : pd.DataFrame
        已按 total_score 排序的 DataFrame，需含 lat, lon, total_score
    k : int
        聚类数
    random_state : int
        随机种子

    Returns
    -------
    pd.DataFrame
        每类一个代表格点
    """
    coords = df_top[["lat", "lon"]].values
    kmeans = KMeans(n_clusters=k, random_state=random_state, n_init="auto")
    df_top = df_top.copy()
    df_top["cluster"] = kmeans.fit_predict(coords)

    selected = []
    for _, dfc in df_top.groupby("cluster"):
        best = dfc.sort_values("total_score", ascending=False).head(1)
        selected.append(best)

    return pd.concat(selected, ignore_index=True)


def build_regression_factor(
    df_weather: pd.DataFrame,
    df_load: pd.DataFrame,
    df_selected: pd.DataFrame,
    factor: str,
) -> pd.Series:
    """
    仅为选定格点，用正约束线性回归合成气象因子。

    Parameters
    ----------
    df_weather : pd.DataFrame
        天气数据（含 datetime, lat, lon, 变量列）
    df_load : pd.DataFrame
        负荷数据（含 datetime, load）
    df_selected : pd.DataFrame
        选定格点（含 lat, lon）
    factor : str
        气象变量名

    Returns
    -------
    pd.Series
        合成的有效气象因子序列，name = "{factor}_eff"
    """
    # 1) 仅保留选定格点
    df_sub = df_weather.merge(
        df_selected[["lat", "lon"]].drop_duplicates(),
        on=["lat", "lon"],
        how="inner",
    )

    if df_sub.empty or factor not in df_sub.columns:
        return pd.Series(name=f"{factor}_eff", dtype=float)

    # 2) 展开为列：datetime × 多格点
    df_w = df_sub.pivot_table(
        index="datetime", columns=["lat", "lon"], values=factor
    )
    df_w = df_w.sort_index().ffill().bfill()
    # 展平 MultiIndex 列名，避免 merge 级别冲突
    df_w.columns = [f"g{i}" for i in range(len(df_w.columns))]
    w_cols = list(df_w.columns)

    # 3) 与负荷合并
    df_w = df_w.reset_index()
    df_m = df_w.merge(df_load, on="datetime", how="inner")

    if df_m.empty or df_m["load"].std() == 0:
        return pd.Series(name=f"{factor}_eff", dtype=float)

    X = df_m[w_cols].values
    y = df_m["load"].values

    if X.shape[1] == 0:
        return pd.Series(name=f"{factor}_eff", dtype=float)

    # 4) 正约束回归求权重（Ridge 防止共线不稳定）
    try:
        model = LinearRegression(positive=True, fit_intercept=False)
        model.fit(X, y)
    except (ValueError, np.linalg.LinAlgError):
        model = Ridge(positive=True, alpha=1e-3, fit_intercept=False)
        model.fit(X, y)

    w_sum = model.coef_.sum()
    if w_sum == 0:
        w = np.ones(X.shape[1]) / X.shape[1]
    else:
        w = model.coef_ / w_sum

    # 5) 合成气象因子
    df_out = df_m[w_cols].values.dot(w)
    return pd.Series(df_out, index=df_m["datetime"].values, name=f"{factor}_eff")


# ===========================================================================
# Section 3: Spatial Interpolation
# ===========================================================================

def rbf_interpolate(
    df_corr: pd.DataFrame,
    value_col: str = "max_abs_corr",
    grid_size: int = 200,
    function: str = "linear",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    RBF（径向基函数）空间插值。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 lon, lat 和 value_col 列
    value_col : str
        要插值的列名
    grid_size : int
        输出网格分辨率
    function : str
        RBF 核函数类型: 'linear', 'cubic', 'thin_plate', 'multiquadric' 等

    Returns
    -------
    grid_lon, grid_lat, grid_val : np.ndarray
    """
    lon = df_corr["lon"].values
    lat = df_corr["lat"].values
    val = df_corr[value_col].values

    rbf = Rbf(lon, lat, val, function=function)

    lon_lin = np.linspace(lon.min(), lon.max(), grid_size)
    lat_lin = np.linspace(lat.min(), lat.max(), grid_size)
    grid_lon, grid_lat = np.meshgrid(lon_lin, lat_lin)
    grid_val = rbf(grid_lon, grid_lat)

    return grid_lon, grid_lat, grid_val


def kriging_interpolate(
    df_corr: pd.DataFrame,
    value_col: str = "max_abs_corr",
    grid_size: int = 200,
    variogram_model: str = "spherical",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    克里金空间插值（需要 pykrige）。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 lon, lat 和 value_col 列
    value_col : str
        要插值的列名
    grid_size : int
        输出网格分辨率
    variogram_model : str
        变异函数模型: 'spherical', 'linear', 'exponential', 'gaussian'

    Returns
    -------
    grid_lon, grid_lat, grid_val : np.ndarray
    """
    if not HAS_PYKRIGE:
        raise ImportError(
            "pykrige 未安装，无法使用克里金插值。请运行: pip install pykrige"
        )

    lon = df_corr["lon"].values
    lat = df_corr["lat"].values
    val = df_corr[value_col].values

    OK = OrdinaryKriging(lon, lat, val, variogram_model=variogram_model)

    lon_lin = np.linspace(lon.min(), lon.max(), grid_size)
    lat_lin = np.linspace(lat.min(), lat.max(), grid_size)

    grid_val, _ = OK.execute("grid", lon_lin, lat_lin)
    grid_lon, grid_lat = np.meshgrid(lon_lin, lat_lin)

    return grid_lon, grid_lat, grid_val


# ===========================================================================
# Section 4: Visualization
# ===========================================================================

def plot_spatial_heatmap(
    df_corr: pd.DataFrame,
    value_col: str = "max_abs_corr",
    ax: Optional[plt.Axes] = None,
) -> plt.Axes:
    """
    绘制气象-负荷相关性空间热力图。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 lat, lon 和 value_col 列
    value_col : str
        要展示的列名
    ax : matplotlib Axes, optional
        指定绘图轴

    Returns
    -------
    ax : matplotlib Axes
    """
    pivot = df_corr.pivot_table(
        index="lat", columns="lon", values=value_col
    )
    if ax is None:
        _, ax = plt.subplots(figsize=(12, 8))
    sns.heatmap(pivot, cmap="coolwarm", annot=False, ax=ax)
    ax.set_title(f"气象-负荷相关性空间热力图 ({value_col})")
    return ax


def plot_rbf_heatmap(
    df_corr: pd.DataFrame,
    value_col: str = "max_abs_corr",
    function: str = "linear",
    grid_size: int = 200,
    ax: Optional[plt.Axes] = None,
) -> plt.Axes:
    """
    RBF 插值空间热力图，叠加原始散点。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 lon, lat 和 value_col 列
    value_col : str
        要展示的列名
    function : str
        RBF 核函数类型
    grid_size : int
        输出网格分辨率
    ax : matplotlib Axes, optional

    Returns
    -------
    ax : matplotlib Axes
    """
    grid_lon, grid_lat, grid_val = rbf_interpolate(
        df_corr, value_col=value_col,
        grid_size=grid_size, function=function,
    )

    if ax is None:
        _, ax = plt.subplots(figsize=(10, 8))

    mesh = ax.pcolormesh(
        grid_lon, grid_lat, grid_val,
        cmap="coolwarm", shading="auto",
    )
    cbar = plt.colorbar(mesh, ax=ax)
    cbar.set_label(value_col)

    ax.scatter(
        df_corr["lon"], df_corr["lat"],
        c=df_corr[value_col], cmap="coolwarm",
        edgecolors="black", s=50,
    )

    ax.set_xlabel("经度")
    ax.set_ylabel("纬度")
    ax.set_title(f"{value_col} 空间影响强度（RBF 插值）")
    return ax


def plot_cluster_map(
    df_corr: pd.DataFrame,
    cluster_col: str = "cluster",
    ax: Optional[plt.Axes] = None,
) -> plt.Axes:
    """
    绘制聚类结果的空间分布图。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 lon, lat 和 cluster_col 列
    cluster_col : str
        聚类标签列名
    ax : matplotlib Axes, optional

    Returns
    -------
    ax : matplotlib Axes
    """
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 6))
    scatter = ax.scatter(
        df_corr["lon"], df_corr["lat"],
        c=df_corr[cluster_col], cmap="tab10", s=50,
    )
    plt.colorbar(scatter, ax=ax)
    ax.set_xlabel("经度")
    ax.set_ylabel("纬度")
    ax.set_title("气象-负荷相关性区域聚类")
    return ax


# ===========================================================================
# Section 5: Clustering
# ===========================================================================

def run_clustering(
    df_corr: pd.DataFrame,
    method: str = "kmeans",
    n_clusters: int = 4,
    feature_columns: Optional[List[str]] = None,
    random_state: int = 42,
) -> pd.DataFrame:
    """
    对相关性结果做聚类分析。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含相关性指标列
    method : str
        'kmeans' 或 'dbscan'
    n_clusters : int
        KMeans 聚类数
    feature_columns : list of str, optional
        聚类特征列，默认自动选取所有 pearson 列
    random_state : int
        随机种子

    Returns
    -------
    pd.DataFrame
        添加了 'cluster' 列的 DataFrame
    """
    if feature_columns is None:
        feature_columns = [c for c in df_corr.columns if "pearson" in c]

    X = df_corr[feature_columns].fillna(0).values
    X_scaled = StandardScaler().fit_transform(X)

    df_res = df_corr.copy()

    if method == "kmeans":
        km = KMeans(n_clusters=n_clusters, random_state=random_state,
                     n_init="auto")
        labels = km.fit_predict(X_scaled)
        df_res["cluster"] = labels
    elif method == "dbscan":
        db = DBSCAN(eps=0.7, min_samples=3)
        labels = db.fit_predict(X_scaled)
        df_res["cluster"] = labels
    else:
        raise ValueError(f"不支持的聚类方法: {method}，可选 'kmeans' 或 'dbscan'")

    return df_res


def cluster_corr_points(
    df_corr: pd.DataFrame,
    n_clusters: int = 10,
    random_state: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    对相关性格点按空间位置聚类，返回聚类结果和中心坐标。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 lat, lon 列
    n_clusters : int
        聚类数
    random_state : int
        随机种子

    Returns
    -------
    df_corr : pd.DataFrame
        添加了 'cluster' 列
    centers : pd.DataFrame
        聚类中心，列 ['lat', 'lon', 'cluster']
    """
    coords = df_corr[["lat", "lon"]].values
    kmeans = KMeans(n_clusters=n_clusters, random_state=random_state,
                     n_init="auto").fit(coords)

    df_corr = df_corr.copy()
    df_corr["cluster"] = kmeans.labels_

    centers = pd.DataFrame(kmeans.cluster_centers_, columns=["lat", "lon"])
    centers["cluster"] = centers.index

    return df_corr, centers


def match_points_to_centers(
    df_points: pd.DataFrame,
    centers: pd.DataFrame,
) -> pd.DataFrame:
    """
    将城镇点通过最近邻匹配到聚类中心。

    Parameters
    ----------
    df_points : pd.DataFrame
        含 lat, lon 列（城镇点）
    centers : pd.DataFrame
        含 lat, lon, cluster 列（聚类中心）

    Returns
    -------
    pd.DataFrame
        添加了 'cluster' 列的城镇点
    """
    coords_points = df_points[["lat", "lon"]].values
    coords_centers = centers[["lat", "lon"]].values

    nbrs = NearestNeighbors(n_neighbors=1).fit(coords_centers)
    _, idx = nbrs.kneighbors(coords_points)

    df_points = df_points.copy()
    df_points["cluster"] = idx.flatten()
    return df_points


# ===========================================================================
# Section 6: Weight Computation
# ===========================================================================

def spatial_join_nearest(
    df_points: pd.DataFrame,
    df_corr: pd.DataFrame,
) -> pd.DataFrame:
    """
    将城镇点匹配到最近的相关性格点。

    Parameters
    ----------
    df_points : pd.DataFrame
        城镇点（areacode, lat, lon）
    df_corr : pd.DataFrame
        格点相关性结果（lat, lon, ...pearson 列...）

    Returns
    -------
    pd.DataFrame
        城镇点 + 最近格点的相关性数据
    """
    coords_points = df_points[["lat", "lon"]].values
    coords_corr = df_corr[["lat", "lon"]].values

    nbrs = NearestNeighbors(n_neighbors=1, algorithm="ball_tree")
    nbrs.fit(coords_corr)
    _, indices = nbrs.kneighbors(coords_points)

    df_points = df_points.copy()
    df_points["nearest_idx"] = indices.flatten()

    df_merge = df_points.join(
        df_corr.iloc[indices.flatten()].reset_index(drop=True),
        rsuffix="_corr",
    )
    return df_merge


def compute_spatial_weights(
    df_merged: pd.DataFrame,
    w_temp: float = 0.40,
    w_ws: float = 0.25,
    w_rh: float = 0.20,
    w_ssrd: float = 0.15,
) -> pd.DataFrame:
    """
    基于最近格点相关性计算各城镇权重（最近邻方案）。

    Parameters
    ----------
    df_merged : pd.DataFrame
        spatial_join_nearest 的输出
    w_temp, w_ws, w_rh, w_ssrd : float
        各气象因子的加权系数

    Returns
    -------
    pd.DataFrame
        列: areacode, lat, lon, score, weight
    """
    df = df_merged.copy()

    df["score"] = 0.0
    if "t_avg_pearson" in df.columns:
        df["score"] += w_temp * df["t_avg_pearson"].abs()
    if "ws_pearson" in df.columns:
        df["score"] += w_ws * df["ws_pearson"].abs()
    if "rh_pearson" in df.columns:
        df["score"] += w_rh * df["rh_pearson"].abs()
    if "ssrd_pearson" in df.columns:
        df["score"] += w_ssrd * df["ssrd_pearson"].abs()

    total = df["score"].sum()
    df["weight"] = df["score"] / total if total > 0 else 1.0 / len(df)

    return df[["areacode", "lat", "lon", "score", "weight"]]


def compute_cluster_weights(
    df_corr: pd.DataFrame,
    df_points: pd.DataFrame,
    w_temp: float = 0.40,
    w_ws: float = 0.25,
    w_rh: float = 0.20,
    w_ssrd: float = 0.15,
) -> pd.DataFrame:
    """
    基于聚类中心+城镇匹配计算权重（聚类方案）。

    对 df_corr 按 cluster 分组，计算每类平均相关性得分，
    再匹配回城镇点并归一化为权重。

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 cluster 列和 pearson 相关性列
    df_points : pd.DataFrame
        含 cluster 列的城镇点
    w_temp, w_ws, w_rh, w_ssrd : float
        各气象因子的加权系数

    Returns
    -------
    pd.DataFrame
        城镇点 + score, weight 列
    """

    def _score_group(g: pd.DataFrame) -> float:
        s = 0.0
        if "t_avg_pearson" in g.columns:
            s += w_temp * g["t_avg_pearson"].abs().mean()
        if "ws_pearson" in g.columns:
            s += w_ws * g["ws_pearson"].abs().mean()
        if "rh_pearson" in g.columns:
            s += w_rh * g["rh_pearson"].abs().mean()
        if "ssrd_pearson" in g.columns:
            s += w_ssrd * g["ssrd_pearson"].abs().mean()
        return s

    df_scores = (
        df_corr.groupby("cluster")
        .apply(_score_group)
        .reset_index(name="score")
    )

    total = df_scores["score"].sum()
    df_scores["weight"] = (
        df_scores["score"] / total if total > 0 else 1.0 / len(df_scores)
    )

    df_out = df_points.merge(df_scores, on="cluster", how="left")
    return df_out


def select_top_centers(
    df_corr: pd.DataFrame,
    n_centers: int = 10,
    weights: Optional[Dict[str, float]] = None,
    random_state: int = 42,
) -> pd.DataFrame:
    """
    选出 N 个代表性气象中心格点。

    1. 用加权公式计算每个格点的综合得分
    2. 空间 KMeans 聚类
    3. 每类选得分最高的格点作为代表

    Parameters
    ----------
    df_corr : pd.DataFrame
        含 lat, lon 和 {var}_pearson 列
    n_centers : int
        选出的中心数量
    weights : dict, optional
        各变量权重，默认 {"t_avg": 0.40, "ws": 0.25, "rh": 0.20, "ssrd": 0.15}
    random_state : int
        随机种子

    Returns
    -------
    pd.DataFrame
        代表性中心格点，含 lat, lon, score, cluster, weight
    """
    if weights is None:
        weights = DEFAULT_SCORE_WEIGHTS

    df = df_corr.copy()

    # 加权综合得分
    df["score"] = 0.0
    for var, w in weights.items():
        col = f"{var}_pearson"
        if col in df.columns:
            df["score"] += df[col].abs() * w

    # 空间聚类（聚类数不超过样本数）
    n_clusters = min(n_centers, len(df))
    coords = df[["lat", "lon"]].values
    kmeans = KMeans(n_clusters=n_clusters, random_state=random_state,
                     n_init="auto").fit(coords)
    df["cluster"] = kmeans.labels_

    # 每类选得分最高
    centers = (
        df.sort_values("score", ascending=False)
        .groupby("cluster")
        .head(1)
        .reset_index(drop=True)
    )

    # 归一化权重
    total = centers["score"].sum()
    centers["weight"] = (
        centers["score"] / total if total > 0 else 1.0 / len(centers)
    )

    return centers


def compute_center_weights(df_centers: pd.DataFrame) -> pd.DataFrame:
    """
    简单基于中心格点的 score 归一化为权重。

    Parameters
    ----------
    df_centers : pd.DataFrame
        含 score 列的中心格点

    Returns
    -------
    pd.DataFrame
        添加了 weight 列
    """
    df = df_centers.copy()
    total = df["score"].sum()
    df["weight"] = df["score"] / total if total > 0 else 1.0 / len(df)
    return df


def match_centers_to_city(
    df_centers: pd.DataFrame,
    df_city: pd.DataFrame,
) -> pd.DataFrame:
    """
    将代表性中心格点通过最近邻匹配到实际城市编码。

    Parameters
    ----------
    df_centers : pd.DataFrame
        含 lat, lon, score 列的代表性格点
    df_city : pd.DataFrame
        城市编码表，含 lat, lon, areacode, city 等

    Returns
    -------
    pd.DataFrame
        中心格点 + 对应城市信息 + 匹配距离（度）
    """
    nbrs = NearestNeighbors(n_neighbors=1, algorithm="ball_tree").fit(
        df_city[["lat", "lon"]]
    )
    dists, idx = nbrs.kneighbors(df_centers[["lat", "lon"]])

    match = df_city.iloc[idx.flatten()].reset_index(drop=True)
    result = pd.concat(
        [df_centers.reset_index(drop=True), match], axis=1
    )
    result["match_distance_deg"] = dists.flatten()
    return result


# ===========================================================================
# Section 7: Demo Pipeline
# ===========================================================================

def run_analysis_pipeline(
    df_weather: pd.DataFrame,
    df_load: pd.DataFrame,
    df_city: Optional[pd.DataFrame] = None,
    n_centers: int = 5,
) -> Dict[str, Any]:
    """
    运行完整的相关性分析 → 中心选取 → 权重计算管线。

    Parameters
    ----------
    df_weather : pd.DataFrame
        天气数据（含 datetime, lat, lon + 气象变量）
    df_load : pd.DataFrame
        负荷数据（含 datetime, load）
    df_city : pd.DataFrame, optional
        城市编码表，用于最终匹配
    n_centers : int
        选取的中心格点数

    Returns
    -------
    dict
        包含所有中间结果:
        - 'df_corr': 相关性 DataFrame
        - 'df_lag': 滞后相关性 DataFrame
        - 'df_best': 最佳滞后
        - 'df_centers': 代表性中心格点
        - 'df_final': 最终结果（如提供 df_city）
    """
    results: Dict[str, Any] = {}

    # 1. 相关性分析
    results["df_corr"] = compute_correlations(df_weather, df_load)

    # 2. 滞后相关性
    results["df_lag"] = compute_multi_lag_correlations(df_weather, df_load)

    # 3. 最佳滞后
    if not results["df_lag"].empty:
        results["df_best"] = pick_best_correlation(results["df_lag"])

    # 4. 选取代表性格点
    if not results["df_corr"].empty:
        results["df_centers"] = select_top_centers(
            results["df_corr"], n_centers=n_centers
        )

        # 5. 匹配城市
        if df_city is not None:
            results["df_final"] = match_centers_to_city(
                results["df_centers"], df_city
            )

    return results


def main():
    """演示：使用 weather 模块的模拟数据运行完整分析管线。"""
    from wind_pv_es_calc.weather.weather import (
        make_sample_weather_dataset,
        make_sample_load_data,
        WeatherSimulator,
        get_real_for_points,
    )

    print("=" * 60)
    print("气象-负荷相关性分析管线 — 模拟数据演示")
    print("=" * 60)

    # ---- 准备数据 ----
    print("\n[1/4] 生成模拟数据...")
    df_load = make_sample_load_data(n_times=24 * 30, seed=42)

    sim = WeatherSimulator(seed=42)
    df_points = pd.DataFrame({
        "areacode": [f"10108010{i}" for i in range(1, 6)],
        "lat": [40.8, 40.6, 39.7, 41.0, 42.0],
        "lon": [111.7, 109.8, 106.8, 114.1, 116.0],
    })
    df_real = get_real_for_points(sim, df_points, 2024010100, 2024013123)

    # 时间格式统一
    df_real["datetime"] = pd.to_datetime(
        df_real["datatime"].astype(str).str.zfill(10), format="%Y%m%d%H"
    )
    df_load["datetime"] = df_load["datetime"]

    # 合并
    df_merger = df_real.dropna(how="any").merge(
        df_load, on="datetime"
    )
    print(f"  合并后数据: {df_merger.shape}")

    # ---- 准备输入 ----
    df_weather = df_merger[["datetime", "lat", "lon", "t_avg", "rh", "ssrd", "ws", "wd"]]
    df_load_input = df_merger[["datetime", "value"]].rename(columns={"value": "load"})

    # ---- 运行管线 ----
    print("\n[2/4] 相关性分析...")
    df_corr = compute_correlations(df_weather, df_load_input)
    print(f"  格点相关性: {df_corr.shape}")

    # 计算 max_abs_corr
    corr_cols = [c for c in df_corr.columns
                 if any(m in c for m in ("pearson", "spearman", "kendall"))]
    df_corr["max_abs_corr"] = df_corr[corr_cols].abs().max(axis=1)

    print("\n[3/4] 选取代表性中心格点...")
    df_centers = select_top_centers(df_corr, n_centers=5)
    print(f"  选出 {len(df_centers)} 个中心格点:")
    print(df_centers[["lat", "lon", "score", "weight"]].to_string(index=False))

    print("\n[4/4] 滞后相关性分析...")
    df_lag = compute_multi_lag_correlations(df_weather, df_load_input, max_lag=6)
    if not df_lag.empty:
        df_best = pick_best_correlation(df_lag)
        print(f"  最佳滞后分析: {df_best.shape[0]} 个格点")
        print(df_best.head().to_string(index=False))

    print("\n" + "=" * 60)
    print("分析管线演示完成。")
    print("=" * 60)


if __name__ == "__main__":
    main()
