"""weather 模块测试。

所有测试使用模拟数据，不依赖 MongoDB 或 NetCDF 文件。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
import pytest
from unittest import mock

from wind_pv_es_calc.weather.weather import (
    make_sample_weather_dataset,
    make_sample_load_data,
    NetCDFToJSON,
    WeatherMongoClient,
    WeatherMongoReader,
    WeatherSimulator,
    get_real_for_points,
    read_measured_folder,
)
from wind_pv_es_calc.weather.analysis import (
    compute_correlations,
    compute_multi_lag_correlations,
    pick_best_correlation,
    build_sensitivity_score,
    cluster_and_select,
    build_regression_factor,
    rbf_interpolate,
    run_clustering,
    plot_spatial_heatmap,
    plot_cluster_map,
    plot_rbf_heatmap,
    cluster_corr_points,
    match_points_to_centers,
    spatial_join_nearest,
    compute_spatial_weights,
    compute_cluster_weights,
    select_top_centers,
    compute_center_weights,
    match_centers_to_city,
    run_analysis_pipeline,
    _safe_corr,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def sample_dataset():
    """24 小时 × 10×10 格点的小型模拟数据集。"""
    return make_sample_weather_dataset(n_times=24, seed=42)


@pytest.fixture
def sample_load():
    """30 天逐小时模拟负荷数据。"""
    return make_sample_load_data(n_times=24 * 30, seed=42)


@pytest.fixture
def sample_weather_df(sample_dataset):
    """将 xr.Dataset 展平为 DataFrame 用于分析测试。"""
    ds = sample_dataset
    df = ds.to_dataframe().reset_index()
    df["datetime"] = df["time"]
    df["datatime"] = df["time"].dt.strftime("%Y%m%d%H").astype(int)
    return df


@pytest.fixture
def sample_load_df(sample_load):
    """负荷 DataFrame 标准格式。"""
    df = sample_load.copy()
    df["load"] = df["value"]
    return df[["datetime", "load"]]


@pytest.fixture
def sample_corr_df(sample_weather_df, sample_load_df):
    """预计算的相关性 DataFrame，用于后续测试。"""
    # 只取部分格点加速测试
    lats = sample_weather_df["lat"].unique()[:3]
    df_w = sample_weather_df[sample_weather_df["lat"].isin(lats)]
    return compute_correlations(df_w, sample_load_df)


# ===========================================================================
# Test: Simulated Data
# ===========================================================================

class TestMakeSampleData:
    def test_dataset_shape(self, sample_dataset):
        ds = sample_dataset
        assert "t_avg" in ds
        assert "rh" in ds
        assert "ssrd" in ds
        assert "ws" in ds
        assert "wd" in ds
        assert "pre" in ds
        assert ds["t_avg"].shape == (24, ds.sizes["lat"], ds.sizes["lon"])

    def test_dataset_units(self, sample_dataset):
        ds = sample_dataset
        assert ds["t_avg"].attrs["units"] == "°C"
        assert ds["rh"].attrs["units"] == "%"

    def test_temperature_range(self, sample_dataset):
        t = sample_dataset["t_avg"].values
        assert -30 < t.min() < 50
        assert t.max() > t.min()

    def test_solar_zero_at_night(self, sample_dataset):
        """夜间（hour 0-5）太阳辐射应为 0。"""
        ds = sample_dataset
        night_ssrd = ds["ssrd"].isel(time=slice(0, 6))
        # 夜间辐照接近 0（可能有极小噪声因为 clip 0 边界）
        assert np.all(night_ssrd.values <= 1.0)

    def test_load_data_shape(self, sample_load):
        assert "datetime" in sample_load.columns
        assert "value" in sample_load.columns
        assert len(sample_load) == 720

    def test_load_data_range(self, sample_load):
        assert sample_load["value"].min() > 0
        assert sample_load["value"].max() < 20000

    def test_reproducibility(self):
        ds1 = make_sample_weather_dataset(seed=42)
        ds2 = make_sample_weather_dataset(seed=42)
        np.testing.assert_array_equal(ds1["t_avg"].values, ds2["t_avg"].values)


# ===========================================================================
# Test: NetCDFToJSON
# ===========================================================================

class TestNetCDFToJSON:
    def test_init_with_dataset(self, sample_dataset):
        parser = NetCDFToJSON(dataset=sample_dataset)
        assert parser.ds is sample_dataset

    def test_init_without_args_raises(self):
        with pytest.raises(ValueError, match="必须提供"):
            NetCDFToJSON()

    def test_extract_units(self, sample_dataset):
        parser = NetCDFToJSON(dataset=sample_dataset)
        units = parser.extract_units()
        assert units["t_avg"] == "°C"
        assert units["rh"] == "%"

    def test_to_dataframe(self, sample_dataset):
        parser = NetCDFToJSON(dataset=sample_dataset)
        df = parser.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert "t_avg" in df.columns
        assert "lat" in df.columns
        assert "lon" in df.columns

    def test_to_json_records(self, sample_dataset):
        parser = NetCDFToJSON(dataset=sample_dataset)
        records = parser.to_json_records()
        assert isinstance(records, list)
        assert isinstance(records[0], dict)

    def test_to_api_format(self, sample_dataset):
        parser = NetCDFToJSON(dataset=sample_dataset)
        result = parser.to_api_format()
        assert "data" in result
        assert "units" in result
        assert len(result["data"]) > 0


# ===========================================================================
# Test: WeatherMongoClient / WeatherMongoReader (with mocks)
# ===========================================================================

class TestWeatherMongoClient:
    def test_init_builds_uri(self):
        with mock.patch("wind_pv_es_calc.weather.weather.MongoClient"):
            client = WeatherMongoClient(
                host="testhost", port=12345, database="testdb",
                username="user", password="pass",
            )
            assert "testhost:12345" in client.uri
            assert "testdb" in client.uri

    def test_find_to_df_empty(self):
        with mock.patch("wind_pv_es_calc.weather.weather.MongoClient"):
            client = WeatherMongoClient()
            client.db = mock.MagicMock()
            client.db["test_coll"].find.return_value = []
            df = client.find_to_df("test_coll")
            assert df.empty

    def test_find_to_df_drops_id(self):
        with mock.patch("wind_pv_es_calc.weather.weather.MongoClient"):
            client = WeatherMongoClient()
            client.db = mock.MagicMock()
            client.db["test_coll"].find.return_value = [
                {"_id": "abc", "lat": 40.0, "val": 1.0}
            ]
            df = client.find_to_df("test_coll")
            assert "_id" not in df.columns
            assert "lat" in df.columns

    def test_find_to_json_default_no_id(self):
        with mock.patch("wind_pv_es_calc.weather.weather.MongoClient"):
            client = WeatherMongoClient()
            client.db = mock.MagicMock()
            client.db["test_coll"].find.return_value = []
            result = client.find_to_json("test_coll")
            assert result == []


class TestWeatherMongoReader:
    def test_connect_false_skips_connection(self):
        reader = WeatherMongoReader(connect=False)
        assert reader.client is None
        assert reader.db is None

    def test_query_methods_build_correct_queries(self):
        reader = WeatherMongoReader(connect=False)
        reader.db = mock.MagicMock()
        # MagicMock chain 自动支持 .find().sort().limit()
        result = reader.get_real_by_point_and_time(40.0, 111.0, 2024010100, 2024013123)
        assert isinstance(result, pd.DataFrame)


# ===========================================================================
# Test: WeatherSimulator
# ===========================================================================

class TestWeatherSimulator:
    def test_init_creates_dataset(self):
        sim = WeatherSimulator(seed=42)
        assert sim._ds is not None

    def test_get_real_by_point_and_time(self):
        sim = WeatherSimulator(seed=42)
        df = sim.get_real_by_point_and_time(40.0, 111.0, 2024010100, 2024013123)
        assert not df.empty
        assert "t_avg" in df.columns
        assert "datatime" in df.columns

    def test_get_real_by_point_and_time_v2(self):
        sim = WeatherSimulator(seed=42)
        df = sim.get_real_by_point_and_time_v2(40.0, 111.0, 2024010100, 2024013123)
        assert not df.empty

    def test_get_forecast_by_areacode_and_time(self):
        sim = WeatherSimulator(seed=42)
        df = sim.get_forecast_by_areacode_and_time(
            "101080101", "2024-01-01 00:00:00", "2024-01-07 23:00:00"
        )
        assert not df.empty
        assert "areacode" in df.columns

    def test_get_forecast_multi_areacode(self):
        sim = WeatherSimulator(seed=42)
        df = sim.get_forecast_by_multi_areacode_and_time(
            ["101080101", "101080201"],
            "2024-01-01 00:00:00", "2024-01-07 23:00:00",
            limit=50000,
        )
        assert not df.empty
        assert df["areacode"].nunique() == 2

    def test_get_real_for_points(self):
        sim = WeatherSimulator(seed=42)
        df_points = pd.DataFrame({
            "lat": [40.8, 41.0],
            "lon": [111.7, 114.1],
            "areacode": ["A1", "A2"],
        })
        df = get_real_for_points(sim, df_points, 2024010100, 2024013123)
        assert not df.empty
        assert df["lat"].nunique() <= 2


# ===========================================================================
# Test: Correlation
# ===========================================================================

class TestSafeCorr:
    def test_perfect_correlation(self):
        a = pd.Series([1, 2, 3, 4, 5])
        b = pd.Series([2, 4, 6, 8, 10])
        assert abs(_safe_corr(a, b, "pearson") - 1.0) < 1e-10

    def test_zero_variance(self):
        a = pd.Series([5, 5, 5, 5, 5])
        b = pd.Series([1, 2, 3, 4, 5])
        assert _safe_corr(a, b, "pearson") == 0.0

    def test_too_few_points(self):
        a = pd.Series([1, 2])
        b = pd.Series([3, 4])
        assert _safe_corr(a, b, "pearson") == 0.0

    def test_with_nan(self):
        a = pd.Series([1, 2, np.nan, 4, 5])
        b = pd.Series([2, 4, 6, 8, 10])
        assert abs(_safe_corr(a, b, "pearson") - 1.0) < 1e-10


class TestComputeCorrelations:
    def test_returns_expected_columns(self, sample_weather_df, sample_load_df):
        df = compute_correlations(sample_weather_df, sample_load_df)
        assert "lat" in df.columns
        assert "lon" in df.columns
        assert "t_avg_pearson" in df.columns
        assert "t_avg_spearman" in df.columns
        assert "t_avg_kendall" in df.columns

    def test_empty_weather_returns_empty(self):
        df_w = pd.DataFrame({"datetime": pd.to_datetime([]), "lat": pd.Series(dtype="float64"), "lon": pd.Series(dtype="float64")})
        df_l = pd.DataFrame({"datetime": pd.to_datetime([]), "load": pd.Series(dtype="float64")})
        result = compute_correlations(df_w, df_l)
        assert result.empty

    def test_custom_weather_cols(self, sample_weather_df, sample_load_df):
        df = compute_correlations(
            sample_weather_df, sample_load_df, weather_cols=["t_avg", "ws"]
        )
        assert "t_avg_pearson" in df.columns
        assert "ws_pearson" in df.columns
        assert "rh_pearson" not in df.columns


class TestMultiLagCorrelations:
    def test_returns_lag_structure(self, sample_weather_df, sample_load_df):
        df = compute_multi_lag_correlations(
            sample_weather_df, sample_load_df, max_lag=3,
            weather_vars=["t_avg"],
        )
        assert "lag" in df.columns
        assert "correlation" in df.columns
        assert df["lag"].max() <= 3

    def test_empty_group_handled(self):
        """空格点分组不应报错。"""
        df_w = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=5, freq="h"),
            "lat": [40.0] * 5, "lon": [111.0] * 5, "t_avg": [10.0] * 5,
        })
        df_l = pd.DataFrame({
            "datetime": [pd.Timestamp("2024-02-01")], "load": [1000.0],
        })
        result = compute_multi_lag_correlations(df_w, df_l, max_lag=2)
        assert result.empty


class TestPickBestCorrelation:
    def test_picks_max_per_group(self):
        df = pd.DataFrame({
            "lat": [40.0, 40.0, 41.0, 41.0],
            "lon": [111.0, 111.0, 112.0, 112.0],
            "variable": ["t_avg", "rh", "t_avg", "rh"],
            "lag": [0, 1, 0, 1],
            "correlation": [0.3, 0.5, 0.2, 0.8],
        })
        best = pick_best_correlation(df)
        assert len(best) == 2
        assert best.iloc[0]["correlation"] == 0.8


# ===========================================================================
# Test: Sensitivity & Regression
# ===========================================================================

class TestSensitivityScore:
    def test_adds_score_columns(self, sample_corr_df):
        df = build_sensitivity_score(sample_corr_df)
        assert "t_avg_score" in df.columns
        assert "total_score" in df.columns
        assert df["total_score"].notna().all()

    def test_custom_factors(self, sample_corr_df):
        df = build_sensitivity_score(sample_corr_df, factors=["t_avg"])
        assert "t_avg_score" in df.columns
        assert "rh_score" not in df.columns


class TestClusterAndSelect:
    def test_returns_k_groups(self, sample_corr_df):
        df = build_sensitivity_score(sample_corr_df)
        top = df.sort_values("total_score", ascending=False)
        result = cluster_and_select(top, k=3)
        assert 1 <= len(result) <= 3


class TestBuildRegressionFactor:
    def test_returns_correct_series_name(self, sample_weather_df, sample_load_df, sample_corr_df):
        df = sample_weather_df.copy()
        df_l = sample_load_df.copy()
        # 选一个格点
        selected = df[["lat", "lon"]].drop_duplicates().head(2)
        result = build_regression_factor(df, df_l, selected, "t_avg")
        assert result.name == "t_avg_eff"
        assert len(result) > 0

    def test_empty_selection_returns_empty(self):
        df_w = pd.DataFrame(columns=["datetime", "lat", "lon", "t_avg"])
        df_l = pd.DataFrame({"datetime": [], "load": []})
        selected = pd.DataFrame(columns=["lat", "lon"])
        result = build_regression_factor(df_w, df_l, selected, "t_avg")
        assert result.empty


# ===========================================================================
# Test: Spatial Interpolation
# ===========================================================================

class TestRbfInterpolate:
    def test_returns_correct_shapes(self, sample_corr_df):
        df = sample_corr_df.copy()
        corr_cols = [c for c in df.columns if "pearson" in c]
        df["max_abs_corr"] = df[corr_cols].abs().max(axis=1)
        lon, lat, val = rbf_interpolate(df, grid_size=50, function="linear")
        assert lon.shape == (50, 50)
        assert lat.shape == (50, 50)
        assert val.shape == (50, 50)

    def test_no_nan_in_output(self, sample_corr_df):
        df = sample_corr_df.copy()
        corr_cols = [c for c in df.columns if "pearson" in c]
        df["max_abs_corr"] = df[corr_cols].abs().max(axis=1)
        _, _, val = rbf_interpolate(df, grid_size=30)
        assert not np.any(np.isnan(val))


# ===========================================================================
# Test: Clustering
# ===========================================================================

class TestRunClustering:
    def test_kmeans_adds_cluster_column(self, sample_corr_df):
        df = sample_corr_df.copy()
        df = run_clustering(df, method="kmeans", n_clusters=3)
        assert "cluster" in df.columns
        assert df["cluster"].nunique() <= 3

    def test_dbscan(self, sample_corr_df):
        df = sample_corr_df.copy()
        df = run_clustering(df, method="dbscan")
        assert "cluster" in df.columns

    def test_invalid_method_raises(self, sample_corr_df):
        with pytest.raises(ValueError, match="不支持的聚类方法"):
            run_clustering(sample_corr_df, method="invalid")


class TestClusterCorrPoints:
    def test_returns_centers(self, sample_corr_df):
        df_c, centers = cluster_corr_points(sample_corr_df, n_clusters=3)
        assert "cluster" in df_c.columns
        assert len(centers) == 3
        assert "lat" in centers.columns
        assert "lon" in centers.columns


class TestMatchPointsToCenters:
    def test_matches_correctly(self):
        points = pd.DataFrame({"lat": [40.1, 41.1], "lon": [111.1, 112.1]})
        centers = pd.DataFrame({
            "lat": [40.0, 41.0], "lon": [111.0, 112.0], "cluster": [0, 1],
        })
        result = match_points_to_centers(points, centers)
        assert "cluster" in result.columns
        assert result.iloc[0]["cluster"] == 0


# ===========================================================================
# Test: Weights
# ===========================================================================

class TestSpatialJoinNearest:
    def test_returns_merged_data(self, sample_corr_df):
        points = pd.DataFrame({
            "areacode": ["A1", "A2"],
            "lat": [40.1, 41.1],
            "lon": [111.1, 112.1],
        })
        result = spatial_join_nearest(points, sample_corr_df)
        assert "areacode" in result.columns

    def test_custom_weights(self, sample_corr_df):
        points = pd.DataFrame({
            "areacode": ["A1"],
            "lat": [40.1], "lon": [111.1],
        })
        merged = spatial_join_nearest(points, sample_corr_df)
        weights = compute_spatial_weights(
            merged, w_temp=0.5, w_ws=0.3, w_rh=0.1, w_ssrd=0.1
        )
        assert abs(weights["weight"].sum() - 1.0) < 1e-10

    def test_zero_score_uniform_weights(self):
        """所有相关性为 0 时权重均匀分配。"""
        merged = pd.DataFrame({
            "areacode": ["A1", "A2"],
            "lat": [40.0, 41.0],
            "lon": [111.0, 112.0],
            "t_avg_pearson": [0.0, 0.0],
            "ws_pearson": [0.0, 0.0],
            "rh_pearson": [0.0, 0.0],
            "ssrd_pearson": [0.0, 0.0],
        })
        result = compute_spatial_weights(merged)
        assert abs(result["weight"].sum() - 1.0) < 1e-10
        assert (result["weight"] == 0.5).all()


class TestSelectTopCenters:
    def test_returns_n_centers(self, sample_corr_df):
        centers = select_top_centers(sample_corr_df, n_centers=3)
        assert len(centers) == 3
        assert "score" in centers.columns
        assert "weight" in centers.columns
        assert abs(centers["weight"].sum() - 1.0) < 1e-10

    def test_custom_weights(self, sample_corr_df):
        centers = select_top_centers(
            sample_corr_df, n_centers=3,
            weights={"t_avg": 0.50, "ws": 0.25, "rh": 0.10, "ssrd": 0.15},
        )
        assert len(centers) == 3

    def test_n_centers_exceeds_data(self, sample_corr_df):
        """请求的中心数超过数据点数时不应崩溃。"""
        centers = select_top_centers(sample_corr_df, n_centers=100)
        assert len(centers) > 0


class TestComputeCenterWeights:
    def test_weights_sum_to_one(self):
        df = pd.DataFrame({
            "lat": [40.0, 41.0, 42.0],
            "lon": [111.0, 112.0, 113.0],
            "score": [0.5, 0.3, 0.2],
        })
        result = compute_center_weights(df)
        assert abs(result["weight"].sum() - 1.0) < 1e-10

    def test_zero_scores_uniform(self):
        df = pd.DataFrame({
            "lat": [40.0, 41.0],
            "lon": [111.0, 112.0],
            "score": [0.0, 0.0],
        })
        result = compute_center_weights(df)
        assert (result["weight"] == 0.5).all()


class TestMatchCentersToCity:
    def test_adds_city_info(self, sample_corr_df):
        centers = select_top_centers(sample_corr_df, n_centers=2)
        city_df = pd.DataFrame({
            "areacode": ["101080101", "101080201"],
            "city": ["呼和浩特市", "包头市"],
            "lat": [40.8, 40.6],
            "lon": [111.7, 109.8],
        })
        result = match_centers_to_city(centers, city_df)
        assert "areacode" in result.columns
        assert "city" in result.columns
        assert "match_distance_deg" in result.columns


class TestComputeClusterWeights:
    def test_returns_weights(self, sample_corr_df):
        df_c, centers = cluster_corr_points(sample_corr_df, n_clusters=3)
        points = match_points_to_centers(
            pd.DataFrame({"lat": [40.1, 41.1], "lon": [111.1, 112.1]}),
            centers,
        )
        result = compute_cluster_weights(df_c, points)
        assert "score" in result.columns
        assert "weight" in result.columns


# ===========================================================================
# Test: Visualization (no display)
# ===========================================================================

class TestVisualization:
    def test_plot_spatial_heatmap(self, sample_corr_df):
        import matplotlib
        matplotlib.use("Agg")
        df = sample_corr_df.copy()
        corr_cols = [c for c in df.columns if any(m in c for m in ("pearson", "spearman", "kendall"))]
        df["max_abs_corr"] = df[corr_cols].abs().max(axis=1)
        ax = plot_spatial_heatmap(df, value_col="max_abs_corr")
        assert ax is not None
        import matplotlib.pyplot as plt
        plt.close("all")

    def test_plot_rbf_heatmap(self, sample_corr_df):
        import matplotlib
        matplotlib.use("Agg")
        df = sample_corr_df.copy()
        corr_cols = [c for c in df.columns if any(m in c for m in ("pearson", "spearman", "kendall"))]
        df["max_abs_corr"] = df[corr_cols].abs().max(axis=1)
        ax = plot_rbf_heatmap(df, value_col="max_abs_corr", grid_size=30)
        assert ax is not None
        import matplotlib.pyplot as plt
        plt.close("all")

    def test_plot_cluster_map(self, sample_corr_df):
        import matplotlib
        matplotlib.use("Agg")
        df = run_clustering(sample_corr_df, method="kmeans", n_clusters=2)
        ax = plot_cluster_map(df)
        assert ax is not None
        import matplotlib.pyplot as plt
        plt.close("all")


# ===========================================================================
# Test: Pipeline
# ===========================================================================

class TestRunAnalysisPipeline:
    def test_returns_all_steps(self, sample_weather_df, sample_load_df):
        results = run_analysis_pipeline(
            sample_weather_df, sample_load_df, n_centers=3
        )
        assert "df_corr" in results
        assert "df_lag" in results
        assert "df_best" in results
        assert "df_centers" in results

    def test_with_city_matching(self, sample_weather_df, sample_load_df):
        city_df = pd.DataFrame({
            "areacode": ["A1"], "city": ["TestCity"],
            "lat": [40.0], "lon": [111.0],
        })
        results = run_analysis_pipeline(
            sample_weather_df, sample_load_df,
            df_city=city_df, n_centers=2,
        )
        assert "df_final" in results
        assert "areacode" in results["df_final"].columns


# ===========================================================================
# Test: Edge Cases
# ===========================================================================

class TestEdgeCases:
    def test_empty_dataframe_handling(self):
        df_w = pd.DataFrame(columns=["datetime", "lat", "lon"])
        df_l = pd.DataFrame(columns=["datetime", "load"])
        result = compute_correlations(df_w, df_l)
        assert result.empty

    def test_single_point_correlation(self):
        df_w = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=24, freq="h"),
            "lat": [40.0] * 24, "lon": [111.0] * 24,
            "t_avg": np.sin(np.linspace(0, 2 * np.pi, 24)) * 5 + 15,
        })
        df_l = pd.DataFrame({
            "datetime": df_w["datetime"],
            "load": np.cos(np.linspace(0, 2 * np.pi, 24)) * 1000 + 10000,
        })
        result = compute_correlations(df_w, df_l)
        assert len(result) == 1

    def test_constant_weather_variable(self):
        """恒定气象变量（零方差）相关系数应为 0。"""
        df_w = pd.DataFrame({
            "datetime": pd.date_range("2024-01-01", periods=24, freq="h"),
            "lat": [40.0] * 24, "lon": [111.0] * 24,
            "t_avg": [15.0] * 24,  # 恒定
        })
        df_l = pd.DataFrame({
            "datetime": df_w["datetime"],
            "load": np.sin(np.linspace(0, 2 * np.pi, 24)) * 1000 + 10000,
        })
        result = compute_correlations(df_w, df_l)
        assert result["t_avg_pearson"].iloc[0] == 0.0
