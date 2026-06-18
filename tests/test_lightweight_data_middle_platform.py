from __future__ import annotations

import unittest

import pandas as pd

from ops_data_workflow.data_service import build_analysis_status_tables, build_overview_service_tables
from ops_data_workflow.metric_registry import get_metric, list_metrics
from ops_data_workflow.quality_report import build_quality_report
from ops_data_workflow.asset_matching import match_assets_to_ledger
from ops_data_workflow.platform_normalizers import normalize_platform_identities


class LightweightDataMiddlePlatformTests(unittest.TestCase):
    def test_metric_registry_defines_atomic_derived_and_composite_value_metrics(self):
        metrics = {item.name: item for item in list_metrics()}

        self.assertEqual(metrics["spend"].metric_type, "atomic")
        self.assertEqual(metrics["activation_cost"].metric_type, "derived")
        self.assertEqual(metrics["content_value"].metric_type, "composite")
        self.assertEqual(metrics["content_value"].formula, "activations*m + first_pay_count*n")
        self.assertTrue(get_metric("activation_cost").lower_is_better)
        self.assertFalse(get_metric("content_value").lower_is_better)

    def test_platform_matchers_use_only_declared_platform_strategy(self):
        ledger = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "content_id": "7594830477777751338",
                    "content_url": "https://www.douyin.com/video/7594830477777751338",
                    "title": "为什么开始炒股后物欲变低了？",
                    "account": "投资号",
                    "category_l1": "投教",
                    "category_l2": "方法论",
                    "content_type": "方法论",
                    "title_key": "",
                    "title_key_no_tags": "",
                },
                {
                    "platform": "小红书",
                    "content_id": "note-1",
                    "content_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "小红书标题",
                    "account": "投资号",
                    "category_l1": "投教",
                    "category_l2": "图文教程",
                    "content_type": "图文教程",
                },
                {
                    "platform": "B站",
                    "content_id": "BV1abcde2345",
                    "content_url": "https://www.bilibili.com/video/BV1abcde2345/",
                    "title": "B站标题",
                    "account": "投资号",
                    "bilibili_content_type": "长视频",
                    "content_type": "长视频",
                },
            ]
        )
        frame = normalize_platform_identities(
            pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_id": "7594830477777751338",
                        "title": "为什么开始炒股后物欲变低了? #财经",
                        "content_url": "https://www.douyin.com/video/7594830477777751338",
                    },
                    {
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_id": "",
                        "material_id": "wrong-note",
                        "title": "小红书标题",
                        "content_url": "",
                    },
                    {
                        "platform": "B站",
                        "channel": "B站",
                        "content_id": "",
                        "material_id": "BV1abcde2345",
                        "title": "B站标题",
                    },
                ]
            )
        )

        matched = match_assets_to_ledger(frame, ledger)

        self.assertEqual(list(matched["match_status"]), ["已匹配", "未匹配", "已匹配"])
        self.assertEqual(matched.iloc[0]["match_source"], "作品ID")
        self.assertGreaterEqual(float(matched.iloc[0]["match_confidence"]), 0.9)
        self.assertEqual(matched.iloc[0]["matched_category_l1"], "投教")
        self.assertEqual(matched.iloc[0]["matched_category_l2"], "方法论")
        self.assertEqual(matched.iloc[1]["match_reason"], "未匹配飞书自有内容")
        self.assertEqual(matched.iloc[2]["match_source"], "BV号")
        self.assertEqual(matched.iloc[2]["matched_bilibili_content_type"], "长视频")

    def test_quality_report_surfaces_matching_and_type_gaps(self):
        canonical = pd.DataFrame(
            [
                {"platform": "抖音", "channel": "抖音商业化", "match_status": "已匹配", "spend": 1000, "content_type": "方法论"},
                {"platform": "抖音", "channel": "抖音商业化", "match_status": "未匹配", "spend": 3000, "content_type": ""},
                {"platform": "小红书", "channel": "小红书商业化", "match_status": "已匹配", "spend": 2000, "content_type": ""},
            ]
        )
        top_content = pd.DataFrame(
            [
                {"platform": "抖音", "channel": "抖音商业化", "match_status": "未匹配", "spend": 3000},
                {"platform": "小红书", "channel": "小红书商业化", "match_status": "已匹配", "spend": 2000},
            ]
        )

        report = build_quality_report(canonical, top_content)
        by_metric = {row["metric"]: row for _, row in report.iterrows()}

        self.assertEqual(by_metric["飞书匹配率"]["value"], 2 / 3)
        self.assertEqual(by_metric["内容类型缺失率"]["count"], 2)
        self.assertEqual(by_metric["Top未匹配消耗占比"]["value"], 3000 / 5000)

    def test_data_service_builds_overview_value_and_top_share_tables(self):
        canonical = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "match_status": "已匹配",
                    "content_identity_key": "dy-1",
                    "spend": 1000,
                    "impressions": 10000,
                    "activations": 10,
                    "first_pay_count": 2,
                },
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "match_status": "已匹配",
                    "content_identity_key": "dy-2",
                    "spend": 500,
                    "impressions": 5000,
                    "activations": 5,
                    "first_pay_count": 1,
                },
            ]
        )
        top_content = canonical.iloc[[0]].copy()

        tables = build_overview_service_tables(canonical, top_content, m=3, n=20)

        channel = tables.channel_overview.iloc[0]
        self.assertEqual(channel["channel"], "抖音商业化")
        self.assertEqual(float(channel["content_value"]), 15 * 3 + 3 * 20)
        self.assertEqual(float(channel["value_per_spend"]), 105 / 1500)
        self.assertEqual(float(channel["value_share"]), 1.0)
        top_share = tables.top_share.iloc[0]
        self.assertEqual(float(top_share["top_spend_share"]), 1000 / 1500)
        self.assertEqual(float(top_share["top_impressions_share"]), 10000 / 15000)

    def test_data_service_totals_all_channels_but_status_scope_is_top_three_platforms(self):
        canonical = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "match_status": "已匹配",
                    "content_identity_key": "dy-1",
                    "spend": 1000,
                    "impressions": 10000,
                    "activations": 10,
                    "first_pay_count": 2,
                },
                {
                    "platform": "腾讯广告",
                    "channel": "腾讯广告",
                    "match_status": "未匹配",
                    "content_identity_key": "tx-1",
                    "spend": 9000,
                    "impressions": 90000,
                    "activations": 90,
                    "first_pay_count": 9,
                },
            ]
        )
        top_content = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_identity_key": "dy-1",
                    "spend": 1000,
                    "impressions": 10000,
                    "activations": 10,
                    "first_pay_count": 2,
                }
            ]
        )
        harvester_jobs = pd.DataFrame(
            [
                {
                    "job_id": "job-1",
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_identity_key": "dy-1",
                    "status": "failed",
                    "error_message": "抖音登录状态失效，请运行 npm run login:douyin",
                }
            ]
        )
        multimodal_jobs = pd.DataFrame(
            [
                {
                    "job_id": "mm-1",
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_identity_key": "dy-1",
                    "status": "succeeded",
                    "result_json": '{"内容形态":"短视频","标题钩子":"反差开场","视觉结构":"真人口播","共性总结":"强问题开场"}',
                    "error_message": "",
                }
            ]
        )

        overview = build_overview_service_tables(canonical, top_content, m=1, n=10)
        status = build_analysis_status_tables(
            canonical,
            top_content,
            harvester_jobs=harvester_jobs,
            multimodal_jobs=multimodal_jobs,
        )

        totals = overview.channel_overview.set_index("channel")
        self.assertIn("腾讯广告", totals.index)
        self.assertEqual(float(totals.loc["腾讯广告", "spend"]), 9000.0)
        self.assertEqual(set(status.top_pool["channel"]), {"抖音商业化"})
        self.assertEqual(status.summary.set_index("metric").loc["高价值池素材数", "value"], 1)
        self.assertEqual(status.harvester_status.iloc[0]["status"], "failed")
        self.assertIn("npm run login:douyin", status.harvester_status.iloc[0]["error_message"])
        self.assertEqual(status.multimodal_status.iloc[0]["内容形态"], "短视频")
        self.assertEqual(status.multimodal_status.iloc[0]["标题钩子"], "反差开场")
        self.assertEqual(status.multimodal_status.iloc[0]["共性总结"], "强问题开场")


if __name__ == "__main__":
    unittest.main()
