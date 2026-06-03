from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from ops_data_workflow.source_import import build_source_import_plan, execute_source_import_plan


def _write_workbook(path: Path, sheet_names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name in sheet_names:
            pd.DataFrame(
                [
                    {
                        "标题": f"{sheet_name} 样本",
                        "笔记ID": f"{sheet_name}-id",
                        "消费": 10,
                        "激活数": 1,
                    }
                ]
            ).to_excel(writer, sheet_name=sheet_name, index=False)


class SourceImportTests(unittest.TestCase):
    def test_build_source_import_plan_raises_when_source_directory_cannot_be_listed(self):
        with TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "external"
            source_root.mkdir()

            with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(PermissionError, "无法读取外部数据目录"):
                    build_source_import_plan(source_root, Path(tmp) / "project" / "data", default_year=2026)

    def test_build_source_import_plan_maps_weeks_to_date_dirs_and_records_sheets(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_root = tmp_path / "external"
            data_root = tmp_path / "project" / "data"
            _write_workbook(source_root / "0508-0514数据" / "小红书市场部.xlsx", ["新户", "老户"])
            _write_workbook(source_root / "原生内容投稿-20260527.xlsx", ["小红书渠道", "B站渠道"])
            (source_root / "readme.txt").write_text("ignore me", encoding="utf-8")

            plan = build_source_import_plan(source_root, data_root, default_year=2026)

            raw_entries = [entry for entry in plan.entries if entry.kind == "raw"]
            reference_entries = [entry for entry in plan.entries if entry.kind == "reference"]
            self.assertEqual(len(raw_entries), 1)
            self.assertEqual(raw_entries[0].relative_path, "0508-0514数据/小红书市场部.xlsx")
            self.assertEqual(raw_entries[0].period_key, "20260508-20260514")
            self.assertEqual(raw_entries[0].target_path, data_root / "weeks" / "20260508-20260514" / "小红书市场部.xlsx")
            self.assertEqual(raw_entries[0].sheet_names, ["新户", "老户"])
            self.assertEqual(raw_entries[0].channel, "小红书市场部")
            self.assertEqual(len(reference_entries), 1)
            self.assertEqual(reference_entries[0].target_path, data_root / "reference" / "原生内容投稿-20260527.xlsx")
            self.assertEqual(reference_entries[0].sheet_names, ["小红书渠道", "B站渠道"])

    def test_build_source_import_plan_maps_month_directory_when_file_name_has_no_date(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_root = tmp_path / "external"
            data_root = tmp_path / "project" / "data"
            _write_workbook(source_root / "mouths" / "202605" / "B站.xlsx", ["Sheet1"])

            plan = build_source_import_plan(source_root, data_root, default_year=2026)

            raw_entries = [entry for entry in plan.entries if entry.kind == "raw"]
            self.assertEqual(len(raw_entries), 1)
            self.assertEqual(raw_entries[0].relative_path, "mouths/202605/B站.xlsx")
            self.assertEqual(raw_entries[0].period_key, "2026-05")
            self.assertEqual(raw_entries[0].period_label, "月｜2026年05月")
            self.assertEqual(raw_entries[0].target_path, data_root / "months" / "202605" / "B站.xlsx")

    def test_execute_source_import_plan_replace_all_clears_runtime_and_copies_only_planned_files(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_root = tmp_path / "project"
            source_root = tmp_path / "external"
            data_root = project_root / "data"
            _write_workbook(source_root / "0508-0514数据" / "B站.xlsx", ["Sheet1"])
            _write_workbook(source_root / "原生内容投稿-20260527.xlsx", ["B站渠道"])
            for stale in [
                data_root / "weeks" / "202605w2" / "旧.xlsx",
                data_root / "raw" / "20260508-20260514" / "cleaned.xlsx",
                project_root / "processed" / "stale" / "cleaned.xlsx",
                project_root / "outputs" / "stale" / "report.html",
                project_root / ".runtime" / "workflow.sqlite3",
                data_root / "workflow.sqlite3",
            ]:
                stale.parent.mkdir(parents=True, exist_ok=True)
                stale.write_text("stale", encoding="utf-8")

            plan = build_source_import_plan(source_root, data_root, default_year=2026)
            result = execute_source_import_plan(plan, project_root=project_root, replace_all=True)

            self.assertEqual(result.copied_count, 2)
            self.assertTrue((data_root / "weeks" / "20260508-20260514" / "B站.xlsx").exists())
            self.assertTrue((data_root / "reference" / "原生内容投稿-20260527.xlsx").exists())
            self.assertFalse((data_root / "weeks" / "202605w2").exists())
            self.assertFalse((data_root / "raw").exists())
            self.assertFalse((project_root / "processed" / "stale").exists())
            self.assertFalse((project_root / "outputs" / "stale").exists())
            self.assertTrue((project_root / ".runtime" / "workflow.sqlite3").exists())
            self.assertFalse((data_root / "workflow.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
