"""Persist and apply manual data-review decisions."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd

from .raw_cleaning import load_cleaned_canonical, rewrite_cleaned_canonical
from .storage import init_db, read_batch_record
from .workflow import run_archived_workflow


REVIEW_ACTIONS = ["保留", "删除", "合并到主记录", "改字段", "确认非重复"]


def load_data_review_items(db_path: Path, batch_id: str) -> pd.DataFrame:
    """Load data-quality review rows for the data review page."""
    if not batch_id or not Path(db_path).exists():
        return _empty_review_items()
    _ensure_review_resolution_table(db_path)
    review_queue = _read_batch_table(db_path, "review_queue_items", batch_id)
    duplicate_items = _read_batch_table(db_path, "duplicate_merge_items", batch_id)
    conflict_items = _read_batch_table(db_path, "conflict_retention_items", batch_id)
    rows: list[dict[str, object]] = []
    for _, row in review_queue.iterrows():
        issue_type = str(row.get("review_reasons", "") or "人工审核")
        rows.append(
            {
                "issue_id": _issue_id(batch_id, "queue", row),
                "issue_type": issue_type,
                "review_action": str(row.get("review_action", "") or "保留"),
                "channel": row.get("channel", ""),
                "title": row.get("title", ""),
                "content_id": row.get("content_id", ""),
                "material_id": row.get("material_id", ""),
                "dedupe_key": row.get("dedupe_key", ""),
                "duplicate_group_id": row.get("duplicate_group_id", ""),
                "conflict_details": row.get("conflict_details", ""),
                "ledger_match_source": row.get("ledger_match_source", ""),
                "ledger_content_type": row.get("ledger_content_type", ""),
                "ledger_source_file": row.get("ledger_source_file", ""),
                "ledger_source_sheet": row.get("ledger_source_sheet", ""),
                "ledger_source_row": row.get("ledger_source_row", ""),
                "match_risk_level": row.get("match_risk_level", ""),
                "match_risk_reason": row.get("match_risk_reason", ""),
                "spend": row.get("spend", ""),
                "activations": row.get("activations", ""),
                "activation_cost": row.get("activation_cost", ""),
                "source_file": row.get("source_file", ""),
                "source_sheet": row.get("source_sheet", ""),
                "field_name": "",
                "new_value": "",
                "merge_target_content_id": "",
            }
        )
    for _, row in duplicate_items.iterrows():
        rows.append(
            {
                "issue_id": _issue_id(batch_id, "duplicate", row),
                "issue_type": row.get("issue_type", "重复内容"),
                "review_action": "保留",
                "channel": row.get("channel", ""),
                "title": "",
                "content_id": row.get("content_id", ""),
                "material_id": row.get("material_ids", ""),
                "dedupe_key": row.get("dedupe_key", ""),
                "duplicate_group_id": row.get("dedupe_key", ""),
                "conflict_details": "",
                "ledger_match_source": "",
                "ledger_content_type": "",
                "ledger_source_file": "",
                "ledger_source_sheet": "",
                "ledger_source_row": "",
                "match_risk_level": "",
                "match_risk_reason": "",
                "spend": row.get("spend", ""),
                "activations": row.get("activations", ""),
                "activation_cost": row.get("activation_cost", ""),
                "source_file": row.get("source_files", ""),
                "source_sheet": "",
                "field_name": "",
                "new_value": "",
                "merge_target_content_id": "",
            }
        )
    for _, row in conflict_items.iterrows():
        rows.append(
            {
                "issue_id": _issue_id(batch_id, "conflict", row),
                "issue_type": row.get("issue_type", "冲突项"),
                "review_action": "保留",
                "channel": row.get("channel", ""),
                "title": "",
                "content_id": row.get("content_id", ""),
                "material_id": "",
                "dedupe_key": row.get("dedupe_key", ""),
                "duplicate_group_id": row.get("dedupe_key", ""),
                "conflict_details": f"{row.get('column', '')}: {row.get('values', '')}",
                "ledger_match_source": "",
                "ledger_content_type": "",
                "ledger_source_file": "",
                "ledger_source_sheet": "",
                "ledger_source_row": "",
                "match_risk_level": "",
                "match_risk_reason": "",
                "spend": row.get("spend", ""),
                "activations": row.get("activations", ""),
                "activation_cost": row.get("activation_cost", ""),
                "source_file": "",
                "source_sheet": "",
                "field_name": row.get("column", ""),
                "new_value": "",
                "merge_target_content_id": "",
            }
        )
    if not rows:
        return _empty_review_items()
    items = pd.DataFrame(rows)
    return items.drop_duplicates(subset=["issue_id"], keep="first").reset_index(drop=True)


def save_review_resolutions(db_path: Path, batch_id: str, edited_items: pd.DataFrame) -> int:
    if not batch_id or edited_items.empty:
        return 0
    _ensure_review_resolution_table(db_path)
    stored = edited_items.copy()
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with closing(sqlite3.connect(db_path)) as conn:
        for _, row in stored.iterrows():
            issue_id = str(row.get("issue_id", "") or "")
            if not issue_id:
                continue
            action = str(row.get("review_action", "") or "保留")
            if action not in REVIEW_ACTIONS:
                action = "保留"
            conn.execute(
                """
                insert into review_resolutions (
                    batch_id, issue_id, issue_type, review_action, channel, title,
                    content_id, material_id, dedupe_key, duplicate_group_id,
                    field_name, new_value, merge_target_content_id, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(batch_id, issue_id) do update set
                    issue_type = excluded.issue_type,
                    review_action = excluded.review_action,
                    channel = excluded.channel,
                    title = excluded.title,
                    content_id = excluded.content_id,
                    material_id = excluded.material_id,
                    dedupe_key = excluded.dedupe_key,
                    duplicate_group_id = excluded.duplicate_group_id,
                    field_name = excluded.field_name,
                    new_value = excluded.new_value,
                    merge_target_content_id = excluded.merge_target_content_id,
                    updated_at = excluded.updated_at
                """,
                (
                    batch_id,
                    issue_id,
                    str(row.get("issue_type", "") or ""),
                    action,
                    str(row.get("channel", "") or ""),
                    str(row.get("title", "") or ""),
                    str(row.get("content_id", "") or ""),
                    str(row.get("material_id", "") or ""),
                    str(row.get("dedupe_key", "") or ""),
                    str(row.get("duplicate_group_id", "") or ""),
                    str(row.get("field_name", "") or ""),
                    str(row.get("new_value", "") or ""),
                    str(row.get("merge_target_content_id", "") or ""),
                    now,
                ),
            )
            count += 1
        conn.commit()
    return count


def apply_review_resolutions_and_regenerate(
    db_path: Path,
    batch_id: str,
    *,
    output_root: Path,
    processed_root: Path,
    category_rules_path: Path,
    env_path: Path,
    output_mode: str = "full",
    enable_deepseek: bool = True,
    enable_external_context: bool = True,
):
    """Apply saved decisions to cleaned.xlsx, then regenerate the selected period."""
    record = read_batch_record(db_path, batch_id)
    if not record:
        raise ValueError("未找到当前周期，无法同步审核结果。")
    processed_dir = Path(record["archive_dir"])
    cleaned_workbook = processed_dir / "cleaned.xlsx"
    if not cleaned_workbook.exists():
        raise ValueError("当前周期不是由 cleaned.xlsx 生成，无法直接同步 Excel。")
    resolutions = _load_saved_resolutions(db_path, batch_id)
    canonical = apply_resolutions_to_frame(load_cleaned_canonical(cleaned_workbook), resolutions)
    rewrite_cleaned_canonical(cleaned_workbook, canonical)
    return run_archived_workflow(
        processed_dir,
        record.get("period_start", ""),
        record.get("period_end", ""),
        output_root=output_root,
        processed_root=processed_root,
        db_path=db_path,
        category_rules_path=category_rules_path,
        env_path=env_path,
        period_level=record.get("period_level", ""),
        period_key=record.get("period_key", ""),
        period_label=record.get("period_label", ""),
        data_start=record.get("data_start", ""),
        data_end=record.get("data_end", ""),
        source_type=record.get("source_type", ""),
        output_mode=output_mode,
        enable_deepseek=enable_deepseek,
        enable_external_context=enable_external_context,
    )


def apply_resolutions_to_frame(canonical: pd.DataFrame, resolutions: pd.DataFrame) -> pd.DataFrame:
    if canonical.empty or resolutions.empty:
        return canonical
    result = canonical.copy()
    for _, row in resolutions.iterrows():
        action = str(row.get("review_action", "") or "")
        content_id = str(row.get("content_id", "") or "")
        dedupe_key = str(row.get("dedupe_key", "") or "")
        duplicate_group_id = str(row.get("duplicate_group_id", "") or "")
        mask = pd.Series([False] * len(result), index=result.index)
        if content_id:
            mask = mask | result.get("content_id", pd.Series("", index=result.index)).astype(str).eq(content_id)
        if dedupe_key:
            mask = mask | result.get("dedupe_key", pd.Series("", index=result.index)).astype(str).eq(dedupe_key)
        if duplicate_group_id:
            mask = mask | result.get("duplicate_group_id", pd.Series("", index=result.index)).astype(str).eq(duplicate_group_id)
        if not mask.any():
            continue
        if action == "删除":
            result = result[~mask].copy()
            continue
        if action == "改字段":
            field_name = str(row.get("field_name", "") or "")
            if field_name and field_name in result.columns:
                result.loc[mask, field_name] = str(row.get("new_value", "") or "")
        if action == "确认非重复":
            result.loc[mask, "review_reasons"] = result.loc[mask, "review_reasons"].map(_remove_title_conflict_reason)
            result.loc[mask, "needs_manual_review"] = result.loc[mask, "review_reasons"].astype(str).str.strip().ne("")
        result.loc[mask, "review_action"] = action or "保留"
    return result.reset_index(drop=True)


def _ensure_review_resolution_table(db_path: Path) -> None:
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            create table if not exists review_resolutions (
                batch_id text not null,
                issue_id text not null,
                issue_type text not null default '',
                review_action text not null default '',
                channel text not null default '',
                title text not null default '',
                content_id text not null default '',
                material_id text not null default '',
                dedupe_key text not null default '',
                duplicate_group_id text not null default '',
                field_name text not null default '',
                new_value text not null default '',
                merge_target_content_id text not null default '',
                updated_at text not null,
                primary key (batch_id, issue_id)
            )
            """
        )
        conn.commit()


