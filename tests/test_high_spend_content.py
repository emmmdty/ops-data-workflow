import unittest

import pandas as pd

from ops_data_workflow.pipeline import build_high_spend_content_pool
from ops_data_workflow.top_asset_service import (
    RECAP_TIER_1_SPEND_TOP,
    RECAP_TIER_2_EXPOSURE_TOP,
    RECAP_TIER_3_THRESHOLD,
    build_executable_top_content_pool,
    filter_executable_top_content_pool,
    build_recap_tier_pool,
)


class HighSpendContentPoolTests(unittest.TestCase):
    def test_high_spend_pool_aggregates_identity_before_topn_and_threshold(self):
        rows = []
        rows.extend(
            [
                {
                    "platform": "小红书",
                    "platform_group": "小红书",
                    "channel": "小红书商业化",
                    "content_id": "note-merged",
                    "title": "同一笔记A",
                    "account": "投资号",
                    "content_url": "https://www.xiaohongshu.com/explore/note-merged",
                    "manual_category": "图文",
                    "content_category": "图文",
                    "spend": 900.0,
                    "activations": 9.0,
                },
                {
                    "platform": "小红书",
                    "platform_group": "小红书",
                    "channel": "小红书商业化",
                    "content_id": "note-merged",
                    "title": "同一笔记B",
                    "account": "投资号",
                    "content_url": "https://www.xiaohongshu.com/explore/note-merged",
                    "manual_category": "图文",
                    "content_category": "图文",
                    "spend": 800.0,
                    "activations": 8.0,
                },
            ]
        )
        rows.extend(
            {
                "platform": "小红书",
                "platform_group": "小红书",
                "channel": "小红书商业化",
                "content_id": f"note-{index:02d}",
                "title": f"小红书普通{index:02d}",
                "account": "投资号",
                "content_url": f"https://www.xiaohongshu.com/explore/note-{index:02d}",
                "manual_category": "图文",
                "content_category": "图文",
                "spend": float(1500 - index * 100),
                "impressions": float(1000 - index * 10),
                "activations": 1.0,
            }
            for index in range(11)
        )
        rows.extend(
            {
                "platform": "B站",
                "platform_group": "B站",
                "channel": "B站",
                "content_id": f"BV{index:03d}",
                "title": f"B站普通{index:03d}",
                "account": "投资号",
                "content_url": "" if index == 8 else f"https://www.bilibili.com/video/BV{index:03d}/",
                "manual_category": "B站全部",
                "content_category": "B站全部",
                "spend": 2500.0 if index == 8 else float(1000 - index * 10),
                "impressions": 120000.0 if index == 11 else (5000.0 if index == 9 else float(1000 - index * 20)),
                "activations": 1.0,
            }
            for index in range(12)
        )
        rows.extend(
            [
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "title": "同一个抖音标题",
                    "account": "投资号",
                    "content_url": "",
                    "manual_category": "资讯",
                    "content_category": "资讯",
                    "spend": 1100.0,
                    "impressions": 1000.0,
                    "activations": 2.0,
                },
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "title": "同一个抖音标题",
                    "account": "投资号",
                    "content_url": "",
                    "manual_category": "资讯",
                    "content_category": "资讯",
                    "spend": 950.0,
                    "impressions": 800.0,
                    "activations": 3.0,
                },
            ]
        )
        rows.append(
            {
                "platform": "腾讯广告",
                "platform_group": "腾讯广告",
                "channel": "腾讯广告",
                "content_id": "tencent-high",
                "title": "非三平台高消耗",
                "account": "投放号",
                "content_url": "https://example.com/tencent-high",
                "spend": 999999.0,
                "impressions": 9999999.0,
                "activations": 999.0,
            }
        )

        pool = build_high_spend_content_pool(pd.DataFrame(rows))

        self.assertNotIn("腾讯广告", set(pool["channel"]))

        merged = pool[pool["content_identity_key"].str.contains("note-merged")].iloc[0]
        self.assertEqual(float(merged["spend"]), 1700.0)
        self.assertEqual(int(merged["merged_row_count"]), 2)
        self.assertEqual(int(merged["rank_in_channel"]), 1)

        xhs_titles = set(pool[pool["channel"].eq("小红书商业化")]["title"])
        self.assertIn("同一笔记A", xhs_titles)
        self.assertNotIn("小红书普通10", xhs_titles)

        bilibili_spend_top = pool[pool["content_id"].eq("BV008")].iloc[0]
        self.assertEqual(int(bilibili_spend_top["rank_in_channel"]), 1)
        self.assertIn("分渠道消耗Top10", bilibili_spend_top["high_spend_reason"])
        self.assertIn("单条消耗>2000元", bilibili_spend_top["high_spend_reason"])
        self.assertTrue(bool(bilibili_spend_top["missing_high_spend_link"]))
        bilibili_exposure_top = pool[pool["content_id"].eq("BV009")].iloc[0]
        self.assertIn("分渠道曝光Top10", bilibili_exposure_top["high_spend_reason"])
        bilibili_exposure_threshold = pool[pool["content_id"].eq("BV011")].iloc[0]
        self.assertIn("单条曝光>100000", bilibili_exposure_threshold["high_spend_reason"])
        bilibili_titles = set(pool[pool["channel"].eq("B站")]["title"])
        self.assertIn("B站普通009", bilibili_titles)
        self.assertNotIn("B站普通010", bilibili_titles)

        douyin = pool[pool["channel"].eq("抖音商业化")].iloc[0]
        self.assertEqual(float(douyin["spend"]), 2050.0)
        self.assertIn("title_account", douyin["content_identity_key"])
        self.assertIn("单条消耗>2000元", douyin["high_spend_reason"])

    def test_douyin_uses_top20_and_other_target_platforms_use_top10(self):
        rows = []
        rows.extend(
            {
                "platform": "抖音",
                "platform_group": "抖音",
                "channel": "抖音商业化",
                "content_id": f"dy-{index:02d}",
                "title": f"抖音{index:02d}",
                "account": "投资号",
                "content_url": f"https://www.douyin.com/video/{index:02d}",
                "spend": float(100 - index),
                "impressions": float(1000 - index),
            }
            for index in range(21)
        )
        rows.extend(
            {
                "platform": "小红书",
                "platform_group": "小红书",
                "channel": "小红书商业化",
                "content_id": f"note-rank-{index:02d}",
                "title": f"小红书{index:02d}",
                "account": "投资号",
                "content_url": f"https://www.xiaohongshu.com/explore/note-rank-{index:02d}",
                "spend": float(100 - index),
                "impressions": float(1000 - index),
            }
            for index in range(11)
        )

        pool = build_high_spend_content_pool(pd.DataFrame(rows))

        douyin_ids = set(pool[pool["channel"].eq("抖音商业化")]["content_id"])
        xhs_ids = set(pool[pool["channel"].eq("小红书商业化")]["content_id"])
        self.assertIn("dy-19", douyin_ids)
        self.assertNotIn("dy-20", douyin_ids)
        self.assertIn("note-rank-09", xhs_ids)
        self.assertNotIn("note-rank-10", xhs_ids)

    def test_douyin_identity_prefers_work_url_over_exported_material_url(self):
        pool = build_high_spend_content_pool(
            pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "platform_group": "抖音",
                        "channel": "抖音商业化",
                        "content_id": "7594830477777751338",
                        "material_id": "7595047544461393956",
                        "work_id": "7594830477777751338",
                        "work_url": "https://www.douyin.com/video/7594830477777751338",
                        "title": "巨量导出素材",
                        "account": "投资号",
                        "content_url": "https://www.douyin.com/video/7595047544461393956",
                        "spend": 3000.0,
                        "impressions": 1000.0,
                    }
                ]
            )
        )

        self.assertEqual(pool.iloc[0]["content_identity_key"], "抖音商业化::抖音::id::7594830477777751338")

    def test_douyin_giant_asset_links_are_preserved_but_not_identity(self):
        pool = build_high_spend_content_pool(
            pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "platform_group": "抖音",
                        "channel": "抖音商业化",
                        "content_id": "",
                        "material_id": "7626286546770968627",
                        "title": "只有巨量素材",
                        "account": "投放号",
                        "content_url": "",
                        "work_id": "",
                        "work_url": "",
                        "ad_material_url": "https://巨量.example/video.mp4",
                        "ad_cover_url": "https://巨量.example/cover.jpg",
                        "spend": 3000.0,
                        "impressions": 1000.0,
                    }
                ]
            )
        )

        row = pool.iloc[0]
        self.assertIn("title_account", row["content_identity_key"])
        self.assertNotIn("7626286546770968627", row["content_identity_key"])
        self.assertEqual(row["ad_material_url"], "https://巨量.example/video.mp4")
        self.assertEqual(row["ad_cover_url"], "https://巨量.example/cover.jpg")

    def test_executable_top_content_pool_filters_unmatched_or_unanalyzable_rows(self):
        rows = [
            {
                "platform": "抖音",
                "platform_group": "抖音",
                "channel": "抖音商业化",
                "content_id": "dy-analyzable",
                "title": "可分析",
                "spend": 100.0,
                "analysis_status": "可分析",
                "match_status": "未匹配",
            },
            {
                "platform": "小红书",
                "platform_group": "小红书",
                "channel": "小红书商业化",
                "content_id": "note-matched",
                "title": "已匹配",
                "spend": 90.0,
                "analysis_status": "不可分析",
                "match_status": "已匹配",
            },
            {
                "platform": "B站",
                "platform_group": "B站",
                "channel": "B站",
                "content_id": "BVunmatched",
                "title": "未匹配不可分析",
                "spend": 80.0,
                "analysis_status": "不可分析",
                "match_status": "未匹配",
            },
        ]

        pool = build_executable_top_content_pool(pd.DataFrame(rows))

        self.assertEqual(set(pool["content_id"]), {"dy-analyzable", "note-matched"})

    def test_executable_top_content_pool_can_filter_existing_display_pool(self):
        display_pool = pd.DataFrame(
            [
                {"content_id": "dy-analyzable", "analysis_status": "可分析", "match_status": "未匹配"},
                {"content_id": "note-matched", "analysis_status": "不可分析", "match_status": "已匹配"},
                {"content_id": "BVunmatched", "analysis_status": "不可分析", "match_status": "未匹配"},
            ]
        )

        pool = filter_executable_top_content_pool(display_pool)

        self.assertEqual(set(pool["content_id"]), {"dy-analyzable", "note-matched"})

    def test_executable_top_content_pool_returns_empty_without_status_fields(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-no-status",
                    "title": "缺状态字段",
                    "spend": 100.0,
                }
            ]
        )

        self.assertTrue(build_executable_top_content_pool(frame).empty)

    def test_recap_tier_pool_splits_spend_exposure_and_threshold_scopes(self):
        rows = []
        rows.extend(
            {
                "platform": "抖音",
                "platform_group": "抖音",
                "channel": "抖音商业化",
                "content_id": f"dy-{index:02d}",
                "title": f"抖音{index:02d}",
                "account": "投放号",
                "content_url": f"https://www.douyin.com/video/{index:02d}",
                "spend": float(5000 - index * 10),
                "impressions": float(1000 + index),
                "analysis_status": "可分析",
            }
            for index in range(22)
        )
        rows.extend(
            {
                "platform": "小红书",
                "platform_group": "小红书",
                "channel": "小红书市场部",
                "content_id": f"note-{index:02d}",
                "title": f"小红书{index:02d}",
                "account": "投放号",
                "content_url": f"https://www.xiaohongshu.com/explore/note-{index:02d}",
                "spend": float(3000 - index * 10),
                "impressions": float(1000 + index),
                "match_status": "已匹配",
            }
            for index in range(12)
        )
        rows.extend(
            {
                "platform": "B站",
                "platform_group": "B站",
                "channel": "B站市场部",
                "content_id": f"BV{index:02d}",
                "title": f"B站{index:02d}",
                "account": "投放号",
                "content_url": f"https://www.bilibili.com/video/BV{index:02d}",
                "spend": float(100 - index),
                "impressions": float(500000 - index * 100) if index >= 10 else float(200000 - index * 100),
                "match_status": "已匹配",
            }
            for index in range(12)
        )
        rows.append(
            {
                "platform": "小红书",
                "platform_group": "小红书",
                "channel": "小红书市场部",
                "content_id": "note-threshold",
                "title": "阈值补充",
                "account": "投放号",
                "content_url": "https://www.xiaohongshu.com/explore/note-threshold",
                "spend": 2100.0,
                "impressions": 1000.0,
                "match_status": "已匹配",
            }
        )

        canonical = pd.DataFrame(rows)
        tier1 = build_recap_tier_pool(canonical, RECAP_TIER_1_SPEND_TOP)
        tier2 = build_recap_tier_pool(canonical, RECAP_TIER_2_EXPOSURE_TOP)
        tier3 = build_recap_tier_pool(canonical, RECAP_TIER_3_THRESHOLD)

        self.assertEqual(len(tier1[tier1["channel"].eq("抖音商业化")]), 20)
        self.assertEqual(len(tier1[tier1["channel"].eq("小红书市场部")]), 10)
        self.assertNotIn("dy-20", set(tier1["content_id"]))
        self.assertIn("note-09", set(tier1["content_id"]))
        self.assertNotIn("note-10", set(tier1["content_id"]))
        self.assertEqual(set(tier2[tier2["channel"].eq("B站市场部")]["content_id"]), {"BV10", "BV11"})
        tier1_ids = set(tier1["content_identity_key"])
        tier2_ids = set(tier2["content_identity_key"])
        tier3_ids = set(tier3["content_identity_key"])
        self.assertFalse(tier1_ids & tier2_ids)
        self.assertFalse((tier1_ids | tier2_ids) & tier3_ids)
        self.assertIn("note-threshold", set(tier3["content_id"]))
        self.assertNotIn("dy-20", set(tier3["content_id"]))
        self.assertNotIn("dy-21", set(tier3["content_id"]))
        self.assertTrue(tier1["recap_tier"].eq(RECAP_TIER_1_SPEND_TOP).all())
        self.assertTrue(tier2["recap_tier"].eq(RECAP_TIER_2_EXPOSURE_TOP).all())
        self.assertTrue(tier3["recap_tier"].eq(RECAP_TIER_3_THRESHOLD).all())


if __name__ == "__main__":
    unittest.main()
