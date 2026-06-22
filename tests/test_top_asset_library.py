from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ops_data_workflow.storage import list_top_asset_cache_entries
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
