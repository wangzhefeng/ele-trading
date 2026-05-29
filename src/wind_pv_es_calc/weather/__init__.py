# -*- coding: utf-8 -*-

# ***************************************************
# * File        : __init__.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-28
# * Version     : 2.0.052814
# * Description : weather 包统一导出
# * Link        : link
# * Requirement : numpy, pandas, scipy, scikit-learn
# ***************************************************

# Data I/O & simulation
from wind_pv_es_calc.weather.weather import (
    # Constants
    WEATHER_VARS,
    DEFAULT_LAG,
    DEFAULT_QUERY_LIMIT,
    # Simulated data
    make_sample_weather_dataset,
    make_sample_load_data,
    # NetCDF
    NetCDFToJSON,
    # MongoDB
    WeatherMongoClient,
    WeatherMongoReader,
    # Simulator
    WeatherSimulator,
    # Batch operations
    get_real_for_points,
    read_measured_folder,
)

# Analysis
from wind_pv_es_calc.weather.analysis import (
    # Module constants
    DEFAULT_SCORE_WEIGHTS,
    # Correlation
    compute_correlations,
    compute_multi_lag_correlations,
    pick_best_correlation,
    # Sensitivity
    build_sensitivity_score,
    cluster_and_select,
    build_regression_factor,
    # Spatial
    rbf_interpolate,
    kriging_interpolate,
    # Visualization
    plot_spatial_heatmap,
    plot_rbf_heatmap,
    plot_cluster_map,
    # Clustering
    run_clustering,
    cluster_corr_points,
    match_points_to_centers,
    # Weights
    spatial_join_nearest,
    compute_spatial_weights,
    compute_cluster_weights,
    select_top_centers,
    compute_center_weights,
    match_centers_to_city,
    # Pipeline
    run_analysis_pipeline,
)

__all__ = [
    # weather.py
    "WEATHER_VARS",
    "DEFAULT_LAG",
    "DEFAULT_QUERY_LIMIT",
    "make_sample_weather_dataset",
    "make_sample_load_data",
    "NetCDFToJSON",
    "WeatherMongoClient",
    "WeatherMongoReader",
    "WeatherSimulator",
    "get_real_for_points",
    "read_measured_folder",
    # analysis.py
    "DEFAULT_SCORE_WEIGHTS",
    "compute_correlations",
    "compute_multi_lag_correlations",
    "pick_best_correlation",
    "build_sensitivity_score",
    "cluster_and_select",
    "build_regression_factor",
    "rbf_interpolate",
    "kriging_interpolate",
    "plot_spatial_heatmap",
    "plot_rbf_heatmap",
    "plot_cluster_map",
    "run_clustering",
    "cluster_corr_points",
    "match_points_to_centers",
    "spatial_join_nearest",
    "compute_spatial_weights",
    "compute_cluster_weights",
    "select_top_centers",
    "compute_center_weights",
    "match_centers_to_city",
    "run_analysis_pipeline",
]
