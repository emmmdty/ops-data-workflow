#!/usr/bin/env python3
"""Command-line controller for Douyin historical ledger collection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ops_data_workflow.douyin_history_ledger import (
    DEFAULT_HARVESTER_ROOT,
    copy_harvester_feishu_env,
    history_records_from_harvester_json,
    init_douyin_history_sheet,
    load_harvester_douyin_accounts,
    run_harvester_douyin_history_crawl,
    upsert_douyin_history_records,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="抖音历史台账采集与飞书写入主控。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    copy_env = subparsers.add_parser("copy-env", help="从 harvester-THS .env 复制 FEISHU_* 配置到本项目 .env。")
    copy_env.add_argument("--source", default=str(DEFAULT_HARVESTER_ROOT / ".env"), help="harvester-THS .env 路径。")
    copy_env.add_argument("--target", default=".env", help="本项目 .env 路径。")

    init_sheet = subparsers.add_parser("init-sheet", help="创建或初始化飞书 Sheet：抖音历史台账。")
    init_sheet.add_argument("--env", default=".env", help="本项目 .env 路径。")
    init_sheet.add_argument("--no-env-update", action="store_true", help="不把新 sheet_id 回写到 FEISHU_SHEET_DOUYIN_HISTORY。")

    import_json = subparsers.add_parser("import-json", help="把 harvester Douyin JSON 输出导入抖音历史台账。")
    import_json.add_argument("--json", required=True, help="harvester output/douyin_notes_*.json 路径。")
    import_json.add_argument("--harvester-root", default=str(DEFAULT_HARVESTER_ROOT), help="harvester-THS 项目路径。")
    import_json.add_argument("--env", default=".env", help="本项目 .env 路径。")
    import_json.add_argument("--batch-size", type=int, default=100, help="每批追加写入行数。")
    import_json.add_argument("--records-output", default=".runtime/douyin-history/imported-records.json", help="本地审计副本路径。")
    import_json.add_argument("--skip-feishu", action="store_true", help="只生成本地审计副本，不写入飞书。")

    crawl = subparsers.add_parser("crawl", help="调用 harvester-THS 抖音爬虫采集当前可见历史作品。")
    crawl.add_argument("--harvester-root", default=str(DEFAULT_HARVESTER_ROOT), help="harvester-THS 项目路径。")
    crawl.add_argument("--env", default=".env", help="本项目 .env 路径。")
    crawl.add_argument("--since", default="2000-01-01", help="采集起始日期；历史全量默认 2000-01-01。")
    crawl.add_argument("--until", default="", help="采集结束日期；默认今天。")
    crawl.add_argument("--max-scrolls", type=int, default=500, help="每个账号最大下翻次数。")
    crawl.add_argument("--max-detail-pages", type=int, default=5000, help="最大详情页检查数。")
    crawl.add_argument("--max-items", type=int, default=None, help="小批测试时限制导入记录数。")
    crawl.add_argument("--skip-feishu", action="store_true", help="只生成本地审计副本，不写入飞书。")
    crawl.add_argument("--records-output-dir", default=".runtime/douyin-history", help="本地审计副本目录。")

    args = parser.parse_args(argv)

    if args.command == "copy-env":
        result = copy_harvester_feishu_env(Path(args.source), Path(args.target))
        print(
            "飞书配置复制完成："
            f" copied={len(result.copied)} kept={len(result.kept)} skipped_empty={len(result.skipped_empty)} "
            f"target={result.target_path}"
        )
        return

    if args.command == "init-sheet":
        result = init_douyin_history_sheet(
            env_path=Path(args.env),
            update_env_path=None if args.no_env_update else Path(args.env),
        )
        print(
            "抖音历史台账 Sheet 初始化完成："
            f"title={result.title} sheet_id={result.sheet_id} created={result.created}"
        )
        return

    if args.command == "import-json":
        accounts = load_harvester_douyin_accounts(Path(args.harvester_root))
        records = history_records_from_harvester_json(Path(args.json), accounts=accounts, source="harvester-THS")
        records_output = Path(args.records_output)
        records_output.parent.mkdir(parents=True, exist_ok=True)
        records_output.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.skip_feishu:
            print(f"本地审计副本已生成：records={len(records)} path={records_output}")
            return
        result = upsert_douyin_history_records(records, env_path=Path(args.env), batch_size=args.batch_size)
        print(
            "抖音历史台账导入完成："
            f"records={len(records)} created={result.created} updated={result.updated} skipped={result.skipped} "
            f"sheet_id={result.sheet_id}"
        )
        return

    if args.command == "crawl":
        result = run_harvester_douyin_history_crawl(
            harvester_root=Path(args.harvester_root),
            since=args.since,
            until=args.until or None,
            max_scrolls=args.max_scrolls,
            max_detail_pages=args.max_detail_pages,
            max_items=args.max_items,
            skip_feishu=args.skip_feishu,
            env_path=Path(args.env),
            records_output_dir=Path(args.records_output_dir),
        )
        print(
            "抖音历史采集完成："
            f"records={result.record_count} json={result.json_path} audit={result.records_path}"
        )
        return


if __name__ == "__main__":
    main()
