from pathlib import Path
from contextlib import closing
import json
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

from ops_data_workflow.channel_clean import write_unified_channel_clean_workbook
from ops_data_workflow.workflow import refresh_historical_source_periods, run_archived_workflow, run_workflow
from ops_data_workflow.pipeline import analyze_canonical_frame, analyze_input_dir
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
                    "发布作者": "同顺股民社区",
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
                {"笔记ID": "note-1", "账号": "同花顺投资", "内容类型": "资讯"},
            ]
        ).to_excel(writer, sheet_name="内容表格", index=False, startrow=1)

    with pd.ExcelWriter(raw_dir / "抖音商业化.xlsx", engine="openpyxl") as writer:
        pd.DataFrame(
            [
                {
                    "视频标题": "什么样的人能成为交易高手？ #股友说 #股民",
                    "视频id": "dy-1",
                    "素材ID": "mat-1",
                    "账号": "同花顺投资",
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
                    "账号": "同花顺投资",
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
                    "账号": "同花顺投资",
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
    def test_unified_channel_clean_workbook_writes_channel_and_system_sheets(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "processed"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-04-01",
                        "period_end": "2026-04-07",
                        "channel": "小红书商业化",
                        "platform": "小红书",
                        "platform_group": "小红书",
                        "account": "同花顺投资",
                        "account_raw": "同花顺投资原始",
                        "content_form": "图文",
                        "content_category": "热点行情",
                        "title": "小红书内容",
                        "content_id": "note-1",
                        "material_id": "mat-xhs",
                        "content_url": "https://xhs.example/note-1",
                        "source_time": "2026-04-02",
                        "spend": 100,
                        "impressions": 1000,
                        "clicks": 100,
                        "activations": 10,
                        "first_pay_count": 2,
                        "activation_cost": 10,
                        "first_pay_cost": 50,
                        "metadata_source": "xhs_public",
                        "metadata_confidence": 0.8,
                        "review_status": "待审核",
                        "review_reasons": "内容类型缺失",
                        "raw__小红书商业化__7日付费率": 0.2,
                    },
                    {
                        "period_start": "2026-04-01",
                        "period_end": "2026-04-07",
                        "channel": "B站",
                        "platform": "B站",
                        "platform_group": "B站",
                        "account": "同花顺投资",
                        "content_form": "视频",
                        "manual_category": "投教",
                        "title": "B站内容",
                        "content_id": "BV1abc",
                        "content_url": "https://www.bilibili.com/video/BV1abc/",
                        "spend": 80,
                        "raw__B站__播放完成率": 0.61,
                    },
                ]
            )

            workbook_path = write_unified_channel_clean_workbook(
                canonical,
                output_dir,
                period_label="2026-04-01 至 2026-04-07",
                batch_id="batch-a",
                import_log=pd.DataFrame([{"source_file": "小红书商业化.xlsx", "status": "imported"}]),
                duplicate_content=pd.DataFrame([{"dedupe_key": "note-1", "rows": 2}]),
                conflicts=pd.DataFrame([{"issue_type": "同ID标题冲突", "content_id": "note-1"}]),
                fill_sources=pd.DataFrame(
                    [
                        {
                            "batch_id": "batch-a",
                            "channel": "小红书商业化",
                            "content_id": "note-1",
                            "field_name": "title",
                            "source": "xhs_public",
                            "confidence": 0.8,
                            "status": "filled",
                        }
                    ]
                ),
                review_records=pd.DataFrame([{"content_id": "note-1", "action": "待审核"}]),
            )

            self.assertEqual(workbook_path, output_dir / "cleaned_channels.xlsx")
            workbook = load_workbook(workbook_path, read_only=True)
            self.assertEqual(
                workbook.sheetnames,
                ["小红书商业化", "B站", "导入日志", "重复内容", "冲突项", "补齐来源", "审核记录"],
            )
            workbook.close()

            xhs = pd.read_excel(workbook_path, sheet_name="小红书商业化")
            self.assertEqual(
                list(xhs.columns[:24]),
                [
                    "周期",
                    "渠道",
                    "平台",
                    "账号",
                    "原始账号",
                    "内容形式",
                    "内容类型",
                    "标题",
                    "内容ID",
                    "素材ID",
                    "唯一标识",
                    "内容链接",
                    "发布时间",
                    "消耗",
                    "曝光量",
                    "点击量",
                    "激活数",
                    "付费数",
                    "激活成本",
                    "付费成本",
                    "补齐来源",
                    "补齐置信度",
                    "复核状态",
                    "复核原因",
                ],
            )
            self.assertIn("原始字段__7日付费率", xhs.columns)
            self.assertEqual(xhs.iloc[0]["周期"], "2026-04-01 至 2026-04-07")
            self.assertEqual(xhs.iloc[0]["唯一标识"], "note-1")
            self.assertAlmostEqual(float(xhs.iloc[0]["原始字段__7日付费率"]), 0.2)

            fill_sources = pd.read_excel(workbook_path, sheet_name="补齐来源")
            self.assertEqual(list(fill_sources["batch_id"]), ["batch-a"])

    def test_workflow_writes_channel_clean_workbooks_with_required_columns(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)
            _remove_total_fixture(raw_dir)

            result = run_workflow(raw_dir, "2026-04-01", "2026-04-27", output_dir)

            self.assertFalse((raw_dir / "cleaned.xlsx").exists())
            self.assertFalse((raw_dir / "period_manifest.json").exists())
            self.assertTrue((output_dir / "cleaned.xlsx").exists())
            self.assertTrue((output_dir / "period_manifest.json").exists())
            import_log = pd.read_excel(output_dir / "cleaned.xlsx", sheet_name="导入日志")
            self.assertIn("B站.xlsx", set(import_log["source_file"]))
            clean_dir = output_dir / "channel_clean"
            xhs_clean = clean_dir / "小红书商业化_clean.xlsx"
            self.assertIn(xhs_clean, result.channel_clean_workbooks)
            self.assertTrue(xhs_clean.exists())

            workbook = load_workbook(xhs_clean, read_only=True)
            self.assertEqual(workbook.sheetnames, ["清理后明细"])
            workbook.close()

            cleaned = pd.read_excel(xhs_clean, sheet_name="清理后明细")
            self.assertEqual(
                list(cleaned.columns),
                [
                    "周期",
                    "渠道",
                    "账号",
                    "内容形式",
                    "内容类型",
                    "内容分类",
                    "标题",
                    "id/BV或者唯一标识",
                    "内容链接",
                    "消耗",
                    "曝光量",
                    "激活数",
                    "激活成本",
                    "付费",
                    "付费成本",
                    "匹配来源",
                    "复核原因",
                ],
            )
            row = cleaned[cleaned["id/BV或者唯一标识"].eq("note-1")].iloc[0]
            self.assertEqual(row["周期"], "2026-04-01 至 2026-04-27")
            self.assertEqual(row["渠道"], "小红书商业化")
            self.assertEqual(row["账号"], "同花顺理财")
            self.assertEqual(row["内容形式"], "图文")
            self.assertEqual(row["内容分类"], "热点行情")
            self.assertEqual(row["标题"], "存储芯片板块再度爆发")
            self.assertEqual(row["内容类型"], "热点行情")
            self.assertEqual(float(row["消耗"]), 60.0)
            self.assertEqual(float(row["曝光量"]), 6000.0)
            self.assertEqual(float(row["激活数"]), 12.0)
            self.assertEqual(float(row["激活成本"]), 5.0)
            self.assertEqual(float(row["付费"]), 2.0)
            self.assertEqual(float(row["付费成本"]), 30.0)

            workbook = load_workbook(result.analysis_xlsx, read_only=True)
            self.assertIn("账号过滤规则", workbook.sheetnames)
            self.assertIn("账号过滤明细", workbook.sheetnames)
            workbook.close()

    def test_channel_clean_uses_tagless_title_for_douyin_missing_id(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "抖音商业化.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "视频标题": "人和人的缘分就像炒股 #同花顺股友说 #投资",
                            "账号": "同花顺投资",
                            "消耗": 90,
                            "展示数": 9000,
                            "激活数": 9,
                            "付费次数": 3,
                            "内容类型": "股友说",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sheet2", index=False)

            result = run_workflow(raw_dir, "2026-05-19", "2026-05-25", output_dir)

            dy_clean = output_dir / "channel_clean" / "抖音商业化_clean.xlsx"
            self.assertIn(dy_clean, result.channel_clean_workbooks)
            cleaned = pd.read_excel(dy_clean, sheet_name="清理后明细")
            row = cleaned.iloc[0]
            self.assertEqual(row["标题"], "人和人的缘分就像炒股 #同花顺股友说 #投资")
            self.assertEqual(row["id/BV或者唯一标识"], "人和人的缘分就像炒股")
            self.assertEqual(row["内容分类"], "股友说")
            self.assertEqual(row["内容类型"], "股友说")

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
                            "Up主mid": "1622777305",
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
                            "账号": "同花顺投资",
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
                            "Up主mid": "1622777305",
                            "求和项:总花费": 589.62,
                            "求和项:展示量": 33224,
                            "求和项:点击量": 187,
                            "求和项:应用激活数": 14,
                            "求和项:应用内首次付费次数": 3,
                        },
                        {
                            "视频bvid": "BV17EoKB9E7e",
                            "Up主mid": "1622777305",
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
            self.assertEqual(first["content_form"], "视频")
            self.assertEqual(first["content_category"], "")
            self.assertEqual(first["category_l2"], "")
            self.assertEqual(first["category_l3"], "")

    def test_bilibili_impression_aliases_are_ingested_by_keyword(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "B站.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "视频AVID": "av-alias",
                            "视频BVID": "BVImpressionAlias",
                            "视频标题": "B站展示量别名内容",
                            "Up主mid": "1622777305",
                            "花费": 100.0,
                            "视频展示量": 4321,
                            "曝光转化率": 0.12,
                            "千次展示费用": 23.5,
                            "点击量": 321,
                            "应用激活数": 12,
                            "激活成本": 8.3,
                            "应用内首次付费次数": 4,
                            "付费成本": 25.0,
                        }
                    ]
                ).to_excel(writer, sheet_name="Sheet1", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-05-15",
                "2026-05-21",
                category_matcher=lambda items, category_library, env_path: {},
            )

            row = result.canonical.iloc[0]
            self.assertEqual(row["channel"], "B站")
            self.assertAlmostEqual(row["impressions"], 4321.0)
            self.assertAlmostEqual(row["clicks"], 321.0)
            self.assertAlmostEqual(row["activations"], 12.0)
            self.assertAlmostEqual(row["first_pay_count"], 4.0)

    def test_xiaohongshu_commercial_metric_count_aliases_are_ingested(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "小红书商业化.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "时间": "2026-05-15~2026-05-21",
                            "标题": "小红书投放内容",
                            "笔记ID": "note-app-activation",
                            "发布作者": "同花顺理财",
                            "内容分类": "产品科普",
                            "消费": 100.0,
                            "展现量": 1000,
                            "点击量": 100,
                            "APP激活数": 9,
                            "首次付费数": 3,
                        }
                    ]
                ).to_excel(writer, sheet_name="kos账号笔记投放数据", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-05-15",
                "2026-05-21",
                category_matcher=lambda items, category_library, env_path: {},
            )

            row = result.canonical.iloc[0]
            self.assertEqual(row["channel"], "小红书商业化")
            self.assertAlmostEqual(row["activations"], 9.0)
            self.assertAlmostEqual(row["first_pay_count"], 3.0)
            channel_summary = result.channel_summary.set_index("channel")
            self.assertAlmostEqual(channel_summary.loc["小红书商业化", "activations"], 9.0)
            self.assertAlmostEqual(channel_summary.loc["小红书商业化", "first_pay_count"], 3.0)

    def test_xiaohongshu_commercial_metric_rates_are_not_ingested_as_counts(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "小红书商业化.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "时间": "2026-05-15~2026-05-21",
                            "标题": "小红书投放内容",
                            "笔记ID": "note-rate-only",
                            "发布作者": "同花顺理财",
                            "内容分类": "产品科普",
                            "消费": 100.0,
                            "展现量": 1000,
                            "点击量": 100,
                            "激活成本": 12.3,
                            "激活率": 0.12,
                            "首次付费成本": 33.3,
                            "首次付费率": 0.25,
                        }
                    ]
                ).to_excel(writer, sheet_name="kos账号笔记投放数据", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-05-15",
                "2026-05-21",
                category_matcher=lambda items, category_library, env_path: {},
            )

            row = result.canonical.iloc[0]
            self.assertTrue(pd.isna(row["activations"]))
            self.assertTrue(pd.isna(row["first_pay_count"]))

    def test_xiaohongshu_account_filter_applies_to_raw_excel_input(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            config_path = tmp_path / "config" / "account_filters.yml"
            config_path.parent.mkdir()
            config_path.write_text(
                """
xiaohongshu:
  include_accounts:
    - 股民社区
    - 研习社
  aliases:
    同顺股民社区: 股民社区
    同花顺研习社: 研习社
  exclude_blank: true
""".strip(),
                encoding="utf-8",
            )
            with pd.ExcelWriter(raw_dir / "小红书商业化.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "时间": "2026-05-15~2026-05-21",
                            "标题": "股民社区内容",
                            "笔记ID": "note-included-1",
                            "发布作者": "同顺股民社区",
                            "内容分类": "投教",
                            "消费": 10.0,
                            "激活数": 1,
                            "首次付费次数": 1,
                        },
                        {
                            "时间": "2026-05-15~2026-05-21",
                            "标题": "研习社内容",
                            "笔记ID": "note-included-2",
                            "发布作者": "同花顺研习社",
                            "内容分类": "投教",
                            "消费": 20.0,
                            "激活数": 2,
                            "首次付费次数": 1,
                        },
                        {
                            "时间": "2026-05-15~2026-05-21",
                            "标题": "APP内容",
                            "笔记ID": "note-excluded-app",
                            "发布作者": "同花顺APP",
                            "内容分类": "产品",
                            "消费": 999.0,
                            "激活数": 99,
                            "首次付费次数": 9,
                        },
                        {
                            "时间": "2026-05-15~2026-05-21",
                            "标题": "空账号内容",
                            "笔记ID": "note-included-blank",
                            "发布作者": "",
                            "内容分类": "产品",
                            "消费": 888.0,
                            "激活数": 88,
                            "首次付费次数": 8,
                        },
                    ]
                ).to_excel(writer, sheet_name="kos账号笔记投放数据", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-05-15",
                "2026-05-21",
                account_filters_path=config_path,
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual(
                set(result.canonical["content_id"]),
                {"note-included-1", "note-included-2", "note-included-blank"},
            )
            self.assertEqual(set(result.canonical["account"].fillna("")), {"", "股民社区", "研习社"})
            self.assertAlmostEqual(result.canonical["spend"].sum(), 918.0)
            channel_summary = result.channel_summary.set_index("channel")
            self.assertAlmostEqual(channel_summary.loc["小红书商业化", "spend"], 918.0)
            self.assertEqual(len(result.account_filter_details), 1)
            self.assertEqual(set(result.account_filter_details["filter_reason"]), {"不在小红书账号白名单"})
            filter_note = result.preprocessing_report.set_index("metric").loc["小红书账号过滤排除行数", "note"]
            self.assertIn("空账号行默认记录", filter_note)
            self.assertNotIn("空账号小红书行不进入汇总", filter_note)

    def test_xiaohongshu_account_filter_applies_to_cleaned_replay_frame(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "account_filters.yml"
            config_path.write_text(
                """
xiaohongshu:
  include_accounts:
    - 股民社区
    - 研习社
  aliases:
    同顺股民社区: 股民社区
    同花顺研习社: 研习社
  exclude_blank: true
""".strip(),
                encoding="utf-8",
            )
            canonical = pd.DataFrame(
                [
                    {
                        "channel": "小红书商业化",
                        "content_id": "note-included-1",
                        "title": "股民社区内容",
                        "account": "同顺股民社区",
                        "manual_category": "投教",
                        "spend": 10.0,
                        "activations": 1,
                        "first_pay_count": 1,
                    },
                    {
                        "channel": "小红书商业化",
                        "content_id": "note-included-2",
                        "title": "研习社内容",
                        "account": "同花顺研习社",
                        "manual_category": "投教",
                        "spend": 20.0,
                        "activations": 2,
                        "first_pay_count": 1,
                    },
                    {
                        "channel": "小红书商业化",
                        "content_id": "note-excluded-etf",
                        "title": "ETF内容",
                        "account": "同花顺ETF",
                        "manual_category": "产品",
                        "spend": 999.0,
                        "activations": 99,
                        "first_pay_count": 9,
                    },
                    {
                        "channel": "小红书商业化",
                        "content_id": "note-included-blank",
                        "title": "空账号内容",
                        "account": "",
                        "manual_category": "产品",
                        "spend": 888.0,
                        "activations": 88,
                        "first_pay_count": 8,
                    },
                    {
                        "channel": "抖音商业化",
                        "content_id": "dy-kept",
                        "title": "抖音内容",
                        "account": "",
                        "manual_category": "投教",
                        "spend": 50.0,
                        "activations": 5,
                        "first_pay_count": 1,
                    },
                ]
            )

            result = analyze_canonical_frame(
                canonical,
                "2026-05-15",
                "2026-05-21",
                account_filters_path=config_path,
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual(
                set(result.canonical["content_id"]),
                {"note-included-1", "note-included-2", "note-included-blank", "dy-kept"},
            )
            self.assertEqual(
                set(result.canonical[result.canonical["channel"].eq("小红书商业化")]["account"].fillna("")),
                {"", "股民社区", "研习社"},
            )
            self.assertEqual(len(result.account_filter_details), 1)
            self.assertAlmostEqual(result.channel_summary.set_index("channel").loc["小红书商业化", "spend"], 918.0)

    def test_xiaohongshu_market_reads_all_metric_sheets(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "小红书市场部.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "笔记/素材ID": "note-new",
                            "标题": "新户内容",
                            "账号": "问财",
                            "消费": 10.0,
                            "展现量": 100,
                            "点击量": 10,
                            "激活数": 2,
                            "首次付费次数": 1,
                        }
                    ]
                ).to_excel(writer, sheet_name="新户", index=False)
                pd.DataFrame(
                    [
                        {
                            "笔记/素材ID": "note-old-1",
                            "标题": "老户内容1",
                            "账号": "投资号",
                            "消费": 20.0,
                            "展现量": 200,
                            "点击量": 20,
                            "激活数": 4,
                            "首次付费次数": 2,
                        },
                        {
                            "笔记/素材ID": "note-old-2",
                            "标题": "老户内容2",
                            "账号": "财经号",
                            "消费": 30.0,
                            "展现量": 300,
                            "点击量": 30,
                            "激活数": 6,
                            "首次付费次数": 3,
                        },
                    ]
                ).to_excel(writer, sheet_name="老户", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-05-01",
                "2026-05-31",
                category_matcher=lambda items, category_library, env_path: {},
            )

            xhs = result.canonical[result.canonical["channel"].eq("小红书市场部")]
            self.assertEqual(len(xhs), 3)
            self.assertEqual(set(xhs["source_sheet"]), {"新户", "老户"})
            self.assertAlmostEqual(xhs["spend"].sum(), 60.0)
            self.assertAlmostEqual(xhs["activations"].sum(), 12.0)
            self.assertAlmostEqual(xhs["first_pay_count"].sum(), 6.0)

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
                        "账号": "同花顺投资",
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
                        "账号": "同花顺投资",
                        "账号": "同花顺投资",
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

    def test_ai_category_matcher_can_return_confidence(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "已有内容类型样本",
                        "视频id": "dy-known-confidence",
                        "素材ID": "mat-known-confidence",
                        "消耗": 10.0,
                        "内容类型": "热点行情",
                    },
                    {
                        "视频标题": "完全陌生内容",
                        "视频id": "dy-ai-confidence",
                        "素材ID": "mat-ai-confidence",
                        "消耗": 9.0,
                        "内容类型": "",
                    },
                ]
            ).to_csv(raw_dir / "抖音商业化.csv", index=False, encoding="utf-8-sig")

            def matcher(items, category_library, env_path):
                self.assertIn("热点行情", category_library)
                return {int(index): {"category": "热点行情", "confidence": 0.84} for index in items.index}

            result = analyze_input_dir(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                category_matcher=matcher,
                env_path=Path(tmp) / ".env",
            )

            inferred = result.canonical[result.canonical["content_id"].eq("dy-ai-confidence")].iloc[0]
            self.assertEqual(inferred["content_category"], "热点行情")
            self.assertEqual(inferred["ai_category"], "热点行情")
            self.assertEqual(inferred["category_status"], "DeepSeek匹配")
            self.assertAlmostEqual(float(inferred["category_confidence"]), 0.84)

    def test_user_defined_hashtag_completes_missing_xhs_and_douyin_categories_only(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-tag-title",
                    "title": "盘中热点 #同顺图解",
                    "spend": 10.0,
                    "manual_category": "",
                },
                {
                    "platform": "小红书",
                    "channel": "小红书商业化",
                    "content_id": "xhs-tag-column",
                    "title": "财商小课堂",
                    "spend": 9.0,
                    "manual_category": "",
                    "raw__小红书商业化__tag词": "#同顺财商 #投教",
                },
                {
                    "platform": "B站",
                    "channel": "B站",
                    "content_id": "bv-tag-title",
                    "title": "深度财经 #同顺深度财经",
                    "spend": 8.0,
                    "manual_category": "",
                },
            ]
        )

        result = analyze_canonical_frame(
            frame,
            "2026-04-01",
            "2026-04-27",
            category_matcher=lambda items, category_library, env_path: {},
        )

        canonical = result.canonical.set_index("content_id")
        self.assertEqual(canonical.loc["dy-tag-title", "content_category"], "图文")
        self.assertEqual(canonical.loc["dy-tag-title", "category_status"], "TAG匹配")
        self.assertAlmostEqual(float(canonical.loc["dy-tag-title", "category_confidence"]), 0.95)
        self.assertEqual(canonical.loc["xhs-tag-column", "content_category"], "财商动画")
        self.assertEqual(canonical.loc["xhs-tag-column", "category_status"], "TAG匹配")
        self.assertEqual(canonical.loc["bv-tag-title", "content_category"], "")
        self.assertNotEqual(canonical.loc["bv-tag-title", "category_status"], "TAG匹配")

    def test_user_defined_hashtag_does_not_override_existing_manual_category(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-manual-tag-conflict",
                    "title": "图解走势 #同顺图解",
                    "spend": 10.0,
                    "manual_category": "资讯",
                }
            ]
        )

        result = analyze_canonical_frame(
            frame,
            "2026-04-01",
            "2026-04-27",
            category_matcher=lambda items, category_library, env_path: {},
        )

        item = result.canonical.iloc[0]
        self.assertEqual(item["manual_category"], "资讯")
        self.assertEqual(item["content_category"], "资讯")
        self.assertEqual(item["ai_category"], "")
        self.assertEqual(item["category_status"], "人工标记")

    def test_high_spend_douyin_unmatched_uses_stronger_local_category_rules(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-trading-mindset",
                    "title": "股市是仅次于高考，最公平的竞争 #同花顺 #交易心法",
                    "spend": 262031.87,
                    "manual_category": "",
                },
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-unknown-low",
                    "title": "全国在校大学生博主招募，有意私信",
                    "spend": 20.0,
                    "manual_category": "",
                },
            ]
        )

        result = analyze_canonical_frame(
            frame,
            "2026-05-01",
            "2026-05-31",
            category_matcher=lambda items, category_library, env_path: {},
        )

        canonical = result.canonical.set_index("content_id")
        self.assertEqual(canonical.loc["dy-trading-mindset", "content_category"], "交易心法")
        self.assertEqual(canonical.loc["dy-trading-mindset", "category_status"], "高消耗规则匹配")
        self.assertEqual(canonical.loc["dy-unknown-low", "content_category"], "")
        self.assertEqual(canonical.loc["dy-unknown-low", "category_status"], "未匹配")

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
                        "账号": "同花顺投资",
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
                        "账号": "同花顺投资",
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
                        "账号": "同花顺投资",
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
                        "账号": "同花顺投资",
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
            self.assertEqual(bilibili["content_form"], "视频")
            self.assertEqual(bilibili["content_category"], "大佬采访")
            self.assertEqual(bilibili["category_l2"], "大佬采访")
            self.assertEqual(bilibili["category_l3"], "实盘大赛冠军孙辉--370万到2000万的传奇交易之路")
            self.assertEqual(bilibili["category_status"], "标题关键词匹配")
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
            douyin_market = canonical[canonical["content_id"].eq("dy-m-1")].iloc[0]

            self.assertEqual(bilibili["account_id"], "1622777305")
            self.assertEqual(bilibili["account"], "同花顺投资")
            self.assertEqual(bilibili["author"], "同花顺投资")
            self.assertEqual(xhs["account"], "同花顺理财")
            self.assertEqual(xhs["author"], "同花顺理财")
            self.assertEqual(douyin_market["account"], "同花顺投资")

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

            self.assertEqual(len(result.canonical), 1)
            row = result.canonical.iloc[0]
            self.assertEqual(row["account_id"], "999999")
            self.assertEqual(row["account"], "")
            self.assertEqual(row["account_filter_status"], "")
            self.assertIn("账号映射缺失", row["review_reasons"])
            self.assertTrue(result.account_filter_details.empty)

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
            self.assertAlmostEqual(commercial["impressions"], 2005.0)
            self.assertTrue(commercial["needs_manual_review"])
            self.assertIn("数值冲突", commercial["review_reasons"])
            self.assertIn("spend", commercial["conflict_details"])
            self.assertEqual(market["merged_row_count"], 1)

    def test_channel_dedupe_prefers_id_then_url_then_title_and_recomputes_rates(self):
        frame = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-stable-1",
                    "content_url": "https://www.douyin.com/video/dy-stable-1",
                    "title": "同 ID 原始标题",
                    "account": "投资号",
                    "manual_category": "热点行情",
                    "spend": 100,
                    "impressions": 1000,
                    "clicks": 100,
                    "activations": 10,
                    "first_pay_count": 2,
                    "source_file": "a.csv",
                },
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "dy-stable-1",
                    "content_url": "https://www.douyin.com/video/dy-stable-1?share_token=abc",
                    "title": "同 ID 补充标题 #投教",
                    "account": "财经号",
                    "manual_category": "热点行情",
                    "spend": 100,
                    "impressions": 1000,
                    "clicks": 100,
                    "activations": 10,
                    "first_pay_count": 2,
                    "source_file": "b.csv",
                },
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "content_url": "https://www.douyin.com/video/7291234567890123456?foo=1",
                    "title": "链接行标题 A",
                    "account": "投资号",
                    "manual_category": "热点行情",
                    "spend": 30,
                    "impressions": 300,
                    "clicks": 30,
                    "activations": 3,
                    "first_pay_count": 1,
                    "source_file": "c.csv",
                },
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "content_url": "https://www.douyin.com/video/7291234567890123456/",
                    "title": "链接行标题 B #补充",
                    "account": "财经号",
                    "manual_category": "热点行情",
                    "spend": 40,
                    "impressions": 400,
                    "clicks": 40,
                    "activations": 4,
                    "first_pay_count": 1,
                    "source_file": "d.csv",
                },
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "content_url": "",
                    "title": " 标题  兜底 合并 ",
                    "account": "投资号",
                    "manual_category": "热点行情",
                    "spend": 10,
                    "impressions": 100,
                    "clicks": 10,
                    "activations": 1,
                    "first_pay_count": 0,
                    "source_file": "e.csv",
                },
                {
                    "platform": "抖音",
                    "platform_group": "抖音",
                    "channel": "抖音商业化",
                    "content_id": "",
                    "content_url": "",
                    "title": "标题兜底合并",
                    "account": "财经号",
                    "manual_category": "热点行情",
                    "spend": 15,
                    "impressions": 150,
                    "clicks": 15,
                    "activations": 2,
                    "first_pay_count": 1,
                    "source_file": "f.csv",
                },
            ]
        )

        with TemporaryDirectory() as tmp:
            result = analyze_canonical_frame(
                frame,
                "2026-04-01",
                "2026-04-27",
                category_matcher=lambda items, category_library, env_path: {},
                reference_tables_path=Path(tmp) / "reference_tables.xlsx",
            )

            self.assertEqual(len(result.canonical), 3)
            by_key = result.canonical.set_index("dedupe_key")
            id_row = by_key.loc["抖音商业化::id::dy-stable-1"]
            self.assertEqual(id_row["merged_row_count"], 2)
            self.assertEqual(id_row["title"], "同 ID 补充标题 #投教")
            self.assertAlmostEqual(id_row["spend"], 200.0)
            self.assertAlmostEqual(id_row["impressions"], 2000.0)
            self.assertAlmostEqual(id_row["activation_cost"], 10.0)
            self.assertAlmostEqual(id_row["first_pay_rate"], 0.2)
            self.assertIn("同ID标题不一致", id_row["review_reasons"])
            self.assertIn("title", id_row["conflict_details"])

            url_row = by_key.loc["抖音商业化::url::https://www.douyin.com/video/7291234567890123456"]
            self.assertEqual(url_row["merged_row_count"], 2)
            self.assertAlmostEqual(url_row["spend"], 70.0)

            title_row = by_key.loc["抖音商业化::title::标题兜底合并"]
            self.assertEqual(title_row["merged_row_count"], 2)
            self.assertAlmostEqual(title_row["spend"], 25.0)

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

    def test_market_and_new_platform_sources_are_mapped_to_distinct_channels(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "小红书（市场部）.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "笔记/素材ID": "xhs-market-1",
                            "笔记/素材链接": "https://xhs.example/1",
                            "账号": "问财",
                            "消费": 10,
                            "展现量": 100,
                            "点击量": 20,
                            "激活数(转化时间)": 3,
                            "首次付费次数(转化时间)": 1,
                        }
                    ]
                ).to_excel(writer, sheet_name="计划-数据", index=False)
            with pd.ExcelWriter(raw_dir / "微信市场部.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "创意名称": "微信创意一",
                            "花费": 20,
                            "曝光次数": 200,
                            "点击次数": 30,
                            "APP激活次数": 4,
                            "注册次数": 2,
                        }
                    ]
                ).to_excel(writer, sheet_name="Sheet1", index=False)
            with pd.ExcelWriter(raw_dir / "腾讯（市场部）.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "链接": "https://tencent.example/creative",
                            "花费": 30,
                            "曝光次数": 300,
                            "点击次数": 40,
                            "APP激活次数": 5,
                            "注册次数（点击归因）": 2,
                        }
                    ]
                ).to_excel(writer, sheet_name="Sheet1", index=False)
            with pd.ExcelWriter(raw_dir / "视频号投放.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "创意名称": "视频号创意一",
                            "花费": 25,
                            "曝光次数": 250,
                            "点击次数": 35,
                            "APP激活次数": 7,
                            "注册次数": 3,
                        }
                    ]
                ).to_excel(writer, sheet_name="Sheet1", index=False)
            with pd.ExcelWriter(raw_dir / "抖音原生-达人数据情况（商业化）.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "视频链接": "https://douyin.example/video/1",
                            "账号": "同花顺投资",
                            "消耗": 40,
                            "展示数": 400,
                            "激活数": 6,
                            "付费数": 3,
                        }
                    ]
                ).to_excel(writer, sheet_name="Sheet4", index=False)

            result = analyze_input_dir(
                raw_dir,
                "2026-05-08",
                "2026-05-14",
                category_matcher=lambda items, category_library, env_path: {},
            )

            channels = set(result.canonical["channel"])
            self.assertIn("小红书市场部", channels)
            self.assertIn("微信市场部", channels)
            self.assertIn("达人数据", channels)
            self.assertNotIn("微信/腾讯/视频号市场部", channels)
            self.assertNotIn("腾讯市场部", channels)
            self.assertNotIn("抖音达人内容", channels)
            social_rows = result.canonical[result.canonical["channel"].eq("微信市场部")]
            self.assertEqual(len(social_rows), 3)
            self.assertEqual(set(social_rows["platform"]), {"微信", "腾讯", "视频号"})
            self.assertEqual(set(social_rows["platform_group"]), {"微信"})

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
                            "Up主mid": "1622777305",
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
            self.assertEqual(len(result.review_queue), 1)

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
                    "复盘统一字段",
                    "分渠道总数据",
                    "分渠道栏目题材排名",
                    "内容类型分级表",
                    "周期报告",
                    "字段映射表",
                    "账号映射表",
                    "账号内容类型对照表",
                    "账号过滤规则",
                    "账号过滤明细",
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

    def test_archived_workflow_ui_only_runs_ai_review_without_report_api_calls(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)
            pd.DataFrame(
                [
                    {
                        "视频标题": "已知类型样本",
                        "视频id": "dy-known-ui-only",
                        "素材ID": "mat-known-ui-only",
                        "消耗": 10.0,
                        "内容类型": "资讯",
                    },
                    {
                        "视频标题": "完全陌生主题",
                        "视频id": "dy-pending-ui-only",
                        "素材ID": "mat-pending-ui-only",
                        "消耗": 9.0,
                        "内容类型": "",
                    },
                ]
            ).to_csv(raw_dir / "抖音市场部.csv", index=False, encoding="utf-8-sig")
            env_path = tmp_path / ".env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=test-key\nDEEPSEEK_BASE_URL=https://api.deepseek.com\n",
                encoding="utf-8",
            )
            reviewed_batches = []

            def category_matcher(items, category_library, env_path_arg):
                reviewed_batches.append((len(items), tuple(category_library)))
                return {}

            with patch(
                "ops_data_workflow.topic_analysis.group_topic_labels",
                side_effect=AssertionError("DeepSeek topic call"),
            ), patch(
                "ops_data_workflow.workflow.fetch_external_context",
                side_effect=AssertionError("external context call"),
            ), patch(
                "ops_data_workflow.workflow.generate_ai_summary",
                side_effect=AssertionError("DeepSeek summary call"),
            ):
                result = run_archived_workflow(
                    raw_dir,
                    "2026-04-01",
                    "2026-04-27",
                    output_root=tmp_path / "outputs",
                    processed_root=tmp_path / "processed",
                    db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                    env_path=env_path,
                    category_matcher=category_matcher,
                    output_mode="ui_only",
                    enable_deepseek=True,
                    enable_external_context=False,
                )

            self.assertTrue(reviewed_batches)
            self.assertTrue((result.archive_dir / "cleaned.xlsx").exists())
            self.assertEqual(result.cleaned_channels_workbook, result.archive_dir / "cleaned_channels.xlsx")
            self.assertTrue(result.cleaned_channels_workbook.exists())
            self.assertIsNone(result.report_html)
            self.assertIsNone(result.analysis_xlsx)
            self.assertIsNone(result.canonical_csv)
            self.assertIsNone(result.total_summary_xlsx)
            self.assertEqual(result.channel_clean_workbooks, [])
            self.assertFalse((tmp_path / "outputs" / result.batch_id).exists())
            self.assertEqual(result.ai_summary, "")
            with closing(sqlite3.connect(tmp_path / ".runtime" / "workflow.sqlite3")) as conn:
                canonical_count = conn.execute(
                    "select count(*) from canonical_items where batch_id = ?",
                    (result.batch_id,),
                ).fetchone()[0]
                topic_providers = [
                    row[0]
                    for row in conn.execute(
                        "select distinct provider from topic_label_items where batch_id = ?",
                        (result.batch_id,),
                    ).fetchall()
                ]
                ai_count = conn.execute(
                    "select count(*) from ai_reports where batch_id = ?",
                    (result.batch_id,),
                ).fetchone()[0]
            self.assertGreater(canonical_count, 0)
            self.assertNotIn("deepseek", topic_providers)
            self.assertEqual(ai_count, 0)

    def test_workflow_writes_report_when_category_spend_is_blank(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            output_dir = Path(tmp) / "out"
            raw_dir.mkdir()
            with pd.ExcelWriter(raw_dir / "抖音商业化.xlsx", engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "视频标题": "有曝光但无消耗的内容",
                            "视频id": "dy-organic",
                            "视频链接": "https://douyin.example/organic",
                            "账号": "同花顺投资",
                            "内容类型": "股友说",
                            "消耗": "",
                            "展示数": 5317,
                            "激活数": "",
                            "付费次数": "",
                        }
                    ]
                ).to_excel(writer, sheet_name="Sheet2", index=False)

            result = run_workflow(raw_dir, "2026-04-10", "2026-04-16", output_dir)

            self.assertTrue(result.report_html.exists())
            html = result.report_html.read_text(encoding="utf-8")
            self.assertIn("暂无可绘制消耗气泡", html)
            self.assertEqual(list(result.canonical["content_id"]), ["dy-organic"])

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
            self.assertIn("内容类型", csv_columns)
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
            self.assertIn("内容类型", detail_headers)
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

            self.assertEqual(bilibili["内容类型"], "")
            self.assertEqual(bilibili["AI生成内容类别"], "大佬采访")
            self.assertEqual(bilibili["最终内容类别"], "大佬采访")
            self.assertEqual(bilibili["栏目"], "大佬采访")
            self.assertEqual(bilibili["题材"], "实盘大赛冠军孙辉--370万到2000万的传奇交易之路")
            self.assertEqual(bilibili["内容类别来源"], "标题关键词匹配")
            self.assertNotIn("一级内容分类", exported.columns)
            self.assertNotIn("一级类型", exported.columns)
            self.assertEqual(douyin_market["点击量"], "")
            self.assertEqual(xhs_missing_secondary["内容类型"], "")
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
                    "复盘统一字段",
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
            self.assertIn("付费率", headers)
            platform_headers = [cell.value for cell in next(workbook["分渠道总数据"].iter_rows(max_row=1))]
            platform_category_headers = [
                cell.value for cell in next(workbook["分渠道栏目题材排名"].iter_rows(max_row=1))
            ]
            self.assertIn("渠道", platform_headers)
            self.assertIn("消耗", platform_headers)
            self.assertIn("渠道", platform_category_headers)
            self.assertIn("最终内容类别", platform_category_headers)
            workbook.close()

    def test_archived_workflow_persists_safe_public_metadata_enrichment_from_cache(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            metadata_cache = tmp_path / "metadata-cache"
            cache_item = metadata_cache / "bilibili" / "BV1workflow1.json"
            cache_item.parent.mkdir(parents=True)
            cache_item.write_text(
                json.dumps(
                    {
                        "id": "BV1workflow1",
                        "link": "https://www.bilibili.com/video/BV1workflow1/",
                        "title": "缓存标题",
                        "tags": "财经",
                        "published_at": "2026-05-09",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {
                        "视频BVID": "BV1workflow1",
                        "花费": 10.0,
                        "展示量": 100,
                        "点击量": 10,
                        "应用激活数": 2,
                        "应用内首次付费次数": 1,
                    }
                ]
            ).to_csv(raw_dir / "B站数据.csv", index=False, encoding="utf-8-sig")

            result = run_archived_workflow(
                raw_dir,
                "2026-05-08",
                "2026-05-14",
                output_root=tmp_path / "outputs",
                processed_root=tmp_path / "processed",
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                output_mode="ui_only",
                enable_deepseek=False,
                enable_external_context=False,
                metadata_enrichment_mode="safe_public",
                metadata_cache_dir=metadata_cache,
            )

            with closing(sqlite3.connect(tmp_path / ".runtime" / "workflow.sqlite3")) as conn:
                row = conn.execute(
                    """
                    select content_url, title, source_time, metadata_source, metadata_tags
                    from canonical_items
                    where batch_id = ?
                    """,
                    (result.batch_id,),
                ).fetchone()

            self.assertEqual(row[0], "https://www.bilibili.com/video/BV1workflow1/")
            self.assertEqual(row[1], "缓存标题")
            self.assertEqual(row[2], "2026-05-09")
            self.assertEqual(row[3], "metadata_cache")
            self.assertEqual(row[4], "财经")

    def test_archived_workflow_can_force_reclean_existing_cleaned_workbook_for_metadata_enrichment(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            stale_cleaned = raw_dir / "cleaned.xlsx"
            with pd.ExcelWriter(stale_cleaned, engine="openpyxl") as writer:
                pd.DataFrame(
                    [
                        {
                            "platform": "B站",
                            "platform_group": "B站",
                            "channel": "B站",
                            "content_id": "BV1stale001",
                            "title": "旧清洗标题",
                            "content_url": "",
                            "spend": 1.0,
                            "impressions": 1.0,
                            "clicks": 1.0,
                            "activations": 1.0,
                            "first_pay_count": 1.0,
                        }
                    ]
                ).to_excel(writer, sheet_name="清洗后明细", index=False)
            pd.DataFrame(
                [
                    {
                        "视频BVID": "BV1fresh001",
                        "花费": 10.0,
                        "展示量": 100,
                        "点击量": 10,
                        "应用激活数": 2,
                        "应用内首次付费次数": 1,
                    }
                ]
            ).to_csv(raw_dir / "B站数据.csv", index=False, encoding="utf-8-sig")

            metadata_cache = tmp_path / "metadata-cache"
            cache_item = metadata_cache / "bilibili" / "BV1fresh001.json"
            cache_item.parent.mkdir(parents=True)
            cache_item.write_text(
                json.dumps(
                    {
                        "id": "BV1fresh001",
                        "link": "https://www.bilibili.com/video/BV1fresh001/",
                        "title": "原始表补全标题",
                        "published_at": "2026-05-09",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_archived_workflow(
                raw_dir,
                "2026-05-08",
                "2026-05-14",
                output_root=tmp_path / "outputs",
                processed_root=tmp_path / "processed",
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                output_mode="ui_only",
                enable_deepseek=False,
                enable_external_context=False,
                metadata_enrichment_mode="safe_public",
                metadata_cache_dir=metadata_cache,
                force_reclean=True,
            )

            self.assertIn("BV1fresh001", set(result.canonical["content_id"]))
            self.assertNotIn("BV1stale001", set(result.canonical["content_id"]))
            self.assertEqual(result.top_content_items.iloc[0]["title"], "原始表补全标题")

    def test_refresh_historical_source_periods_rebuilds_from_raw_sources_with_safe_public_metadata(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_root = tmp_path / "data"
            source_dir = data_root / "weeks" / "20260508-20260514"
            source_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {
                        "视频BVID": "BV1history1",
                        "花费": 20.0,
                        "展示量": 200,
                        "点击量": 20,
                        "应用激活数": 4,
                        "应用内首次付费次数": 2,
                    }
                ]
            ).to_csv(source_dir / "B站数据.csv", index=False, encoding="utf-8-sig")
            processed_dir = tmp_path / "processed" / "20260508-20260514" / "upload:week:20260508-20260514"
            processed_dir.mkdir(parents=True)
            with pd.ExcelWriter(processed_dir / "cleaned.xlsx", engine="openpyxl") as writer:
                pd.DataFrame([{"channel": "B站", "content_id": "BV1oldhist"}]).to_excel(
                    writer,
                    sheet_name="清洗后明细",
                    index=False,
                )
            metadata_cache = tmp_path / "metadata-cache"
            cache_item = metadata_cache / "bilibili" / "BV1history1.json"
            cache_item.parent.mkdir(parents=True)
            cache_item.write_text(
                json.dumps(
                    {
                        "id": "BV1history1",
                        "link": "https://www.bilibili.com/video/BV1history1/",
                        "title": "历史重算标题",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            results = refresh_historical_source_periods(
                data_root=data_root,
                processed_root=tmp_path / "processed",
                output_root=tmp_path / "outputs",
                db_path=tmp_path / ".runtime" / "workflow.sqlite3",
                metadata_cache_dir=metadata_cache,
            )

            self.assertEqual([result.batch_id for result in results], ["upload:week:20260508-20260514"])
            with closing(sqlite3.connect(tmp_path / ".runtime" / "workflow.sqlite3")) as conn:
                canonical_row = conn.execute(
                    """
                    select content_id, title, metadata_source
                    from canonical_items
                    where batch_id = 'upload:week:20260508-20260514'
                    """
                ).fetchone()
                top_row = conn.execute(
                    """
                    select content_id, title, content_url
                    from top_content_items
                    where batch_id = 'upload:week:20260508-20260514'
                    """
                ).fetchone()

            self.assertEqual(canonical_row, ("BV1history1", "历史重算标题", "metadata_cache"))
            self.assertEqual(top_row, ("BV1history1", "历史重算标题", "https://www.bilibili.com/video/BV1history1/"))


if __name__ == "__main__":
    unittest.main()
