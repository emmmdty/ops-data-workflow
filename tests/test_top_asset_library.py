from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ops_data_workflow.storage import list_top_asset_cache_entries
from ops_data_workflow.storage import list_top_asset_cache_refs
from ops_data_workflow.storage import upsert_top_asset_cache_entry
from ops_data_workflow.storage import upsert_top_asset_cache_ref
from ops_data_workflow.top_asset_library import consolidate_top_asset_library


class TopAssetLibraryConsolidationTests(unittest.TestCase):
    def test_consolidates_legacy_ops_cache_dir_to_real_id_dir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            legacy_dir = cache_root / "douyin" / "抖音_id_7594830477777751338"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "7594830477777751338.mp4").write_text("video", encoding="utf-8")
            (legacy_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "job_id": "job-1",
                                "status": "succeeded",
                                "platform": "抖音",
                                "asset_key": "抖音::id::7594830477777751338",
                                "asset_dir": str(legacy_dir),
                                "video_path": str(legacy_dir / "7594830477777751338.mp4"),
                                "screenshots": [],
                                "frames": [],
                                "metadata": {},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            target_dir = cache_root / "douyin" / "7594830477777751338"
            target_manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
            entries = list_top_asset_cache_entries(root / "workflow.sqlite3")
            self.assertEqual(result.copied_count, 1)
            self.assertTrue((target_dir / "7594830477777751338.mp4").exists())
            self.assertTrue((legacy_dir / "7594830477777751338.mp4").exists())
            self.assertEqual(target_manifest["items"][0]["asset_key"], "抖音::id::7594830477777751338")
            self.assertEqual(Path(target_manifest["items"][0]["asset_dir"]).resolve(), target_dir.resolve())
            self.assertEqual(entries.iloc[0]["asset_key"], "抖音::id::7594830477777751338")
            self.assertEqual(Path(entries.iloc[0]["asset_dir"]).resolve(), target_dir.resolve())

    def test_skips_douyin_giant_only_manifest_without_real_work_id(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = cache_root / "_capture-runs" / "batch-1" / "output" / "2026-06-11" / "douyin" / "7623721481431780662"
            source_dir.mkdir(parents=True)
            (source_dir / "asset.mp4").write_text("giant", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_key": "",
                        "asset_dir": str(source_dir),
                        "video_path": str(source_dir / "asset.mp4"),
                        "ad_material_id": "7623721481431780662",
                        "metadata": {"source": "giant_asset"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            self.assertEqual(result.copied_count, 0)
            self.assertEqual(result.skipped_giant_only, 1)
            self.assertFalse((cache_root / "douyin" / "7623721481431780662").exists())
            self.assertTrue(list_top_asset_cache_entries(root / "workflow.sqlite3").empty)

    def test_consolidates_harvester_output_to_project_cache(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = root / "harvester-THS" / "output" / "2026-06-09" / "bilibili" / "BV1maEm6hEAN"
            source_dir.mkdir(parents=True)
            (source_dir / "BV1maEm6hEAN.mp4").write_text("video", encoding="utf-8")
            (source_dir / "BV1maEm6hEAN.jpg").write_text("cover", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "platformId": "bilibili",
                        "id": "BV1maEm6hEAN",
                        "link": "https://www.bilibili.com/video/BV1maEm6hEAN/",
                        "dir": str(source_dir),
                        "ok": True,
                        "videoPath": str(source_dir / "BV1maEm6hEAN.mp4"),
                        "imagePaths": [str(source_dir / "BV1maEm6hEAN.jpg")],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            target_dir = cache_root / "bilibili" / "BV1maEm6hEAN"
            target_manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(result.copied_by_platform["bilibili"], 1)
            self.assertTrue((target_dir / "BV1maEm6hEAN.mp4").exists())
            self.assertEqual(target_manifest["items"][0]["asset_key"], "B站::id::BV1maEm6hEAN")
            self.assertEqual(Path(target_manifest["items"][0]["cover_path"]).resolve(), (target_dir / "BV1maEm6hEAN.jpg").resolve())

    def test_consolidates_harvester_runtime_classifier_asset(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = (
                root
                / "harvester-THS"
                / ".runtime"
                / "douyin-channel-type-classifier"
                / "assets"
                / "2026-06-18"
                / "7630649509467241472"
            )
            screenshots_dir = source_dir / "screenshots"
            screenshots_dir.mkdir(parents=True)
            (screenshots_dir / "001.jpg").write_text("shot", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "platform": "douyin",
                        "purpose": "douyin-channel-type-classifier",
                        "link": "https://www.douyin.com/video/7630649509467241472",
                        "awemeId": "7630649509467241472",
                        "assetDir": str(source_dir),
                        "imagePaths": [str(screenshots_dir / "001.jpg")],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            target_dir = cache_root / "douyin" / "7630649509467241472"
            target_manifest = json.loads((target_dir / "manifest.json").read_text(encoding="utf-8"))
            entries = list_top_asset_cache_entries(root / "workflow.sqlite3")
            self.assertEqual(result.copied_count, 1)
            self.assertTrue((target_dir / "screenshots" / "001.jpg").exists())
            self.assertEqual(target_manifest["items"][0]["asset_key"], "抖音::id::7630649509467241472")
            self.assertEqual(entries.iloc[0]["source"], "harvester_runtime_classifier_history")

    def test_step15_numeric_directory_requires_real_douyin_work_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = root / "harvester-THS" / "output" / "step15-assets" / "2026-05-18" / "douyin" / "7641144458641247524"
            source_dir.mkdir(parents=True)
            (source_dir / "asset.mp4").write_text("video", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "platform": "douyin",
                        "targetDate": "2026-05-18",
                        "assetDir": str(source_dir),
                        "videoPath": str(source_dir / "asset.mp4"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            self.assertEqual(result.copied_count, 0)
            self.assertEqual(result.skipped_no_real_id, 1)
            self.assertFalse((cache_root / "douyin" / "7641144458641247524").exists())
            self.assertTrue(list_top_asset_cache_entries(root / "workflow.sqlite3").empty)

    def test_step15_with_aweme_id_is_consolidated(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = root / "harvester-THS" / "output" / "step15-assets" / "2026-05-18" / "douyin" / "7641144458641247524"
            source_dir.mkdir(parents=True)
            (source_dir / "asset.mp4").write_text("video", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "platform": "douyin",
                        "targetDate": "2026-05-18",
                        "link": "https://www.douyin.com/note/7641144458641247524",
                        "awemeId": "7641144458641247524",
                        "assetDir": str(source_dir),
                        "videoPath": str(source_dir / "asset.mp4"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            target_dir = cache_root / "douyin" / "7641144458641247524"
            self.assertEqual(result.copied_count, 1)
            self.assertTrue((target_dir / "asset.mp4").exists())

    def test_step15_with_aweme_id_but_no_media_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = root / "harvester-THS" / "output" / "step15-assets" / "2026-05-18" / "douyin" / "7641144458641247524"
            source_dir.mkdir(parents=True)
            (source_dir / "asr.txt").write_text("", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "platform": "douyin",
                        "targetDate": "2026-05-18",
                        "link": "https://www.douyin.com/note/7641144458641247524",
                        "awemeId": "7641144458641247524",
                        "assetDir": str(source_dir),
                        "videoPath": "",
                        "imagePaths": [],
                        "framePaths": [],
                        "asrPath": str(source_dir / "asr.txt"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            self.assertEqual(result.copied_count, 0)
            self.assertEqual(result.skipped_by_reason["no_media"], 1)
            self.assertFalse((cache_root / "douyin" / "7641144458641247524").exists())

    def test_douyin_giant_metadata_source_is_skipped_even_with_numeric_material_id(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = root / ".runtime" / "top-assets" / "_capture-runs" / "batch-1" / "output" / "2026-06-11" / "douyin" / "7623721481431780662"
            source_dir.mkdir(parents=True)
            (source_dir / "asset.mp4").write_text("giant", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_dir": str(source_dir),
                        "video_path": str(source_dir / "asset.mp4"),
                        "material_id": "7623721481431780662",
                        "ad_material_url": "https://巨量.example/video.mp4",
                        "metadata": {"source": "giant_asset"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            self.assertEqual(result.copied_count, 0)
            self.assertEqual(result.skipped_giant_only, 1)
            self.assertFalse((cache_root / "douyin" / "7623721481431780662").exists())
            self.assertTrue(list_top_asset_cache_entries(root / "workflow.sqlite3").empty)

    def test_douyin_asset_key_id_is_not_giant_from_metadata_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            legacy_dir = cache_root / "douyin" / "抖音_id_7249502467044540419"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "cover.jpg").write_text("cover", encoding="utf-8")
            (legacy_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "status": "succeeded",
                                "platform": "抖音",
                                "asset_key": "抖音::id::7249502467044540419",
                                "asset_dir": str(legacy_dir),
                                "cover_path": str(legacy_dir / "cover.jpg"),
                                "metadata": {
                                    "ops_cache_source_asset_dir": str(legacy_dir),
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            self.assertEqual(result.skipped_giant_only, 0)
            self.assertEqual(result.copied_count, 1)
            self.assertTrue((cache_root / "douyin" / "7249502467044540419" / "cover.jpg").exists())

    def test_existing_target_dir_counts_as_updated_not_copied_in_dry_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            target_dir = cache_root / "bilibili" / "BV1same"
            target_dir.mkdir(parents=True)
            (target_dir / "cover.jpg").write_text("cover", encoding="utf-8")
            (target_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "status": "succeeded",
                                "platform": "B站",
                                "asset_key": "B站::id::BV1same",
                                "asset_dir": str(target_dir),
                                "cover_path": str(target_dir / "cover.jpg"),
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
                dry_run=True,
            )

            self.assertEqual(result.copied_count, 0)
            self.assertEqual(result.updated_count, 1)

    def test_migrates_legacy_douyin_work_cache_entry_to_standard_id_key(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "workflow.sqlite3"
            cache_root = root / ".runtime" / "top-assets"
            legacy_dir = cache_root / "douyin" / "douyin_work_7633640394797993259"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "video.mp4").write_text("video", encoding="utf-8")
            (legacy_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "platform": "douyin",
                        "link": "https://www.douyin.com/note/7633640394797993259",
                        "awemeId": "7633640394797993259",
                        "assetDir": str(legacy_dir),
                        "videoPath": str(legacy_dir / "video.mp4"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            upsert_top_asset_cache_entry(
                db_path,
                {
                    "asset_key": "douyin:work:7633640394797993259",
                    "content_id": "7633640394797993259",
                    "platform": "抖音",
                    "source": "legacy",
                    "asset_dir": str(legacy_dir),
                    "size_bytes": 1,
                    "last_used_batch_id": "batch-1",
                },
            )
            upsert_top_asset_cache_ref(
                db_path,
                batch_id="batch-1",
                job_id="job-1",
                content_identity_key="identity-1",
                asset_key="douyin:work:7633640394797993259",
                retained=True,
            )

            consolidate_top_asset_library(
                db_path=db_path,
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
            )

            entries = list_top_asset_cache_entries(db_path)
            refs = list_top_asset_cache_refs(db_path)
            self.assertIn("抖音::id::7633640394797993259", set(entries["asset_key"].astype(str)))
            self.assertNotIn("douyin:work:7633640394797993259", set(entries["asset_key"].astype(str)))
            self.assertEqual(refs.iloc[0]["asset_key"], "抖音::id::7633640394797993259")
            self.assertTrue((cache_root / "douyin" / "7633640394797993259" / "video.mp4").exists())
            self.assertTrue((legacy_dir / "video.mp4").exists())

    def test_dry_run_records_skip_samples_and_source_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_root = root / ".runtime" / "top-assets"
            source_dir = cache_root / "_capture-runs" / "batch-1" / "output" / "2026-06-11" / "douyin" / "7623721481431780662"
            source_dir.mkdir(parents=True)
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_dir": str(source_dir),
                        "ad_material_id": "7623721481431780662",
                        "metadata": {"source": "giant_asset"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = consolidate_top_asset_library(
                db_path=root / "workflow.sqlite3",
                cache_root=cache_root,
                harvester_root=root / "harvester-THS",
                ops_runtime_root=root / ".runtime" / "harvester",
                dry_run=True,
            )

            self.assertEqual(result.scanned_manifests, 1)
            self.assertEqual(result.skipped_by_reason["giant_only"], 1)
            self.assertEqual(result.scanned_by_source["ops_top_assets_history"], 1)
            self.assertIn("giant_only", result.skip_samples)
            self.assertEqual(Path(result.skip_samples["giant_only"][0]).resolve(), (source_dir / "manifest.json").resolve())
