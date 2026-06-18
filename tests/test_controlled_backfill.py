from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ops_data_workflow.controlled_backfill import (
    CONTROLLED_BACKFILL_BATCH_IDS,
    ControlledBackfillResult,
    rerun_controlled_periods,
    select_controlled_periods,
)


class ControlledBackfillTests(unittest.TestCase):
    def test_allowed_periods_are_exactly_the_four_local_validation_periods(self):
        self.assertEqual(
            CONTROLLED_BACKFILL_BATCH_IDS,
            (
                "upload:week:20260526-20260604",
                "upload:week:20260605-20260611",
                "upload:month:2026-04",
                "upload:month:2026-05",
            ),
        )
        self.assertNotIn("upload:month:2026-06", CONTROLLED_BACKFILL_BATCH_IDS)

    def test_select_controlled_periods_rejects_unapproved_periods(self):
        with self.assertRaisesRegex(ValueError, "不在本轮受控回填白名单"):
            select_controlled_periods(["upload:month:2026-06"])

    def test_rerun_controlled_periods_uses_local_sources_and_safe_workflow_defaults(self):
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            for source in [
                project_root / "data" / "weeks" / "20260526-20260604",
                project_root / "data" / "months" / "202605",
            ]:
                source.mkdir(parents=True)

            calls: list[dict[str, object]] = []

            class Result:
                batch_id = "fake-batch"
                archive_dir = project_root / "processed" / "fake-batch"

            def fake_runner(input_dir, period_start, period_end, **kwargs):
                calls.append(
                    {
                        "input_dir": input_dir,
                        "period_start": period_start,
                        "period_end": period_end,
                        **kwargs,
                    }
                )
                return Result()

            results = rerun_controlled_periods(
                project_root=project_root,
                selected_batch_ids=[
                    "upload:week:20260526-20260604",
                    "upload:month:2026-05",
                ],
                runner=fake_runner,
            )

            self.assertEqual([result.batch_id for result in results], ["fake-batch", "fake-batch"])
            self.assertEqual(calls[0]["input_dir"], project_root / "data" / "weeks" / "20260526-20260604")
            self.assertEqual(calls[0]["period_start"], "2026-05-26")
            self.assertEqual(calls[0]["period_end"], "2026-06-04")
            self.assertEqual(calls[0]["period_level"], "week")
            self.assertEqual(calls[0]["period_key"], "20260526-20260604")
            self.assertEqual(calls[1]["input_dir"], project_root / "data" / "months" / "202605")
            self.assertEqual(calls[1]["period_start"], "2026-05-01")
            self.assertEqual(calls[1]["period_end"], "2026-05-31")
            self.assertEqual(calls[1]["period_level"], "month")
            self.assertEqual(calls[1]["period_key"], "2026-05")
            for call in calls:
                self.assertEqual(call["output_mode"], "ui_only")
                self.assertFalse(call["enable_external_context"])
                self.assertFalse(call["enable_deepseek"])
                self.assertEqual(call["metadata_enrichment_mode"], "safe_public")
                self.assertTrue(call["force_reclean"])
                self.assertTrue(call["enqueue_background_analysis"])
                self.assertEqual(call["background_trigger"], "controlled_backfill")
                self.assertEqual(call["metadata_cache_dir"], project_root / "data" / "metadata_cache")
                self.assertEqual(call["enrichment_queue_root"], project_root / "data" / "enrichment_queue")

    def test_dry_run_reports_sources_without_calling_workflow(self):
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "data" / "weeks" / "20260605-20260611").mkdir(parents=True)

            def fail_runner(*args, **kwargs):
                raise AssertionError("dry run must not invoke workflow")

            results = rerun_controlled_periods(
                project_root=project_root,
                selected_batch_ids=["upload:week:20260605-20260611"],
                dry_run=True,
                runner=fail_runner,
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].batch_id, "upload:week:20260605-20260611")
            self.assertEqual(results[0].status, "dry-run")
            self.assertEqual(results[0].source_dir, project_root / "data" / "weeks" / "20260605-20260611")

    def test_rerun_skips_periods_with_harvester_manifests_by_default(self):
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "data" / "months" / "202605").mkdir(parents=True)

            def fail_runner(*args, **kwargs):
                raise AssertionError("periods with captured manifests should be protected by default")

            results = rerun_controlled_periods(
                project_root=project_root,
                selected_batch_ids=["upload:month:2026-05"],
                existing_harvester_manifest_counts=lambda batch_id: 1,
                runner=fail_runner,
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].batch_id, "upload:month:2026-05")
            self.assertEqual(results[0].status, "skipped")
            self.assertIn("已有 harvester manifest", results[0].message)

    def test_rerun_can_include_periods_with_harvester_manifests_when_explicit(self):
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            (project_root / "data" / "months" / "202605").mkdir(parents=True)
            calls = 0

            class Result:
                batch_id = "upload:month:2026-05"
                archive_dir = project_root / "processed" / "202605" / "upload:month:2026-05"

            def fake_runner(*args, **kwargs):
                nonlocal calls
                calls += 1
                return Result()

            results = rerun_controlled_periods(
                project_root=project_root,
                selected_batch_ids=["upload:month:2026-05"],
                existing_harvester_manifest_counts=lambda batch_id: 1,
                include_harvester_periods=True,
                runner=fake_runner,
            )

            self.assertEqual(calls, 1)
            self.assertEqual(results, [ControlledBackfillResult(
                batch_id="upload:month:2026-05",
                status="rerun",
                source_dir=project_root / "data" / "months" / "202605",
                archive_dir=project_root / "processed" / "202605" / "upload:month:2026-05",
                message="已重跑并写入 SQLite。",
            )])


if __name__ == "__main__":
    unittest.main()
