from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from ops_data_workflow.ai import DeepSeekSettings, generate_manual_recap_report, match_missing_categories


class AiTests(unittest.TestCase):
    def test_match_missing_categories_returns_confidence_payloads(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"matches":[{"index":7,"category":"资讯","confidence":0.91,"reason":"标题命中行情"}]}'
                    )
                )
            ]
        )
        settings = DeepSeekSettings(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            checked_paths=[],
            source="test",
        )

        with patch("ops_data_workflow.ai.resolve_deepseek_settings", return_value=settings), patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            result = match_missing_categories(
                pd.DataFrame([{"title": "半导体行情复盘", "channel": "抖音"}], index=[7]),
                ["资讯", "股友说"],
                Path(".env"),
            )

        self.assertEqual(result[7]["category"], "资讯")
        self.assertAlmostEqual(result[7]["confidence"], 0.91)
        self.assertEqual(result[7]["reason"], "标题命中行情")

    def test_generate_manual_recap_report_requests_execution_oriented_recap(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"overview":{"report":"整体消耗100，激活5，素材A证明图文方向有效。",'
                            '"next_cycle_direction":"下周期总体方向：继续补齐图文执行。",'
                            '"sections":[{"title":"核心判断","items":["整体消耗100，激活5，图文方向有效。"]},'
                            '{"title":"数据证据","items":["素材A消耗80、激活4。"]},'
                            '{"title":"原因判断","items":["短线交易题材承接交易场景。"]},'
                            '{"title":"下周期动作","items":["继续补齐图文执行。"]}]},'
                            '"channels":[{"channel":"抖音商业化",'
                            '"analysis":"素材A消耗80、激活4，短线交易题材和股友说内容类型表现较好，原因是标题直接承接交易场景。",'
                            '"next_cycle_direction":"下一周期执行方向：围绕短线交易补充同类素材。",'
                            '"sections":[{"title":"表现判断","items":["素材A消耗80、激活4。"]},'
                            '{"title":"有效素材","items":["素材A适合继续复测。"]},'
                            '{"title":"题材/内容类型","items":["短线交易题材和股友说内容类型表现较好。"]},'
                            '{"title":"原因判断","items":["标题直接承接交易场景。"]},'
                            '{"title":"下一周期执行动作","items":["围绕短线交易补充同类素材。"]}]}]}'
                        )
                    )
                )
            ]
        )
        settings = DeepSeekSettings(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            checked_paths=[],
            source="test",
        )

        with patch("ops_data_workflow.ai.resolve_deepseek_settings", return_value=settings), patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            report = generate_manual_recap_report(
                total_summary=pd.DataFrame([{"channel": "总计", "spend": 100.0, "activations": 5.0}]),
                platform_summary=pd.DataFrame([{"channel": "抖音商业化", "spend": 80.0, "activations": 4.0}]),
                channel_comparison=pd.DataFrame([{"channel": "总计", "spend_change_rate": -0.1}]),
                top_content_cases=pd.DataFrame([{"channel": "抖音商业化", "title": "素材A", "spend": 80.0}]),
                overview_recommendations="题材侧：短线交易适合扩量，低成本题材做小预算测试。",
                channel_topic_context=pd.DataFrame(
                    [
                        {
                            "channel": "抖音商业化",
                            "topic_insights": "重点题材分析结论：短线交易拉新贡献最高。",
                            "top_topics": [{"topic_name": "短线交易", "spend": 80.0}],
                        }
                    ]
                ),
                period_level="week",
                env_path=Path(".env"),
            )

        self.assertEqual(report["overview"]["report"], "整体消耗100，激活5，素材A证明图文方向有效。")
        self.assertEqual(report["overview"]["next_cycle_direction"], "继续补齐图文执行。")
        self.assertEqual([section["title"] for section in report["overview"]["sections"]], ["核心判断", "数据证据", "原因判断", "下周期动作"])
        self.assertIn("整体消耗100", report["overview"]["sections"][0]["items"][0])
        self.assertEqual(report["channels"][0]["channel"], "抖音商业化")
        self.assertIn("素材A", report["channels"][0]["analysis"])
        self.assertEqual(report["channels"][0]["next_cycle_direction"], "围绕短线交易补充同类素材。")
        self.assertEqual(
            [section["title"] for section in report["channels"][0]["sections"]],
            ["表现判断", "有效素材", "原因判断", "下一周期执行动作"],
        )
        self.assertNotIn("题材/内容类型", [section["title"] for section in report["channels"][0]["sections"]])
        prompt = client.return_value.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("周期复盘", prompt)
        self.assertIn("从数据中进行复盘", prompt)
        self.assertIn("素材案例", prompt)
        self.assertIn("内容类型", prompt)
        self.assertNotIn("题材/内容类型", prompt)
        self.assertIn("多个渠道之间不要写成竞争关系", prompt)
        self.assertIn("周周期", prompt)
        self.assertIn("执行的内容", prompt)
        self.assertIn("月周期、季度、年度", prompt)
        self.assertIn("策略、方案或预算结构调整", prompt)
        self.assertIn("overview_recommendations", prompt)
        self.assertIn("channel_topic_context", prompt)
        self.assertIn("短线交易适合扩量", prompt)
        self.assertIn("短线交易拉新贡献最高", prompt)
        self.assertIn("sections", prompt)
        self.assertIn("核心判断", prompt)
        self.assertIn("数据证据", prompt)
        self.assertIn("原因判断", prompt)
        self.assertIn("下周期动作", prompt)
        self.assertIn("表现判断", prompt)
        self.assertIn("有效素材", prompt)
        self.assertIn("渠道页 AI 只负责执行建议", prompt)
        self.assertIn("下一周期执行动作", prompt)
        self.assertIn("每个模块输出 2-4 条短要点", prompt)
        self.assertIn("当前数据未提供足够证据", prompt)
        self.assertIn("禁止编造", prompt)
        self.assertIn("\"overview\":{\"report\"", prompt)
        self.assertIn("\"sections\":[{\"title\":\"核心判断\"", prompt)
        self.assertIn("\"channels\":[{\"channel\"", prompt)
        self.assertIn("\"sections\":[{\"title\":\"表现判断\"", prompt)
        self.assertIn("{\"title\":\"原因判断\"", prompt)

    def test_generate_manual_recap_report_keeps_legacy_recap_fields_compatible(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"overview":{"summary":"整体承压","cause":"激活成本上行","action":"补齐低成本素材"},'
                            '"channels":[{"channel":"小红书商业化","summary":"互动强但转化弱","cause":"题材偏泛",'
                            '"action":"复测强场景内容"}]}'
                        )
                    )
                )
            ]
        )
        settings = DeepSeekSettings(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            checked_paths=[],
            source="test",
        )

        with patch("ops_data_workflow.ai.resolve_deepseek_settings", return_value=settings), patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            report = generate_manual_recap_report(
                total_summary=pd.DataFrame([{"channel": "总计", "spend": 100.0}]),
                platform_summary=pd.DataFrame([{"channel": "小红书商业化", "spend": 100.0}]),
                channel_comparison=pd.DataFrame(),
                top_content_cases=pd.DataFrame(),
                env_path=Path(".env"),
            )

        self.assertEqual(report["overview"]["report"], "整体承压\n\n激活成本上行")
        self.assertEqual(report["overview"]["next_cycle_direction"], "补齐低成本素材")
        self.assertEqual(report["overview"]["sections"], [])
        self.assertEqual(report["channels"][0]["analysis"], "互动强但转化弱\n\n题材偏泛")
        self.assertEqual(report["channels"][0]["next_cycle_direction"], "复测强场景内容")
        self.assertEqual(report["channels"][0]["sections"], [])


if __name__ == "__main__":
    unittest.main()
