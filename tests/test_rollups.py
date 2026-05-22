from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from ops_data_workflow.rollups import rollup_period_for, select_rollup_component_batches
from ops_data_workflow.storage import init_db


def _insert_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    start: str,
    end: str,
    level: str,
    key: str,
    created_at: str = "2026-05-01T00:00:00+00:00",
) -> None:
    conn.execute(
        """
        insert into upload_batches (
            batch_id, period_start, period_end, created_at, archive_dir, output_dir,
            status, comparison_batch_id, comparison_note, period_level, period_key,
            period_label, data_start, data_end, source_type
        )
        values (?, ?, ?, ?, '', '', 'ok', '', '', ?, ?, ?, ?, ?, 'upload')
        """,
        (batch_id, start, end, created_at, level, key, f"{level}:{key}", start, end),
    )


class RollupTests(unittest.TestCase):
    def test_rollup_period_for_quarter_and_year_uses_calendar_bounds(self):
        quarter = rollup_period_for("quarter", 2026, 2)
        year = rollup_period_for("year", 2026)

        self.assertEqual(quarter.period_key, "2026-Q2")
        self.assertEqual(quarter.period_start, "2026-04-01")
        self.assertEqual(quarter.period_end, "2026-06-30")
        self.assertEqual(year.period_key, "2026")
        self.assertEqual(year.period_start, "2026-01-01")
        self.assertEqual(year.period_end, "2026-12-31")

    def test_select_rollup_components_uses_month_batches_before_week_fillers(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                _insert_batch(conn, "apr-month", "2026-04-01", "2026-04-30", "month", "2026-04")
                _insert_batch(conn, "apr-w3", "2026-04-17", "2026-04-23", "week", "20260417-20260423")
                _insert_batch(conn, "may-w1", "2026-05-08", "2026-05-14", "week", "20260508-20260514")
                _insert_batch(conn, "may-w2", "2026-05-15", "2026-05-21", "week", "20260515-20260521")
                _insert_batch(conn, "jun-month", "2026-06-01", "2026-06-30", "month", "2026-06")
                conn.commit()

            period = rollup_period_for("quarter", 2026, 2)
            components = select_rollup_component_batches(db_path, period)

            self.assertEqual(components, ["apr-month", "may-w1", "may-w2", "jun-month"])


if __name__ == "__main__":
    unittest.main()
