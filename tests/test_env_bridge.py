from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ops_data_workflow.env_bridge import copy_missing_runtime_env, resolve_harvester_env_path, resolve_harvester_root
from ops_data_workflow.platform_sessions import (
    login_profile_dir,
    prepare_local_login_profile,
)


class EnvBridgeTests(unittest.TestCase):
    def test_resolve_harvester_root_defaults_to_sibling_project(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ops-data-workflow"

            self.assertEqual(
                resolve_harvester_root(project_root=project, env={}),
                (root / "harvester-THS").resolve(),
            )
            self.assertEqual(
                resolve_harvester_env_path(project_root=project, env={}),
                (root / "harvester-THS" / ".env").resolve(),
            )

    def test_resolve_harvester_root_uses_env_override(self):
        with TemporaryDirectory() as tmp:
            override = Path(tmp) / "custom-harvester"

            self.assertEqual(
                resolve_harvester_root(project_root=Path("/unused"), env={"HARVESTER_ROOT": str(override)}),
                override.resolve(),
            )

    def test_resolve_harvester_root_reads_relative_env_from_project_env(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ops-data-workflow"
            project.mkdir()
            (project / ".env").write_text("HARVESTER_ROOT=../harvester-THS\n", encoding="utf-8")

            self.assertEqual(
                resolve_harvester_root(project_root=project),
                (root / "harvester-THS").resolve(),
            )

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

    def test_copy_missing_runtime_env_reports_missing_when_sibling_env_absent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ops-data-workflow"
            project.mkdir()
            target = project / ".env"
            target.write_text("FEISHU_APP_ID=ops-app\n", encoding="utf-8")

            source = resolve_harvester_env_path(project_root=project, env={})
            result = copy_missing_runtime_env(source, target)

            self.assertFalse(source.exists())
            self.assertIn("FEISHU_APP_ID", result.kept)
            self.assertIn("FEISHU_APP_SECRET", result.missing)
            self.assertEqual(target.read_text(encoding="utf-8"), "FEISHU_APP_ID=ops-app\n")

    def test_copy_missing_runtime_env_uses_sibling_env_by_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ops-data-workflow"
            harvester = root / "harvester-THS"
            project.mkdir()
            harvester.mkdir()
            (harvester / ".env").write_text("FEISHU_APP_SECRET=harvester-secret\n", encoding="utf-8")
            target = project / ".env"
            target.write_text("", encoding="utf-8")

            result = copy_missing_runtime_env(
                resolve_harvester_env_path(project_root=project, env={}),
                target,
                keys=["FEISHU_APP_SECRET"],
            )

            self.assertIn("FEISHU_APP_SECRET", result.copied)
            self.assertIn("FEISHU_APP_SECRET=harvester-secret", target.read_text(encoding="utf-8"))

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

    def test_prepare_local_login_profile_defaults_to_sibling_harvester(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "ops-data-workflow"
            harvester = root / "harvester-THS"
            (harvester / ".xhs-profile").mkdir(parents=True)
            (harvester / ".xhs-profile" / "state.txt").write_text("logged-in", encoding="utf-8")

            result = prepare_local_login_profile("xhs", project_root=project)

            self.assertEqual(result.source_profile_dir.resolve(), (harvester / ".xhs-profile").resolve())
            self.assertTrue((project / ".xhs-profile" / "state.txt").exists())
            self.assertTrue(result.bootstrapped)
