"""Core recap export writer."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


CORE_RECAP_WORKBOOK = "content_recap_core.xlsx"
CORE_RECAP_SHEETS = ["清洗后素材表", "内容复盘表", "不可分析汇总"]
ATTRIBUTION_RECAP_SHEETS = ["匹配覆盖率", "已匹配账号类型分析", "未匹配归因"]


def write_core_recap_workbook(
    output_dir: Path,
    cleaned_asset_table: pd.DataFrame,
    content_recap_table: pd.DataFrame,
    unanalyzable_summary: pd.DataFrame,
    attribution_coverage: pd.DataFrame | None = None,
    matched_attribution: pd.DataFrame | None = None,
    unmatched_attribution: pd.DataFrame | None = None,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = output_dir / CORE_RECAP_WORKBOOK
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        cleaned_asset_table.to_excel(writer, sheet_name=CORE_RECAP_SHEETS[0], index=False)
        content_recap_table.to_excel(writer, sheet_name=CORE_RECAP_SHEETS[1], index=False)
        unanalyzable_summary.to_excel(writer, sheet_name=CORE_RECAP_SHEETS[2], index=False)
        _frame_or_empty(attribution_coverage).to_excel(writer, sheet_name=ATTRIBUTION_RECAP_SHEETS[0], index=False)
        _frame_or_empty(matched_attribution).to_excel(writer, sheet_name=ATTRIBUTION_RECAP_SHEETS[1], index=False)
        _frame_or_empty(unmatched_attribution).to_excel(writer, sheet_name=ATTRIBUTION_RECAP_SHEETS[2], index=False)
    return workbook_path


def _frame_or_empty(frame: pd.DataFrame | None) -> pd.DataFrame:
    return frame if frame is not None else pd.DataFrame()
