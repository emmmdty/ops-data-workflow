from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.analysis_jobs import (
    ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
    ANALYSIS_PURPOSE_STRATEGY_RECAP,
    enqueue_top_multimodal_jobs,
    list_analysis_jobs,
    record_job_failure,
    run_top_multimodal_analysis_from_manifests,
    reset_top_multimodal_jobs,
)
from ops_data_workflow.storage import persist_harvester_asset_jobs, persist_harvester_asset_manifests


class AnalysisJobTests(unittest.TestCase):
    def test_enqueues_top_multimodal_jobs_once_per_batch_and_marks_failures_visible(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "douyin-1",
                        "title": "抖音Top",
                        "content_url": "https://www.douyin.com/video/1",
                        "spend": 100.0,
                    }
                ]
            )

            first = enqueue_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="upload",
                prompt_hint="重点分析爆量共性",
            )
            second = enqueue_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="upload",
                prompt_hint="重点分析爆量共性",
            )

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            record_job_failure(db_path, first[0], "429 Too Many Requests", max_attempts=1)
            jobs = list_analysis_jobs(db_path, batch_id="batch-1")

            self.assertEqual(jobs.iloc[0]["status"], "failed")
            self.assertTrue(bool(jobs.iloc[0]["visible_alert"]))
            self.assertIn("429", jobs.iloc[0]["error_message"])
            self.assertEqual(jobs.iloc[0]["trigger"], "upload")
            self.assertEqual(jobs.iloc[0]["prompt_hint"], "重点分析爆量共性")
            self.assertEqual(jobs.iloc[0]["analysis_purpose"], ANALYSIS_PURPOSE_STRATEGY_RECAP)

    def test_top_multimodal_jobs_are_distinct_per_analysis_purpose(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "douyin-1",
                        "title": "抖音Top",
                        "content_url": "https://www.douyin.com/video/1",
                    }
                ]
            )

            fill_jobs = enqueue_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="manual_fill_type",
                analysis_purpose=ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
            )
            strategy_jobs = enqueue_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="manual_strategy",
                analysis_purpose=ANALYSIS_PURPOSE_STRATEGY_RECAP,
            )
            duplicate_fill = enqueue_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="manual_fill_type",
                analysis_purpose=ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
            )

            jobs = list_analysis_jobs(db_path, batch_id="batch-1").sort_values("analysis_purpose")

            self.assertEqual(len(fill_jobs), 1)
            self.assertEqual(len(strategy_jobs), 1)
            self.assertNotEqual(fill_jobs[0], strategy_jobs[0])
            self.assertEqual(duplicate_fill, [])
            self.assertEqual(set(jobs["analysis_purpose"]), {ANALYSIS_PURPOSE_FILL_MISSING_TYPE, ANALYSIS_PURPOSE_STRATEGY_RECAP})
            self.assertIn(ANALYSIS_PURPOSE_FILL_MISSING_TYPE, jobs.iloc[0]["payload_json"])

    def test_strategy_jobs_are_distinct_per_recap_tier_purpose(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "douyin-1",
                        "title": "抖音Top",
                        "content_url": "https://www.douyin.com/video/1",
                    }
                ]
            )

            tier1 = enqueue_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="tier1",
                analysis_purpose="strategy_recap:tier1_spend_top",
            )
            tier2 = enqueue_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="tier2",
                analysis_purpose="strategy_recap:tier2_exposure_top",
            )
            jobs = list_analysis_jobs(db_path, batch_id="batch-1")

            self.assertEqual(len(tier1), 1)
            self.assertEqual(len(tier2), 1)
            self.assertNotEqual(tier1[0], tier2[0])
            self.assertEqual(
                set(jobs["analysis_purpose"]),
                {"strategy_recap:tier1_spend_top", "strategy_recap:tier2_exposure_top"},
            )

    def test_manual_top_analysis_reset_requeues_existing_jobs_with_new_prompt(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "B站",
                        "platform": "B站",
                        "content_identity_key": "bilibili-1",
                        "title": "B站Top",
                        "content_url": "https://www.bilibili.com/video/BV1/",
                        "spend": 2500.0,
                    }
                ]
            )
            job_id = enqueue_top_multimodal_jobs(db_path, "batch-1", top_content, trigger="upload")[0]
            record_job_failure(db_path, job_id, "登录态失效", max_attempts=1)

            reset_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="manual_top_analysis",
                prompt_hint="重点看选题共性",
                analysis_purpose=ANALYSIS_PURPOSE_STRATEGY_RECAP,
            )

            jobs = list_analysis_jobs(db_path, batch_id="batch-1")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs.iloc[0]["status"], "queued")
            self.assertFalse(bool(jobs.iloc[0]["visible_alert"]))
            self.assertEqual(jobs.iloc[0]["attempts"], 0)
            self.assertEqual(jobs.iloc[0]["trigger"], "manual_top_analysis")
            self.assertEqual(jobs.iloc[0]["prompt_hint"], "重点看选题共性")
            self.assertEqual(jobs.iloc[0]["analysis_purpose"], ANALYSIS_PURPOSE_STRATEGY_RECAP)

    def test_manual_top_analysis_reset_removes_stale_jobs_outside_current_pool(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            old_pool = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "keep-1",
                        "title": "保留",
                        "content_url": "https://www.douyin.com/video/1",
                    },
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "stale-1",
                        "title": "过期",
                        "content_url": "https://www.douyin.com/video/2",
                    },
                ]
            )
            enqueue_top_multimodal_jobs(db_path, "batch-1", old_pool, trigger="upload")

            reset_top_multimodal_jobs(
                db_path,
                "batch-1",
                old_pool.iloc[[0]].copy(),
                trigger="manual_top_analysis",
            )

            jobs = list_analysis_jobs(db_path, batch_id="batch-1")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs.iloc[0]["content_identity_key"], "keep-1")

    def test_multimodal_payload_keeps_douyin_giant_asset_links_out_of_work_url(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "douyin-title-1",
                        "content_id": "",
                        "title": "巨量素材可分析",
                        "content_url": "",
                        "ad_material_url": "https://巨量.example/video.mp4",
                        "ad_cover_url": "https://巨量.example/cover.jpg",
                        "spend": 3000.0,
                    }
                ]
            )

            enqueue_top_multimodal_jobs(db_path, "batch-1", top_content, trigger="upload")
            jobs = list_analysis_jobs(db_path, batch_id="batch-1")
            payload = jobs.iloc[0]["payload_json"]

            self.assertIn('"content_url": ""', payload)
            self.assertIn('"ad_material_url": "https://巨量.example/video.mp4"', payload)
            self.assertIn('"ad_cover_url": "https://巨量.example/cover.jpg"', payload)

    def test_runs_top_multimodal_analysis_from_successful_harvester_manifests(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "小红书商业化",
                        "platform": "小红书",
                        "content_identity_key": "xhs-1",
                        "title": "小红书Top",
                        "account": "福利官",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "spend": 3000.0,
                        "impressions": 120000.0,
                        "activations": 42.0,
                        "first_pay_count": 6.0,
                        "category_l1": "图文",
                        "category_l2": "理财方法",
                    }
                ]
            )
            job_id = enqueue_top_multimodal_jobs(db_path, "batch-1", top_content, trigger="upload")[0]
            persist_harvester_asset_manifests(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": job_id,
                        "status": "succeeded",
                        "platform": "小红书",
                        "asset_dir": "/tmp/assets/note-1",
                        "cover_path": "/tmp/assets/note-1/cover.jpg",
                        "video_path": "",
                        "screenshots": ["/tmp/assets/note-1/screen.jpg"],
                        "frames": [],
                        "metadata": {"category_l1": "投教", "category_l2": "方法论"},
                        "error_message": "",
                    }
                ],
            )

            updated = run_top_multimodal_analysis_from_manifests(
                db_path,
                "batch-1",
                analyzer=lambda job, manifest: {
                    "内容形态": "图文",
                    "一级内容类型": manifest["metadata"]["category_l1"],
                    "二级内容类型": manifest["metadata"]["category_l2"],
                    "B站内容类型": "",
                    "标题钩子": "利益点开头",
                    "视觉结构": "封面加正文截图",
                    "信息密度": "中",
                    "转化路径": "标题引导到正文",
                    "可复用点": "明确问题场景",
                    "不建议复用点": "避免过长标题",
                    "下周期策略建议": "保留强问题标题",
                    "共性总结": "问题场景明确",
                },
            )
            jobs = list_analysis_jobs(db_path, batch_id="batch-1")

            self.assertEqual(updated, 1)
            row = jobs.iloc[0]
            self.assertEqual(row["status"], "succeeded")
            self.assertIn('"account": "福利官"', row["payload_json"])
            self.assertIn('"impressions": "120000.0"', row["payload_json"])
            self.assertIn('"activations": "42.0"', row["payload_json"])
            self.assertIn('"first_pay_count": "6.0"', row["payload_json"])
            self.assertIn('"category_l1": "图文"', row["payload_json"])
            self.assertIn('"category_l2": "理财方法"', row["payload_json"])
            self.assertIn('"内容形态": "图文"', row["result_json"])
            self.assertIn('"共性总结": "问题场景明确"', row["result_json"])
            self.assertEqual(row["error_message"], "")

    def test_multimodal_analysis_matches_harvester_manifest_by_content_identity_key(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "douyin-content-1",
                        "title": "抖音Top",
                        "content_url": "https://www.douyin.com/video/1",
                        "spend": 3000.0,
                    }
                ]
            )
            enqueue_top_multimodal_jobs(db_path, "batch-1", top_content, trigger="upload")
            persist_harvester_asset_jobs(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": "harvester-job-1",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "douyin-content-1",
                    }
                ],
                status="succeeded",
                harvester_root=tmp_path / "harvester-THS",
                jobs_path=tmp_path / "jobs.jsonl",
                manifest_path=tmp_path / "manifest.json",
            )
            persist_harvester_asset_manifests(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": "harvester-job-1",
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_dir": "/tmp/assets/douyin-1",
                        "cover_path": "",
                        "video_path": "/tmp/assets/douyin-1/video.mp4",
                        "screenshots": [],
                        "frames": ["/tmp/assets/douyin-1/frame.jpg"],
                        "metadata": {"category_l1": "投教", "category_l2": "股票入门"},
                        "error_message": "",
                    }
                ],
            )

            updated = run_top_multimodal_analysis_from_manifests(db_path, "batch-1")
            jobs = list_analysis_jobs(db_path, batch_id="batch-1")

            self.assertEqual(updated, 1)
            row = jobs.iloc[0]
            self.assertEqual(row["status"], "succeeded")
            self.assertIn('"内容形态": "视频"', row["result_json"])
            self.assertIn('"一级内容类型": "投教"', row["result_json"])

    def test_multimodal_analysis_keeps_jobs_queued_until_assets_are_collected(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "B站",
                        "platform": "B站",
                        "content_identity_key": "bili-1",
                        "title": "B站Top",
                        "content_url": "https://www.bilibili.com/video/BV1/",
                        "spend": 3000.0,
                    }
                ]
            )
            enqueue_top_multimodal_jobs(db_path, "batch-1", top_content, trigger="upload")

            updated = run_top_multimodal_analysis_from_manifests(db_path, "batch-1")
            jobs = list_analysis_jobs(db_path, batch_id="batch-1")

            self.assertEqual(updated, 0)
            self.assertEqual(jobs.iloc[0]["status"], "queued")
            self.assertFalse(bool(jobs.iloc[0]["visible_alert"]))
            self.assertEqual(jobs.iloc[0]["error_message"], "")

    def test_multimodal_analysis_uses_douyin_ad_material_evidence_without_manifest(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "dy-giant-1",
                        "content_id": "",
                        "title": "巨量素材可先分析",
                        "content_url": "",
                        "ad_material_url": "https://巨量.example/video.mp4",
                        "ad_cover_url": "https://巨量.example/cover.jpg",
                        "spend": 3000.0,
                        "impressions": 120000.0,
                    }
                ]
            )
            enqueue_top_multimodal_jobs(db_path, "batch-1", top_content, trigger="upload")

            seen = {}
            updated = run_top_multimodal_analysis_from_manifests(
                db_path,
                "batch-1",
                analyzer=lambda job, manifest: seen.setdefault(
                    "payload",
                    {
                        "内容形态": "视频",
                        "一级内容类型": "视频",
                        "二级内容类型": "投顾观点",
                        "视觉结构": ",".join(manifest["remote_media_urls"]),
                        "共性总结": manifest["metadata"]["evidence_source"],
                    },
                ),
            )
            jobs = list_analysis_jobs(db_path, batch_id="batch-1")

            self.assertEqual(updated, 1)
            self.assertEqual(jobs.iloc[0]["status"], "succeeded")
            self.assertEqual(seen["payload"]["视觉结构"], "https://巨量.example/cover.jpg,https://巨量.example/video.mp4")
            self.assertIn('"共性总结": "douyin_ad_material"', jobs.iloc[0]["result_json"])

    def test_multimodal_analysis_can_run_only_one_tier_purpose(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "platform": "抖音",
                        "content_identity_key": "dy-1",
                        "title": "抖音Top",
                        "content_url": "https://www.douyin.com/video/1",
                    }
                ]
            )
            reset_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="tier1",
                analysis_purpose="strategy_recap:tier1_spend_top",
            )
            reset_top_multimodal_jobs(
                db_path,
                "batch-1",
                top_content,
                trigger="tier2",
                analysis_purpose="strategy_recap:tier2_exposure_top",
            )
            persist_harvester_asset_jobs(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": "harvester-job-1",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-1",
                    }
                ],
                status="succeeded",
                harvester_root=Path(tmp) / "harvester",
                jobs_path=Path(tmp) / "jobs.jsonl",
                manifest_path=Path(tmp) / "manifest.json",
            )
            persist_harvester_asset_manifests(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": "harvester-job-1",
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_dir": "/tmp/assets/dy-1",
                    }
                ],
            )

            updated = run_top_multimodal_analysis_from_manifests(
                db_path,
                "batch-1",
                analysis_purpose="strategy_recap:tier1_spend_top",
            )
            jobs = list_analysis_jobs(db_path, batch_id="batch-1").set_index("analysis_purpose")

            self.assertEqual(updated, 1)
            self.assertEqual(jobs.loc["strategy_recap:tier1_spend_top", "status"], "succeeded")
            self.assertEqual(jobs.loc["strategy_recap:tier2_exposure_top", "status"], "queued")
