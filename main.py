from __future__ import annotations

import argparse
from pathlib import Path

from ops_data_workflow.storage import purge_history_state
from ops_data_workflow.reference_tables import parse_period_from_raw_dir
from ops_data_workflow.workflow import run_archived_workflow, run_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ops content category reports.")
    parser.add_argument("--input", help="Directory containing raw Excel files.")
    parser.add_argument("--period-start", help="Report period start date, e.g. 2026-04-01.")
    parser.add_argument("--period-end", help="Report period end date, e.g. 2026-04-27.")
    parser.add_argument("--output", default="outputs/latest", help="Directory for generated artifacts.")
    parser.add_argument("--archive-root", default="archive", help="Directory for archived raw uploads.")
    parser.add_argument("--db", default="data/workflow.sqlite3", help="SQLite database path.")
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
    args = parser.parse_args()

    if args.purge_history:
        purge_history_state(Path(args.db), Path(args.output), Path(args.archive_root))
        print("History results purged.")
        return

    missing = [flag for flag, value in [("--input", args.input)] if not value]
    if missing:
        parser.error(f"the following arguments are required unless --purge-history is used: {', '.join(missing)}")

    period_start = args.period_start
    period_end = args.period_end
    if not period_start or not period_end:
        try:
            period_start, period_end = parse_period_from_raw_dir(Path(args.input))
        except ValueError as exc:
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
            archive_root=Path(args.archive_root),
            db_path=Path(args.db),
            category_rules_path=Path(args.category_rules),
            env_path=Path(args.env),
        )
        print(f"Period stored as {result.batch_id}")
        print(f"Archive written to {result.archive_dir}")
    print(f"Report written to {result.report_html}")
    print(f"Workbook written to {result.analysis_xlsx}")
    print(f"Canonical CSV written to {result.canonical_csv}")
    print(f"Total summary written to {result.total_summary_xlsx}")


if __name__ == "__main__":
    main()
