from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
from openpyxl import load_workbook

from ops_data_workflow.workflow import run_workflow
from ops_data_workflow.pipeline import analyze_input_dir
from ops_data_workflow.reference_tables import account_mapping_lookup, load_reference_tables, parse_period_from_raw_dir


def _write_raw_fixture(raw_dir: Path) -> None:
    with pd.ExcelWriter(raw_dir / "B站.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "视频AVID": "av1",
                    "视频BVID": "bv1",
                    "视频标题": "实盘大赛冠军孙辉--370万到2000万的传奇交易之路",
                    "Up主mid": "1622777305",
                    "日期": "2026-04-01~2026-04-27",
                    "花费": 100.0,
                    "展示量": 10000,
                    "点击量": 500,
                    "应用激活数": 20,
                    "应用内付费": 4,
                }
            ]
        ).to_excel(writer, sheet_name="sheet1", index=False)

    with pd.ExcelWriter(raw_dir / "小红书商业化.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "时间": "2026-04-01~2026-04-27",
                    "标题": "存储芯片板块再度爆发",
                    "笔记ID": "note-1",
                    "发布作者": "同花顺理财",
                    "类型": "图文",
                    "内容分类": "热点行情",
                    "消费": 60.0,
                    "展现量": 6000,
                    "点击量": 300,
                    "激活数": 12,
                    "首次付费次数": 2,
                    "内容类型": "",
                },
                {
                    "时间": "2026-04-01~2026-04-27",
                    "标题": "给短线交易者的完美范例 #股友说",
                    "笔记ID": "note-2",
                    "发布作者": "同花顺研习社",
                    "类型": "视频",
                    "内容分类": "",
                    "消费": 40.0,
                    "展现量": 4000,
                    "点击量": 100,
                    "激活数": 4,
                    "首次付费次数": 1,
                    "内容类型": "",
                },
            ]
        ).to_excel(writer, sheet_name="kos账户投放数据", index=False)
        pd.DataFrame(
            [
                {"笔记ID": "note-1", "账号": "投资号", "内容类型": "资讯"},
            ]
        ).to_excel(writer, sheet_name="内容表格", index=False, startrow=1)

    with pd.ExcelWriter(raw_dir / "抖音商业化.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "视频标题": "什么样的人能成为交易高手？ #股友说 #股民",
                    "视频id": "dy-1",
                    "素材ID": "mat-1",
                    "消耗": 200.0,
                    "展示数": 20000,
                    "点击数": 800,
                    "激活数": 50,
                    "付费次数": 20,
                    "内容类型": "",
                },
                {
                    "视频标题": "股市是仅次于高考最公平的竞争",
                    "视频id": "dy-2",
                    "素材ID": "mat-2",
                    "消耗": 100.0,
                    "展示数": 10000,
                    "点击数": 400,
                    "激活数": 20,
                    "付费次数": 8,
                    "内容类型": "股友说",
                },
            ]
        ).to_excel(writer, sheet_name="Sheet2", index=False)

    with pd.ExcelWriter(raw_dir / "抖音市场部.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "创建时间": "2026-04-01",
                    "素材ID": "mat-3",
                    "视频标题": "是天才就来同花顺证明给我看 #同花顺进行曲",
                    "视频id": "dy-m-1",
                    "消耗": 120.0,
                    "展示数": 12000,
                    "激活数": 24,
                    "付费次数": 10,
                    "内容类型": "",
                }
            ]
        ).to_excel(writer, sheet_name="Sheet2", index=False)

    total = pd.DataFrame(
        [
            {"渠道": "B站", "消耗": "50", "激活": "10", "付费": "2"},
            {"渠道": "小红书商业化", "消耗": "100", "激活": "16", "付费": "3"},
            {"渠道": "抖音商业化", "消耗": "150", "激活": "35", "付费": "14"},
            {"渠道": "抖音市场部", "消耗": "60", "激活": "12", "付费": "5"},
        ]
    )
    with pd.ExcelWriter(raw_dir / "四月消耗，占比等总数据.xlsx", engine="openpyxl") as writer:
        total.to_excel(writer, sheet_name="Sheet1", index=False, startrow=5, startcol=8)


def _remove_total_fixture(raw_dir: Path) -> None:
    (raw_dir / "四月消耗，占比等总数据.xlsx").unlink()


def _looks_like_english_field_name(value: object) -> bool:
    text = "" if value is None else str(value)
    return bool("__" in text or "_" in text or text in {"channel", "source_file", "canonical_column"})


