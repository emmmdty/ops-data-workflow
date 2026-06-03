from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.content_metadata import enrich_content_metadata, fetch_bilibili_metadata


class ContentMetadataEnrichmentTests(unittest.TestCase):
    def test_safe_public_enriches_bilibili_from_api_and_cache(self):
        calls: list[str] = []

        def fetch_bilibili(bvid: str) -> dict:
            calls.append(bvid)
            return {
                "id": bvid,
                "link": f"https://www.bilibili.com/video/{bvid}/",
                "title": "公开接口标题",
                "tags": "财经,投教",
                "published_at": "2026-04-11",
            }

        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "channel": "B站",
                    "content_id": "BV1abcde2345",
                    "content_url": "",
                    "title": "",
                    "source_time": "",
                }
            ]
        )

        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "metadata-cache"
            first, first_stats = enrich_content_metadata(
                frame,
                mode="safe_public",
                cache_dir=cache_dir,
                fetch_bilibili=fetch_bilibili,
                fetched_at="2026-06-03T12:00:00+08:00",
            )
            second, second_stats = enrich_content_metadata(
                frame,
                mode="safe_public",
                cache_dir=cache_dir,
                fetch_bilibili=fetch_bilibili,
                fetched_at="2026-06-03T12:10:00+08:00",
            )

        self.assertEqual(calls, ["BV1abcde2345"])
        row = first.iloc[0]
        self.assertEqual(row["content_url"], "https://www.bilibili.com/video/BV1abcde2345/")
        self.assertEqual(row["title"], "公开接口标题")
        self.assertEqual(row["source_time"], "2026-04-11")
        self.assertEqual(row["metadata_tags"], "财经,投教")
        self.assertEqual(row["metadata_source"], "bilibili_public_api")
        self.assertEqual(row["metadata_confidence"], 0.9)
        self.assertEqual(first_stats["filled_rows"], 1)
        self.assertEqual(first_stats["cache_hits"], 0)
        self.assertEqual(second.iloc[0]["metadata_source"], "metadata_cache")
        self.assertEqual(second_stats["cache_hits"], 1)

    def test_safe_public_keeps_low_spend_excel_conflicts_out_of_manual_review(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "content_id": "BV1conflict1",
                    "content_url": "https://example.com/original",
                    "title": "Excel标题",
                    "source_time": "2026-04-10",
                    "spend": 1999.99,
                    "needs_manual_review": False,
                    "review_reasons": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetch_bilibili=lambda bvid: {
                "id": bvid,
                "link": f"https://www.bilibili.com/video/{bvid}/",
                "title": "接口标题",
                "tags": "",
                "published_at": "2026-04-11",
            },
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_url"], "https://example.com/original")
        self.assertEqual(row["title"], "Excel标题")
        self.assertEqual(row["source_time"], "2026-04-10")
        self.assertFalse(row["needs_manual_review"])
        self.assertIn("公开信息与Excel字段冲突", row["metadata_review_reason"])
        self.assertEqual(row["review_reasons"], "")
        self.assertEqual(stats["hint_rows"], 1)
        self.assertEqual(stats["conflict_rows"], 1)
        self.assertEqual(stats["review_rows"], 0)

    def test_safe_public_promotes_high_spend_conflicts_to_manual_review(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "content_id": "BV1conflict2",
                    "title": "Excel标题",
                    "source_time": "2026-04-10",
                    "spend": 2000,
                    "needs_manual_review": False,
                    "review_reasons": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetch_bilibili=lambda bvid: {
                "id": bvid,
                "title": "接口标题",
                "published_at": "2026-04-11",
            },
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertTrue(row["needs_manual_review"])
        self.assertIn("高消耗公开信息冲突", row["metadata_review_reason"])
        self.assertIn("高消耗公开信息冲突", row["review_reasons"])
        self.assertEqual(stats["conflict_rows"], 1)
        self.assertEqual(stats["review_rows"], 1)

    def test_safe_public_does_not_count_already_manual_rows_as_new_review(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "content_id": "BV1already1",
                    "title": "Excel标题",
                    "spend": 3000,
                    "needs_manual_review": 1,
                    "review_reasons": "既有复核原因",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetch_bilibili=lambda bvid: {
                "id": bvid,
                "title": "接口标题",
            },
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertTrue(row["needs_manual_review"])
        self.assertIn("高消耗公开信息冲突", row["review_reasons"])
        self.assertEqual(stats["review_rows"], 0)

    def test_safe_public_ignores_url_standardization_conflict_for_same_content_id(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "content_id": "BV1sameurl1",
                    "content_url": "https://m.bilibili.com/video/BV1sameurl1",
                    "spend": 5000,
                    "needs_manual_review": False,
                    "review_reasons": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetch_bilibili=lambda bvid: {
                "id": bvid,
                "link": f"https://www.bilibili.com/video/{bvid}/",
            },
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_url"], "https://m.bilibili.com/video/BV1sameurl1")
        self.assertFalse(row["needs_manual_review"])
        self.assertEqual(row["metadata_review_reason"], "")
        self.assertEqual(stats["conflict_rows"], 0)
        self.assertEqual(stats["review_rows"], 0)

    def test_safe_public_keeps_xhs_derived_publish_date_out_of_manual_review(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "小红书",
                    "content_id": "65f00000abcdef",
                    "content_url": "https://www.xiaohongshu.com/explore/65f00000abcdef",
                    "source_time": "2026-05-01",
                    "spend": 5000,
                    "needs_manual_review": False,
                    "review_reasons": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_url"], "https://www.xiaohongshu.com/explore/65f00000abcdef")
        self.assertEqual(row["source_time"], "2026-05-01")
        self.assertFalse(row["needs_manual_review"])
        self.assertIn("小红书公开补全需复核", row["metadata_review_reason"])
        self.assertEqual(row["review_reasons"], "")
        self.assertEqual(stats["hint_rows"], 1)
        self.assertEqual(stats["conflict_rows"], 1)
        self.assertEqual(stats["review_rows"], 0)

    def test_safe_public_promotes_content_id_conflict_to_manual_review(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "content_id": "BV1original1",
                    "spend": 10,
                    "needs_manual_review": False,
                    "review_reasons": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetch_bilibili=lambda bvid: {"id": "BV1different1"},
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_id"], "BV1original1")
        self.assertTrue(row["needs_manual_review"])
        self.assertIn("内容ID冲突", row["metadata_review_reason"])
        self.assertEqual(stats["review_rows"], 1)

    def test_safe_public_normalizes_douyin_shortlink_without_manual_review(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "content_url": "https://v.douyin.com/abc123/",
                    "content_id": "",
                    "source_time": "",
                    "needs_manual_review": False,
                    "review_reasons": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            resolve_douyin_shortlink=lambda link: "https://www.douyin.com/video/7291234567890123456",
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_url"], "https://www.douyin.com/video/7291234567890123456")
        self.assertEqual(row["content_id"], "7291234567890123456")
        self.assertRegex(str(row["source_time"]), r"^20\d{2}-\d{2}-\d{2}$")
        self.assertFalse(row["needs_manual_review"])
        self.assertIn("抖音公开补全需复核", row["metadata_review_reason"])
        self.assertEqual(row["review_reasons"], "")
        self.assertEqual(stats["filled_rows"], 1)
        self.assertEqual(stats["hint_rows"], 1)
        self.assertEqual(stats["review_rows"], 0)

    def test_safe_public_derives_douyin_url_from_scientific_material_id_without_id_conflict(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "content_id": "v02033g10000d5i7evfog65sp8itt3k0",
                    "material_id": "7.593368545867317e+18",
                    "content_url": "",
                    "source_time": "",
                    "needs_manual_review": False,
                    "review_reasons": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_id"], "v02033g10000d5i7evfog65sp8itt3k0")
        self.assertEqual(row["content_url"], "https://www.douyin.com/video/7593368545867317248")
        self.assertRegex(str(row["source_time"]), r"^20\d{2}-\d{2}-\d{2}$")
        self.assertFalse(row["needs_manual_review"])
        self.assertEqual(row["review_reasons"], "")
        self.assertEqual(stats["filled_rows"], 1)
        self.assertEqual(stats["review_rows"], 0)

    def test_safe_public_records_douyin_shortlink_failure_without_blocking(self):
        frame = pd.DataFrame([{"platform": "抖音", "content_url": "https://v.douyin.com/missing/"}])

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            resolve_douyin_shortlink=lambda link: "",
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_url"], "https://v.douyin.com/missing/")
        self.assertIn("抖音短链未解析", row["metadata_error"])
        self.assertEqual(stats["error_rows"], 1)

    def test_safe_public_normalizes_xhs_note_id(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "小红书",
                    "content_id": "65f00000abcdef",
                    "content_url": "",
                    "source_time": "",
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["content_url"], "https://www.xiaohongshu.com/discovery/item/65f00000abcdef")
        self.assertEqual(row["source_time"], "2024-03-12")
        self.assertEqual(row["metadata_source"], "xhs_id_derived")
        self.assertEqual(stats["filled_rows"], 1)
        self.assertEqual(stats["review_rows"], 0)

    def test_safe_public_uses_harvester_bilibili_cache_before_api(self):
        calls: list[str] = []
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "content_id": "BV1harvest1",
                    "title": "",
                    "source_time": "",
                }
            ]
        )
        with TemporaryDirectory() as tmp:
            harvester_root = Path(tmp) / "harvester"
            cache_file = harvester_root / ".runtime" / "detail-cache" / "bilibili" / "BV1harvest1.json"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_text(
                '{"bvid":"BV1harvest1","videoUrl":"https://www.bilibili.com/video/BV1harvest1/","title":"缓存标题","tags":"#财经 #投教","publishedAt":"2026-05-20"}',
                encoding="utf-8",
            )

            enriched, stats = enrich_content_metadata(
                frame,
                mode="safe_public",
                harvester_root=harvester_root,
                fetch_bilibili=lambda bvid: calls.append(bvid) or {},
                fetched_at="2026-06-03T12:00:00+08:00",
            )

        row = enriched.iloc[0]
        self.assertEqual(calls, [])
        self.assertEqual(row["title"], "缓存标题")
        self.assertEqual(row["source_time"], "2026-05-20")
        self.assertEqual(row["metadata_source"], "harvester_cache")
        self.assertEqual(stats["cache_hits"], 1)

    def test_safe_public_can_skip_bilibili_public_api_for_batch_refresh(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "B站",
                    "content_id": "BV1skipapi1",
                    "title": "",
                    "source_time": "",
                    "spend": 3000,
                }
            ]
        )

        enriched, stats = enrich_content_metadata(
            frame,
            mode="safe_public",
            allow_public_api=False,
            fetch_bilibili=lambda bvid: (_ for _ in ()).throw(AssertionError("should not call public API")),
            fetched_at="2026-06-03T12:00:00+08:00",
        )

        row = enriched.iloc[0]
        self.assertEqual(row["title"], "")
        self.assertIn("B站公开接口跳过", row["metadata_error"])
        self.assertIn("高消耗B站公开接口失败，可登录态补抓", row["metadata_review_reason"])
        self.assertEqual(stats["error_rows"], 1)

    def test_bilibili_public_api_sends_user_agent(self):
        calls: list[dict] = []

        class Response:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        def fake_get(url, **kwargs):
            calls.append({"url": url, "headers": kwargs.get("headers", {})})
            if "view" in url:
                return Response({"code": 0, "data": {"title": "接口标题", "pubdate": 1770296894}})
            return Response({"code": 0, "data": [{"tag_name": "财经"}]})

        result = fetch_bilibili_metadata("BV1uaheader1", request_get=fake_get)

        self.assertEqual(result["title"], "接口标题")
        self.assertEqual(result["tags"], "财经")
        self.assertTrue(calls)
        self.assertTrue(all(call["headers"].get("User-Agent") for call in calls))


if __name__ == "__main__":
    unittest.main()
