from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ops_data_workflow.env_bridge import copy_missing_runtime_env
from ops_data_workflow.platform_sessions import (
    login_profile_dir,
    prepare_local_login_profile,
)


class EnvBridgeTests(unittest.TestCase):
    def test_copy_missing_runtime_env_adds_feishu_minimax_without_overwriting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "harvester.env"
            target = root / ".env"
            source.write_text(
                "\n".join(
                    [
                        "FEISHU_APP_ID=harvester-app",
                        "FEISHU_APP_SECRET=harvester-secret",
                        "MINIMAX_API_KEY=minimax-key",
                        "MINIMAX_MODEL=MiniMax-M3",
                        "DEEPSEEK_API_KEY=deepseek-key",
                    ]
                ),
                encoding="utf-8",
            )
            target.write_text("FEISHU_APP_ID=ops-app\nMINIMAX_MODEL=ops-model\n", encoding="utf-8")

            result = copy_missing_runtime_env(source, target)
            values = target.read_text(encoding="utf-8")

            self.assertIn("FEISHU_APP_ID=ops-app", values)
            self.assertIn("MINIMAX_MODEL=ops-model", values)
            self.assertIn("FEISHU_APP_SECRET=harvester-secret", values)
            self.assertIn("MINIMAX_API_KEY=minimax-key", values)
            self.assertIn("DEEPSEEK_API_KEY=deepseek-key", values)
            self.assertIn("FEISHU_APP_SECRET", result.copied)
            self.assertIn("FEISHU_APP_ID", result.kept)

    def test_prepare_local_login_profile_bootstraps_three_platform_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            harvester = root / "harvester"
            project = root / "project"
            (harvester / ".douyin-profile").mkdir(parents=True)
            (harvester / ".douyin-profile" / "state.txt").write_text("logged-in", encoding="utf-8")

            result = prepare_local_login_profile("douyin", project_root=project, harvester_root=harvester)

            self.assertEqual(result.platform, "douyin")
            self.assertEqual(result.profile_dir, login_profile_dir("douyin", project))
            self.assertTrue((result.profile_dir / "state.txt").exists())
            self.assertTrue(result.bootstrapped)
