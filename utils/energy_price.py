from __future__ import annotations

import pandas as pd


def flatten_valley_price_diff(
    df: pd.DataFrame,
    *,
    price_col: str = "elePrice",
    type_col: str = "eleType",
    valley_types: tuple[str, str] = ("谷", "深谷"),
    inplace: bool = False,
) -> pd.DataFrame:
    """Flatten valley/deep-valley prices using the last valley-like price."""
    _require_columns(df, price_col, type_col)

    result = df if inplace else df.copy()
    valley_mask = result[type_col].isin(valley_types)
    if not valley_mask.any():
        return result

    flat_price = result.loc[valley_mask, price_col].iloc[-1]
    result.loc[valley_mask, price_col] = flat_price
    return result


def _require_columns(df: pd.DataFrame, price_col: str, type_col: str) -> None:
    missing_columns = [column for column in (price_col, type_col) if column not in df.columns]
    if missing_columns:
        raise ValueError(f"missing required columns: {', '.join(missing_columns)}")
