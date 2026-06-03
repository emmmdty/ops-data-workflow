"""Import historical organic-post mappings and rebuild selected periods."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable, Optional

import pandas as pd

from .storage import init_db, upsert_category_mappings
from .source_storage import source_period_from_path
from .title_matching import extract_historical_title, normalized_title_key
from .workflow import run_archived_workflow


HISTORICAL_ROW_COLUMNS = [
    "sheet",
    "platform",
    "platform_group",
    "channel",
    "post_time",
    "content_url",
    "raw_link",
    "content_id",
    "content_type",
    "account",
    "title",
    "title_key",
    "source_row",
]

HISTORICAL_MAPPING_COLUMNS = [
    "mapping_key",
    "platform",
    "platform_group",
    "channel",
    "content_id",
    "material_id",
    "title",
    "title_key",
    "category_l1",
    "category_l2",
    "category_l3",
]

CONFLICT_COLUMNS = ["mapping_key", "conflict_type", "values", "row_count", "sample_titles"]


@dataclass(frozen=True)
class HistoricalMappingResult:
    rows: pd.DataFrame
    mappings: pd.DataFrame
    conflicts: pd.DataFrame


@dataclass(frozen=True)
class HistoricalApplyResult:
    mapping_result: HistoricalMappingResult
    written_keys: int
    rebuilt_batches: list[str]
    preview: pd.DataFrame


def parse_historical_content_workbook(workbook_path: Path) -> pd.DataFrame:
    workbook_path = Path(workbook_path)
    rows: list[dict[str, object]] = []
    with pd.ExcelFile(workbook_path) as workbook:
        for sheet_name in workbook.sheet_names:
            if "规则" in str(sheet_name):
                continue
            raw = pd.read_excel(workbook_path, sheet_name=sheet_name, header=None)
            header_index, header = _find_header(raw)
            if header_index is None:
                continue
            frame = raw.iloc[header_index + 1 :].copy()
            frame.columns = _unique_columns(header)
            frame = frame.dropna(how="all")
            platform_group = _platform_group_for_sheet(sheet_name)
            for offset, row in frame.iterrows():
                parsed = _parse_history_row(row, sheet_name, platform_group, int(offset) + 1)
                if parsed is not None:
                    rows.append(parsed)
    if not rows:
        return pd.DataFrame(columns=HISTORICAL_ROW_COLUMNS)
    return pd.DataFrame(rows, columns=HISTORICAL_ROW_COLUMNS)


def build_historical_category_mappings(workbook_path: Path) -> HistoricalMappingResult:
    rows = parse_historical_content_workbook(workbook_path)
    if rows.empty:
        return HistoricalMappingResult(
            rows,
            pd.DataFrame(columns=HISTORICAL_MAPPING_COLUMNS),
            pd.DataFrame(columns=CONFLICT_COLUMNS),
        )

    mapping_rows: list[dict[str, object]] = []
    conflict_rows: list[dict[str, object]] = []
    typed = rows[rows["content_type"].astype(str).str.strip().ne("")].copy()

    id_candidates = typed[typed["content_id"].astype(str).str.strip().ne("")].copy()
    _collect_mapping_group(
        id_candidates,
        key_column="content_id",
        key_prefix="content_id",
        conflict_type="内容ID类型冲突",
        mapping_rows=mapping_rows,
        conflict_rows=conflict_rows,
    )

    title_candidates = typed[
        typed["platform_group"].astype(str).eq("抖音") & typed["title_key"].astype(str).str.len().ge(4)
    ].copy()
    _collect_mapping_group(
        title_candidates,
        key_column="title_key",
        key_prefix="title_key",
        conflict_type="标题类型冲突",
        mapping_rows=mapping_rows,
        conflict_rows=conflict_rows,
    )

    mappings = pd.DataFrame(mapping_rows, columns=HISTORICAL_MAPPING_COLUMNS)
    if not mappings.empty:
        mappings = mappings.drop_duplicates(subset=["mapping_key"], keep="first").reset_index(drop=True)
    conflicts = pd.DataFrame(conflict_rows, columns=CONFLICT_COLUMNS)
    if not conflicts.empty:
        conflicts = conflicts.drop_duplicates(subset=["mapping_key"], keep="first").reset_index(drop=True)
    return HistoricalMappingResult(rows, mappings, conflicts)


def preview_historical_mappings_for_targets(
    db_path: Path,
    mappings: pd.DataFrame,
    target_periods: Iterable[str],
) -> pd.DataFrame:
    if mappings.empty:
        return pd.DataFrame(columns=["period_key", "batch_id", "matched_rows", "changed_rows"])
    init_db(db_path)
    mapping_by_key = {
        str(row.get("mapping_key", "")).strip(): str(row.get("category_l2", "")).strip()
        for _, row in mappings.iterrows()
        if str(row.get("mapping_key", "")).strip() and str(row.get("category_l2", "")).strip()
    }
    rows: list[dict[str, object]] = []
    with closing(sqlite3.connect(db_path)) as conn:
        for period_key in target_periods:
            batch = _latest_batch_for_period_key(conn, period_key)
            if not batch:
                rows.append({"period_key": period_key, "batch_id": "", "matched_rows": 0, "changed_rows": 0})
                continue
            items = pd.read_sql_query(
                "select content_id, material_id, title, category_l2 from canonical_items where batch_id = ?",
                conn,
                params=(batch,),
            )
            matched = 0
            changed = 0
            for _, item in items.iterrows():
                category = _lookup_mapping_category(item, mapping_by_key)
                if not category:
                    continue
                matched += 1
                if str(item.get("category_l2", "") or "").strip() != category:
                    changed += 1
            rows.append({"period_key": period_key, "batch_id": batch, "matched_rows": matched, "changed_rows": changed})
    return pd.DataFrame(rows, columns=["period_key", "batch_id", "matched_rows", "changed_rows"])


def import_historical_content_mappings(
    workbook_path: Path,
    *,
    db_path: Path,
    data_root: Path,
    output_root: Path,
    processed_root: Path,
    category_rules_path: Optional[Path],
    env_path: Optional[Path],
    target_periods: Iterable[str],
    apply: bool = False,
    rebuild: bool = False,
) -> HistoricalApplyResult:
    mapping_result = build_historical_category_mappings(workbook_path)
    targets = list(target_periods)
    preview = preview_historical_mappings_for_targets(db_path, mapping_result.mappings, targets)
    written = 0
    rebuilt: list[str] = []
    if apply:
        written = upsert_category_mappings(db_path, mapping_result.mappings)
    if apply and rebuild:
        for period_key in targets:
            batch_id = _rebuild_target_period(
                period_key,
                data_root=data_root,
                output_root=output_root,
                processed_root=processed_root,
                db_path=db_path,
                category_rules_path=category_rules_path,
                env_path=env_path,
            )
            rebuilt.append(batch_id)
    return HistoricalApplyResult(mapping_result, written, rebuilt, preview)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Import historical organic-post content mappings.")
    parser.add_argument("--workbook", required=True, help="历史投稿 Excel 文件路径。")
    parser.add_argument("--db", default=".runtime/workflow.sqlite3", help="SQLite database path.")
    parser.add_argument("--data-root", default="data", help="Raw source data root.")
    parser.add_argument("--output-root", default="outputs", help="Output root for regenerated batches.")
    parser.add_argument("--processed-root", default="processed", help="Processed artifact root for regenerated batches.")
    parser.add_argument("--category-rules", default="config/category_rules.yml", help="Category rules YAML path.")
    parser.add_argument("--env", default=".env", help="Environment file path.")
    parser.add_argument("--target-period", action="append", default=[], help="Period key/raw dir name to preview or rebuild.")
    parser.add_argument("--apply", action="store_true", help="Write non-conflicting mappings into category_mappings.")
    parser.add_argument("--rebuild", action="store_true", help="Regenerate target periods after applying mappings.")
    args = parser.parse_args(argv)

    result = import_historical_content_mappings(
        Path(args.workbook),
        db_path=Path(args.db),
        data_root=Path(args.data_root),
        output_root=Path(args.output_root),
        processed_root=Path(args.processed_root),
        category_rules_path=Path(args.category_rules),
        env_path=Path(args.env),
        target_periods=args.target_period,
        apply=bool(args.apply),
        rebuild=bool(args.rebuild),
    )
    print(_format_result_summary(result, applied=bool(args.apply), rebuilt=bool(args.rebuild)))


def _collect_mapping_group(
    candidates: pd.DataFrame,
    *,
    key_column: str,
    key_prefix: str,
    conflict_type: str,
    mapping_rows: list[dict[str, object]],
    conflict_rows: list[dict[str, object]],
) -> None:
    if candidates.empty:
        return
    for value, group in candidates.groupby(key_column, dropna=False, sort=False):
        clean_value = str(value or "").strip()
        if not clean_value:
            continue
        mapping_key = f"{key_prefix}:{clean_value}"
        types = sorted({str(item).strip() for item in group["content_type"].tolist() if str(item).strip()})
        if len(types) != 1:
            conflict_rows.append(
                {
                    "mapping_key": mapping_key,
                    "conflict_type": conflict_type,
                    "values": " | ".join(types),
                    "row_count": int(len(group)),
                    "sample_titles": " | ".join(_unique_nonblank(group["title"].tolist())[:3]),
                }
            )
            continue
        first = group.iloc[0]
        title = str(first.get("title", "") or "").strip()
        title_key = str(first.get("title_key", "") or "").strip()
        content_id = clean_value if key_prefix == "content_id" else ""
        mapping_rows.append(
            {
                "mapping_key": mapping_key,
                "platform": str(first.get("platform", "") or ""),
                "platform_group": str(first.get("platform_group", "") or ""),
                "channel": str(first.get("channel", "") or ""),
                "content_id": content_id,
                "material_id": "",
                "title": title if key_prefix == "title_key" else "",
                "title_key": title_key if key_prefix == "title_key" else "",
                "category_l1": "",
                "category_l2": types[0],
                "category_l3": title,
            }
        )


def _parse_history_row(
    row: pd.Series,
    sheet_name: str,
    platform_group: str,
    source_row: int,
) -> Optional[dict[str, object]]:
    raw_link = _clean_text(row.get("内容链接", ""))
    content_id = _clean_text(row.get("笔记ID", "")) or _clean_text(row.get("短链id", ""))
    content_type = _clean_text(row.get("内容类型", ""))
    post_time = _clean_text(row.get("投稿时间", ""))
    account = _clean_text(row.get("账号", ""))
    if not any([raw_link, content_id, content_type, account]):
        return None
    if "投稿" in post_time and not any([raw_link, content_id, content_type, account]):
        return None
    title = extract_historical_title(raw_link)
    return {
        "sheet": sheet_name,
        "platform": platform_group,
        "platform_group": platform_group,
        "channel": platform_group,
        "post_time": post_time,
        "content_url": _first_url(raw_link) or raw_link,
        "raw_link": raw_link,
        "content_id": content_id,
        "content_type": content_type,
        "account": account,
        "title": title,
        "title_key": normalized_title_key(title),
        "source_row": source_row,
    }


def _find_header(raw: pd.DataFrame) -> tuple[Optional[int], list[str]]:
    for index in range(min(len(raw), 25)):
        values = [_clean_text(value) for value in raw.iloc[index].tolist()]
        if "编号" in values and "内容链接" in values:
            return index, values
    return None, []


def _unique_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for index, column in enumerate(columns):
        name = str(column or "").strip() or f"未命名_{index}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        result.append(name)
    return result


def _platform_group_for_sheet(sheet_name: object) -> str:
    text = str(sheet_name or "")
    if "抖音" in text:
        return "抖音"
    if "小红书" in text:
        return "小红书"
    if "B站" in text:
        return "B站"
    return text.strip() or "未知"


def _first_url(value: object) -> str:
    text = _clean_text(value)
    if "http" not in text:
        return ""
    import re

    match = re.search(r"https?://\S+", text)
    return match.group(0).strip() if match else ""


def _unique_nonblank(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _lookup_mapping_category(item: pd.Series, mapping_by_key: dict[str, str]) -> str:
    for column in ["content_id", "material_id"]:
        value = str(item.get(column, "") or "").strip()
        if value:
            category = mapping_by_key.get(f"{column}:{value}") or mapping_by_key.get(f"content_id:{value}")
            if category:
                return category
    title_key = normalized_title_key(item.get("title", ""))
    return mapping_by_key.get(f"title_key:{title_key}", "")


def _latest_batch_for_period_key(conn: sqlite3.Connection, period_key: str) -> str:
    key = str(period_key or "").strip()
    row = conn.execute(
        """
        select batch_id
        from upload_batches
        where status = 'ok'
            and (period_key = ? or replace(period_start, '-', '') || '-' || replace(period_end, '-', '') = ?)
        order by period_end desc, period_start desc, created_at desc
        limit 1
        """,
        (key, key),
    ).fetchone()
    return str(row[0]) if row else ""


def _rebuild_target_period(
    period_key: str,
    *,
    data_root: Path,
    output_root: Path,
    processed_root: Path,
    db_path: Path,
    category_rules_path: Optional[Path],
    env_path: Optional[Path],
) -> str:
    raw_dir = _source_dir_for_target(Path(data_root), period_key)
    if not raw_dir.exists():
        raise ValueError(f"未找到目标周期目录：{raw_dir}")
    period = source_period_from_path(raw_dir)
    result = run_archived_workflow(
        raw_dir,
        period.period_start,
        period.period_end,
        output_root=output_root,
        processed_root=processed_root,
        db_path=db_path,
        category_rules_path=category_rules_path,
        env_path=env_path,
        reference_root=Path(data_root) / "reference",
        period_level=period.period_level,
        period_key=period.period_key,
        period_label=period.period_label,
        data_start=period.data_start,
        data_end=period.data_end,
        source_type=period.source_type,
    )
    return result.batch_id


def _source_dir_for_target(data_root: Path, period_key: str) -> Path:
    key = str(period_key or "").strip()
    compact = key.replace("-", "")
    if len(compact) == 16 and compact.isdigit():
        return data_root / "weeks" / f"{compact[:8]}-{compact[8:]}"
    if len(compact) == 6 and compact.isdigit():
        return data_root / "months" / compact
    raise ValueError(f"无法从目标周期定位源文件目录：{period_key}")


def _format_result_summary(result: HistoricalApplyResult, *, applied: bool, rebuilt: bool) -> str:
    lines = [
        f"历史投稿行数：{len(result.mapping_result.rows)}",
        f"可写入映射：{len(result.mapping_result.mappings)}",
        f"冲突映射：{len(result.mapping_result.conflicts)}",
        f"写入 mapping keys：{result.written_keys if applied else 0}",
    ]
    if not result.preview.empty:
        lines.append("目标周期预览：")
        lines.append(result.preview.to_string(index=False))
    if rebuilt:
        lines.append("重建批次：")
        lines.extend(result.rebuilt_batches)
    if not result.mapping_result.conflicts.empty:
        lines.append("冲突样例：")
        lines.append(result.mapping_result.conflicts.head(20).to_string(index=False))
    return "\n".join(lines)


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


if __name__ == "__main__":
    main()
