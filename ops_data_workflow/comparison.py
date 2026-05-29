"""Historical comparison helpers."""

from __future__ import annotations

import pandas as pd


COMPARISON_METRICS = [
    "spend",
    "impressions",
    "activations",
    "activation_cost",
    "first_pay_count",
    "first_pay_cost",
    "first_pay_rate",
]


def build_channel_comparison(current: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
    if previous.empty:
        return pd.DataFrame()
    current_cols = ["channel", *COMPARISON_METRICS]
    previous_cols = ["channel", *COMPARISON_METRICS]
    merged = current[current_cols].merge(
        previous[previous_cols],
        on="channel",
        how="left",
        suffixes=("_current", "_previous"),
    )
    for metric in COMPARISON_METRICS:
        merged[f"{metric}_change_rate"] = _change_rate(
            merged[f"{metric}_current"], merged[f"{metric}_previous"]
        )
    return merged


def _change_rate(current: pd.Series, previous: pd.Series) -> pd.Series:
    current_values = pd.to_numeric(current, errors="coerce").astype(float)
    previous_values = pd.to_numeric(previous, errors="coerce").astype(float)
    result = pd.Series(pd.NA, index=current.index, dtype="Float64")
    mask = previous_values.ne(0.0) & previous_values.notna()
    result.loc[mask] = (current_values.loc[mask] - previous_values.loc[mask]) / previous_values.loc[mask]
    return result
