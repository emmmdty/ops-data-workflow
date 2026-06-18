import unittest

import pandas as pd

from ops_data_workflow.manual_recap_evidence import build_manual_recap_evidence


class ManualRecapEvidenceTests(unittest.TestCase):
    def test_content_type_drivers_mark_increment_and_inefficient_scale_up(self):
        evidence = build_manual_recap_evidence(
            current_items=pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "title": "A素材",
                        "content_id": "a-1",
                        "content_type": "内容类型A",
                        "spend": 180.0,
                        "impressions": 1800.0,
                        "activations": 25.0,
                        "first_pay_count": 8.0,
                    },
                    {
                        "channel": "抖音商业化",
                        "title": "B素材",
                        "content_id": "b-1",
                        "content_type": "内容类型B",
                        "spend": 180.0,
                        "impressions": 1500.0,
                        "activations": 9.0,
                        "first_pay_count": 1.0,
                    },
                ]
            ),
            previous_items=pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "title": "A素材历史",
                        "content_id": "a-old",
                        "content_type": "内容类型A",
                        "spend": 100.0,
                        "impressions": 900.0,
                        "activations": 10.0,
                        "first_pay_count": 3.0,
                    },
                    {
                        "channel": "抖音商业化",
                        "title": "B素材历史",
                        "content_id": "b-old",
                        "content_type": "内容类型B",
                        "spend": 100.0,
                        "impressions": 900.0,
                        "activations": 10.0,
                        "first_pay_count": 2.0,
                    },
                ]
            ),
            channel_comparison=pd.DataFrame(
                [
                    {
                        "channel": "总计",
                        "spend_current": 360.0,
                        "spend_previous": 200.0,
                        "spend_change_rate": 0.8,
                        "activations_current": 34.0,
                        "activations_previous": 20.0,
                        "activations_change_rate": 0.7,
                        "activation_cost_current": 360.0 / 34.0,
                        "activation_cost_previous": 10.0,
                        "activation_cost_change_rate": 0.0588235294,
                    }
                ]
            ),
        )

        self.assertIn("change_driver_summary", evidence)
        self.assertIn("historical_content_context", evidence)
        total_activation = evidence["change_driver_summary"]["overview_metrics"]["activations"]
        self.assertEqual(total_activation["evidence_id"], "overview.metric.activations")
        self.assertAlmostEqual(total_activation["delta"], 14.0)

        by_type = {
            item["name"]: item
            for item in evidence["historical_content_context"]["channels"][0]["content_type_drivers"]
        }
        self.assertEqual(by_type["内容类型A"]["driver_tag"], "放量有效")
        self.assertAlmostEqual(by_type["内容类型A"]["activations_delta"], 15.0)
        self.assertEqual(by_type["内容类型B"]["driver_tag"], "放量低效")
        self.assertAlmostEqual(by_type["内容类型B"]["activation_cost_delta"], 10.0)
        self.assertTrue(by_type["内容类型A"]["evidence_id"].startswith("channel.douyinshangyehua.content_type."))

    def test_missing_previous_period_is_reported_as_data_gap(self):
        evidence = build_manual_recap_evidence(
            current_items=pd.DataFrame(
                [
                    {
                        "channel": "小红书商业化",
                        "title": "新增素材",
                        "content_id": "new-1",
                        "content_type": "互动话题",
                        "spend": 50.0,
                        "impressions": 500.0,
                        "activations": 5.0,
                        "first_pay_count": 1.0,
                    }
                ]
            ),
            previous_items=pd.DataFrame(),
            channel_comparison=pd.DataFrame(),
        )

        gaps = evidence["change_driver_summary"]["data_gaps"]
        self.assertIn("缺少可比周期", [item["type"] for item in gaps])
        self.assertNotIn("首次接入", str(evidence))
        driver = evidence["historical_content_context"]["channels"][0]["content_type_drivers"][0]
        self.assertEqual(driver["driver_tag"], "数据不足")

    def test_data_gaps_cover_unmatched_types_zero_impressions_and_missing_titles(self):
        evidence = build_manual_recap_evidence(
            current_items=pd.DataFrame(
                [
                    {
                        "channel": "B站市场部",
                        "title": "",
                        "content_id": "bv-1",
                        "content_type": "未匹配",
                        "spend": 120.0,
                        "impressions": 0.0,
                        "activations": 0.0,
                        "first_pay_count": 0.0,
                    }
                ]
            ),
            previous_items=pd.DataFrame(
                [
                    {
                        "channel": "B站市场部",
                        "title": "历史素材",
                        "content_id": "bv-old",
                        "content_type": "未匹配",
                        "spend": 60.0,
                        "impressions": 100.0,
                        "activations": 2.0,
                        "first_pay_count": 1.0,
                    }
                ]
            ),
            channel_comparison=pd.DataFrame(),
        )

        gap_types = [item["type"] for item in evidence["change_driver_summary"]["data_gaps"]]
        self.assertIn("内容类型未匹配", gap_types)
        self.assertIn("曝光为0", gap_types)
        self.assertIn("素材标题缺失", gap_types)
        driver = evidence["historical_content_context"]["channels"][0]["content_type_drivers"][0]
        self.assertEqual(driver["driver_tag"], "高消耗低转化")


if __name__ == "__main__":
    unittest.main()