class WorkflowTests(unittest.TestCase):
    def test_workflow_reads_csv_sources_and_builds_platform_summaries(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频AVID": "av-csv",
                        "视频BVID": "bv-csv",
                        "视频标题": "B站财经内容",
                        "花费": 80,
                        "展示量": 8000,
                        "点击量": 400,
                        "应用激活数": 16,
                        "应用内付费": 4,
                        "素材中心id": "mat-b",
                    }
                ]
            ).to_csv(raw_dir / "B站.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame(
                [
                    {
                        "视频标题": "抖音投流内容",
                        "视频id": "dy-csv",
                        "素材ID": "mat-dy",
                        "消耗": 120,
                        "展示数": 10000,
                        "点击数": 500,
                        "激活数": 30,
                        "付费次数": 6,
                        "内容类型": "热点行情",
                    }
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            self.assertEqual(len(result.canonical), 2)
            platform_summary = result.platform_summary.set_index("channel")
            self.assertAlmostEqual(platform_summary.loc["B站", "spend"], 80.0)
            self.assertAlmostEqual(platform_summary.loc["抖音商业化", "activations"], 30.0)
            platform_category = result.platform_category_summary
            self.assertIn("channel", platform_category.columns)
            self.assertIn("content_category", platform_category.columns)

    def test_bilibili_aggregate_export_without_title_is_ingested_with_xiaohongshu(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "B站视频投放数据-5.15.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "视频bvid": "BV14Vo5BFE1w",
                            "求和项:总花费": 589.62,
                            "求和项:展示量": 33224,
                            "求和项:点击量": 187,
                            "求和项:应用激活数": 14,
                            "求和项:应用内首次付费次数": 3,
                        },
                        {
                            "视频bvid": "BV17EoKB9E7e",
                            "求和项:总花费": 147.84,
                            "求和项:展示量": 9547,
                            "求和项:点击量": 299,
                            "求和项:应用激活数": 3,
                            "求和项:应用内首次付费次数": 1,
                        },
                    ]
                ).to_excel(writer, sheet_name="Sheet1", index=False)
            with pd.ExcelWriter(raw_dir / "小红书账号投放数据（2026.05.08-05.14）.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "时间": "2026-05-08~2026-05-14",
                            "标题": "小红书内容",
                            "笔记ID": "note-1",
                            "发布作者": "同花顺理财",
                            "类型": "图文",
                            "内容分类": "热点行情",
                            "消费": 60.0,
                            "展现量": 6000,
                            "点击量": 300,
                            "激活数": 12,
                            "首次付费次数": 2,
                        }
                    ]
                ).to_excel(writer, sheet_name="02户", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-05-08",
                "2026-05-14",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual(result.canonical["channel"].value_counts().to_dict(), {"B站": 2, "小红书商业化": 1})
            bilibili = result.canonical[result.canonical["channel"].eq("B站")].sort_values("content_id")
            first = bilibili.iloc[0]
            self.assertEqual(first["content_id"], "BV14Vo5BFE1w")
            self.assertEqual(first["title"], "")
            self.assertAlmostEqual(first["spend"], 589.62)
            self.assertAlmostEqual(first["activations"], 14.0)
            self.assertAlmostEqual(first["first_pay_count"], 3.0)
            self.assertEqual(first["content_category"], "B站全部")
            self.assertEqual(first["category_l2"], "B站全部")
            self.assertEqual(first["category_l3"], "")

    def test_missing_manual_category_is_completed_by_injected_ai_matcher(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "一个暂时无法从标题判断的内容",
                        "视频id": "dy-unknown",
                        "素材ID": "mat-unknown",
                        "消耗": 100.0,
                        "展示数": 1000,
                        "点击数": 100,
                        "激活数": 10,
                        "付费次数": 2,
                        "内容类型": "",
                    }
                ]
            ).to_csv(raw_dir / "抖音市场部.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame(
                [
                    {
                        "视频标题": "人工标记内容",
                        "视频id": "dy-manual",
                        "素材ID": "mat-manual",
                        "消耗": 120,
                        "展示数": 10000,
                        "点击数": 500,
                        "激活数": 30,
                        "付费次数": 6,
                        "内容类型": "热点行情",
                    }
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")

            def matcher(items, category_library, env_path):
                self.assertIn("热点行情", category_library)
                return {int(index): "热点行情" for index in items.index}

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=matcher,
                env_path=Path(tmp) / ".env",
            )

            inferred = result.canonical[result.canonical["content_id"].eq("dy-unknown")].iloc[0]
            manual = result.canonical[result.canonical["content_id"].eq("dy-manual")].iloc[0]
            self.assertEqual(inferred["manual_category"], "")
            self.assertEqual(inferred["ai_category"], "热点行情")
            self.assertEqual(inferred["content_category"], "热点行情")
            self.assertEqual(inferred["category_status"], "DeepSeek匹配")
            self.assertEqual(manual["manual_category"], "热点行情")
            self.assertEqual(manual["ai_category"], "")
            self.assertEqual(manual["content_category"], "热点行情")
            self.assertEqual(manual["category_status"], "人工标记")

    def test_title_keyword_rules_complete_missing_category_before_ai_matcher(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "短线交易高手如何控制回撤",
                        "视频id": "dy-keyword",
                        "素材ID": "mat-keyword",
                        "消耗": 100.0,
                        "展示数": 1000,
                        "点击数": 100,
                        "激活数": 10,
                        "付费次数": 2,
                        "内容类型": "",
                    }
                ]
            ).to_csv(raw_dir / "抖音市场部.csv", index=False, encoding="utf-8-sig")

            def matcher(items, category_library, env_path):
                return {int(index): "资讯" for index in items.index}

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=matcher,
                env_path=Path(tmp) / ".env",
            )

            item = result.canonical.iloc[0]
            self.assertEqual(item["manual_category"], "")
            self.assertEqual(item["ai_category"], "股友说")
            self.assertEqual(item["content_category"], "股友说")
            self.assertEqual(item["category_status"], "标题关键词匹配")

    def test_missing_secondary_category_uses_channel_account_majority_fallback(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "人工分类内容",
                        "视频id": "dy-known",
                        "素材ID": "mat-known",
                        "账号": "同花顺投资",
                        "消耗": 120,
                        "展示数": 10000,
                        "点击数": 500,
                        "激活数": 30,
                        "付费次数": 6,
                        "内容类型": "热点行情",
                    },
                    {
                        "视频标题": "没有分类但同账号",
                        "视频id": "dy-missing",
                        "素材ID": "mat-missing",
                        "账号": "同花顺投资",
                        "消耗": 80,
                        "展示数": 5000,
                        "点击数": 200,
                        "激活数": 8,
                        "付费次数": 1,
                        "内容类型": "",
                    },
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=lambda items, category_library, env_path: {},
            )

            inferred = result.canonical[result.canonical["content_id"].eq("dy-missing")].iloc[0]
            self.assertEqual(inferred["category_l2"], "热点行情")
            self.assertEqual(inferred["content_category"], "热点行情")
            self.assertEqual(inferred["category_status"], "同账号栏目补全")
            self.assertEqual(inferred["category_l2_source"], "同账号栏目补全")

    def test_manual_category_still_takes_priority_over_title_keyword_rules(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "短线交易高手聊板块轮动",
                        "视频id": "dy-manual",
                        "素材ID": "mat-manual",
                        "消耗": 120,
                        "展示数": 10000,
                        "点击数": 500,
                        "激活数": 30,
                        "付费次数": 6,
                        "内容类型": "热点行情",
                    }
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                env_path=Path(tmp) / ".env",
            )

            item = result.canonical.iloc[0]
            self.assertEqual(item["manual_category"], "热点行情")
            self.assertEqual(item["ai_category"], "")
            self.assertEqual(item["content_category"], "热点行情")
            self.assertEqual(item["category_status"], "人工标记")

    def test_ai_category_matcher_rejects_categories_outside_current_library(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "一个暂时无法从标题判断的内容",
                        "视频id": "dy-unknown",
                        "素材ID": "mat-unknown",
                        "消耗": 100.0,
                        "展示数": 1000,
                        "点击数": 100,
                        "激活数": 10,
                        "付费次数": 2,
                        "内容类型": "",
                    }
                ]
            ).to_csv(raw_dir / "抖音市场部.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame(
                [
                    {
                        "视频标题": "人工标记内容",
                        "视频id": "dy-manual",
                        "素材ID": "mat-manual",
                        "消耗": 120,
                        "展示数": 10000,
                        "点击数": 500,
                        "激活数": 30,
                        "付费次数": 6,
                        "内容类型": "热点行情",
                    }
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")

            def matcher(items, category_library, env_path):
                return {int(index): "不存在的新类别" for index in items.index}

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=matcher,
                env_path=Path(tmp) / ".env",
            )

            inferred = result.canonical[result.canonical["content_id"].eq("dy-unknown")].iloc[0]
            self.assertEqual(inferred["manual_category"], "")
            self.assertEqual(inferred["ai_category"], "")
            self.assertEqual(inferred["content_category"], "")
            self.assertEqual(inferred["category_status"], "未匹配")

    def test_ai_category_matcher_uses_channel_scoped_category_library_first(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "知乎投放.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "标题": "知乎已知栏目",
                            "内容ID": "zh-known",
                            "账号": "同花顺投资",
                            "内容类型": "投教问答",
                            "消耗": 30,
                            "展示数": 300,
                            "点击数": 30,
                            "激活数": 3,
                            "付费次数": 1,
                        },
                        {
                            "标题": "知乎未知栏目",
                            "内容ID": "zh-missing",
                            "账号": "同花顺投资",
                            "内容类型": "",
                            "消耗": 40,
                            "展示数": 400,
                            "点击数": 40,
                            "激活数": 4,
                            "付费次数": 1,
                        },
                    ]
                ).to_excel(writer, sheet_name="投放数据", index=False)
            with pd.ExcelWriter(raw_dir / "微博投放.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "标题": "微博已知栏目",
                            "内容ID": "wb-known",
                            "账号": "同花顺投资",
                            "内容类型": "热点行情",
                            "消耗": 50,
                            "展示数": 500,
                            "点击数": 50,
                            "激活数": 5,
                            "付费次数": 1,
                        }
                    ]
                ).to_excel(writer, sheet_name="投放数据", index=False)

            seen_libraries = []

            def matcher(items, category_library, env_path):
                seen_libraries.append(tuple(category_library))
                return {int(index): category_library[0] for index in items.index}

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=matcher,
                env_path=Path(tmp) / ".env",
            )

            inferred = result.canonical[result.canonical["content_id"].eq("zh-missing")].iloc[0]
            self.assertIn(("投教问答",), seen_libraries)
            self.assertNotIn(("投教问答", "热点行情"), seen_libraries)
            self.assertEqual(inferred["category_l2"], "投教问答")
            self.assertEqual(inferred["category_source"], "DeepSeek匹配")

    def test_workflow_standardizes_sources_and_preserves_pending_categories(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            canonical = result.canonical
            self.assertEqual(len(canonical), 6)
            self.assertIn("source_file", canonical.columns)
            self.assertEqual(set(canonical["primary_category"].fillna("").astype(str)), {""})
            self.assertEqual(set(canonical["category_l1"].fillna("").astype(str)), {""})
            bilibili = canonical[canonical["content_id"].eq("bv1")].iloc[0]
            self.assertEqual(bilibili["content_category"], "B站全部")
            self.assertEqual(bilibili["category_l2"], "B站全部")
            self.assertEqual(bilibili["category_l3"], "实盘大赛冠军孙辉--370万到2000万的传奇交易之路")
            self.assertEqual(bilibili["category_status"], "渠道固定规则")
            self.assertEqual(
                canonical.loc[canonical["content_id"].eq("note-1"), "content_category"].iloc[0],
                "热点行情",
            )
            self.assertEqual(
                canonical.loc[canonical["content_id"].eq("note-1"), "manual_category"].iloc[0],
                "热点行情",
            )
            self.assertEqual(
                canonical.loc[canonical["content_id"].eq("note-2"), "ai_category"].iloc[0],
                "股友说",
            )
            self.assertEqual(
                canonical.loc[canonical["content_id"].eq("note-2"), "category_status"].iloc[0],
                "标题关键词匹配",
            )
            self.assertEqual(len(result.pending_categories), 0)

    def test_workflow_uses_independent_channels_without_l1_categories(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)
            canonical = result.canonical

            self.assertEqual(
                set(canonical["platform"]),
                {"B站", "小红书商业化", "抖音商业化", "抖音市场部"},
            )
            self.assertEqual(
                set(canonical.loc[canonical["platform"].str.contains("抖音"), "platform_group"]),
                {"抖音"},
            )
            self.assertEqual(set(canonical["category_l1"].fillna("").astype(str)), {""})
            self.assertEqual(set(canonical["primary_category"].fillna("").astype(str)), {""})
            self.assertTrue(canonical["category_l2"].equals(canonical["content_category"]))
            self.assertTrue(canonical["category_source"].equals(canonical["category_status"]))
            self.assertEqual(
                canonical.loc[canonical["content_id"].eq("dy-m-1"), "category_l3"].iloc[0],
                "是天才就来同花顺证明给我看 #同花顺进行曲",
            )

    def test_workflow_resolves_known_bilibili_mid_to_account_name(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)
            canonical = result.canonical

            bilibili = canonical[canonical["content_id"].eq("bv1")].iloc[0]
            xhs = canonical[canonical["content_id"].eq("note-1")].iloc[0]
            douyin_without_account = canonical[canonical["content_id"].eq("dy-m-1")].iloc[0]

            self.assertEqual(bilibili["account_id"], "1622777305")
            self.assertEqual(bilibili["account"], "同花顺投资")
            self.assertEqual(bilibili["author"], "同花顺投资")
            self.assertEqual(xhs["account"], "同花顺理财")
            self.assertEqual(xhs["author"], "同花顺理财")
            self.assertEqual(douyin_without_account["account"], "")

    def test_reference_tables_initialize_account_mapping_and_period_from_raw_dir(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_period_dir = tmp_path / "data" / "raw" / "20260401-20260427"
            raw_period_dir.mkdir(parents=True)

            self.assertEqual(parse_period_from_raw_dir(raw_period_dir), ("2026-04-01", "2026-04-27"))

            references = load_reference_tables(tmp_path / "config" / "reference_tables.xlsx")

            self.assertTrue((tmp_path / "config" / "reference_tables.xlsx").exists())
            account_mapping = references.account_mapping.set_index(["channel", "source_account_id"])
            self.assertEqual(account_mapping.loc[("B站", "1622777305"), "account"], "同花顺投资")
            self.assertIn("账号映射表", references.tables)
            self.assertIn("字段映射表", references.tables)

    def test_reference_tables_workbook_uses_chinese_headers_and_is_still_readable(self):
        with TemporaryDirectory() as tmp:
            reference_path = Path(tmp) / "config" / "reference_tables.xlsx"

            references = load_reference_tables(reference_path)
            workbook = load_workbook(reference_path, read_only=True)

            account_headers = [cell.value for cell in next(workbook["账号映射表"].iter_rows(max_row=1))]
            field_headers = [cell.value for cell in next(workbook["字段映射表"].iter_rows(max_row=1))]
            workbook.close()

            self.assertEqual(account_headers, ["渠道", "来源账号ID", "来源账号名", "实际账号", "映射来源", "说明"])
            self.assertIn("标准字段", field_headers)
            self.assertNotIn("source_account_id", account_headers)
            self.assertNotIn("canonical_column", field_headers)
            lookup = account_mapping_lookup(references.account_mapping)
            self.assertEqual(lookup[("B站", "1622777305")]["account"], "同花顺投资")

    def test_unknown_bilibili_mid_is_kept_for_manual_review(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频AVID": "av-unknown-mid",
                        "视频BVID": "bv-unknown-mid",
                        "视频标题": "B站未知账号内容",
                        "Up主mid": "999999",
                        "花费": 80,
                        "展示量": 8000,
                        "点击量": 400,
                        "应用激活数": 16,
                        "应用内付费": 4,
                    }
                ]
            ).to_csv(raw_dir / "B站.csv", index=False, encoding="utf-8-sig")

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                reference_tables_path=Path(tmp) / "config" / "reference_tables.xlsx",
                category_matcher=lambda items, category_library, env_path: {},
            )

            row = result.canonical.iloc[0]
            self.assertEqual(row["account_id"], "999999")
            self.assertEqual(row["account"], "")
            self.assertEqual(row["account_mapping_source"], "未匹配")
            self.assertTrue(row["needs_manual_review"])
            self.assertIn("账号映射缺失", row["review_reasons"])

    def test_channel_dedupe_sums_large_numeric_conflicts_and_marks_review(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "同一视频A",
                        "视频id": "dy-dup",
                        "素材ID": "mat-1",
                        "账号": "同花顺投资",
                        "消耗": 100,
                        "展示数": 1000,
                        "点击数": 100,
                        "激活数": 10,
                        "付费次数": 2,
                        "内容类型": "热点行情",
                    },
                    {
                        "视频标题": "同一视频A",
                        "视频id": "dy-dup",
                        "素材ID": "mat-2",
                        "账号": "同花顺投资",
                        "消耗": 120,
                        "展示数": 1005,
                        "点击数": 100,
                        "激活数": 10,
                        "付费次数": 2,
                        "内容类型": "热点行情",
                    },
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")
            pd.DataFrame(
                [
                    {
                        "视频标题": "同一视频跨渠道不合并",
                        "视频id": "dy-dup",
                        "素材ID": "mat-other",
                        "账号": "同花顺投资",
                        "消耗": 70,
                        "展示数": 700,
                        "点击数": 70,
                        "激活数": 7,
                        "付费次数": 1,
                        "内容类型": "热点行情",
                    }
                ]
            ).to_csv(raw_dir / "抖音市场部.csv", index=False, encoding="utf-8-sig")

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual(len(result.canonical), 2)
            commercial = result.canonical[result.canonical["channel"].eq("抖音商业化")].iloc[0]
            market = result.canonical[result.canonical["channel"].eq("抖音市场部")].iloc[0]
            self.assertEqual(commercial["merged_row_count"], 2)
            self.assertAlmostEqual(commercial["spend"], 220.0)
            self.assertAlmostEqual(commercial["impressions"], 1000.0)
            self.assertTrue(commercial["needs_manual_review"])
            self.assertIn("数值冲突", commercial["review_reasons"])
            self.assertIn("spend", commercial["conflict_details"])
            self.assertEqual(market["merged_row_count"], 1)

    def test_generic_excel_is_treated_as_channel_from_file_name(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "知乎投放.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "标题": "知乎财经问答",
                            "内容ID": "zh-1",
                            "账号": "同花顺投资",
                            "内容类型": "投教问答",
                            "消耗": 30,
                            "展示数": 300,
                            "点击数": 30,
                            "激活数": 3,
                            "付费次数": 1,
                        }
                    ]
                ).to_excel(writer, sheet_name="投放数据", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=lambda items, category_library, env_path: {},
            )

            row = result.canonical.iloc[0]
            self.assertEqual(row["channel"], "知乎投放")
            self.assertEqual(row["account"], "同花顺投资")
            self.assertEqual(row["content_id"], "zh-1")
            self.assertEqual(row["category_l2"], "投教问答")

    def test_raw_extra_columns_export_with_chinese_display_names(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "知乎投放.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "标题": "知乎财经问答",
                            "内容ID": "zh-1",
                            "账号": "同花顺投资",
                            "内容类型": "投教问答",
                            "消耗": 30,
                            "展示数": 300,
                            "点击数": 30,
                            "激活数": 3,
                            "付费次数": 1,
                            "自定义列": "保留原始字段",
                        }
                    ]
                ).to_excel(writer, sheet_name="投放数据", index=False)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            csv_columns = list(pd.read_csv(result.canonical_csv).columns)
            self.assertIn("原始字段：知乎投放：自定义列", csv_columns)
            self.assertFalse(any(column.startswith("raw__") for column in csv_columns))

    def test_bilibili_numeric_mid_is_normalized_without_decimal_suffix(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频AVID": "av-mid",
                        "视频BVID": "bv-mid",
                        "视频标题": "B站财经内容",
                        "Up主mid": 1622777305.0,
                        "花费": 80,
                        "展示量": 8000,
                        "点击量": 400,
                        "应用激活数": 16,
                        "应用内付费": 4,
                    }
                ]
            ).to_csv(raw_dir / "B站.csv", index=False, encoding="utf-8-sig")

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual(result.canonical.iloc[0]["account_id"], "1622777305")
            self.assertEqual(result.canonical.iloc[0]["account"], "同花顺投资")

    def test_workflow_builds_data_quality_report_and_review_queue(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "B站.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "视频AVID": "av-unknown",
                            "视频BVID": "bv-unknown",
                            "视频标题": "一个暂时无法从标题判断的内容",
                            "花费": 100.0,
                            "展示量": 0,
                            "点击量": 10,
                            "应用激活数": 1,
                            "应用内付费": 0,
                        }
                    ]
                ).to_excel(writer, sheet_name="sheet1", index=False)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            quality = result.data_quality.set_index("metric")
            self.assertNotIn("一级分类缺失率", quality.index)
            self.assertIn("二级分类缺失率", quality.index)
            self.assertEqual(quality.loc["展示为0但点击大于0", "count"], 1)
            self.assertEqual(len(result.review_queue), 0)

            workbook = load_workbook(result.analysis_xlsx, read_only=True)
            self.assertIn("数据质量报告", workbook.sheetnames)
            self.assertIn("人工审核表", workbook.sheetnames)
            workbook.close()

    def test_workflow_summarizes_channels_without_external_totals(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            channel_summary = result.channel_summary.set_index("channel")
            self.assertAlmostEqual(channel_summary.loc["B站", "spend"], 100.0)
            self.assertAlmostEqual(channel_summary.loc["抖音商业化", "spend"], 300.0)
            self.assertAlmostEqual(channel_summary.loc["抖音商业化", "activations"], 70.0)

            self.assertNotIn("spend_ratio", result.canonical.columns)
            self.assertNotIn("spend_calibrated", result.canonical.columns)

    def test_workflow_writes_html_excel_and_canonical_csv_outputs(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            self.assertTrue(result.report_html.exists())
            self.assertTrue(result.analysis_xlsx.exists())
            self.assertTrue(result.canonical_csv.exists())
            html = result.report_html.read_text(encoding="utf-8")
            self.assertIn("渠道化内容投放分析与定点投流报告", html)
            self.assertIn("缺失分类影响说明", html)

            workbook = load_workbook(result.analysis_xlsx, read_only=True)
            self.assertEqual(
                set(workbook.sheetnames),
                {
                    "总表",
                    "分渠道总数据",
                    "分渠道栏目题材排名",
                    "内容类型分级表",
                    "周期报告",
                    "字段映射表",
                    "账号映射表",
                    "账号内容类型对照表",
                    "原始分类统计",
                    "缺失分类清单",
                    "人工审核表",
                    "账号覆盖校验",
                    "消耗Top内容",
                    "封面曝光分析",
                    "数据预处理报告",
                    "重复合并明细",
                    "冲突保留明细",
                    "缺失值处理明细",
                    "数据质量报告",
                    "历史对比",
                    "AI结论",
                },
            )
            workbook.close()
            self.assertTrue(result.total_summary_xlsx.exists())

    def test_exported_tables_use_readable_chinese_column_names(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            csv_columns = list(pd.read_csv(result.canonical_csv).columns)
            self.assertIn("渠道", csv_columns)
            self.assertIn("视频/笔记id", csv_columns)
            self.assertNotIn("一级内容分类", csv_columns)
            self.assertNotIn("一级类型", csv_columns)
            self.assertIn("人工内容类别", csv_columns)
            self.assertIn("AI生成内容类别", csv_columns)
            self.assertIn("最终内容类别", csv_columns)
            self.assertIn("内容类别来源", csv_columns)
            self.assertIn("消耗", csv_columns)
            self.assertNotIn("content_id", csv_columns)
            self.assertNotIn("manual_category", csv_columns)
            self.assertNotIn("ai_category", csv_columns)
            self.assertNotIn("spend_calibrated", csv_columns)
            self.assertNotIn("校准激活数", csv_columns)
            self.assertNotIn("对账状态", csv_columns)

            workbook = load_workbook(result.analysis_xlsx, read_only=True)
            detail_headers = [cell.value for cell in next(workbook["总表"].iter_rows(max_row=1))]
            ranking_headers = [cell.value for cell in next(workbook["分渠道栏目题材排名"].iter_rows(max_row=1))]
            channel_headers = [cell.value for cell in next(workbook["分渠道总数据"].iter_rows(max_row=1))]
            total_headers = [cell.value for cell in next(workbook["周期报告"].iter_rows(max_row=1))]
            mapping_headers = [cell.value for cell in next(workbook["字段映射表"].iter_rows(max_row=1))]
            self.assertIn("视频/笔记id", detail_headers)
            self.assertNotIn("平台", detail_headers)
            self.assertNotIn("平台组", detail_headers)
            self.assertNotIn("一级内容分类", detail_headers)
            self.assertNotIn("一级类型", detail_headers)
            self.assertNotIn("一级内容分类", ranking_headers)
            self.assertNotIn("一级类型", ranking_headers)
            self.assertIn("人工内容类别", detail_headers)
            self.assertIn("AI生成内容类别", detail_headers)
            self.assertIn("最终内容类别", detail_headers)
            self.assertIn("拉新综合评分", ranking_headers)
            self.assertIn("消耗", channel_headers)
            self.assertIn("消耗占比", total_headers)
            self.assertIn("缺失分类消耗占比", total_headers)
            self.assertIn("标准字段", mapping_headers)
            self.assertNotIn("canonical_column", mapping_headers)
            for headers in [detail_headers, ranking_headers, channel_headers, total_headers, mapping_headers]:
                self.assertFalse(
                    any(_looks_like_english_field_name(header) for header in headers if header),
                    headers,
                )
            self.assertNotIn("消耗校准比例", channel_headers)
            self.assertNotIn("overall_score", ranking_headers)
            workbook.close()

    def test_workflow_accepts_only_the_four_raw_platform_excels(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)
            _remove_total_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            self.assertEqual(len(result.canonical), 6)
            self.assertTrue(result.analysis_xlsx.exists())
            workbook = load_workbook(result.analysis_xlsx, read_only=True)
            self.assertIn("分渠道总数据", workbook.sheetnames)
            self.assertNotIn("渠道对账", workbook.sheetnames)
            self.assertIn("缺失分类清单", workbook.sheetnames)
            workbook.close()

            exported = pd.read_csv(result.canonical_csv)
            self.assertIn("激活数", exported.columns)
            self.assertNotIn("校准激活数", exported.columns)

    def test_missing_values_keep_raw_blanks_while_categories_are_inferred(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)
            exported = pd.read_csv(result.canonical_csv, keep_default_na=False)

            bilibili = exported[exported["视频/笔记id"].eq("bv1")].iloc[0]
            douyin_market = exported[exported["视频/笔记id"].eq("dy-m-1")].iloc[0]
            xhs_missing_secondary = exported[exported["视频/笔记id"].eq("note-2")].iloc[0]

            self.assertEqual(bilibili["人工内容类别"], "")
            self.assertEqual(bilibili["AI生成内容类别"], "B站全部")
            self.assertEqual(bilibili["最终内容类别"], "B站全部")
            self.assertEqual(bilibili["二级栏目"], "B站全部")
            self.assertEqual(bilibili["三级题材"], "实盘大赛冠军孙辉--370万到2000万的传奇交易之路")
            self.assertEqual(bilibili["内容类别来源"], "渠道固定规则")
            self.assertNotIn("一级内容分类", exported.columns)
            self.assertNotIn("一级类型", exported.columns)
            self.assertEqual(douyin_market["点击量"], "")
            self.assertEqual(xhs_missing_secondary["人工内容类别"], "")
            self.assertEqual(xhs_missing_secondary["AI生成内容类别"], "股友说")
            self.assertEqual(xhs_missing_secondary["最终内容类别"], "股友说")

    def test_workflow_collects_raw_category_statistics(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            stats = result.raw_category_stats
            self.assertIn("热点行情", set(stats["value"]))
            self.assertIn("图文", set(stats["value"]))
            self.assertIn("股友说", set(stats["value"]))

            workbook = load_workbook(result.analysis_xlsx, read_only=True)
            stat_headers = [cell.value for cell in next(workbook["原始分类统计"].iter_rows(max_row=1))]
            self.assertEqual(stat_headers, ["来源文件", "Sheet", "原始字段", "原始分类值", "出现次数"])
            workbook.close()

    def test_workflow_writes_total_summary_workbook_like_monthly_total(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            workbook = load_workbook(result.total_summary_xlsx, read_only=True)
            self.assertEqual(
                workbook.sheetnames,
                [
                    "总表",
                    "分渠道总数据",
                    "分渠道栏目题材排名",
                    "内容类型分级表",
                    "字段映射表",
                    "账号映射表",
                    "人工审核表",
                    "数据预处理报告",
                    "周期报告",
                ],
            )
            headers = [cell.value for cell in next(workbook["周期报告"].iter_rows(max_row=1))]
            self.assertIn("渠道", headers)
            self.assertIn("消耗", headers)
            self.assertIn("消耗占比", headers)
            self.assertIn("激活成本", headers)
            self.assertIn("首次付费率", headers)
            platform_headers = [cell.value for cell in next(workbook["分渠道总数据"].iter_rows(max_row=1))]
            platform_category_headers = [
                cell.value for cell in next(workbook["分渠道栏目题材排名"].iter_rows(max_row=1))
            ]
            self.assertIn("渠道", platform_headers)
            self.assertIn("消耗", platform_headers)
            self.assertIn("渠道", platform_category_headers)
            self.assertIn("最终内容类别", platform_category_headers)
            workbook.close()


if __name__ == "__main__":
    unittest.main()
