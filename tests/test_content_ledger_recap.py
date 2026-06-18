from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.content_ledger import load_content_ledger
from ops_data_workflow.pipeline import analyze_canonical_frame


def _write_xlsx(path: Path, sheets: dict[str, pd.DataFrame], *, startrow: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False, startrow=startrow)


class ContentLedgerTests(unittest.TestCase):
    def test_harvester_ledger_rows_normalize_current_and_legacy_sheet_shapes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_path = root / "原生内容投稿.xlsx"
            _write_xlsx(
                ledger_path,
                {
                    "抖音渠道": pd.DataFrame(
                        [
                            {
                                "编号": 1,
                                "投稿时间": "05 22",
                                "内容链接": "1.28 tRk:/ 人和人的缘分就像炒股 # 同花顺股友说 # 投资 https://v.douyin.com/abc/ 复制此链接，打开Dou音搜索，直接观看视频！",
                                "标题": "",
                                "tag词": "",
                                "筛选状态": "通过",
                                "账号": "投资号",
                                "内容类型": "",
                                "内容类型标签审核": "",
                            }
                        ]
                    ),
                    "小红书渠道": pd.DataFrame(
                        [
                            {
                                "编号": 1,
                                "投稿时间": "05 22",
                                "内容链接": "https://www.xiaohongshu.com/discovery/item/6a115b0e00000000360031a6?source=webshare",
                                "笔记ID": "",
                                "账号": "投资号",
                                "内容类型": "资讯",
                            }
                        ]
                    ),
                    "B站渠道": pd.DataFrame(
                        [
                            {
                                "编号": 1,
                                "投稿时间": "05 22",
                                "内容链接": "https://www.bilibili.com/video/BV1crLm6yE5V/",
                                "短链id": "",
                                "账号": "投资号",
                            }
                        ]
                    ),
                },
            )

            ledger = load_content_ledger(root, default_year=2026)

            self.assertEqual(set(ledger["platform"]), {"抖音", "小红书", "B站"})
            douyin = ledger[ledger["platform"].eq("抖音")].iloc[0]
            self.assertEqual(douyin["account"], "投资号")
            self.assertEqual(douyin["title"], "人和人的缘分就像炒股")
            self.assertEqual(douyin["tags"], "#同花顺股友说 #投资")
            self.assertEqual(douyin["content_type"], "股友说")
            xhs = ledger[ledger["platform"].eq("小红书")].iloc[0]
            self.assertEqual(xhs["content_id"], "6a115b0e00000000360031a6")
            bili = ledger[ledger["platform"].eq("B站")].iloc[0]
            self.assertEqual(bili["content_id"], "BV1crLm6yE5V")

    def test_configured_feishu_export_paths_load_as_ledger_sources(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_path = root / "feishu_exports" / "原生内容投稿.xlsx"
            _write_xlsx(
                export_path,
                {
                    "小红书渠道": pd.DataFrame(
                        [
                            {
                                "编号": 1,
                                "内容链接": "https://www.xiaohongshu.com/explore/6a115b0e00000000360031a6",
                                "账号": "投资号",
                                "内容类型": "资讯",
                            }
                        ]
                    )
                },
            )
            config_path = root / "config" / "feishu_sources.yml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "content_ledgers:\n"
                "  - type: feishu_sheet_export\n"
                "    path: ../feishu_exports/原生内容投稿.xlsx\n",
                encoding="utf-8",
            )

            ledger = load_content_ledger(root / "empty_input", default_year=2026, config_path=config_path)

            self.assertEqual(len(ledger), 1)
            row = ledger.iloc[0]
            self.assertEqual(row["platform"], "小红书")
            self.assertEqual(row["content_id"], "6a115b0e00000000360031a6")
            self.assertEqual(row["source_file"], "../feishu_exports/原生内容投稿.xlsx")

    def test_configured_xhs_ledger_directory_loads_all_tabular_sources(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_dir = root / "data" / "reference" / "xhs"
            _write_xlsx(
                ledger_dir / "xhs_content_ledger.xlsx",
                {
                    "小红书专项": pd.DataFrame(
                        [
                            {
                                "内容链接": "https://www.xiaohongshu.com/explore/65f00000abcdef?xsec_token=token-1",
                                "笔记ID": "65f00000abcdef",
                                "标题": "专项台账标题",
                                "账号": "投资号",
                                "tag词": "#财经",
                            }
                        ]
                    )
                },
            )
            config_path = root / "config" / "feishu_sources.yml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "content_ledgers:\n"
                "  - type: local_excel\n"
                "    path: ../data/reference/xhs\n",
                encoding="utf-8",
            )

            ledger = load_content_ledger(root / "empty_input", default_year=2026, config_path=config_path)

            self.assertEqual(len(ledger), 1)
            row = ledger.iloc[0]
            self.assertEqual(row["platform"], "小红书")
            self.assertEqual(row["content_id"], "65f00000abcdef")
            self.assertEqual(row["title"], "专项台账标题")

    def test_content_ledger_no_longer_exports_old_autofill_api(self):
        import ops_data_workflow.content_ledger as content_ledger

        self.assertFalse(hasattr(content_ledger, "apply_content_ledger"))

    def test_ledger_candidates_feed_asset_matching_without_backfilling_unmatched_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_xlsx(
                root / "原生内容投稿.xlsx",
                {
                    "小红书渠道": pd.DataFrame(
                        [
                            {
                                "编号": 1,
                                "内容链接": "https://www.xiaohongshu.com/explore/65f00000abcdef",
                                "标题": "小红书自有标题",
                                "账号": "投资号",
                                "内容类型": "资讯",
                            }
                        ]
                    )
                },
            )
            ledger = load_content_ledger(root)
            canonical = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "platform_group": "小红书",
                        "channel": "小红书商业化",
                        "content_id": "65f00000abcdef",
                        "title": "投放标题",
                        "spend": 100,
                        "impressions": 1000,
                    },
                    {
                        "platform": "小红书",
                        "platform_group": "小红书",
                        "channel": "小红书商业化",
                        "content_id": "66f00000abcdef",
                        "title": "未入台账标题",
                        "spend": 50,
                        "impressions": 500,
                    },
                ]
            )

            analysis = analyze_canonical_frame(
                canonical,
                "2026-05-19",
                "2026-05-25",
                category_matcher=lambda items, category_library, env_path: {},
                content_ledger=ledger,
            )

            by_id = analysis.canonical.set_index("work_id")
            self.assertEqual(by_id.loc["65f00000abcdef", "analysis_status"], "可分析")
            self.assertEqual(by_id.loc["65f00000abcdef", "matched_ledger_title"], "小红书自有标题")
            self.assertEqual(by_id.loc["66f00000abcdef", "analysis_status"], "不可分析")
            self.assertEqual(by_id.loc["66f00000abcdef", "content_category"], "")


if __name__ == "__main__":
    unittest.main()
