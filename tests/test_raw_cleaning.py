from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook
import pandas as pd

from ops_data_workflow.periods import PERIOD_LEVEL_MONTH
from ops_data_workflow.raw_cleaning import (
    clean_source_directory,
    load_cleaned_canonical,
    reset_runtime_data,
)
from ops_data_workflow.storage import init_db
from ops_data_workflow.workflow import run_archived_workflow


def _write_xlsx(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)


def _bilibili_row(content_id: str, title: str, spend: float = 10.0) -> dict:
    return {
        "视频AVID": content_id.replace("BV", "av"),
        "视频BVID": content_id,
        "视频标题": title,
        "Up主mid": "123456",
        "日期": "2026-04-10",
        "花费": spend,
        "展示量": 100,
        "点击量": 10,
        "应用激活数": 2,
        "应用内首次付费次数": 1,
    }


def _xiaohongshu_row(content_id: str, title: str, spend: float = 10.0) -> dict:
    return {
        "时间": "2026-03-02",
        "标题": title,
        "笔记ID": content_id,
        "发布作者": "同花顺投资",
        "类型": "图文",
        "内容分类": "热点行情",
        "笔记链接": f"https://xhs.example/{content_id}",
        "消费": spend,
        "展现量": 100,
        "点击量": 10,
        "激活数": 2,
        "首次付费次数": 1,
    }


