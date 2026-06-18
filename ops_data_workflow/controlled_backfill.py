"""Controlled rerun helpers for the handoff validation periods."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .storage import list_harvester_asset_manifests
from .workflow import run_archived_workflow


CONTROLLED_BACKFILL_BATCH_IDS = (
    "upload:week:20260526-20260604",
    "upload:week:20260605-20260611",
    "upload:month:2026-04",
    "upload:month:2026-05",
)


@dataclass(frozen=True)
class ControlledPeriod:
    batch_id: str
    period_level: str
    period_key: str
    period_start: str
    period_end: str
    source_dir: Path


@dataclass(frozen=True)
class ControlledBackfillResult:
    batch_id: str
    status: str
    source_dir: Path
    archive_dir: Path | None = None
    message: str = ""


_CONTROLLED_PERIODS = {
    "upload:week:20260526-20260604": {
        "period_level": "week",
        "period_key": "20260526-20260604",
        "period_start": "2026-05-26",
        "period_end": "2026-06-04",
        "source_parts": ("data", "weeks", "20260526-20260604"),
    },
    "upload:week:20260605-20260611": {
        "period_level": "week",
        "period_key": "20260605-20260611",
        "period_start": "2026-06-05",
        "period_end": "2026-06-11",
        "source_parts": ("data", "weeks", "20260605-20260611"),
    },
    "upload:month:2026-04": {
        "period_level": "month",
        "period_key": "2026-04",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "source_parts": ("data", "months", "202604"),
    },
    "upload:month:2026-05": {
        "period_level": "month",
        "period_key": "2026-05",
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "source_parts": ("data", "months", "202605"),
    },
}


def select_controlled_periods(selected_batch_ids: Iterable[str] | None = None, *, project_root: Path = Path(".")) -> list[ControlledPeriod]:
    batch_ids = list(selected_batch_ids or CONTROLLED_BACKFILL_BATCH_IDS)
    invalid = [batch_id for batch_id in batch_ids if batch_id not in _CONTROLLED_PERIODS]
    if invalid:
        allowed = ", ".join(CONTROLLED_BACKFILL_BATCH_IDS)
        raise ValueError(f"周期不在本轮受控回填白名单：{', '.join(invalid)}。允许周期：{allowed}")

    project_root = Path(project_root)
    periods: list[ControlledPeriod] = []
    for batch_id in batch_ids:
        spec = _CONTROLLED_PERIODS[batch_id]
        periods.append(
            ControlledPeriod(
                batch_id=batch_id,
                period_level=str(spec["period_level"]),
                period_key=str(spec["period_key"]),
                period_start=str(spec["period_start"]),
                period_end=str(spec["period_end"]),
                source_dir=project_root.joinpath(*spec["source_parts"]),
            )
        )
    return periods


def rerun_controlled_periods(
    *,
    project_root: Path = Path("."),
    selected_batch_ids: Iterable[str] | None = None,
    dry_run: bool = False,
    include_harvester_periods: bool = False,
    existing_harvester_manifest_counts: Callable[[str], int] | None = None,
    runner: Callable[..., object] = run_archived_workflow,
) -> list[ControlledBackfillResult]:
    project_root = Path(project_root)
    periods = select_controlled_periods(selected_batch_ids, project_root=project_root)
    db_path = project_root / ".runtime" / "workflow.sqlite3"
    manifest_count = existing_harvester_manifest_counts or (lambda batch_id: _harvester_manifest_count(db_path, batch_id))
    results: list[ControlledBackfillResult] = []
    for period in periods:
        if not period.source_dir.exists():
            raise FileNotFoundError(f"本地周期源目录不存在：{period.source_dir}")
        existing_manifests = int(manifest_count(period.batch_id))
        if existing_manifests and not include_harvester_periods:
            results.append(
                ControlledBackfillResult(
                    batch_id=period.batch_id,
                    status="skipped",
                    source_dir=period.source_dir,
                    message=f"该周期已有 harvester manifest {existing_manifests} 条；默认保护已采集资产，未重跑。",
                )
            )
            continue
        if dry_run:
            results.append(
                ControlledBackfillResult(
                    batch_id=period.batch_id,
                    status="dry-run",
                    source_dir=period.source_dir,
                    message="已验证源目录，未执行重跑。",
                )
            )
            continue

        workflow_result = runner(
            period.source_dir,
            period.period_start,
            period.period_end,
            output_root=project_root / "outputs",
            processed_root=project_root / "processed",
            db_path=db_path,
            category_rules_path=project_root / "config" / "category_rules.yml",
            env_path=project_root / ".env",
            period_level=period.period_level,
            period_key=period.period_key,
            period_label="",
            data_start=period.period_start,
            data_end=period.period_end,
            source_type="upload",
            output_mode="ui_only",
            enable_deepseek=False,
            enable_external_context=False,
            metadata_enrichment_mode="safe_public",
            metadata_cache_dir=project_root / "data" / "metadata_cache",
            enrichment_queue_root=project_root / "data" / "enrichment_queue",
            force_reclean=True,
            enqueue_background_analysis=True,
            background_trigger="controlled_backfill",
        )
        results.append(
            ControlledBackfillResult(
                batch_id=str(getattr(workflow_result, "batch_id", period.batch_id)),
                status="rerun",
                source_dir=period.source_dir,
                archive_dir=getattr(workflow_result, "archive_dir", None),
                message="已重跑并写入 SQLite。",
            )
        )
    return results


def _harvester_manifest_count(db_path: Path, batch_id: str) -> int:
    manifests = list_harvester_asset_manifests(db_path, batch_id=batch_id)
    return int(len(manifests))
