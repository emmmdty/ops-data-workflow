#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_data_workflow.harvester_bridge import resolve_harvester_root
from ops_data_workflow.top_asset_library import consolidate_top_asset_library


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidate reusable historical Top assets into the project material library.")
    parser.add_argument("--db", default=".runtime/workflow.sqlite3", help="workflow SQLite database path")
    parser.add_argument("--cache-root", default=".runtime/top-assets", help="project material library root")
    parser.add_argument("--harvester-root", default="", help="sibling harvester-THS root; defaults to ../harvester-THS")
    parser.add_argument("--ops-runtime-root", default=".runtime/harvester", help="ops harvester manifest runtime root")
    parser.add_argument("--dry-run", action="store_true", help="scan only; do not copy files or update database")
    args = parser.parse_args()

    harvester_root = Path(args.harvester_root).expanduser().resolve() if args.harvester_root else resolve_harvester_root()
    result = consolidate_top_asset_library(
        db_path=Path(args.db),
        cache_root=Path(args.cache_root),
        harvester_root=harvester_root,
        ops_runtime_root=Path(args.ops_runtime_root),
        dry_run=bool(args.dry_run),
    )
    print(f"scanned_manifests={result.scanned_manifests}")
    print(f"copied_count={result.copied_count}")
    print(f"updated_count={result.updated_count}")
    print(f"skipped_no_real_id={result.skipped_no_real_id}")
    print(f"skipped_giant_only={result.skipped_giant_only}")
    print(f"skipped_missing_dir={result.skipped_missing_dir}")
    for platform, count in sorted(result.copied_by_platform.items()):
        print(f"copied_by_platform.{platform}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
