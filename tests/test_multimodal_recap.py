from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.multimodal_recap import build_type_recap_items, persist_multimodal_recap
from ops_data_workflow.recap_settings import update_recap_settings
from ops_data_workflow.storage import list_multimodal_recap_items, list_type_recap_items


class MultimodalRecapTests(unittest.TestCase):
    def test_type_recap_uses_saved_value_weights_and_douyin_l1_fallback(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            update_recap_settings(db_path, activation_weight=2.0, first_pay_weight=10.0)
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-1",
                        "content_id": "111",
                        "title": "长视频内容",
                        "category_l1": "长视频",
                        "category_l2": "",
                        "spend": 3000,
                        "impressions": 120000,
                        "activations": 20,
                        "first_pay_count": 3,
                    },
                    {
                        "platform": "B站",
                        "channel": "B站市场部",
                        "content_identity_key": "bili-1",
                        "content_id": "BV1",
                        "title": "投教视频",
                        "bilibili_content_type": "",
                        "content_type": "指标教学",
                        "spend": 1000,
                        "impressions": 50000,
                        "activations": 5,
                        "first_pay_count": 1,
                    },
                ]
            )
            multimodal_results = pd.DataFrame(
                [
                    {
                        "content_identity_key": "bili-1",
                        "B站内容类型": "新手教学",
                    }
                ]
            )

            recap = build_type_recap_items(
                db_path,
                "batch-1",
                top_content,
                multimodal_results=multimodal_results,
            )

            by_key = recap.set_index(["platform", "type_level", "content_type"])
            douyin = by_key.loc[("抖音", "douyin_l2", "长视频")]
            bilibili = by_key.loc[("B站", "bilibili", "新手教学")]
            self.assertEqual(float(douyin["value"]), 70.0)
            self.assertEqual(float(douyin["activation_cost"]), 150.0)
            self.assertEqual(float(douyin["first_pay_cost"]), 1000.0)
            self.assertEqual(float(bilibili["value"]), 20.0)

    def test_persist_multimodal_recap_items_and_type_recap(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            update_recap_settings(db_path, activation_weight=1.5, first_pay_weight=8.0)
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "xhs-1",
                        "content_id": "note-1",
                        "title": "爆款图文",
                        "category_l1": "投教",
                        "category_l2": "",
                        "content_type": "",
                        "spend": 2500,
                        "impressions": 90000,
                        "activations": 10,
                        "first_pay_count": 2,
                    }
                ]
            )

            result = persist_multimodal_recap(
                db_path,
                "batch-1",
                top_content,
                analyzer=lambda row: {
                    "一级内容类型": "投教",
                    "二级内容类型": "方法论",
                    "共性总结": "问题场景明确",
                },
            )

            items = list_multimodal_recap_items(db_path, batch_id="batch-1")
            types = list_type_recap_items(db_path, batch_id="batch-1")

            self.assertEqual(result.item_count, 1)
            self.assertEqual(result.type_count, 2)
            self.assertEqual(items.iloc[0]["category_l2"], "方法论")
            xhs_l2 = types[types["type_level"].eq("xhs_l2")].iloc[0]
            self.assertEqual(xhs_l2["content_type"], "方法论")
            self.assertEqual(float(xhs_l2["value"]), 31.0)

    def test_multimodal_recap_keeps_existing_types_when_result_is_blank_and_stores_structured_fields(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "xhs-blank",
                        "content_id": "note-blank",
                        "title": "已有类型",
                        "category_l1": "投教",
                        "category_l2": "方法论",
                        "content_type": "方法论",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 5,
                        "first_pay_count": 1,
                    }
                ]
            )

            persist_multimodal_recap(
                db_path,
                "batch-1",
                top_content,
                analyzer=lambda row: {
                    "一级内容类型": "",
                    "二级内容类型": "",
                    "内容形态": "图文",
                    "标题钩子": "问题钩子",
                    "视觉结构": "封面大字",
                    "信息密度": "中",
                    "转化路径": "标题到正文",
                    "可复用点": "问题场景",
                    "不建议复用点": "标题过长",
                    "下周期策略建议": "保留强问题",
                    "共性总结": "投教明确",
                },
            )

            items = list_multimodal_recap_items(db_path, batch_id="batch-1")

            row = items.iloc[0]
            self.assertEqual(row["category_l1"], "投教")
            self.assertEqual(row["category_l2"], "方法论")
            self.assertEqual(row["title_hook"], "问题钩子")
            self.assertEqual(row["visual_structure"], "封面大字")
            self.assertEqual(row["reuse_points"], "问题场景")


if __name__ == "__main__":
    unittest.main()
