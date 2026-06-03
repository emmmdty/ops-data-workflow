from __future__ import annotations

import argparse
from pathlib import Path

from ops_data_workflow.storage import purge_history_state
from ops_data_workflow.reference_tables import parse_period_from_raw_dir
from ops_data_workflow.raw_sync import sync_raw_periods
from ops_data_workflow.source_import import build_source_import_plan, execute_source_import_plan
from ops_data_workflow.source_storage import migrate_legacy_raw_to_source_layout, source_period_from_path
from ops_data_workflow.workflow import run_archived_workflow, run_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ops content category reports.")
    parser.add_argument("--input", help="Directory containing raw Excel files.")
    parser.add_argument("--period-start", help="Report period start date, e.g. 2026-04-01.")
    parser.add_argument("--period-end", help="Report period end date, e.g. 2026-04-27.")
    parser.add_argument("--output", default="outputs/latest", help="Directory for generated artifacts.")
    parser.add_argument("--processed-root", default="processed", help="Directory for cleaned/generated batch artifacts.")
    parser.add_argument("--data-root", default="data", help="Raw source data root.")
    parser.add_argument("--db", default=".runtime/workflow.sqlite3", help="SQLite database path.")
    parser.add_argument("--env", default=".env", help="Path to .env containing DEEPSEEK_API_KEY.")
    parser.add_argument(
        "--legacy-output-only",
        action="store_true",
        help="Generate files without writing the SQLite archive.",
    )
    parser.add_argument(
        "--category-rules",
        default="config/category_rules.yml",
        help="YAML file containing category suggestion rules.",
    )
    parser.add_argument(
        "--purge-history",
        action="store_true",
        help="Delete persisted history results and generated batch artifacts before exiting.",
    )
    parser.add_argument(
        "--migrate-legacy-raw",
        action="store_true",
        help="One-time copy of legacy data/raw source files into data/months or data/weeks.",
    )
    parser.add_argument("--import-source", help="Import an external data directory into data/months, data/weeks, and data/reference.")
    parser.add_argument("--default-year", type=int, default=2026, help="Default year for date ranges without a year.")
    parser.add_argument("--replace-all", action="store_true", help="Clear current source/runtime data before --import-source.")
    parser.add_argument("--dry-run", action="store_true", help="Only print the --import-source plan without copying or generating.")
    parser.add_argument("--ui-only", action="store_true", help="Generate only page data and required cleaned workbooks, without download artifacts or DeepSeek calls.")
    args = parser.parse_args()

    if args.purge_history:
        purge_history_state(Path(args.db), Path(args.output), Path(args.processed_root))
        print("History results purged.")
        return

    if args.migrate_legacy_raw:
        results = migrate_legacy_raw_to_source_layout(Path(args.data_root))
        print(f"Migrated {len(results)} legacy raw directories.")
        return

    if args.import_source:
        try:
            plan = build_source_import_plan(Path(args.import_source), Path(args.data_root), default_year=args.default_year)
        except PermissionError as exc:
            parser.exit(2, f"{exc}\n")
        frame = plan.to_frame()
        if frame.empty:
            print("No importable files found.")
        else:
            print(frame.to_string(index=False))
        if args.dry_run:
            return
        result = execute_source_import_plan(plan, project_root=Path("."), replace_all=args.replace_all)
        print(f"Copied {result.copied_count} files; skipped {result.skipped_count} files.")
        sync_results = sync_raw_periods(
            Path(args.data_root),
            db_path=Path(args.db),
            output_root=Path(args.output),
            processed_root=Path(args.processed_root),
            category_rules_path=Path(args.category_rules),
            env_path=Path(args.env),
            reference_root=Path(args.data_root) / "reference",
            output_mode="ui_only" if args.ui_only else "full",
            enable_deepseek=not args.ui_only,
            enable_external_context=not args.ui_only,
        )
        for item in sync_results:
            print(f"{item.period_name}: {item.status} {item.batch_id} {item.message}")
        return

    missing = [flag for flag, value in [("--input", args.input)] if not value]
    if missing:
        parser.error(f"the following arguments are required unless --purge-history is used: {', '.join(missing)}")

    period_start = args.period_start
    period_end = args.period_end
    if not period_start or not period_end:
        try:
            period = source_period_from_path(Path(args.input))
            period_start, period_end = period.period_start, period.period_end
        except ValueError as exc:
            try:
                period_start, period_end = parse_period_from_raw_dir(Path(args.input))
            except ValueError:
                parser.error(f"{exc}；或显式传入 --period-start 和 --period-end")

    if args.legacy_output_only:
        result = run_workflow(
            Path(args.input),
            period_start,
            period_end,
            Path(args.output),
            Path(args.category_rules),
        )
    else:
        result = run_archived_workflow(
            Path(args.input),
            period_start,
            period_end,
            output_root=Path(args.output),
            processed_root=Path(args.processed_root),
            db_path=Path(args.db),
            category_rules_path=Path(args.category_rules),
            env_path=Path(args.env),
            reference_root=Path(args.data_root) / "reference",
            output_mode="ui_only" if args.ui_only else "full",
            enable_deepseek=not args.ui_only,
            enable_external_context=not args.ui_only,
        )
        print(f"Period stored as {result.batch_id}")
        print(f"Processed artifacts written to {result.archive_dir}")
    if result.report_html is not None:
        print(f"Report written to {result.report_html}")
    if result.analysis_xlsx is not None:
        print(f"Workbook written to {result.analysis_xlsx}")
    if result.canonical_csv is not None:
        print(f"Canonical CSV written to {result.canonical_csv}")
    if result.total_summary_xlsx is not None:
        print(f"Total summary written to {result.total_summary_xlsx}")


if __name__ == "__main__":
    main()
