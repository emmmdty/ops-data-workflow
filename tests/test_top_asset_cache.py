from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ops_data_workflow.storage import (
    get_top_asset_cache_summary,
    list_top_asset_cache_entries,
    list_top_asset_cache_refs,
    upsert_top_asset_cache_entry,
    upsert_top_asset_cache_ref,
)
from ops_data_workflow.top_asset_cache import (
    asset_cache_path,
    asset_key_for_job,
    cleanup_top_asset_cache,
    directory_size,
    reusable_asset_cache_path,
)


class TopAssetCacheTests(unittest.TestCase):
    def test_asset_key_and_path_are_stable_across_batches(self):
        job = {
            "job_id": "batch-specific-job",
            "platform": "B站",
            "content_id": "BV1same",
            "content_url": "https://www.bilibili.com/video/BV1same/",
        }

        asset_key = asset_key_for_job(job)
        path = asset_cache_path(Path("/tmp/cache"), job)

        self.assertEqual(asset_key, "B站::id::BV1same")
        self.assertEqual(path.parent.name, "bilibili")
        self.assertEqual(path.name, "BV1same")
        self.assertEqual(reusable_asset_cache_path(Path("/tmp/cache"), job).name, "BV1same")
        self.assertNotIn("batch-specific-job", str(path))

    def test_cache_entry_and_refs_track_cross_period_usage(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            asset_dir = Path(tmp) / ".runtime" / "top-assets" / "bilibili" / "BV1same"
            asset_dir.mkdir(parents=True)
            (asset_dir / "cover.jpg").write_text("cover", encoding="utf-8")

            upsert_top_asset_cache_entry(
                db_path,
                {
                    "asset_key": "B站::id::BV1same",
                    "content_id": "BV1same",
                    "platform": "B站",
                    "source": "harvester_daily_cache",
                    "asset_dir": str(asset_dir),
                    "size_bytes": directory_size(asset_dir),
                    "last_used_batch_id": "batch-1",
                },
            )
            upsert_top_asset_cache_ref(
                db_path,
                batch_id="batch-1",
                job_id="job-1",
                content_identity_key="B站市场部::B站::id::BV1same",
                asset_key="B站::id::BV1same",
                retained=True,
            )
            upsert_top_asset_cache_ref(
                db_path,
                batch_id="batch-2",
                job_id="job-2",
                content_identity_key="B站商业化::B站::id::BV1same",
                asset_key="B站::id::BV1same",
                retained=True,
            )

            entries = list_top_asset_cache_entries(db_path)
            refs = list_top_asset_cache_refs(db_path)
            summary = get_top_asset_cache_summary(db_path)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries.iloc[0]["asset_key"], "B站::id::BV1same")
            self.assertEqual(int(entries.iloc[0]["ref_count"]), 2)
            self.assertEqual(entries.iloc[0]["last_used_batch_id"], "batch-2")
            self.assertEqual(len(refs), 2)
            self.assertEqual(summary["entry_count"], 1)
            self.assertGreater(summary["size_bytes"], 0)

    def test_cleanup_only_deletes_ops_cache_not_retained_by_recent_batches(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "workflow.sqlite3"
            old_dir = root / ".runtime" / "top-assets" / "douyin" / "old"
            recent_dir = root / ".runtime" / "top-assets" / "douyin" / "recent"
            harvester_dir = root / "harvester-THS" / "output" / "2026-06-11" / "douyin" / "source"
            for folder in [old_dir, recent_dir, harvester_dir]:
                folder.mkdir(parents=True)
                (folder / "asset.bin").write_text("x" * 20, encoding="utf-8")
            upsert_top_asset_cache_entry(
                db_path,
                {
                    "asset_key": "抖音::id::old",
                    "content_id": "old",
                    "platform": "抖音",
                    "source": "harvester_daily_cache",
                    "asset_dir": str(old_dir),
                    "size_bytes": directory_size(old_dir),
                    "last_used_batch_id": "batch-old",
                },
            )
            upsert_top_asset_cache_ref(
                db_path,
                batch_id="batch-old",
                job_id="job-old",
                content_identity_key="old",
                asset_key="抖音::id::old",
                retained=False,
            )
            upsert_top_asset_cache_entry(
                db_path,
                {
                    "asset_key": "抖音::id::recent",
                    "content_id": "recent",
                    "platform": "抖音",
                    "source": "harvester_daily_cache",
                    "asset_dir": str(recent_dir),
                    "size_bytes": directory_size(recent_dir),
                    "last_used_batch_id": "batch-recent",
                },
            )
            upsert_top_asset_cache_ref(
                db_path,
                batch_id="batch-recent",
                job_id="job-recent",
                content_identity_key="recent",
                asset_key="抖音::id::recent",
                retained=True,
            )

            result = cleanup_top_asset_cache(
                db_path,
                cache_root=root / ".runtime" / "top-assets",
                keep_batch_ids=["batch-recent"],
                max_size_bytes=1,
            )

            self.assertEqual(result.deleted_count, 1)
            self.assertFalse(old_dir.exists())
            self.assertTrue(recent_dir.exists())
            self.assertTrue(harvester_dir.exists())

    def test_cleanup_summary_ignores_harvester_source_references(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "workflow.sqlite3"
            cache_root = root / ".runtime" / "top-assets"
            harvester_dir = root / "harvester-THS" / "output" / "2026-06-11" / "douyin" / "source"
            harvester_dir.mkdir(parents=True)
            (harvester_dir / "asset.bin").write_text("x" * 20, encoding="utf-8")

            upsert_top_asset_cache_entry(
                db_path,
                {
                    "asset_key": "抖音::id::source",
                    "content_id": "source",
                    "platform": "抖音",
                    "source": "harvester_daily_cache",
                    "asset_dir": str(harvester_dir),
                    "size_bytes": 0,
                    "last_used_batch_id": "batch-1",
                },
            )

            result = cleanup_top_asset_cache(db_path, cache_root=cache_root, max_size_bytes=1)

            self.assertEqual(result.remaining_bytes, 0)
            self.assertEqual(result.deleted_count, 0)
            self.assertTrue(harvester_dir.exists())


if __name__ == "__main__":
    unittest.main()
