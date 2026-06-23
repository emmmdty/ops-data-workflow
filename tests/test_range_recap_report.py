from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.range_recap_report import build_range_recap_payload, generate_range_recap_report


class RangeRecapReportTests(unittest.TestCase):
    def test_build_range_recap_payload_contains_only_scope_items(self):
        pool = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_identity_key": "dy-1",
                    "title": "一级素材",
                    "spend": 3000,
                    "impressions": 10000,
                    "activations": 3,
                    "first_pay_count": 1,
                    "analysis_status": "可分析",
                }
            ]
        )

        payload = build_range_recap_payload(
            batch_id="batch-1",
            range_key="tier1_spend_top",
            range_label="一级：消耗优先",
            range_definition="抖音消耗Top20，其他渠道Top10",
            top_pool=pool,
        )

        self.assertEqual(payload["scope"]["range_key"], "tier1_spend_top")
        self.assertEqual(payload["scope"]["item_count"], 1)
        self.assertEqual(payload["channel_summary"].iloc[0]["channel"], "抖音商业化")
        self.assertEqual(payload["top_content_cases"].iloc[0]["title"], "一级素材")

    def test_generate_range_recap_report_calls_generator_and_adds_scope_fields(self):
        pool = pd.DataFrame(
            [
                {
                    "platform": "小红书",
                    "channel": "小红书市场部",
                    "content_identity_key": "xhs-1",
                    "title": "曝光素材",
                    "spend": 1000,
                    "impressions": 200000,
                    "activations": 2,
                    "first_pay_count": 0,
                    "analysis_status": "可分析",
                }
            ]
        )
        calls = {}

        def fake_generator(**kwargs):
            calls.update(kwargs)
            return {"overview": {"report": "曝光报告"}, "channels": []}

        with TemporaryDirectory() as tmp:
            report = generate_range_recap_report(
                batch_id="batch-1",
                range_key="tier2_exposure_top",
                range_label="二级：曝光补充",
                range_definition="抖音曝光Top20，其他渠道Top10",
                top_pool=pool,
                env_path=Path(tmp) / ".env",
                report_generator=fake_generator,
            )

        self.assertEqual(report["range_key"], "tier2_exposure_top")
        self.assertEqual(report["range_label"], "二级：曝光补充")
        self.assertIn("报告范围：二级：曝光补充", calls["overview_recommendations"])
        self.assertEqual(calls["top_content_cases"].iloc[0]["title"], "曝光素材")
        self.assertIn("change_driver_context", calls)
        self.assertIn("historical_content_context", calls)

    def test_generate_range_recap_report_prefers_minimax_results_without_deepseek(self):
        pool = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_identity_key": "dy-1",
                    "title": "高消耗素材",
                    "spend": 3000,
                    "impressions": 10000,
                    "activations": 3,
                    "first_pay_count": 1,
                    "analysis_status": "可分析",
                }
            ]
        )
        multimodal_results = pd.DataFrame(
            [
                {
                    "content_identity_key": "dy-1",
                    "channel": "抖音商业化",
                    "platform": "抖音",
                    "title": "高消耗素材",
                    "content_form": "口播短视频",
                    "title_hook": "问题式钩子",
                    "visual_structure": "真人口播+行情截图",
                    "information_density": "中高",
                    "conversion_path": "标题吸引股民共鸣后引导下载体验",
                    "reuse_points": "开头直接抛出交易痛点，画面有行情截图增强可信度",
                    "avoid_points": "不要继续放大收益承诺",
                    "next_period_strategy": "复用痛点开头，补充同类行情截图素材",
                    "summary": "痛点口播能带来高消耗和有效激活",
                }
            ]
        )

        def deepseek_should_not_run(**kwargs):
            raise AssertionError("DeepSeek should only be used as fallback")

        report = generate_range_recap_report(
            batch_id="batch-1",
            range_key="tier1_spend_top",
            range_label="一级：消耗优先",
            range_definition="抖音消耗Top20，其他渠道Top10",
            top_pool=pool,
            multimodal_results=multimodal_results,
            report_generator=deepseek_should_not_run,
        )

        self.assertEqual(report["provider"], "minimax")
        self.assertEqual(report["model_identity"], "我是 MiniMax-M3，多模态素材理解模型。")
        self.assertIn("高消耗素材", report["overview"]["report"])
        self.assertIn("痛点口播能带来高消耗和有效激活", report["overview"]["report"])
        self.assertEqual(report["channels"][0]["channel"], "抖音商业化")
        self.assertIn("复用痛点开头", report["channels"][0]["next_cycle_direction"])

    def test_generate_range_recap_report_rejects_empty_pool(self):
        with self.assertRaises(ValueError):
            generate_range_recap_report(
                batch_id="batch-1",
                range_key="tier3_threshold",
                range_label="三级：阈值补充",
                range_definition="消耗大于2000或曝光大于10万",
                top_pool=pd.DataFrame(),
                report_generator=lambda **kwargs: {},
            )


if __name__ == "__main__":
    unittest.main()
