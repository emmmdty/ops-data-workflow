"""Read-only Feishu and harvester integration smoke check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ops_data_workflow.env_bridge import copy_missing_runtime_env, resolve_harvester_env_path, resolve_harvester_root
from ops_data_workflow.feishu_ledger import load_feishu_content_ledger
from ops_data_workflow.harvester_bridge import harvester_cli_available


PLATFORM_NAMES = ["抖音", "小红书", "B站"]


def run_smoke(*, project_root: Path | None = None, env_path: Path | None = None) -> dict[str, Any]:
    root = Path(project_root) if project_root is not None else Path.cwd()
    env_file = Path(env_path) if env_path is not None else root / ".env"
    harvester_root = resolve_harvester_root(project_root=root)
    harvester_env = resolve_harvester_env_path(project_root=root)
    env_result = copy_missing_runtime_env(harvester_env, env_file)
    ledger = load_feishu_content_ledger(env_path=env_file)
    snapshot = ledger.attrs.get("feishu_snapshot", {}) if hasattr(ledger, "attrs") else {}
    staleness = ledger.attrs.get("feishu_staleness", {}) if hasattr(ledger, "attrs") else {}
    platform_counts = _platform_counts(ledger, snapshot)
    sheet_row_counts = {str(key): int(value) for key, value in (snapshot.get("sheet_row_counts") or {}).items()}
    cli_available = harvester_cli_available(harvester_root)
    warnings = [str(item) for item in (ledger.attrs.get("ledger_warnings", []) if hasattr(ledger, "attrs") else [])]
    summary = {
        "ok": bool(ledger.attrs.get("feishu_enabled", False) if hasattr(ledger, "attrs") else False) and cli_available,
        "project_root": str(root.resolve()),
        "harvester_root": str(harvester_root),
        "harvester_env_exists": bool(harvester_env.exists()),
        "harvester_cli_available": bool(cli_available),
        "feishu_enabled": bool(ledger.attrs.get("feishu_enabled", False) if hasattr(ledger, "attrs") else False),
        "total_rows": int(len(ledger)),
        "platform_counts": platform_counts,
        "sheet_row_counts": sheet_row_counts,
        "staleness_needs_check": bool(staleness.get("needs_check", False)),
        "staleness_platforms": [str(item) for item in staleness.get("needs_check_platforms", [])],
        "warnings": warnings,
        "env_synced": {
            "source_env": str(env_result.source_env),
            "target_env": str(env_result.target_env),
            "copied": list(env_result.copied),
            "kept": list(env_result.kept),
            "missing": list(env_result.missing),
        },
    }
    return summary


def main() -> int:
    summary = run_smoke()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


def _platform_counts(ledger: Any, snapshot: dict[str, Any]) -> dict[str, int]:
    counts = snapshot.get("platform_counts")
    if isinstance(counts, dict) and counts:
        return {str(key): int(value) for key, value in counts.items()}
    if getattr(ledger, "empty", True) or "platform" not in getattr(ledger, "columns", []):
        return {platform: 0 for platform in PLATFORM_NAMES}
    series = ledger["platform"].fillna("").astype(str).value_counts(dropna=False).to_dict()
    return {platform: int(series.get(platform, 0)) for platform in PLATFORM_NAMES}


if __name__ == "__main__":
    raise SystemExit(main())
