from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ele_trading.capacity_planning.pv_profile import PVProfileConfig, load_or_build_pv_profile
from ele_trading.capacity_planning.wind_profile import WindProfileConfig, load_or_build_wind_profile
from ele_trading.data_provider.load_profile import LoadProfileBuildConfig, build_load_profile_from_raw
from ele_trading.data_provider.resource_weather import fetch_weather_open_meteo
from ele_trading.utils.log_util import logger


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "wind_pv_es_calc_data_bridge.yaml"


def load_bridge_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("bridge config must be a mapping")
    return config


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _read_legacy_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "Time" in df.columns:
        df["Time"] = pd.to_datetime(df["Time"])
    return df


def _to_load_build_config(config: dict[str, Any], target_year: int, freq: str) -> LoadProfileBuildConfig:
    load_cfg = dict(config)
    load_cfg["target_year"] = int(load_cfg.get("target_year", target_year))
    load_cfg["freq"] = str(load_cfg.get("freq", freq))
    monthly = load_cfg.get("monthly_energy_targets")
    if monthly is not None:
        load_cfg["monthly_energy_targets"] = {int(key): float(value) for key, value in monthly.items()}
    return LoadProfileBuildConfig(**load_cfg)


def _to_pv_config(config: dict[str, Any]) -> PVProfileConfig:
    return PVProfileConfig(**config)


def _to_wind_config(config: dict[str, Any], target_year: int) -> WindProfileConfig:
    cfg = dict(config)
    cfg["year"] = int(cfg.get("year", target_year))
    return WindProfileConfig(**cfg)


def _save_legacy_frame(df: pd.DataFrame, path: str | Path) -> None:
    output = _resolve_path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False, encoding="utf-8")


def _build_load_frame(config: dict[str, Any]) -> pd.DataFrame:
    run_cfg = config["run"]
    paths = config["paths"]
    mode = str(run_cfg["refresh_mode"])
    output_path = _resolve_path(paths["df_2025_path"])
    if mode == "use_cached" and output_path.exists():
        return _read_legacy_csv(output_path)

    load_cfg = _to_load_build_config(config["load_profile"], int(run_cfg["target_year"]), str(run_cfg["freq"]))
    result = build_load_profile_from_raw(_resolve_path(paths["raw_load_dir"]), load_cfg)
    compat = config["compatibility"]
    load_df = result.data[["timestamp", "load_kw"]].rename(
        columns={"timestamp": compat["time_col"], "load_kw": compat["load_col"]}
    )
    _save_legacy_frame(load_df, paths["df_2025_path"])
    return load_df


def _build_pv_frame(config: dict[str, Any], load_df: pd.DataFrame) -> pd.DataFrame:
    run_cfg = config["run"]
    paths = config["paths"]
    mode = str(run_cfg["refresh_mode"])
    output_path = _resolve_path(paths["df_pv_2025_path"])
    if mode == "use_cached" and output_path.exists():
        return _read_legacy_csv(output_path)

    compat = config["compatibility"]
    pv_cfg = _to_pv_config(config["pv_profile"])
    time_index = pd.to_datetime(load_df[compat["time_col"]])
    result = load_or_build_pv_profile(config=pv_cfg, time_index=pd.DatetimeIndex(time_index))
    pv_df = result.power_series.rename(compat["pv_col"]).to_frame().reset_index()
    pv_df.columns = [compat["time_col"], compat["pv_col"]]
    _save_legacy_frame(pv_df, paths["df_pv_2025_path"])
    return pv_df


def _build_wind_frame(config: dict[str, Any]) -> pd.DataFrame:
    run_cfg = config["run"]
    paths = config["paths"]
    mode = str(run_cfg["refresh_mode"])
    output_path = _resolve_path(paths["df_wind_2025_path"])
    if mode == "use_cached" and output_path.exists():
        return _read_legacy_csv(output_path)

    compat = config["compatibility"]
    wind_cfg = _to_wind_config(config["wind_profile"], int(run_cfg["target_year"]))
    latitude = float(config["site"]["latitude"])
    longitude = float(config["site"]["longitude"])
    weather_df = fetch_weather_open_meteo(
        latitude=latitude,
        longitude=longitude,
        start_date=f"{wind_cfg.year}-01-01",
        end_date=f"{wind_cfg.year}-12-31",
        hourly_fields=["wind_speed_100m", "temperature_2m"],
    ).set_index("timestamp")
    result = load_or_build_wind_profile(config=wind_cfg, weather_df=weather_df)
    wind_df = result.power_series.rename(compat["wind_col"]).to_frame().reset_index()
    wind_df.columns = [compat["time_col"], compat["wind_col"]]
    _save_legacy_frame(wind_df, paths["df_wind_2025_path"])
    return wind_df


def build_legacy_total_frame(
    load_df: pd.DataFrame,
    pv_df: pd.DataFrame,
    wind_df: pd.DataFrame,
    compatibility: dict[str, str],
    freq: str,
) -> pd.DataFrame:
    time_col = compatibility["time_col"]
    load_col = compatibility["load_col"]
    pv_col = compatibility["pv_col"]
    wind_col = compatibility["wind_col"]

    load = load_df.copy()
    load[time_col] = pd.to_datetime(load[time_col])
    pv = pv_df.copy()
    pv[time_col] = pd.to_datetime(pv[time_col])
    wind = wind_df.copy()
    wind[time_col] = pd.to_datetime(wind[time_col])

    load_index = pd.DatetimeIndex(load[time_col])
    pv_series = pv.set_index(time_col)[pv_col].reindex(load_index).interpolate(method="time").ffill().bfill()
    wind_series = wind.set_index(time_col)[wind_col].reindex(load_index).interpolate(method="time").ffill().bfill()

    total = pd.DataFrame(
        {
            time_col: load_index,
            load_col: load[load_col].astype(float).values,
            pv_col: pv_series.astype(float).values,
            wind_col: wind_series.astype(float).values,
        }
    )
    total["Wind_kw"] = total[wind_col] * 1000.0
    total["NetLoad_kw"] = total[load_col] - total[pv_col] - total["Wind_kw"]
    total = total.sort_values(time_col).reset_index(drop=True)
    return total


def build_legacy_temp_data(config: dict[str, Any]) -> dict[str, pd.DataFrame]:
    load_df = _build_load_frame(config)
    pv_df = _build_pv_frame(config, load_df)
    wind_df = _build_wind_frame(config)

    result = {"load": load_df, "pv": pv_df, "wind": wind_df}
    if bool(config["run"].get("write_df_total", True)):
        total = build_legacy_total_frame(
            load_df=load_df,
            pv_df=pv_df,
            wind_df=wind_df,
            compatibility=config["compatibility"],
            freq=str(config["run"]["freq"]),
        )
        _save_legacy_frame(total, config["paths"]["df_total_path"])
        result["total"] = total
    return result


def ensure_legacy_temp_data(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, pd.DataFrame]:
    config = load_bridge_config(config_path)
    result = build_legacy_temp_data(config)
    logger.info(
        "legacy temp data prepared: load=%s pv=%s wind=%s",
        len(result["load"]),
        len(result["pv"]),
        len(result["wind"]),
    )
    return result


if __name__ == "__main__":
    ensure_legacy_temp_data(DEFAULT_CONFIG_PATH)
