from datetime import date
from pathlib import Path
from contextlib import closing
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
from openpyxl import load_workbook

from ops_data_workflow.ai import _build_payload, group_topic_labels, resolve_deepseek_settings
from ops_data_workflow.categories import CATEGORY_TAG_MAP, category_from_tags
from ops_data_workflow.storage import init_db
from ops_data_workflow.storage import (
    delete_batch_permanently,
    list_file_backups,
    load_category_mappings,
    move_batch_to_file_backup,
    purge_history_state,
    read_batch_record,
    restore_file_backup,
    upsert_category_mappings,
)
from ops_data_workflow.upload_input import infer_period_from_upload_names, materialize_uploaded_files
from ops_data_workflow.workflow import run_archived_workflow
from tests.test_workflow import _write_raw_fixture


class FakeUpload:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def _workbook_bytes(frame: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="sheet1", index=False)
    return buffer.getvalue()


class ProductizedWorkflowTests(unittest.TestCase):
    def test_materialize_uploaded_files_accepts_direct_excel_csv_and_zip(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_buffer = BytesIO()
            with ZipFile(zip_buffer, "w") as archive:
                archive.writestr("抖音商业化.csv", "视频标题,视频id,素材ID,消耗,展示数,点击数,激活数,付费次数,内容类型\n标题,dy,mat,1,2,1,1,1,热点行情\n")

            uploads = [
                FakeUpload(
                    "B站.xlsx",
                    _workbook_bytes(
                        pd.DataFrame(
                            [
                                {
                                    "视频AVID": "av",
                                    "视频BVID": "bv",
                                    "视频标题": "标题",
                                    "花费": 1,
                                    "展示量": 2,
                                    "点击量": 1,
                                    "应用激活数": 1,
                                    "应用内付费": 1,
                                }
                            ]
                        )
                    ),
                ),
                FakeUpload(
                    "小红书商业化.csv",
                    "标题,笔记ID,发布作者,类型,内容分类,消费,展现量,点击量,激活数,首次付费次数\n标题,note,作者,图文,热点行情,1,2,1,1,1\n".encode(
                        "utf-8-sig"
                    ),
                ),
                FakeUpload("raw.zip", zip_buffer.getvalue()),
            ]

            result = materialize_uploaded_files(uploads, tmp_path / "raw")

            self.assertTrue((result.raw_dir / "B站.xlsx").exists())
            self.assertTrue((result.raw_dir / "小红书商业化.csv").exists())
            self.assertTrue((result.raw_dir / "抖音商业化.csv").exists())
            self.assertEqual(len(result.original_files), 3)

    def test_materialize_uploaded_files_writes_to_period_raw_directory(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            uploads = [
                FakeUpload(
                    "B站.xlsx",
                    _workbook_bytes(
                        pd.DataFrame(
                            [
                                {
                                    "视频AVID": "av",
                                    "视频BVID": "bv",
                                    "视频标题": "标题",
                                    "花费": 1,
                                    "展示量": 2,
                                    "点击量": 1,
                                    "应用激活数": 1,
                                    "应用内付费": 1,
                                }
                            ]
                        )
                    ),
                )
            ]

            result = materialize_uploaded_files(
                uploads,
                tmp_path / "data" / "raw" / "20260401-20260427",
            )

            self.assertEqual(result.raw_dir.name, "20260401-20260427")
            self.assertTrue((tmp_path / "data" / "raw" / "20260401-20260427" / "B站.xlsx").exists())

    def test_materialize_uploaded_files_preserves_folder_relative_paths(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            uploads = [
                FakeUpload(
                    "20260401-20260427/B站.xlsx",
                    _workbook_bytes(
                        pd.DataFrame(
                            [
                                {
                                    "视频AVID": "av",
                                    "视频BVID": "bv",
                                    "视频标题": "标题",
                                    "花费": 1,
                                    "展示量": 2,
                                    "点击量": 1,
                                    "应用激活数": 1,
                                    "应用内付费": 1,
                                }
                            ]
                        )
                    ),
                )
            ]

            result = materialize_uploaded_files(uploads, tmp_path / "raw")

            self.assertTrue((result.raw_dir / "20260401-20260427" / "B站.xlsx").exists())
            self.assertTrue((result.original_files[0]).exists())

    def test_materialize_uploaded_files_can_strip_common_period_folder(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            uploads = [
                FakeUpload(
                    "20260401-20260427/B站.xlsx",
                    _workbook_bytes(
                        pd.DataFrame(
                            [
                                {
                                    "视频AVID": "av",
                                    "视频BVID": "bv",
                                    "视频标题": "标题",
                                    "花费": 1,
                                    "展示量": 2,
                                    "点击量": 1,
                                    "应用激活数": 1,
                                    "应用内付费": 1,
                                }
                            ]
                        )
                    ),
                )
            ]

            result = materialize_uploaded_files(
                uploads,
                tmp_path / "data" / "raw" / "20260401-20260427",
                strip_common_period_root=True,
            )

            self.assertTrue((result.raw_dir / "B站.xlsx").exists())
            self.assertFalse((result.raw_dir / "20260401-20260427" / "B站.xlsx").exists())
            self.assertTrue((result.original_files[0]).exists())

    def test_materialize_uploaded_files_rejects_parent_escape_paths(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            with self.assertRaisesRegex(ValueError, "非法路径"):
                materialize_uploaded_files(
                    [FakeUpload("../evil.xlsx", b"bad")],
                    tmp_path / "raw",
                )

    def test_materialize_uploaded_files_rejects_absolute_paths(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            with self.assertRaisesRegex(ValueError, "非法路径"):
                materialize_uploaded_files(
                    [FakeUpload("/tmp/evil.xlsx", b"bad")],
                    tmp_path / "raw",
                )

    def test_infer_period_from_upload_names_reads_top_level_folder(self):
        uploads = [FakeUpload("20260401-20260427/B站.xlsx", b"")]

        inferred = infer_period_from_upload_names(uploads)

        self.assertEqual(inferred, (date(2026, 4, 1), date(2026, 4, 27)))

    def test_infer_period_from_upload_names_supports_dashed_folder_pattern(self):
        uploads = [FakeUpload("2026-04-01_2026-04-27/抖音商业化.csv", b"")]

        inferred = infer_period_from_upload_names(uploads)

        self.assertEqual(inferred, (date(2026, 4, 1), date(2026, 4, 27)))

    def test_infer_period_from_upload_names_ignores_non_folder_uploads(self):
        uploads = [FakeUpload("B站.xlsx", b"")]

        self.assertIsNone(infer_period_from_upload_names(uploads))

    def test_cli_infers_period_from_raw_directory_name(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "data" / "raw" / "20260401-20260427"
            raw_dir.mkdir(parents=True)
            _write_raw_fixture(raw_dir)

            completed = subprocess.run(
                [
                    ".venv/bin/python",
                    "main.py",
                    "--input",
                    str(raw_dir),
                    "--output",
                    str(tmp_path / "outputs"),
                    "--archive-root",
                    str(tmp_path / "archive"),
                    "--db",
                    str(tmp_path / "workflow.sqlite3"),
                    "--env",
                    str(tmp_path / "missing.env"),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            with closing(sqlite3.connect(tmp_path / "workflow.sqlite3")) as conn:
                period = conn.execute("select period_start, period_end from upload_batches").fetchone()
            self.assertEqual(period, ("2026-04-01", "2026-04-27"))

    def test_resolve_deepseek_settings_reads_explicit_env_without_leaking_secret(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "deepseek.env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=sk-test-secret\nDEEPSEEK_BASE_URL=https://example.test\nDEEPSEEK_MODEL=deepseek-test\n",
                encoding="utf-8",
            )

            settings = resolve_deepseek_settings(env_path)

            self.assertTrue(settings.configured)
            self.assertEqual(settings.base_url, "https://example.test")
            self.assertEqual(settings.model, "deepseek-test")
            self.assertIn(str(env_path), settings.checked_paths)
            self.assertNotIn("sk-test-secret", settings.public_status)

    def test_ai_payload_serializes_nullable_float_frames(self):
        payload = _build_payload(
            pd.DataFrame([{"channel": "总计", "spend": 1.0}]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            "",
            platform_summary=pd.DataFrame(),
            platform_category_summary=pd.DataFrame(
                {"platform": ["抖音"], "first_pay_rate": pd.Series([pd.NA], dtype="Float64")}
            ),
        )

        self.assertEqual(payload["channel_category_topic_summary"][0]["first_pay_rate"], "")

    def test_group_topic_labels_returns_empty_without_deepseek_key(self):
        with TemporaryDirectory() as tmp:
            frame = pd.DataFrame(
                [
                    {
                        "channel": "抖音商业化",
                        "title": "短线交易高手",
                        "content_id": "dy-1",
                        "material_id": "mat-1",
                        "category_l2": "股友说",
                        "category_l3": "",
                        "spend": 100.0,
                        "activations": 10.0,
                        "first_pay_count": 2.0,
                    }
                ]
            )

            labels = group_topic_labels(frame, env_path=Path(tmp) / "missing.env")

            self.assertEqual(labels, {})

    def test_tag_mapping_uses_exact_user_defined_hashtags(self):
        self.assertEqual(CATEGORY_TAG_MAP["#同花顺资讯"], "资讯")
        self.assertEqual(CATEGORY_TAG_MAP["#同顺图解"], "图文")
        self.assertEqual(CATEGORY_TAG_MAP["#同花顺股民话题"], "社区话题")

        self.assertEqual(category_from_tags("今天的内容 #同顺图解 #财经"), "图文")
        self.assertEqual(category_from_tags("给短线交易者的完美范例 #股友说"), "")
        self.assertEqual(category_from_tags("给短线交易者的完美范例 #同花顺股友说"), "股友说")

    def test_archived_workflow_persists_batch_files_and_ai_fallback(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_archived_workflow(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                db_path=tmp_path / "workflow.sqlite3",
                env_path=tmp_path / "missing.env",
            )

            self.assertTrue(result.batch_id)
            self.assertTrue(result.archive_dir.exists())
            self.assertIn("未配置 DEEPSEEK_API_KEY", result.ai_summary)
            self.assertIn("无历史对比数据", result.comparison_note)
            self.assertTrue((result.archive_dir / "raw" / "B站.xlsx").exists())
            output_dir = result.report_html.parent
            self.assertTrue((output_dir / "report.html").exists())

            record = read_batch_record(tmp_path / "workflow.sqlite3", result.batch_id)
            self.assertEqual(record["batch_id"], result.batch_id)
            self.assertEqual(record["archive_dir"], str(result.archive_dir))
            self.assertEqual(record["output_dir"], str(output_dir))

            with closing(sqlite3.connect(tmp_path / "workflow.sqlite3")) as conn:
                batch_count = conn.execute("select count(*) from upload_batches").fetchone()[0]
                file_count = conn.execute("select count(*) from uploaded_files").fetchone()[0]
                item_count = conn.execute("select count(*) from canonical_items").fetchone()[0]
                ai_count = conn.execute("select count(*) from ai_reports").fetchone()[0]

            self.assertEqual(batch_count, 1)
            self.assertGreaterEqual(file_count, 4)
            self.assertEqual(item_count, len(result.canonical))
            self.assertEqual(ai_count, 1)

    def test_archived_workflow_adds_new_columns_to_existing_sqlite_tables(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "create table canonical_items (batch_id text, platform text, channel text, content_id text)"
                )
                conn.commit()

            result = run_archived_workflow(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            with closing(sqlite3.connect(db_path)) as conn:
                columns = [row[1] for row in conn.execute("pragma table_info(canonical_items)").fetchall()]
                item_count = conn.execute("select count(*) from canonical_items").fetchone()[0]
            self.assertIn("manual_category", columns)
            self.assertIn("ai_category", columns)
            self.assertEqual(item_count, len(result.canonical))

    def test_archived_workflow_reads_previous_batch_for_channel_comparison(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            archive_root = tmp_path / "archive"
            output_root = tmp_path / "outputs"

            first_raw = tmp_path / "first"
            first_raw.mkdir()
            _write_raw_fixture(first_raw)
            run_archived_workflow(
                first_raw,
                "2026-04-01",
                "2026-04-07",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            second_raw = tmp_path / "second"
            second_raw.mkdir()
            _write_raw_fixture(second_raw)
            result = run_archived_workflow(
                second_raw,
                "2026-04-08",
                "2026-04-14",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            comparison = result.channel_comparison.set_index("channel")
            self.assertIn("总计", comparison.index)
            self.assertIn("spend_change_rate", comparison.columns)
            self.assertEqual(result.comparison_note, "")

            with closing(sqlite3.connect(db_path)) as conn:
                batch_count = conn.execute("select count(*) from upload_batches").fetchone()[0]
            self.assertEqual(batch_count, 2)

    def test_archived_workflow_uses_period_order_instead_of_import_order(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            archive_root = tmp_path / "archive"
            output_root = tmp_path / "outputs"

            april_raw = tmp_path / "april"
            april_raw.mkdir()
            _write_raw_fixture(april_raw)
            april_result = run_archived_workflow(
                april_raw,
                "2026-04-01",
                "2026-04-30",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )
            self.assertIn("无历史对比数据", april_result.comparison_note)

            march_raw = tmp_path / "march"
            march_raw.mkdir()
            _write_raw_fixture(march_raw)
            march_result = run_archived_workflow(
                march_raw,
                "2026-03-01",
                "2026-03-31",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )
            self.assertIn("无历史对比数据", march_result.comparison_note)

            may_raw = tmp_path / "may"
            may_raw.mkdir()
            _write_raw_fixture(may_raw)
            may_result = run_archived_workflow(
                may_raw,
                "2026-05-01",
                "2026-05-31",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            self.assertEqual(may_result.comparison_note, "")
            comparison = may_result.channel_comparison.set_index("channel")
            self.assertIn("总计", comparison.index)
            with closing(sqlite3.connect(db_path)) as conn:
                previous = conn.execute(
                    "select comparison_batch_id from upload_batches where batch_id = ?",
                    (may_result.batch_id,),
                ).fetchone()
            self.assertEqual(previous[0], april_result.batch_id)

    def test_purge_history_state_clears_persisted_results_and_batch_artifacts(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            archive_root = tmp_path / "archive"
            output_root = tmp_path / "outputs"
            first_raw = tmp_path / "first"
            first_raw.mkdir()
            _write_raw_fixture(first_raw)

            run_archived_workflow(
                first_raw,
                "2026-04-01",
                "2026-04-07",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )
            second_raw = tmp_path / "second"
            second_raw.mkdir()
            _write_raw_fixture(second_raw)
            run_archived_workflow(
                second_raw,
                "2026-04-08",
                "2026-04-14",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            purge_history_state(db_path, output_root, archive_root)

            with closing(sqlite3.connect(db_path)) as conn:
                for table in ["upload_batches", "uploaded_files", "canonical_items", "ai_reports", "channel_comparison_items"]:
                    count = conn.execute(f"select count(*) from {table}").fetchone()[0]
                    self.assertEqual(count, 0, table)
                category_mapping_count = conn.execute("select count(*) from category_mappings").fetchone()[0]
                self.assertEqual(category_mapping_count, 0)

            self.assertEqual(list(output_root.iterdir()), [])
            self.assertEqual(list(archive_root.iterdir()), [])

    def test_move_batch_to_file_backup_hides_and_restores_period_files(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            archive_root = tmp_path / "archive"
            output_root = tmp_path / "outputs"
            raw_root = tmp_path / "data" / "raw"
            backup_root = tmp_path / "data" / "file_backup"
            raw_dir = raw_root / "20260401-20260407"
            raw_dir.mkdir(parents=True)
            _write_raw_fixture(raw_dir)

            result = run_archived_workflow(
                raw_dir,
                "2026-04-01",
                "2026-04-07",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            move_batch_to_file_backup(db_path, result.batch_id, raw_root, backup_root)
            backups = list_file_backups(db_path)

            self.assertFalse(raw_dir.exists())
            self.assertEqual(list(backups["batch_id"]), [result.batch_id])
            self.assertTrue(Path(backups.iloc[0]["backup_dir"]).exists())

            restore_file_backup(db_path, result.batch_id, raw_root, backup_root)
            restored_backups = list_file_backups(db_path)

            self.assertTrue(raw_dir.exists())
            self.assertTrue(restored_backups.empty)

    def test_delete_batch_permanently_removes_batch_artifacts_but_keeps_mappings(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            archive_root = tmp_path / "archive"
            output_root = tmp_path / "outputs"
            raw_root = tmp_path / "data" / "raw"
            backup_root = tmp_path / "data" / "file_backup"
            raw_dir = raw_root / "20260401-20260407"
            raw_dir.mkdir(parents=True)
            _write_raw_fixture(raw_dir)
            upsert_category_mappings(
                db_path,
                pd.DataFrame(
                    [
                        {
                            "platform": "抖音",
                            "platform_group": "抖音",
                            "channel": "抖音商业化",
                            "content_id": "content",
                            "material_id": "material",
                            "title": "标题",
                            "category_l1": "",
                            "category_l2": "股友说",
                            "category_l3": "短线交易",
                        }
                    ]
                ),
            )
            result = run_archived_workflow(
                raw_dir,
                "2026-04-01",
                "2026-04-07",
                output_root=output_root,
                archive_root=archive_root,
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )
            with closing(sqlite3.connect(db_path)) as conn:
                mapping_count_before = conn.execute("select count(*) from category_mappings").fetchone()[0]

            delete_batch_permanently(db_path, result.batch_id, raw_root, backup_root)

            with closing(sqlite3.connect(db_path)) as conn:
                batch_count = conn.execute("select count(*) from upload_batches").fetchone()[0]
                canonical_count = conn.execute("select count(*) from canonical_items").fetchone()[0]
                mapping_count = conn.execute("select count(*) from category_mappings").fetchone()[0]
            self.assertEqual(batch_count, 0)
            self.assertEqual(canonical_count, 0)
            self.assertEqual(mapping_count, mapping_count_before)
            self.assertFalse(raw_dir.exists())
            self.assertFalse(result.report_html.parent.exists())
            self.assertFalse(result.archive_dir.exists())

    def test_category_mapping_overrides_are_reused_by_archived_workflow(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "视频标题": "人工审核过的未知主题",
                        "视频id": "dy-map",
                        "素材ID": "mat-map",
                        "消耗": 100.0,
                        "展示数": 1000,
                        "点击数": 100,
                        "激活数": 10,
                        "付费次数": 2,
                        "内容类型": "",
                    }
                ]
            ).to_csv(raw_dir / "抖音市场部.csv", index=False, encoding="utf-8-sig")

            init_db(db_path)
            upsert_category_mappings(
                db_path,
                pd.DataFrame(
                    [
                        {
                            "platform": "抖音市场部",
                            "platform_group": "抖音",
                            "content_id": "dy-map",
                            "material_id": "mat-map",
                            "title": "人工审核过的未知主题",
                            "category_l1": "",
                            "category_l2": "人工复用类别",
                            "category_l3": "人工复用主题",
                        }
                    ]
                ),
            )

            mappings = load_category_mappings(db_path)
            self.assertIn("content_id:dy-map", mappings)

            result = run_archived_workflow(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                db_path=db_path,
                env_path=tmp_path / "missing.env",
            )

            row = result.canonical.iloc[0]
            self.assertEqual(row["category_l2"], "人工复用类别")
            self.assertEqual(row["category_l3"], "人工复用主题")
            self.assertEqual(row["category_source"], "历史审核映射")
            self.assertEqual(row["review_status"], "已确认")

    def test_workflow_builds_account_audit_and_top_content_items(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir()
            _write_raw_fixture(raw_dir)

            result = run_archived_workflow(
                raw_dir,
                "2026-04-01",
                "2026-04-27",
                output_root=tmp_path / "outputs",
                archive_root=tmp_path / "archive",
                db_path=tmp_path / "workflow.sqlite3",
                env_path=tmp_path / "missing.env",
            )

            workbook = load_workbook(result.analysis_xlsx, read_only=True)
            self.assertIn("人工审核表", workbook.sheetnames)
            self.assertIn("账号映射表", workbook.sheetnames)
            self.assertIn("分渠道总数据", workbook.sheetnames)
            self.assertIn("分渠道栏目题材排名", workbook.sheetnames)
            workbook.close()

            account_audit = result.account_audit.set_index(["channel", "expected_account"])
            self.assertEqual(account_audit.loc[("小红书", "同花顺投资"), "status"], "缺失")
            self.assertIn("同花顺新手福利官", set(result.account_audit["expected_account"]))

            self.assertGreater(len(result.top_content_items), 0)
            self.assertLessEqual(result.top_content_items.groupby("channel").size().max(), 15)
            self.assertIn("performance_flag", result.top_content_items.columns)


if __name__ == "__main__":
    unittest.main()
