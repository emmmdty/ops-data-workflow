from unittest.mock import patch

import pandas as pd

from app import _show_frame


def test_show_frame_formats_numeric_table_columns_without_stringifying_metrics():
    frame = pd.DataFrame(
        [
            {
                "douyin_l2": "财富动画",
                "item_count": "9",
                "spend": "28831.374",
                "impressions": "2156282.0",
                "activations": "764.0",
                "first_pay_count": "166.0",
                "activation_cost": "37.7373952879581",
                "first_pay_cost": "173.682951807229",
                "value": "930.0",
                "share": "0.033958957131381",
            }
        ]
    )

    with patch("app.st.dataframe") as dataframe:
        _show_frame(frame)

    displayed = dataframe.call_args.args[0]
    assert pd.api.types.is_integer_dtype(displayed["素材数"])
    assert pd.api.types.is_float_dtype(displayed["消耗"])
    assert pd.api.types.is_float_dtype(displayed["激活成本"])
    assert pd.api.types.is_float_dtype(displayed["价值占比"])
    assert displayed.iloc[0]["消耗"] == 28831.37
    assert displayed.iloc[0]["激活成本"] == 37.74
    assert displayed.iloc[0]["付费成本"] == 173.68
    assert displayed.iloc[0]["价值"] == 930
    assert displayed.iloc[0]["价值占比"] == 3.4

    column_config = dataframe.call_args.kwargs["column_config"]
    assert column_config["价值占比"]["type_config"]["format"] == "%g%%"
    assert column_config["消耗"]["type_config"]["format"] == "localized"
    assert column_config["激活成本"]["type_config"]["format"] == "localized"
    assert column_config["付费成本"]["type_config"]["format"] == "localized"


def test_show_frame_formats_already_localized_numeric_table_columns():
    frame = pd.DataFrame(
        [
            {
                "类型": "财富动画",
                "素材数": "9",
                "消耗": "28831.374",
                "激活成本": "37.7373952879581",
                "付费成本": "173.682951807229",
                "价值": "930.0",
                "价值占比": "0.00142",
            }
        ]
    )

    with patch("app.st.dataframe") as dataframe:
        _show_frame(frame)

    displayed = dataframe.call_args.args[0]
    assert pd.api.types.is_integer_dtype(displayed["素材数"])
    assert pd.api.types.is_float_dtype(displayed["消耗"])
    assert displayed.iloc[0]["消耗"] == 28831.37
    assert displayed.iloc[0]["价值"] == 930
    assert displayed.iloc[0]["价值占比"] == 0.1

    column_config = dataframe.call_args.kwargs["column_config"]
    assert column_config["价值占比"]["type_config"]["format"] == "%g%%"
    assert column_config["激活成本"]["type_config"]["format"] == "localized"
