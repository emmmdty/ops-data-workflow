import unittest

import pandas as pd

from ops_data_workflow.attribution import build_attribution_tables


class AttributionAnalysisTests(unittest.TestCase):
    def test_builds_total_matched_unmatched_coverage_with_clear_denominators(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "account": "同花顺投资",
                    "match_status": "已匹配",
                    "analysis_status": "可分析",
                    "category_l1": "股友说",
                    "category_l2": "股民教学",
                    "spend": 100.0,
                    "impressions": 1000.0,
                    "activations": 10.0,
                    "first_pay_count": 1.0,
                },
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "title": "二创投放素材",
                    "metadata_tags": "#二创 #财经",
                    "match_status": "未匹配",
                    "analysis_status": "不可分析",
                    "unanalyzable_reason": "未匹配飞书自有内容",
                    "spend": 300.0,
                    "impressions": 3000.0,
                    "activations": 9.0,
                    "first_pay_count": 0.0,
                },
                {
                    "platform": "B站",
                    "channel": "B站市场部",
                    "title": "",
                    "content_url": "",
                    "match_status": "未匹配",
                    "analysis_status": "待补全",
                    "unanalyzable_reason": "缺少作品ID或链接",
                    "spend": 100.0,
                    "impressions": 500.0,
                    "activations": 1.0,
                    "first_pay_count": 0.0,
                },
            ]
        )

        tables = build_attribution_tables(frame)
        coverage = tables.coverage_summary.set_index("scope")

        self.assertEqual(float(coverage.loc["全量投放", "spend"]), 500.0)
        self.assertEqual(float(coverage.loc["飞书已匹配", "spend_share_of_total"]), 0.2)
        self.assertEqual(float(coverage.loc["飞书未匹配", "spend_share_of_total"]), 0.6)
        self.assertEqual(float(coverage.loc["待补齐", "spend_share_of_total"]), 0.2)
        self.assertIn("分母=全量投放", set(coverage["denominator"]))

        matched = tables.matched_breakdown.iloc[0]
        self.assertEqual(matched["account"], "同花顺投资")
        self.assertEqual(matched["primary_type"], "股友说")
        self.assertEqual(matched["secondary_type"], "股民教学")
        self.assertEqual(float(matched["spend_share_of_matched"]), 1.0)

        unmatched = tables.unmatched_breakdown.set_index(["platform", "unmatched_reason"])
        self.assertEqual(
            unmatched.loc[("抖音", "未匹配飞书自有内容"), "attribution_type"],
            "二创",
        )
        self.assertIn("缺作品链接", unmatched.loc[("B站", "缺少作品ID或链接"), "field_gap"])
