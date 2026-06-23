from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from scripts.feishu_harvester_smoke import run_smoke


class FeishuHarvesterSmokeTests(unittest.TestCase):
    def test_run_smoke_syncs_harvester_env_and_returns_safe_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ops-data-workflow"
            harvester = root / "harvester-THS"
            project.mkdir()
            harvester.mkdir()
            env_path = project / ".env"
            harvester_env = harvester / ".env"
            env_path.write_text("FEISHU_APP_ID=stale-app\n", encoding="utf-8")
            harvester_env.write_text(
                "\n".join(
                    [
                        "FEISHU_APP_ID=harvester-app",
                        "FEISHU_APP_SECRET=harvester-secret",
                        "FEISHU_WIKI_TOKEN=wiki-token",
                        "FEISHU_SHEET_DOUYIN=dySheet",
                        "FEISHU_SHEET_XHS=xhsSheet",
                        "FEISHU_SHEET_BILIBILI=biliSheet",
                        "FEISHU_SHEET_STEP15_FILTERED=step15Sheet",
                    ]
                ),
                encoding="utf-8",
            )
            ledger = pd.DataFrame(
                [
                    {"platform": "抖音"},
                    {"platform": "小红书"},
                    {"platform": "B站"},
                    {"platform": "B站"},
                ]
            )
            ledger.attrs["feishu_enabled"] = True
            ledger.attrs["ledger_warnings"] = []
            ledger.attrs["feishu_snapshot"] = {
                "sheet_row_counts": {"dySheet": 10, "xhsSheet": 20, "biliSheet": 30},
                "platform_counts": {"抖音": 1, "小红书": 1, "B站": 2},
            }
            ledger.attrs["feishu_staleness"] = {"needs_check": False, "needs_check_platforms": []}

            with patch("scripts.feishu_harvester_smoke.load_feishu_content_ledger", return_value=ledger), patch(
                "scripts.feishu_harvester_smoke.harvester_cli_available", return_value=True
            ):
                summary = run_smoke(project_root=project, env_path=env_path)

            values = env_path.read_text(encoding="utf-8")
            self.assertIn("FEISHU_APP_ID=harvester-app", values)
            self.assertIn("FEISHU_SHEET_STEP15_FILTERED=step15Sheet", values)
            self.assertTrue(summary["ok"])
            self.assertTrue(summary["feishu_enabled"])
            self.assertTrue(summary["harvester_cli_available"])
            self.assertEqual(summary["total_rows"], 4)
            self.assertEqual(summary["platform_counts"]["B站"], 2)
            self.assertNotIn("harvester-secret", str(summary))
            self.assertNotIn("wiki-token", str(summary))
