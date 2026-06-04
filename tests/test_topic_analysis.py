from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from ops_data_workflow.storage import init_db, load_topic_labels_for_batch, persist_topic_labels
from ops_data_workflow.topic_analysis import (
    build_topic_label_frame,
    channel_topic_limit,
    select_topic_candidates,
    summarize_persisted_content_types,
    summarize_persisted_topic_labels,
)


def _row(channel: str, index: int, spend: float, category: str = "资讯") -> dict[str, object]:
    return {
        "channel": channel,
        "title": f"{channel}标题{index}",
        "content_id": f"{channel}-content-{index}",
        "material_id": f"{channel}-material-{index}",
        "category_l2": category,
        "content_category": category,
        "category_l3": "",
        "account": "同花顺投资",
        "spend": spend,
        "impressions": spend * 10,
        "clicks": spend,
        "activations": max(index % 5, 1),
        "first_pay_count": index % 3,
    }


class TopicAnalysisTests(unittest.TestCase):
    def test_channel_topic_limit_matches_platform_rules(self):
        self.assertEqual(channel_topic_limit("抖音商业化"), 20)
        self.assertEqual(channel_topic_limit("抖音市场部"), 20)
        self.assertEqual(channel_topic_limit("小红书商业化"), 10)
        self.assertEqual(channel_topic_limit("B站市场部"), 10)
        self.assertEqual(channel_topic_limit("B站商业化"), 10)
        self.assertEqual(channel_topic_limit("微信市场部"), 10)

    def test_ai_topic_prompt_includes_content_type_context(self):
        source = Path("ops_data_workflow/ai.py").read_text(encoding="utf-8")
        self.assertIn('"content_type": _json_text(row.get("content_type", ""))', source)
        self.assertIn("标题、内容类型、已有栏目题材", source)

    def test_select_topic_candidates_uses_channel_spend_top_n(self):
        frame = pd.DataFrame(
            [_row("小红书商业化", index, float(index)) for index in range(1, 13)]
            + [_row("B站", 1, 999.0)]
        )

        result = select_topic_candidates(frame, "小红书商业化")

        self.assertEqual(len(result), 10)
        self.assertEqual(list(result["rank_position"])[:2], [1, 2])
        self.assertEqual(list(result["rank_value"])[:2], [12.0, 11.0])
        self.assertNotIn("小红书商业化-content-2", set(result["content_id"]))
        self.assertNotIn("B站-content-1", set(result["content_id"]))

    def test_build_topic_label_frame_persists_ai_labels_for_focused_rows_only(self):
        frame = pd.DataFrame(
            [_row("抖音商业化", index, float(100 - index), "股友说") for index in range(25)]
            + [_row("小红书商业化", index, float(50 - index), "达人内容") for index in range(12)]
            + [_row("B站市场部", index, float(40 - index), "采访") for index in range(12)]
        )

        def labeler(items: pd.DataFrame, env_path: Path | None = None) -> dict[int, str]:
            return {int(index): f"AI题材{int(index) % 2}" for index in items.index}

        result = build_topic_label_frame(frame, topic_labeler=labeler)

        self.assertEqual(len(result[result["channel"].eq("抖音商业化")]), 20)
        self.assertEqual(len(result[result["channel"].eq("小红书商业化")]), 10)
        self.assertEqual(len(result[result["channel"].eq("B站市场部")]), 10)
        self.assertEqual(set(result["source"]), {"ai"})
        self.assertIn("content_type", result.columns)
        self.assertIn("input_hash", result.columns)

    def test_local_topic_rules_do_not_fall_back_to_title_or_nan(self):
        frame = pd.DataFrame(
            [
                {
                    **_row("抖音商业化", 1, 100.0, ""),
                    "title": "什么样的人能成为交易高手？ #同花顺社区 #股友说 #股民",
                    "category_l2": np.nan,
                    "category_l3": np.nan,
                    "content_category": np.nan,
                    "manual_category": np.nan,
                    "ai_category": np.nan,
                },
                {
                    **_row("抖音商业化", 2, 90.0, ""),
                    "title": "推送视频_1118-客供(常)18.mp4",
                    "category_l2": np.nan,
                    "category_l3": np.nan,
                    "content_category": np.nan,
                    "manual_category": np.nan,
                    "ai_category": np.nan,
                },
            ]
        )

        with TemporaryDirectory() as tmp:
            result = build_topic_label_frame(frame, env_path=Path(tmp) / "missing.env")

        self.assertEqual(list(result["topic_name"]), ["股友说", "未匹配题材"])
        self.assertEqual(list(result["content_type"]), ["未匹配", "未匹配"])
        self.assertEqual(list(result["source"]), ["local_rules", "local_unmatched"])
        self.assertNotIn("nan", {value.lower() for value in result["topic_name"].astype(str)})
        self.assertNotIn(frame.iloc[0]["title"], set(result["topic_name"]))
        self.assertNotIn(frame.iloc[1]["title"], set(result["topic_name"]))

    def test_local_topic_rules_group_similar_high_spend_items(self):
        frame = pd.DataFrame(
            [
                {**_row("抖音商业化", 1, 200.0, "股友说"), "title": "你以为的炒股之路 VS 实际上的炒股之路 #股友说"},
                {**_row("抖音商业化", 2, 120.0, "股友说"), "title": "什么样的人能成为交易高手？ #股友说 #股民"},
                {**_row("抖音商业化", 3, 80.0, "同花顺进行曲"), "title": "据说股民都会唱这首歌了？ #同花顺进行曲"},
            ]
        )

        with TemporaryDirectory() as tmp:
            labels = build_topic_label_frame(frame, env_path=Path(tmp) / "missing.env")
        summary = summarize_persisted_topic_labels(labels, "抖音商业化")

        self.assertEqual(labels.loc[labels["content_id"].eq("抖音商业化-content-1"), "topic_name"].iloc[0], "股友说")
        self.assertEqual(labels.loc[labels["content_id"].eq("抖音商业化-content-2"), "topic_name"].iloc[0], "股友说")
        self.assertEqual(labels.loc[labels["content_id"].eq("抖音商业化-content-3"), "topic_name"].iloc[0], "同花顺进行曲")
        stock_talk = summary[summary["topic_name"].eq("股友说")].iloc[0]
        self.assertEqual(stock_talk["item_count"], 2)
        self.assertEqual(stock_talk["material_count"], 2)
        self.assertAlmostEqual(stock_talk["spend"], 320.0)

    def test_local_topic_rules_ignore_title_like_category_l3_and_urls(self):
        frame = pd.DataFrame(
            [
                {
                    **_row("抖音商业化", 1, 100.0, ""),
                    "title": "当你是家族里第一个 打开K线图的人 #财经 #同花顺投资 #投资 #悟道",
                    "category_l2": "",
                    "category_l3": "当你是家族里第一个 打开K线图的人 财经 同花顺投资 投资 悟道",
                    "content_category": "",
                },
                {
                    **_row("腾讯市场部", 1, 90.0, ""),
                    "title": "https://cli1.mobgi.com/s/ZsEtQc?m=120000",
                    "category_l2": "",
                    "category_l3": "https://cli1.mobgi.com/s/ZsEtQc?m=120000",
                    "content_category": "",
                },
                {
                    **_row("抖音商业化", 2, 80.0, ""),
                    "title": "误闯天家#辞九门回忆 #翻唱#同花顺app",
                    "category_l2": "",
                    "category_l3": "误闯天家辞九门回忆 翻唱同花顺app",
                    "content_category": "",
                },
            ]
        )

        with TemporaryDirectory() as tmp:
            result = build_topic_label_frame(frame, env_path=Path(tmp) / "missing.env")

        self.assertEqual(list(result["topic_name"]), ["交易心法", "未匹配题材", "未匹配题材"])
        self.assertEqual(list(result["source"]), ["local_rules", "local_unmatched", "local_unmatched"])
        self.assertNotIn(frame.iloc[0]["category_l3"], set(result["topic_name"]))
        self.assertNotIn(frame.iloc[1]["title"], set(result["topic_name"]))
        self.assertNotIn(frame.iloc[2]["category_l3"], set(result["topic_name"]))

    def test_persist_and_load_topic_labels_by_batch(self):
        labels = pd.DataFrame(
            [
                {
                    "channel": "抖音商业化",
                    "content_id": "dy-1",
                    "material_id": "mat-1",
                    "title": "短线交易",
                    "content_type": "股友说",
                    "topic_name": "短线交易",
                    "rank_metric": "spend",
                    "rank_value": 100.0,
                    "rank_position": 1,
                    "source": "ai",
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "input_hash": "abc",
                }
            ]
        )
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)

            persist_topic_labels(db_path, "batch-1", labels)
            loaded = load_topic_labels_for_batch(db_path, "batch-1")

            self.assertEqual(list(loaded["topic_name"]), ["短线交易"])
            self.assertEqual(list(loaded["rank_value"]), [100.0])

    def test_summarize_persisted_topic_labels_groups_content_types_and_metrics(self):
        labels = pd.DataFrame(
            [
                {"channel": "抖音商业化", "topic_name": "短线交易", "content_type": "股友说", "material_id": "m1", "spend": 79.0, "activations": 7.0, "first_pay_count": 1.0},
                {"channel": "抖音商业化", "topic_name": "短线交易", "content_type": "资讯", "material_id": "m2", "spend": 20.9, "activations": 3.0, "first_pay_count": 2.0},
                {"channel": "抖音商业化", "topic_name": "芯片行情", "content_type": "资讯", "material_id": "m3", "spend": 50.0, "activations": 5.0, "first_pay_count": 0.0},
            ]
        )

        summary = summarize_persisted_topic_labels(labels, "抖音商业化")

        self.assertEqual(list(summary["topic_name"]), ["短线交易", "芯片行情"])
        self.assertEqual(summary.iloc[0]["content_types"], "股友说、资讯")
        self.assertEqual(summary.iloc[0]["material_count"], 2)
        self.assertAlmostEqual(summary.iloc[0]["spend"], 99.9)
        self.assertAlmostEqual(summary.iloc[0]["spend_share"], 99.9 / 149.9)

    def test_summarize_persisted_content_types_groups_types_and_metrics(self):
        labels = pd.DataFrame(
            [
                {"channel": "抖音商业化", "content_type": "股友说", "spend": 79.0, "impressions": 790.0, "clicks": 79.0, "activations": 7.0, "first_pay_count": 1.0},
                {"channel": "抖音商业化", "content_type": "股友说", "spend": 20.9, "impressions": 210.0, "clicks": 21.0, "activations": 3.0, "first_pay_count": 2.0},
                {"channel": "抖音商业化", "content_type": "", "spend": 50.0, "impressions": 500.0, "clicks": 25.0, "activations": 5.0, "first_pay_count": 0.0},
                {"channel": "B站", "content_type": "长视频", "spend": 999.0, "impressions": 9990.0, "clicks": 999.0, "activations": 9.0, "first_pay_count": 1.0},
            ]
        )

        summary = summarize_persisted_content_types(labels, "抖音商业化")

        self.assertEqual(list(summary["content_type"]), ["股友说", "未匹配"])
        self.assertAlmostEqual(summary.iloc[0]["spend"], 99.9)
        self.assertAlmostEqual(summary.iloc[0]["spend_share"], 99.9 / 149.9)
        self.assertAlmostEqual(summary.iloc[0]["ctr"], 100.0 / 1000.0)
        self.assertAlmostEqual(summary.iloc[0]["activation_cost"], 99.9 / 10.0)
        self.assertAlmostEqual(summary.iloc[0]["first_pay_cost"], 99.9 / 3.0)
        self.assertAlmostEqual(summary.iloc[0]["first_pay_rate"], 3.0 / 10.0)
        self.assertNotIn("长视频", set(summary["content_type"]))


if __name__ == "__main__":
    unittest.main()
