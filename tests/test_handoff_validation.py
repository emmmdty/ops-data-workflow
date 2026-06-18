from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

import pandas as pd

from ops_data_workflow.analysis_jobs import enqueue_top_multimodal_jobs, record_job_success
from ops_data_workflow.controlled_backfill import CONTROLLED_BACKFILL_BATCH_IDS
from ops_data_workflow.handoff_validation import build_handoff_validation_report, format_handoff_report
from ops_data_workflow.harvester_bridge import build_asset_jobs
from ops_data_workflow.storage import (
    init_db,
    persist_harvester_asset_jobs,
    persist_harvester_asset_manifests,
    persist_content_performance_items,
)


class HandoffValidationTests(unittest.TestCase):
    def test_report_covers_only_controlled_periods_and_requires_core_handoff_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "workflow.sqlite3"
            init_db(db_path)
            for batch_id in CONTROLLED_BACKFILL_BATCH_IDS:
                top_content = _top_content(batch_id)
                persist_content_performance_items(db_path, batch_id, top_content)
                _insert_asset_match_result(db_path, batch_id)
                harvester_job = build_asset_jobs(batch_id, top_content)[0]
                persist_harvester_asset_jobs(
                    db_path,
                    batch_id,
                    [harvester_job],
                    status="succeeded",
                    harvester_root=root / "harvester-THS",
                    jobs_path=root / ".runtime" / "harvester" / batch_id / "jobs.jsonl",
                    manifest_path=root / ".runtime" / "harvester" / batch_id / "manifest.json",
                )
                asset_dir = root / ".runtime" / "top-assets" / "douyin" / harvester_job["job_id"]
                persist_harvester_asset_manifests(
                    db_path,
                    batch_id,
                    [
                        {
                            "job_id": harvester_job["job_id"],
                            "status": "succeeded",
                            "platform": "抖音",
                            "asset_dir": str(asset_dir),
                            "video_path": str(asset_dir / "video.mp4"),
                        }
                    ],
                )
                analysis_job_id = enqueue_top_multimodal_jobs(db_path, batch_id, top_content, trigger="test")[0]
                record_job_success(db_path, analysis_job_id, {"内容形态": "视频", "标题钩子": "问题开场"})

            statuses = build_handoff_validation_report(db_path)

            self.assertEqual([status.batch_id for status in statuses], list(CONTROLLED_BACKFILL_BATCH_IDS))
            self.assertNotIn("upload:month:2026-06", [status.batch_id for status in statuses])
            self.assertTrue(all(status.ok for status in statuses))
            self.assertTrue(all(status.performance_count == 1 for status in statuses))
            self.assertTrue(all(status.asset_match_count == 1 for status in statuses))
            self.assertTrue(all(status.cache_path_count == 1 for status in statuses))
            self.assertIn("upload:week:20260605-20260611", format_handoff_report(statuses))

    def test_report_surfaces_missing_multimodal_reason(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "workflow.sqlite3"
            init_db(db_path)
            batch_id = CONTROLLED_BACKFILL_BATCH_IDS[0]
            top_content = _top_content(batch_id)
            persist_content_performance_items(db_path, batch_id, top_content)
            _insert_asset_match_result(db_path, batch_id)
            harvester_job = build_asset_jobs(batch_id, top_content)[0]
            persist_harvester_asset_jobs(
                db_path,
                batch_id,
                [harvester_job],
                status="succeeded",
                harvester_root=root / "harvester-THS",
                jobs_path=root / "jobs.jsonl",
                manifest_path=root / "manifest.json",
            )
            asset_dir = root / ".runtime" / "top-assets" / "douyin" / harvester_job["job_id"]
            persist_harvester_asset_manifests(
                db_path,
                batch_id,
                [{"job_id": harvester_job["job_id"], "status": "succeeded", "platform": "抖音", "asset_dir": str(asset_dir)}],
            )
            enqueue_top_multimodal_jobs(db_path, batch_id, top_content, trigger="test")

            status = build_handoff_validation_report(db_path, batch_ids=[batch_id])[0]

            self.assertFalse(status.ok)
            self.assertEqual(status.failure_reason, "多模态分析尚未成功")


def _top_content(batch_id: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "period_start": "2026-06-05",
                "period_end": "2026-06-11",
                "platform": "抖音",
                "channel": "抖音商业化",
                "content_identity_key": f"{batch_id}::抖音::id::7594830477777751338",
                "content_id": "7594830477777751338",
                "content_url": "https://www.douyin.com/video/7594830477777751338",
                "title": "Top素材",
                "account": "示例账号",
                "match_status": "已匹配",
                "spend": 3000,
                "impressions": 120000,
                "clicks": 100,
                "activations": 10,
                "first_pay_count": 1,
            }
        ]
    )


def _insert_asset_match_result(db_path: Path, batch_id: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into asset_match_results (
                batch_id, period_start, period_end, platform, channel,
                content_identity_key, content_id, material_id, title, content_url,
                match_status, match_source, match_key, match_confidence,
                match_reason, matched_category_l1, matched_category_l2,
                matched_bilibili_content_type, matched_account
            )
            values (?, '2026-06-05', '2026-06-11', '抖音', '抖音商业化',
                ?, '7594830477777751338', '', 'Top素材',
                'https://www.douyin.com/video/7594830477777751338',
                '已匹配', 'ID', '7594830477777751338', 1.0,
                '', '投教', '方法论', '', '示例账号')
            """,
            (batch_id, f"{batch_id}::抖音::id::7594830477777751338"),
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