def _read_batch_table(db_path: Path, table_name: str, batch_id: str) -> pd.DataFrame:
    with closing(sqlite3.connect(db_path)) as conn:
        exists = conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?",
            (table_name,),
        ).fetchone()
        if not exists:
            return pd.DataFrame()
        try:
            return pd.read_sql_query(
                f'select * from "{table_name}" where batch_id = ? order by rowid',
                conn,
                params=(batch_id,),
            ).drop(columns=["batch_id"], errors="ignore")
        except Exception:
            return pd.DataFrame()


def _load_saved_resolutions(db_path: Path, batch_id: str) -> pd.DataFrame:
    _ensure_review_resolution_table(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        return pd.read_sql_query(
            """
            select *
            from review_resolutions
            where batch_id = ?
            order by updated_at, issue_id
            """,
            conn,
            params=(batch_id,),
        )


def _issue_id(batch_id: str, prefix: str, row: pd.Series) -> str:
    parts = [
        batch_id,
        prefix,
        str(row.get("dedupe_key", "") or ""),
        str(row.get("duplicate_group_id", "") or ""),
        str(row.get("content_id", "") or ""),
        str(row.get("material_id", "") or ""),
        str(row.get("title", "") or ""),
        str(row.get("column", "") or ""),
    ]
    return "|".join(parts)


def _remove_title_conflict_reason(value: object) -> str:
    return "；".join(part for part in str(value or "").split("；") if part and part != "标题重复但ID不同")


def _empty_review_items() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "issue_id",
            "issue_type",
            "review_action",
            "channel",
            "title",
            "content_id",
            "material_id",
            "dedupe_key",
            "duplicate_group_id",
            "conflict_details",
            "ledger_match_source",
            "ledger_content_type",
            "ledger_source_file",
            "ledger_source_sheet",
            "ledger_source_row",
            "match_risk_level",
            "match_risk_reason",
            "spend",
            "activations",
            "activation_cost",
            "source_file",
            "source_sheet",
            "field_name",
            "new_value",
            "merge_target_content_id",
        ]
    )
