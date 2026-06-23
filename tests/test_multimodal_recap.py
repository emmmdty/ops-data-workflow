import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sqlite3

import pandas as pd

from ops_data_workflow.multimodal_recap import (
    ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
    ANALYSIS_PURPOSE_STRATEGY_RECAP,
    build_strategy_recap_items,
    build_type_recap_items,
    persist_multimodal_recap,
    persist_type_recap_from_top_content,
)
from ops_data_workflow.platform_taxonomy import load_harvester_taxonomy
from ops_data_workflow.recap_settings import update_recap_settings
from ops_data_workflow.storage import (
    list_content_performance_items,
    list_multimodal_recap_items,
    list_strategy_recap_items,
    list_type_recap_items,
    persist_content_performance_items,
)


class MultimodalRecapTests(unittest.TestCase):
    def _write_harvester_taxonomy_fixture(self, root: Path) -> None:
        taxonomy_dir = root / "src" / "douyin-channel-type-classifier"
        ai_dir = root / "src" / "ai"
        taxonomy_dir.mkdir(parents=True)
        ai_dir.mkdir(parents=True)
        (taxonomy_dir / "taxonomy.mjs").write_text(
            """
export const DOUYIN_CHANNEL_PRIMARY_TYPES = ["测试一级"];
export const DOUYIN_CHANNEL_TAXONOMY = [
  { primaryType: "测试一级", secondaryTypes: [{ label: "测试二级" }] }
];
export function secondaryLabelsForPrimary(primaryType) {
  return (DOUYIN_CHANNEL_TAXONOMY.find((entry) => entry.primaryType === primaryType)?.secondaryTypes || [])
    .map((entry) => entry.label);
}
""",
            encoding="utf-8",
        )
        (ai_dir / "platform-taxonomies.mjs").write_text(
            """
import { DOUYIN_CHANNEL_PRIMARY_TYPES, secondaryLabelsForPrimary } from "../douyin-channel-type-classifier/taxonomy.mjs";
export const BILIBILI_PRIMARY_TYPES = ["测试B站"];
export const XHS_TAXONOMY = {
  primaryTypes: ["测试小红书一级"],
  secondaryTypes: { "测试小红书一级": ["测试小红书二级"] }
};
export { DOUYIN_CHANNEL_PRIMARY_TYPES, secondaryLabelsForPrimary };
""",
            encoding="utf-8",
        )

    def test_taxonomy_loader_reads_harvester_runtime_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "harvester-THS"
            self._write_harvester_taxonomy_fixture(root)

            taxonomy = load_harvester_taxonomy(root)

        self.assertEqual(taxonomy.douyin, {"测试一级": {"测试二级"}})
        self.assertEqual(taxonomy.xhs, {"测试小红书一级": {"测试小红书二级"}})
        self.assertEqual(taxonomy.bilibili, {"测试B站"})

    def test_module_constants_initialize_from_harvester_root_env(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "harvester-THS"
            self._write_harvester_taxonomy_fixture(root)
            old_value = os.environ.get("HARVESTER_ROOT")
            os.environ["HARVESTER_ROOT"] = str(root)
            try:
                import ops_data_workflow.platform_taxonomy as taxonomy_module

                reloaded = importlib.reload(taxonomy_module)
                self.assertEqual(reloaded._EFFECTIVE_TAXONOMY.source, str(root.resolve()))
                self.assertEqual(reloaded.DOUYIN_TAXONOMY, {"测试一级": {"测试二级"}})
                self.assertEqual(reloaded.XHS_TAXONOMY, {"测试小红书一级": {"测试小红书二级"}})
                self.assertEqual(reloaded.BILIBILI_CONTENT_TYPES, {"测试B站"})
            finally:
                if old_value is None:
                    os.environ.pop("HARVESTER_ROOT", None)
                else:
                    os.environ["HARVESTER_ROOT"] = old_value
                import ops_data_workflow.platform_taxonomy as taxonomy_module

                importlib.reload(taxonomy_module)

    def test_type_recap_uses_saved_value_weights_and_keeps_empty_douyin_secondary(self):
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
            douyin = by_key.loc[("抖音", "douyin_l1", "长视频")]
            bilibili = by_key.loc[("B站", "bilibili", "新手教学指标教学")]
            self.assertNotIn(("抖音", "douyin_l2", "长视频"), by_key.index)
            self.assertEqual(float(douyin["value"]), 70.0)
            self.assertEqual(float(douyin["activation_cost"]), 150.0)
            self.assertEqual(float(douyin["first_pay_cost"]), 1000.0)
            self.assertEqual(float(bilibili["value"]), 20.0)

    def test_persist_type_recap_from_top_content_does_not_require_multimodal_results(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-1",
                        "content_id": "111",
                        "title": "股友说",
                        "category_l1": "股友说",
                        "category_l2": "股民教学",
                        "spend": 100,
                        "impressions": 1000,
                        "activations": 4,
                        "first_pay_count": 1,
                    }
                ]
            )

            written = persist_type_recap_from_top_content(db_path, "batch-1", top_content)
            type_items = list_type_recap_items(db_path, batch_id="batch-1")
            multimodal_items = list_multimodal_recap_items(db_path, batch_id="batch-1")

            self.assertEqual(written, 2)
            self.assertEqual(set(type_items["type_level"]), {"douyin_l1", "douyin_l2"})
            self.assertTrue(multimodal_items.empty)

    def test_type_recap_uses_harvester_taxonomy_and_ignores_invalid_ai_labels(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            update_recap_settings(db_path, activation_weight=1.0, first_pay_weight=5.0)
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书市场部",
                        "content_identity_key": "xhs-1",
                        "content_id": "note-1",
                        "title": "K线图文",
                        "category_l1": "图文",
                        "category_l2": "理财方法",
                        "content_type": "理财方法",
                        "spend": 100,
                        "impressions": 1000,
                        "activations": 10,
                        "first_pay_count": 2,
                    },
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-1",
                        "content_id": "111",
                        "title": "动画对比",
                        "category_l1": "财商动画",
                        "category_l2": "对比分析类",
                        "content_type": "对比分析类",
                        "spend": 200,
                        "impressions": 2000,
                        "activations": 8,
                        "first_pay_count": 1,
                    },
                    {
                        "platform": "B站",
                        "channel": "B站市场部",
                        "content_identity_key": "bili-1",
                        "content_id": "BV1",
                        "title": "指标教学",
                        "category_l1": "不应使用",
                        "category_l2": "不应使用",
                        "bilibili_content_type": "新手教学指标教学",
                        "content_type": "旧兼容字段",
                        "spend": 300,
                        "impressions": 3000,
                        "activations": 6,
                        "first_pay_count": 1,
                    },
                ]
            )
            multimodal_results = pd.DataFrame(
                [
                    {"content_identity_key": "xhs-1", "一级内容类型": "财经知识", "二级内容类型": "K线形态科普"},
                    {"content_identity_key": "dy-1", "一级内容类型": "财商教育", "二级内容类型": "对比分析类动画"},
                    {"content_identity_key": "bili-1", "B站内容类型": "新手教学"},
                ]
            )

            recap = build_type_recap_items(db_path, "batch-1", top_content, multimodal_results=multimodal_results)
            labels = set(recap["content_type"])
            by_key = recap.set_index(["platform", "type_level", "content_type"])

            self.assertIn(("小红书", "xhs_l1", "图文"), by_key.index)
            self.assertIn(("小红书", "xhs_l2", "理财方法"), by_key.index)
            self.assertIn(("抖音", "douyin_l1", "财商动画"), by_key.index)
            self.assertIn(("抖音", "douyin_l2", "对比分析类"), by_key.index)
            self.assertIn(("B站", "bilibili", "新手教学指标教学"), by_key.index)
            self.assertFalse({"财经知识", "K线形态科普", "财商教育", "对比分析类动画", "新手教学"} & labels)

    def test_persist_multimodal_strategy_recap_items_without_type_recap_side_effect(self):
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
                        "category_l1": "图文",
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
                analysis_purpose=ANALYSIS_PURPOSE_STRATEGY_RECAP,
                analyzer=lambda row: {
                    "一级内容类型": "图文",
                    "二级内容类型": "理财方法",
                    "共性总结": "问题场景明确",
                },
            )

            items = list_multimodal_recap_items(db_path, batch_id="batch-1")
            types = list_type_recap_items(db_path, batch_id="batch-1")
            strategies = list_strategy_recap_items(db_path, batch_id="batch-1")

            self.assertEqual(result.item_count, 1)
            self.assertEqual(result.type_count, 0)
            self.assertEqual(result.strategy_count, 2)
            self.assertEqual(items.iloc[0]["category_l2"], "理财方法")
            self.assertEqual(items.iloc[0]["analysis_purpose"], ANALYSIS_PURPOSE_STRATEGY_RECAP)
            self.assertEqual(items.iloc[0]["classification_write_status"], "no_classification")
            self.assertIn("策略复盘", items.iloc[0]["classification_write_reason"])
            self.assertTrue(types.empty)
            xhs_l2 = strategies[strategies["type_level"].eq("xhs_l2")].iloc[0]
            self.assertEqual(xhs_l2["content_type"], "理财方法")
            self.assertIn("问题场景明确", xhs_l2["common_patterns"])

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
                        "category_l1": "图文",
                        "category_l2": "理财方法",
                        "content_type": "理财方法",
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
                analysis_purpose=ANALYSIS_PURPOSE_STRATEGY_RECAP,
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
            self.assertEqual(row["category_l1"], "图文")
            self.assertEqual(row["category_l2"], "理财方法")
            self.assertEqual(row["title_hook"], "问题钩子")
            self.assertEqual(row["visual_structure"], "封面大字")
            self.assertEqual(row["reuse_points"], "问题场景")
            self.assertEqual(row["classification_write_status"], "skipped_existing")

    def test_fill_missing_type_only_fills_empty_fields_and_rejects_invalid_taxonomy(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            batch_id = "batch-1"
            performance = pd.DataFrame(
                [
                    {
                        "performance_key": "p-existing",
                        "batch_id": batch_id,
                        "platform": "小红书",
                        "channel": "小红书市场部",
                        "content_identity_key": "xhs-existing",
                        "asset_key": "asset-existing",
                        "content_id": "note-existing",
                        "title": "已有类型不覆盖",
                        "category_l1": "图文",
                        "category_l2": "理财方法",
                        "bilibili_content_type": "",
                        "spend": 100,
                        "impressions": 1000,
                        "activations": 4,
                        "first_pay_count": 1,
                    },
                    {
                        "performance_key": "p-missing",
                        "batch_id": batch_id,
                        "platform": "小红书",
                        "channel": "小红书市场部",
                        "content_identity_key": "xhs-missing",
                        "asset_key": "asset-missing",
                        "content_id": "note-missing",
                        "title": "缺失类型可补",
                        "category_l1": "",
                        "category_l2": "",
                        "bilibili_content_type": "",
                        "spend": 200,
                        "impressions": 2000,
                        "activations": 5,
                        "first_pay_count": 1,
                    },
                    {
                        "performance_key": "p-invalid",
                        "batch_id": batch_id,
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-invalid",
                        "asset_key": "asset-invalid",
                        "content_id": "",
                        "title": "非法类型拒绝",
                        "category_l1": "",
                        "category_l2": "",
                        "bilibili_content_type": "",
                        "spend": 300,
                        "impressions": 3000,
                        "activations": 6,
                        "first_pay_count": 1,
                    },
                ]
            )
            persist_content_performance_items(db_path, batch_id, performance)

            result = persist_multimodal_recap(
                db_path,
                batch_id,
                performance,
                analysis_purpose=ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
                analyzer=lambda row: {
                    "一级内容类型": "视频" if row["content_identity_key"] != "dy-invalid" else "财商教育",
                    "二级内容类型": "资讯" if row["content_identity_key"] != "dy-invalid" else "热点科普",
                    "共性总结": "用于审计",
                },
            )
            items = list_multimodal_recap_items(db_path, batch_id=batch_id).set_index("content_identity_key")
            stored = list_content_performance_items(db_path, batch_id=batch_id).set_index("content_identity_key")

            self.assertEqual(result.item_count, 3)
            self.assertEqual(items.loc["xhs-existing", "classification_write_status"], "skipped_existing")
            self.assertEqual(items.loc["xhs-existing", "category_l1"], "图文")
            self.assertEqual(items.loc["xhs-existing", "category_l2"], "理财方法")
            self.assertEqual(stored.loc["xhs-existing", "category_l1"], "图文")
            self.assertEqual(stored.loc["xhs-existing", "category_l2"], "理财方法")

            self.assertEqual(items.loc["xhs-missing", "classification_write_status"], "filled")
            self.assertEqual(stored.loc["xhs-missing", "category_l1"], "视频")
            self.assertEqual(stored.loc["xhs-missing", "category_l2"], "资讯")

            self.assertEqual(items.loc["dy-invalid", "classification_write_status"], "rejected_invalid_taxonomy")
            self.assertIn("财商教育", items.loc["dy-invalid", "classification_write_reason"])
            self.assertEqual(stored.loc["dy-invalid", "category_l1"], "")
            self.assertEqual(stored.loc["dy-invalid", "category_l2"], "")

            with sqlite3.connect(db_path) as conn:
                columns = {row[1] for row in conn.execute("pragma table_info(multimodal_recap_items)").fetchall()}
            self.assertTrue(
                {
                    "analysis_purpose",
                    "evidence_source",
                    "classification_write_status",
                    "classification_write_reason",
                }.issubset(columns)
            )

    def test_strategy_recap_groups_by_channel_and_content_type_without_mixing_channels(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            update_recap_settings(db_path, activation_weight=1.0, first_pay_weight=10.0)
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-1",
                        "content_id": "dy-1",
                        "title": "抖音股友说",
                        "category_l1": "股友说",
                        "category_l2": "股民教学",
                        "spend": 100,
                        "impressions": 1000,
                        "activations": 3,
                        "first_pay_count": 1,
                    },
                    {
                        "platform": "抖音",
                        "channel": "抖音矩阵号",
                        "content_identity_key": "dy-2",
                        "content_id": "dy-2",
                        "title": "矩阵股友说",
                        "category_l1": "股友说",
                        "category_l2": "股民教学",
                        "spend": 200,
                        "impressions": 2000,
                        "activations": 4,
                        "first_pay_count": 1,
                    },
                ]
            )
            multimodal_results = pd.DataFrame(
                [
                    {
                        "content_identity_key": "dy-1",
                        "analysis_purpose": ANALYSIS_PURPOSE_STRATEGY_RECAP,
                        "summary": "商业化强钩子",
                        "reuse_points": "利益点前置",
                        "avoid_points": "避免承诺收益",
                        "next_period_strategy": "延展问答脚本",
                    },
                    {
                        "content_identity_key": "dy-2",
                        "analysis_purpose": ANALYSIS_PURPOSE_STRATEGY_RECAP,
                        "summary": "矩阵号强人设",
                        "reuse_points": "人设连续",
                        "avoid_points": "避免口径散",
                        "next_period_strategy": "延展系列选题",
                    },
                ]
            )

            strategy = build_strategy_recap_items(
                db_path,
                "batch-1",
                top_content,
                multimodal_results=multimodal_results,
            )
            l2 = strategy[strategy["type_level"].eq("douyin_l2")].set_index("channel")

            self.assertEqual(set(l2.index), {"抖音商业化", "抖音矩阵号"})
            self.assertEqual(int(l2.loc["抖音商业化", "item_count"]), 1)
            self.assertEqual(int(l2.loc["抖音矩阵号", "item_count"]), 1)
            self.assertIn("商业化强钩子", l2.loc["抖音商业化", "common_patterns"])
            self.assertNotIn("矩阵号强人设", l2.loc["抖音商业化", "common_patterns"])

    def test_multimodal_persistence_keeps_fill_type_audit_when_strategy_recap_runs_later(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            batch_id = "batch-1"
            fill_pool = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书市场部",
                        "content_identity_key": "xhs-fill",
                        "content_id": "note-fill",
                        "title": "缺类型素材",
                        "category_l1": "",
                        "category_l2": "",
                    }
                ]
            )
            strategy_pool = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书市场部",
                        "content_identity_key": "xhs-strategy",
                        "content_id": "note-strategy",
                        "title": "策略素材",
                        "category_l1": "图文",
                        "category_l2": "理财方法",
                    }
                ]
            )

            persist_multimodal_recap(
                db_path,
                batch_id,
                fill_pool,
                analysis_purpose=ANALYSIS_PURPOSE_FILL_MISSING_TYPE,
                analyzer=lambda row: {"一级内容类型": "视频", "二级内容类型": "资讯"},
            )
            persist_multimodal_recap(
                db_path,
                batch_id,
                strategy_pool,
                analysis_purpose=ANALYSIS_PURPOSE_STRATEGY_RECAP,
                analyzer=lambda row: {"共性总结": "策略共性"},
            )

            items = list_multimodal_recap_items(db_path, batch_id=batch_id)

            self.assertEqual(
                set(items["analysis_purpose"]),
                {ANALYSIS_PURPOSE_FILL_MISSING_TYPE, ANALYSIS_PURPOSE_STRATEGY_RECAP},
            )

    def test_strategy_recap_persistence_keeps_distinct_tier_purposes(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            batch_id = "batch-1"
            tier1_pool = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-tier1",
                        "content_id": "dy-tier1",
                        "title": "一级素材",
                        "category_l1": "股友说",
                        "category_l2": "股民教学",
                        "spend": 3000,
                        "impressions": 10000,
                    }
                ]
            )
            tier2_pool = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-tier2",
                        "content_id": "dy-tier2",
                        "title": "二级素材",
                        "category_l1": "社区话题",
                        "category_l2": "股市段子互动",
                        "spend": 1000,
                        "impressions": 200000,
                    }
                ]
            )

            persist_multimodal_recap(
                db_path,
                batch_id,
                tier1_pool,
                analysis_purpose="strategy_recap:tier1_spend_top",
                analyzer=lambda row: {"共性总结": "一级共性"},
            )
            persist_multimodal_recap(
                db_path,
                batch_id,
                tier2_pool,
                analysis_purpose="strategy_recap:tier2_exposure_top",
                analyzer=lambda row: {"共性总结": "二级共性"},
            )

            items = list_multimodal_recap_items(db_path, batch_id=batch_id)
            strategies = list_strategy_recap_items(db_path, batch_id=batch_id)

            self.assertEqual(
                set(items["analysis_purpose"]),
                {"strategy_recap:tier1_spend_top", "strategy_recap:tier2_exposure_top"},
            )
            self.assertEqual(
                set(strategies["analysis_purpose"]),
                {"strategy_recap:tier1_spend_top", "strategy_recap:tier2_exposure_top"},
            )
            self.assertIn("一级共性", "；".join(strategies["common_patterns"]))
            self.assertIn("二级共性", "；".join(strategies["common_patterns"]))


if __name__ == "__main__":
    unittest.main()