class RawCleaningTests(unittest.TestCase):
    def test_clean_source_directory_prefers_matching_sheet_and_records_ignored_wide_sheet(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_xlsx(
                source / "3月总数据" / "0301-0323 小红书数据.xlsx",
                {
                    "1-3月": pd.DataFrame([_xiaohongshu_row("wide-1", "宽周期数据")]),
                    "3.1-3.23": pd.DataFrame([_xiaohongshu_row("march-1", "三月数据")]),
                },
            )

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            self.assertEqual(len(buckets), 1)
            bucket = buckets[0]
            self.assertEqual(bucket.review_period.period_level, PERIOD_LEVEL_MONTH)
            self.assertEqual(bucket.review_period.period_key, "2026-03")
            self.assertEqual(bucket.review_period.data_end, "2026-03-23")
            self.assertTrue(bucket.cleaned_workbook.exists())
            cleaned = load_cleaned_canonical(bucket.cleaned_workbook)
            self.assertEqual(list(cleaned["content_id"]), ["march-1"])
            self.assertEqual(list(cleaned["source_sheet"]), ["3.1-3.23"])

            ignored = pd.read_excel(bucket.cleaned_workbook, sheet_name="忽略sheet")
            self.assertEqual(list(ignored["sheet_name"]), ["1-3月"])
            self.assertIn("宽周期", ignored.iloc[0]["reason"])

    def test_clean_source_directory_merges_multi_account_sheets_and_marks_title_conflicts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_xlsx(
                source / "4月单周数据" / "0410-0416数据" / "B站报表（商业化）.xlsx",
                {
                    "新媒体": pd.DataFrame([_bilibili_row("BV001", "同一标题")]),
                    "研习社": pd.DataFrame([_bilibili_row("BV002", "同一标题", spend=20.0)]),
                },
            )

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            self.assertEqual(set(cleaned["content_id"]), {"BV001", "BV002"})
            self.assertEqual(set(cleaned["source_sheet"]), {"新媒体", "研习社"})
            self.assertTrue(cleaned["needs_manual_review"].astype(bool).all())
            self.assertTrue(cleaned["review_reasons"].astype(str).str.contains("标题重复但ID不同").all())
            conflicts = pd.read_excel(buckets[0].cleaned_workbook, sheet_name="冲突项")
            self.assertIn("标题重复但ID不同", set(conflicts["issue_type"]))

    def test_clean_source_directory_marks_exact_duplicate_files_without_double_counting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            frame = pd.DataFrame([_bilibili_row("BV001", "重复文件内容")])
            _write_xlsx(source / "0508-0514 数据" / "B站数据.xlsx", {"Sheet1": frame})
            _write_xlsx(source / "0508-0514 数据" / "B站数据-副本.xlsx", {"Sheet1": frame})

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            duplicate_files = pd.read_excel(buckets[0].cleaned_workbook, sheet_name="重复文件")
            self.assertEqual(len(cleaned), 1)
            self.assertEqual(len(duplicate_files), 1)
            self.assertIn("B站数据-副本.xlsx", duplicate_files.iloc[0]["duplicate_file"])

    def test_clean_source_directory_imports_sum_prefixed_bilibili_and_excludes_total_row(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            frame = pd.DataFrame(
                [
                    {
                        "视频bvid": "BV001",
                        "求和项:总花费": 10.0,
                        "求和项:展示量": 100,
                        "求和项:点击量": 10,
                        "求和项:应用激活数": 2,
                        "求和项:应用内首次付费次数": 1,
                    },
                    {
                        "视频bvid": "BV002",
                        "求和项:总花费": 20.0,
                        "求和项:展示量": 200,
                        "求和项:点击量": 20,
                        "求和项:应用激活数": 4,
                        "求和项:应用内首次付费次数": 2,
                    },
                    {
                        "视频bvid": "",
                        "求和项:总花费": 30.0,
                        "求和项:展示量": 300,
                        "求和项:点击量": 30,
                        "求和项:应用激活数": 6,
                        "求和项:应用内首次付费次数": 3,
                    },
                ]
            )
            _write_xlsx(source / "0508-0514 数据" / "B站数据.xlsx", {"Sheet1": frame})

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            self.assertEqual(set(cleaned["content_id"]), {"BV001", "BV002"})
            self.assertEqual(float(cleaned["spend"].sum()), 30.0)
            ignored = pd.read_excel(buckets[0].cleaned_workbook, sheet_name="忽略sheet")
            self.assertTrue(ignored["reason"].astype(str).str.contains("汇总行").any())

    def test_clean_source_directory_generates_row_ids_for_identityless_social_details_and_excludes_total(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            frame = pd.DataFrame(
                [
                    {"创意名称": "", "花费": 10.0, "曝光次数": 100, "点击次数": 10, "APP激活次数": 2, "注册次数": 1},
                    {"创意名称": "", "花费": 20.0, "曝光次数": 200, "点击次数": 20, "APP激活次数": 4, "注册次数": 2},
                    {"创意名称": "", "花费": 30.0, "曝光次数": 300, "点击次数": 30, "APP激活次数": 6, "注册次数": 3},
                ]
            )
            _write_xlsx(source / "0508-0514 数据" / "微信市场部.xlsx", {"Sheet1": frame})

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            self.assertEqual(len(cleaned), 2)
            self.assertEqual(set(cleaned["channel"]), {"微信市场部"})
            self.assertEqual(float(cleaned["spend"].sum()), 30.0)
            self.assertTrue(cleaned["content_id"].astype(str).str.startswith("row:").all())
            ignored = pd.read_excel(buckets[0].cleaned_workbook, sheet_name="忽略sheet")
            self.assertTrue(ignored["reason"].astype(str).str.contains("汇总行").any())

    def test_clean_source_directory_excludes_group_subtotal_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            frame = pd.DataFrame(
                [
                    {
                        "素材ID": "dy-1",
                        "视频标题": "达人内容一",
                        "视频链接": "https://douyin.example/1",
                        "消耗": 10.0,
                        "展示数": 100,
                        "激活数": 2,
                        "付费次数": 1,
                        "内容类型": "达人内容",
                    },
                    {
                        "素材ID": "dy-2",
                        "视频标题": "股友说一",
                        "视频链接": "https://douyin.example/2",
                        "消耗": 20.0,
                        "展示数": 200,
                        "激活数": 4,
                        "付费次数": 2,
                        "内容类型": "股友说",
                    },
                    {
                        "素材ID": "",
                        "视频标题": "",
                        "视频链接": "",
                        "消耗": 30.0,
                        "展示数": 300,
                        "激活数": 6,
                        "付费次数": 3,
                        "内容类型": "",
                    },
                    {
                        "素材ID": "",
                        "视频标题": "",
                        "视频链接": "",
                        "消耗": 10.0,
                        "展示数": 100,
                        "激活数": 2,
                        "付费次数": 1,
                        "内容类型": "",
                    },
                ]
            )
            _write_xlsx(source / "0508-0514 数据" / "抖音市场部数据.xlsx", {"Sheet2": frame})

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            self.assertEqual(set(cleaned["content_id"]), {"https://douyin.example/1", "https://douyin.example/2"})
            self.assertEqual(float(cleaned["spend"].sum()), 30.0)
            ignored = pd.read_excel(buckets[0].cleaned_workbook, sheet_name="忽略sheet")
            reasons = "\n".join(ignored["reason"].astype(str).tolist())
            self.assertIn("汇总行", reasons)
            self.assertIn("分组小计行", reasons)

    def test_clean_source_directory_ignores_rows_without_statistical_metrics(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_xlsx(
                source / "0508-0514 数据" / "抖音商业化.xlsx",
                {
                    "Sheet2": pd.DataFrame(
                        [
                            {
                                "视频标题": "只有标题没有统计指标",
                                "视频链接": "https://douyin.example/no-metric",
                                "消耗": "",
                                "展示数": "",
                                "激活数": "",
                                "付费次数": "",
                            },
                            {
                                "视频标题": "有效投放内容",
                                "视频链接": "https://douyin.example/valid",
                                "消耗": 10,
                                "展示数": 100,
                                "激活数": 2,
                                "付费次数": 1,
                            }
                        ]
                    ),
                },
            )

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            self.assertEqual(list(cleaned["title"]), ["有效投放内容"])
            self.assertEqual(float(cleaned["spend"].sum()), 10.0)

    def test_clean_source_directory_keeps_identified_exposure_only_content(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_xlsx(
                source / "0410-0416 数据" / "抖音原生内容（商业化）.xlsx",
                {
                    "Sheet1": pd.DataFrame(
                        [
                            {
                                "视频标题": "有标题的自然流量内容",
                                "视频链接": "https://douyin.example/organic",
                                "内容类型": "股友说",
                                "展示数": 5317,
                            }
                        ]
                    )
                },
            )

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            self.assertEqual(list(cleaned["content_id"]), ["https://douyin.example/organic"])
            self.assertEqual(float(cleaned["impressions"].sum()), 5317.0)
            self.assertTrue(cleaned["spend"].isna().all())

    def test_clean_source_directory_ignores_identityless_single_metric_artifacts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_xlsx(
                source / "0410-0416 数据" / "抖音原生-达人数据情况（商业化）.xlsx",
                {
                    "Sheet4": pd.DataFrame(
                        [
                            {"视频链接": "", "消耗": "", "展示数": 5317, "激活数": "", "付费数": ""},
                            {
                                "视频链接": "",
                                "消耗": 10,
                                "展示数": 100,
                                "激活数": 2,
                                "付费数": 1,
                            },
                        ]
                    )
                },
            )

            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")

            cleaned = load_cleaned_canonical(buckets[0].cleaned_workbook)
            self.assertEqual(len(cleaned), 1)
            self.assertTrue(cleaned["content_id"].astype(str).str.startswith("row:").all())
            self.assertEqual(float(cleaned["spend"].sum()), 10.0)
            ignored = pd.read_excel(buckets[0].cleaned_workbook, sheet_name="忽略sheet")
            self.assertTrue(ignored["reason"].astype(str).str.contains("无可追溯标识").any())

    def test_archived_workflow_uses_cleaned_workbook_instead_of_noisy_raw_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            _write_xlsx(
                source / "0508-0514 数据" / "B站数据.xlsx",
                {"Sheet1": pd.DataFrame([_bilibili_row("BV001", "清洗后内容")])},
            )
            buckets = clean_source_directory(source, root / "data" / "raw", default_year=2026, import_id="import-test")
            raw_dir = buckets[0].raw_dir
            _write_xlsx(
                raw_dir / "噪声人工统计.xlsx",
                {"Sheet1": pd.DataFrame([{"渠道": "B站", "消耗": 999999, "激活": 999999}])},
            )

            result = run_archived_workflow(
                raw_dir,
                "",
                "",
                output_root=root / "outputs",
                archive_root=root / "archive",
                db_path=root / "data" / "workflow.sqlite3",
                env_path=root / "missing.env",
                category_matcher=lambda items, category_library, env_path: {},
            )

            self.assertEqual(list(result.canonical["content_id"]), ["BV001"])
            self.assertNotIn("噪声人工统计.xlsx", set(result.canonical["source_file"]))

    def test_reset_runtime_data_clears_generated_runtime_dirs_and_reinitializes_db(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in [
                "data/raw/20260508-20260514/old.xlsx",
                "data/file_backup/old/raw/old.xlsx",
                "archive/old/raw/old.xlsx",
                "outputs/old/report.html",
                "output/playwright/old.png",
            ]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("old", encoding="utf-8")
            init_db(root / "data" / "workflow.sqlite3")

            reset_runtime_data(root)

            self.assertTrue((root / "data" / "workflow.sqlite3").exists())
            self.assertTrue((root / "data" / "raw").exists())
            self.assertFalse((root / "archive" / "old").exists())
            self.assertFalse((root / "outputs" / "old").exists())
            self.assertFalse((root / "output" / "playwright" / "old.png").exists())


if __name__ == "__main__":
    unittest.main()
