from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.harvester_bridge import (
    HarvesterProgressEvent,
    build_asset_jobs,
    build_asset_jobs_to_capture,
    cache_existing_harvester_assets_for_batch,
    harvester_cli_available,
    load_asset_manifests,
    resolve_harvester_root,
    run_harvester_asset_capture,
    write_jobs_jsonl,
    _run_command_with_progress,
)
from ops_data_workflow.storage import (
    init_db,
    list_harvester_asset_jobs,
    list_harvester_asset_manifests,
    list_content_performance_items,
    list_local_content_assets,
    list_top_asset_cache_entries,
    list_top_asset_cache_refs,
    persist_content_performance_items,
    persist_feishu_ledger_snapshot,
    persist_harvester_asset_jobs,
    persist_harvester_asset_manifests,
    upsert_content_assets_from_feishu,
)


class HarvesterBridgeTests(unittest.TestCase):
    def test_resolve_harvester_root_defaults_to_sibling_and_allows_env_override(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ops-data-workflow"
            workspace.mkdir()
            sibling = Path(tmp) / "harvester-THS"
            sibling.mkdir()
            (sibling / "package.json").write_text("{}", encoding="utf-8")

            self.assertEqual(resolve_harvester_root(workspace_root=workspace), sibling.resolve())

            override = Path(tmp) / "custom-harvester"
            override.mkdir()
            (override / "package.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                resolve_harvester_root(workspace_root=workspace, env={"HARVESTER_ROOT": str(override)}),
                override.resolve(),
            )

    def test_build_asset_jobs_keeps_stable_contract_and_metrics(self):
        top_content = pd.DataFrame(
            [
                {
                    "platform": "小红书",
                    "channel": "小红书商业化",
                    "content_identity_key": "小红书商业化::小红书::id::note-1",
                    "content_id": "note-1",
                    "content_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "高价值图文",
                    "account": "示例账号",
                    "period_start": "2026-06-01",
                    "period_end": "2026-06-07",
                    "spend": 3000,
                    "impressions": 10000,
                    "clicks": 100,
                    "activations": 12,
                    "first_pay_count": 3,
                }
            ]
        )

        jobs = build_asset_jobs("batch-1", top_content)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["batch_id"], "batch-1")
        self.assertEqual(job["platform"], "小红书")
        self.assertEqual(job["content_identity_key"], "小红书商业化::小红书::id::note-1")
        self.assertEqual(job["metrics"]["spend"], 3000.0)
        self.assertEqual(job["metrics"]["first_pay_count"], 3.0)
        self.assertTrue(job["job_id"])

    def test_write_jobs_jsonl_and_load_manifest_round_trip(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jobs = [
                {
                    "job_id": "job-1",
                    "batch_id": "batch-1",
                    "platform": "B站",
                    "channel": "B站",
                    "content_identity_key": "B站::B站::id::BV1abc",
                    "content_id": "BV1abc",
                    "content_url": "https://www.bilibili.com/video/BV1abc/",
                    "title": "视频",
                    "account": "账号",
                    "period_start": "2026-06-01",
                    "period_end": "2026-06-07",
                    "metrics": {"spend": 10.0},
                }
            ]
            jobs_path = write_jobs_jsonl(jobs, tmp_path / "jobs.jsonl")
            lines = jobs_path.read_text(encoding="utf-8").splitlines()

            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["job_id"], "job-1")

            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "job_id": "job-1",
                                "status": "succeeded",
                                "platform": "B站",
                                "asset_dir": "assets/job-1",
                                "cover_path": "assets/job-1/cover.jpg",
                                "video_path": "assets/job-1/video.mp4",
                                "screenshots": ["assets/job-1/screenshot.png"],
                                "frames": ["assets/job-1/frame.jpg"],
                                "metadata": {"title": "视频"},
                                "error_message": "",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifests = load_asset_manifests(manifest_path)

            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0]["job_id"], "job-1")
            self.assertEqual(manifests[0]["metadata"]["title"], "视频")

    def test_load_asset_manifests_marks_missing_contract_fields_failed(self):
        with TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "job_id": "job-1",
                                "status": "succeeded",
                                "platform": "小红书",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifests = load_asset_manifests(manifest_path)

            self.assertEqual(manifests[0]["status"], "failed")
            self.assertIn("缺少素材目录", manifests[0]["error_message"])

    def test_build_asset_jobs_resolves_douyin_share_link_before_capture(self):
        top_content = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_identity_key": "抖音商业化::抖音::url::https://v.douyin.com/share",
                    "content_id": "",
                    "content_url": "https://v.douyin.com/share",
                    "title": "复制链接素材",
                    "spend": 3000,
                }
            ]
        )

        jobs = build_asset_jobs(
            "batch-1",
            top_content,
            douyin_resolver=lambda value: {
                "id": "7594830477777751338",
                "link": "https://www.douyin.com/video/7594830477777751338",
            },
        )

        self.assertEqual(jobs[0]["content_id"], "7594830477777751338")
        self.assertEqual(jobs[0]["content_url"], "https://www.douyin.com/video/7594830477777751338")
        self.assertEqual(jobs[0]["content_identity_key"], "抖音商业化::抖音::id::7594830477777751338")

    def test_build_asset_jobs_uses_douyin_work_id_from_url_before_juliang_material_id(self):
        top_content = pd.DataFrame(
            [
                {
                    "platform": "抖音",
                    "channel": "抖音商业化",
                    "content_identity_key": "抖音商业化::抖音::id::7623723655051214889",
                    "content_id": "7623721481431780662",
                    "content_url": "https://www.douyin.com/video/7623723655051214889?previous_page=web_code_link",
                    "title": "巨量素材链接",
                    "spend": 3000,
                }
            ]
        )

        jobs = build_asset_jobs("batch-1", top_content)

        self.assertEqual(jobs[0]["content_id"], "7623723655051214889")
        self.assertEqual(jobs[0]["content_url"], "https://www.douyin.com/video/7623723655051214889")
        self.assertEqual(jobs[0]["content_identity_key"], "抖音商业化::抖音::id::7623723655051214889")

    def test_run_harvester_asset_capture_records_missing_cli_as_failed_jobs(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harvester_root = tmp_path / "harvester-THS"
            harvester_root.mkdir()
            db_path = tmp_path / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-key",
                        "content_id": "dy-1",
                        "title": "标题",
                        "spend": 3000,
                    }
                ]
            )

            result = run_harvester_asset_capture(
                db_path,
                "batch-1",
                top_content,
                harvester_root=harvester_root,
                runtime_root=tmp_path / "runtime",
            )
            jobs = list_harvester_asset_jobs(db_path, batch_id="batch-1")

            self.assertFalse(result.ok)
            self.assertIn("npm run materials:cache-topn", result.message)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs.iloc[0]["status"], "failed")

    def test_harvester_cli_available_uses_package_script_contract(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_json = root / "package.json"
            package_json.write_text(
                json.dumps({"scripts": {"materials:cache-topn": "node src/cache-topn-materials.mjs"}}),
                encoding="utf-8",
            )

            self.assertTrue(harvester_cli_available(root))

            package_json.write_text(json.dumps({"scripts": {}}), encoding="utf-8")
            self.assertFalse(harvester_cli_available(root))

    def test_run_harvester_asset_capture_calls_topn_npm_script_and_persists_manifest(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harvester_root = tmp_path / "harvester-THS"
            harvester_root.mkdir()
            (harvester_root / "package.json").write_text(
                json.dumps({"scripts": {"materials:cache-topn": "node src/cache-topn-materials.mjs"}}),
                encoding="utf-8",
            )
            db_path = tmp_path / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "xhs-key",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "标题",
                        "period_end": "2026-05-31",
                        "spend": 3000,
                    }
                ]
            )
            seen: dict[str, object] = {}

            def fake_runner(command, **kwargs):
                seen["command"] = command
                seen["cwd"] = kwargs.get("cwd")
                seen["env"] = kwargs.get("env") or {}
                input_path = Path(command[command.index("--input") + 1])
                out_path = Path(command[command.index("--out") + 1])
                run_root = Path(command[command.index("--root") + 1])
                asset_dir = run_root / "output" / "2026-05-31" / "xhs" / "note-1"
                asset_dir.mkdir(parents=True)
                (asset_dir / "cover.jpg").write_text("cover", encoding="utf-8")
                (asset_dir / "frame.jpg").write_text("frame", encoding="utf-8")
                self.assertTrue(input_path.exists())
                out_path.write_text(
                    json.dumps(
                        {
                            "items": [
                                {
                                    "job_id": json.loads(input_path.read_text(encoding="utf-8").splitlines()[0])["job_id"],
                                    "status": "succeeded",
                                    "platform": "小红书",
                                    "asset_dir": str(asset_dir),
                                    "cover_path": str(asset_dir / "cover.jpg"),
                                    "frames": [str(asset_dir / "frame.jpg")],
                                    "metadata": {"内容形态": "图文"},
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            old_cwd = Path.cwd()
            previous_crawl_headless = os.environ.get("CRAWL_BROWSER_HEADLESS")
            previous_fallback_headless = os.environ.get("MATERIAL_BROWSER_FALLBACK_HEADLESS")
            try:
                os.chdir(tmp_path)
                os.environ["CRAWL_BROWSER_HEADLESS"] = "0"
                os.environ["MATERIAL_BROWSER_FALLBACK_HEADLESS"] = "0"
                result = run_harvester_asset_capture(
                    db_path,
                    "batch-1",
                    top_content,
                    harvester_root=harvester_root,
                    runtime_root=Path("runtime"),
                    runner=fake_runner,
                )
            finally:
                os.chdir(old_cwd)
                if previous_crawl_headless is None:
                    os.environ.pop("CRAWL_BROWSER_HEADLESS", None)
                else:
                    os.environ["CRAWL_BROWSER_HEADLESS"] = previous_crawl_headless
                if previous_fallback_headless is None:
                    os.environ.pop("MATERIAL_BROWSER_FALLBACK_HEADLESS", None)
                else:
                    os.environ["MATERIAL_BROWSER_FALLBACK_HEADLESS"] = previous_fallback_headless

            self.assertTrue(result.ok)
            self.assertEqual(seen["cwd"], str(harvester_root.resolve()))
            self.assertEqual(seen["command"][:4], ["npm", "run", "materials:cache-topn", "--"])
            self.assertIn("--input", seen["command"])
            self.assertIn("--out", seen["command"])
            self.assertNotEqual(Path(seen["command"][seen["command"].index("--root") + 1]).resolve(), harvester_root.resolve())
            self.assertEqual(seen["env"]["HARVESTER_PROGRESS_LOGS"], "1")
            self.assertEqual(seen["env"]["CRAWL_BROWSER_HEADLESS"], "1")
            self.assertEqual(seen["env"]["MATERIAL_BROWSER_FALLBACK_HEADLESS"], "1")
            self.assertTrue(Path(seen["command"][seen["command"].index("--input") + 1]).is_absolute())
            self.assertTrue(Path(seen["command"][seen["command"].index("--out") + 1]).is_absolute())
            self.assertEqual(seen["command"][seen["command"].index("--target-date") + 1], "2026-05-31")
            jobs = list_harvester_asset_jobs(db_path, batch_id="batch-1")
            manifests = list_harvester_asset_manifests(db_path, batch_id="batch-1")
            cached_dir = tmp_path / ".runtime" / "top-assets" / "xhs" / "小红书_id_note-1"
            self.assertEqual(jobs.iloc[0]["status"], "succeeded")
            self.assertEqual(manifests.iloc[0]["asset_key"], "小红书::id::note-1")
            self.assertEqual(Path(manifests.iloc[0]["asset_dir"]).resolve(), cached_dir.resolve())
            self.assertEqual(Path(manifests.iloc[0]["cover_path"]).resolve(), (cached_dir / "cover.jpg").resolve())
            self.assertTrue((cached_dir / "frame.jpg").exists())
            manifest_payload = json.loads((cached_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(manifest_payload["items"][0]["asset_dir"]).resolve(), cached_dir.resolve())
            entries = list_top_asset_cache_entries(db_path)
            refs = list_top_asset_cache_refs(db_path, batch_id="batch-1")
            self.assertEqual(entries.iloc[0]["asset_key"], "小红书::id::note-1")
            self.assertEqual(len(refs), 1)

    def test_run_harvester_asset_capture_reports_progress_counts(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harvester_root = tmp_path / "harvester-THS"
            harvester_root.mkdir()
            (harvester_root / "package.json").write_text(
                json.dumps({"scripts": {"materials:cache-topn": "node src/cache-topn-materials.mjs"}}),
                encoding="utf-8",
            )
            db_path = tmp_path / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "小红书商业化::小红书::id::note-1",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "标题",
                        "period_end": "2026-05-31",
                        "spend": 3000,
                    }
                ]
            )
            progress_events = []

            def fake_runner(command, **kwargs):
                input_path = Path(command[command.index("--input") + 1])
                out_path = Path(command[command.index("--out") + 1])
                run_root = Path(command[command.index("--root") + 1])
                asset_dir = run_root / "output" / "2026-05-31" / "xhs" / "note-1"
                asset_dir.mkdir(parents=True)
                (asset_dir / "cover.jpg").write_text("cover", encoding="utf-8")
                out_path.write_text(
                    json.dumps(
                        {
                            "items": [
                                {
                                    "job_id": json.loads(input_path.read_text(encoding="utf-8").splitlines()[0])["job_id"],
                                    "status": "succeeded",
                                    "platform": "小红书",
                                    "asset_dir": str(asset_dir),
                                    "cover_path": str(asset_dir / "cover.jpg"),
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                stdout = '__HARVESTER_PROGRESS__{"platformId":"xhs","stage":"material","phase":"manifest","completed":1,"total":3,"action":"小红书素材 manifest 已写入"}\n'
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

            result = run_harvester_asset_capture(
                db_path,
                "batch-1",
                top_content,
                harvester_root=harvester_root,
                runtime_root=tmp_path / "runtime",
                runner=fake_runner,
                progress_callback=progress_events.append,
            )

            self.assertTrue(result.ok)
            self.assertEqual(progress_events[-1].completed, 1)
            self.assertEqual(progress_events[-1].total, 3)
            self.assertEqual(progress_events[-1].remaining_count, 2)
            self.assertIn("小红书素材", progress_events[-1].action)

    def test_run_command_with_progress_streams_events_before_process_exits(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            marker = tmp_path / "marker.txt"
            callback_marker_states: list[bool] = []
            events: list[HarvesterProgressEvent] = []
            script = (
                "import pathlib, sys, time; "
                "print('__HARVESTER_PROGRESS__{\"platformId\":\"douyin\",\"stage\":\"material\",\"phase\":\"prepare\",\"completed\":1,\"total\":2,\"action\":\"抖音素材准备\"}', flush=True); "
                "time.sleep(0.2); "
                f"pathlib.Path({str(marker)!r}).write_text(str(len(sys.argv)), encoding='utf-8'); "
                "time.sleep(0.2)"
            )

            def on_progress(event: HarvesterProgressEvent) -> None:
                callback_marker_states.append(marker.exists())
                events.append(event)

            completed = _run_command_with_progress(
                [os.sys.executable, "-c", script],
                cwd=tmp_path,
                env=os.environ,
                progress_callback=on_progress,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertTrue(marker.exists())
            self.assertEqual(callback_marker_states, [False])
            self.assertEqual(events[0].platform, "抖音")
            self.assertEqual(events[0].completed, 1)
            self.assertEqual(events[0].remaining_count, 1)

    def test_run_harvester_asset_capture_explains_login_expiry_by_platform(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harvester_root = tmp_path / "harvester-THS"
            harvester_root.mkdir()
            (harvester_root / "package.json").write_text(
                json.dumps({"scripts": {"materials:cache-topn": "node src/cache-topn-materials.mjs"}}),
                encoding="utf-8",
            )
            db_path = tmp_path / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "dy-key",
                        "content_id": "7594830477777751338",
                        "content_url": "https://www.douyin.com/video/7594830477777751338",
                        "title": "标题",
                        "spend": 3000,
                    }
                ]
            )

            def fake_runner(command, **kwargs):
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="抖音登录状态已失效，请先运行 npm run login:douyin 重新登录。",
                )

            result = run_harvester_asset_capture(
                db_path,
                "batch-1",
                top_content,
                harvester_root=harvester_root,
                runtime_root=tmp_path / "runtime",
                runner=fake_runner,
            )

            self.assertFalse(result.ok)
            self.assertIn("抖音登录状态失效", result.message)
            self.assertIn("npm run login:douyin", result.message)
            jobs = list_harvester_asset_jobs(db_path, batch_id="batch-1")
            self.assertIn("npm run login:douyin", jobs.iloc[0]["error_message"])

    def test_run_harvester_asset_capture_explains_manifest_item_login_failure(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harvester_root = tmp_path / "harvester-THS"
            harvester_root.mkdir()
            (harvester_root / "package.json").write_text(
                json.dumps({"scripts": {"materials:cache-topn": "node src/cache-topn-materials.mjs"}}),
                encoding="utf-8",
            )
            db_path = tmp_path / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "B站",
                        "channel": "B站",
                        "content_identity_key": "bili-key",
                        "content_id": "BV1abcde2345",
                        "content_url": "https://www.bilibili.com/video/BV1abcde2345/",
                        "title": "标题",
                        "spend": 3000,
                    }
                ]
            )

            def fake_runner(command, **kwargs):
                input_path = Path(command[command.index("--input") + 1])
                out_path = Path(command[command.index("--out") + 1])
                job_id = json.loads(input_path.read_text(encoding="utf-8").splitlines()[0])["job_id"]
                out_path.write_text(
                    json.dumps(
                        {
                            "items": [
                                {
                                    "job_id": job_id,
                                    "status": "failed",
                                    "platform": "B站",
                                    "asset_dir": "",
                                    "error_message": "B站登录态失效，请重新登录。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            result = run_harvester_asset_capture(
                db_path,
                "batch-1",
                top_content,
                harvester_root=harvester_root,
                runtime_root=tmp_path / "runtime",
                runner=fake_runner,
            )
            jobs = list_harvester_asset_jobs(db_path, batch_id="batch-1")

            self.assertFalse(result.ok)
            self.assertIn("失败 1 条", result.message)
            self.assertIn("npm run login:bilibili", jobs.iloc[0]["error_message"])

    def test_run_harvester_asset_capture_marks_empty_manifest_as_failed(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            harvester_root = tmp_path / "harvester-THS"
            harvester_root.mkdir()
            (harvester_root / "package.json").write_text(
                json.dumps({"scripts": {"materials:cache-topn": "node src/cache-topn-materials.mjs"}}),
                encoding="utf-8",
            )
            db_path = tmp_path / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "xhs-key",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "标题",
                        "period_end": "2026-05-31",
                        "spend": 3000,
                    }
                ]
            )

            def fake_runner(command, **kwargs):
                out_path = Path(command[command.index("--out") + 1])
                out_path.write_text(json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            result = run_harvester_asset_capture(
                db_path,
                "batch-1",
                top_content,
                harvester_root=harvester_root,
                runtime_root=tmp_path / "runtime",
                runner=fake_runner,
            )
            jobs = list_harvester_asset_jobs(db_path, batch_id="batch-1")

            self.assertFalse(result.ok)
            self.assertIn("失败 1 条", result.message)
            self.assertEqual(jobs.iloc[0]["status"], "failed")
            self.assertIn("manifest", jobs.iloc[0]["error_message"])

    def test_persist_snapshot_jobs_and_manifests_to_sqlite(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            init_db(db_path)

            snapshot_id = persist_feishu_ledger_snapshot(
                db_path,
                "batch-1",
                {
                    "enabled": True,
                    "fetched_at": "2026-06-15T00:00:00+00:00",
                    "total_rows": 10,
                    "platform_counts": {"抖音": 5},
                    "sheet_row_counts": {"sheet1": 10},
                    "field_completeness": {"content_id": 1.0},
                    "warnings": [],
                },
            )
            persist_harvester_asset_jobs(
                db_path,
                "batch-1",
                [{"job_id": "job-1", "platform": "抖音", "channel": "抖音商业化", "content_identity_key": "dy-key"}],
                status="queued",
                harvester_root=Path("/tmp/harvester"),
                jobs_path=Path("/tmp/jobs.jsonl"),
                manifest_path=Path("/tmp/manifest.json"),
            )
            persist_harvester_asset_manifests(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": "job-1",
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_dir": "/tmp/assets/job-1",
                        "cover_path": "/tmp/assets/job-1/cover.jpg",
                        "video_path": "/tmp/assets/job-1/video.mp4",
                        "screenshots": ["/tmp/assets/job-1/screenshot.png"],
                        "frames": ["/tmp/assets/job-1/frame.jpg"],
                        "metadata": {"title": "标题"},
                        "error_message": "",
                    }
                ],
            )

            jobs = list_harvester_asset_jobs(db_path, batch_id="batch-1")
            manifests = list_harvester_asset_manifests(db_path, batch_id="batch-1")
            with closing(sqlite3.connect(db_path)) as conn:
                snapshot = conn.execute(
                    "select snapshot_id, total_rows from feishu_ledger_snapshots where batch_id = ?",
                    ("batch-1",),
                ).fetchone()

            self.assertEqual(snapshot[0], snapshot_id)
            self.assertEqual(snapshot[1], 10)
            self.assertEqual(jobs.iloc[0]["status"], "queued")
            self.assertEqual(manifests.iloc[0]["asset_dir"], "/tmp/assets/job-1")

    def test_harvester_manifest_persistence_migrates_existing_table_without_asset_key(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    create table harvester_asset_manifests (
                        job_id text not null default '',
                        batch_id text not null default '',
                        status text not null default '',
                        platform text not null default '',
                        asset_dir text not null default '',
                        cover_path text not null default '',
                        video_path text not null default '',
                        screenshots_json text not null default '[]',
                        frames_json text not null default '[]',
                        metadata_json text not null default '{}',
                        error_message text not null default '',
                        created_at text not null default '',
                        updated_at text not null default '',
                        primary key (batch_id, job_id)
                    )
                    """
                )
                conn.commit()

            persist_harvester_asset_manifests(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": "job-1",
                        "status": "succeeded",
                        "platform": "小红书",
                        "asset_key": "小红书::id::note-1",
                        "asset_dir": "/tmp/assets/note-1",
                    }
                ],
            )

            manifests = list_harvester_asset_manifests(db_path, batch_id="batch-1")

            self.assertEqual(manifests.iloc[0]["asset_key"], "小红书::id::note-1")
            self.assertEqual(manifests.iloc[0]["asset_dir"], "/tmp/assets/note-1")

    def test_harvester_jobs_and_manifests_keep_same_asset_across_batches(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            job = {
                "job_id": "same-harvester-job",
                "platform": "B站",
                "channel": "B站市场部",
                "content_identity_key": "B站市场部::B站::id::BV1same",
                "content_id": "BV1same",
                "content_url": "https://www.bilibili.com/video/BV1same/",
                "title": "同一素材",
                "period_end": "2026-05-31",
            }
            manifest = {
                "job_id": "same-harvester-job",
                "status": "succeeded",
                "platform": "B站",
                "asset_dir": "/tmp/assets/BV1same",
                "video_path": "/tmp/assets/BV1same/BV1same.mp4",
            }

            persist_harvester_asset_jobs(
                db_path,
                "batch-old",
                [job],
                status="succeeded",
                harvester_root=Path("/tmp/harvester"),
                jobs_path=Path("/tmp/old/jobs.jsonl"),
                manifest_path=Path("/tmp/old/manifest.json"),
            )
            persist_harvester_asset_manifests(db_path, "batch-old", [manifest])
            persist_harvester_asset_jobs(
                db_path,
                "batch-new",
                [dict(job, period_end="2026-06-04")],
                status="succeeded",
                harvester_root=Path("/tmp/harvester"),
                jobs_path=Path("/tmp/new/jobs.jsonl"),
                manifest_path=Path("/tmp/new/manifest.json"),
            )
            persist_harvester_asset_manifests(db_path, "batch-new", [manifest])

            old_jobs = list_harvester_asset_jobs(db_path, batch_id="batch-old")
            new_jobs = list_harvester_asset_jobs(db_path, batch_id="batch-new")
            old_manifests = list_harvester_asset_manifests(db_path, batch_id="batch-old")
            new_manifests = list_harvester_asset_manifests(db_path, batch_id="batch-new")

            self.assertEqual(len(old_jobs), 1)
            self.assertEqual(len(new_jobs), 1)
            self.assertEqual(old_jobs.iloc[0]["manifest_path"], "/tmp/old/manifest.json")
            self.assertEqual(new_jobs.iloc[0]["manifest_path"], "/tmp/new/manifest.json")
            self.assertEqual(len(old_manifests), 1)
            self.assertEqual(len(new_manifests), 1)
            self.assertEqual(old_manifests.iloc[0]["asset_dir"], "/tmp/assets/BV1same")
            self.assertEqual(new_manifests.iloc[0]["asset_dir"], "/tmp/assets/BV1same")

    def test_feishu_assets_upsert_non_blank_fields_and_preserve_existing_when_blank(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            first = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "旧标题",
                        "account": "",
                        "tags": "股票,投教",
                        "raw_content_type": "图文",
                        "category_l1": "投教",
                        "category_l2": "方法论",
                        "content_type": "方法论",
                        "published_date": "2026/06/01",
                        "source_file": "feishu",
                        "source_sheet": "小红书",
                        "source_row": 2,
                    }
                ]
            )
            second = pd.DataFrame(
                [
                    {
                        "platform": "小红书",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "新标题",
                        "account": "",
                        "tags": "",
                        "raw_content_type": "",
                        "category_l1": "投教",
                        "category_l2": "选题拆解",
                        "content_type": "选题拆解",
                        "published_date": "2026-06-02",
                        "source_file": "feishu",
                        "source_sheet": "小红书",
                        "source_row": 2,
                    }
                ]
            )

            upsert_content_assets_from_feishu(db_path, "batch-1", first)
            upsert_content_assets_from_feishu(db_path, "batch-2", second)
            assets = list_local_content_assets(db_path)

            self.assertEqual(len(assets), 1)
            row = assets.iloc[0]
            self.assertEqual(row["title"], "新标题")
            self.assertEqual(row["account"], "")
            self.assertEqual(row["tags"], "股票,投教")
            self.assertEqual(row["category_l2"], "选题拆解")
            self.assertEqual(row["published_date"], "2026-06-02")
            self.assertEqual(row["last_seen_batch_id"], "batch-2")

    def test_build_asset_jobs_to_capture_skips_successful_existing_manifest(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "B站",
                        "channel": "B站",
                        "content_identity_key": "B站::B站::id::BV1abc",
                        "content_id": "BV1abc",
                        "title": "已抓取",
                        "spend": 100,
                    },
                    {
                        "platform": "B站",
                        "channel": "B站",
                        "content_identity_key": "B站::B站::id::BV2new",
                        "content_id": "BV2new",
                        "title": "待抓取",
                        "spend": 200,
                    },
                ]
            )
            all_jobs = build_asset_jobs("batch-1", top_content)
            persist_harvester_asset_jobs(
                db_path,
                "old-batch",
                [all_jobs[0]],
                status="succeeded",
                harvester_root=Path("/tmp/harvester"),
                jobs_path=Path("/tmp/jobs.jsonl"),
                manifest_path=Path("/tmp/manifest.json"),
            )
            persist_harvester_asset_manifests(
                db_path,
                "old-batch",
                [
                    {
                        "job_id": all_jobs[0]["job_id"],
                        "status": "succeeded",
                        "platform": "B站",
                        "asset_dir": "/tmp/assets/BV1abc",
                    }
                ],
            )

            pending = build_asset_jobs_to_capture(db_path, "batch-1", top_content)

            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["content_id"], "BV2new")

    def test_cache_existing_harvester_assets_copies_harvester_source_to_ops_cache(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            source_dir = tmp_path / "harvester-THS" / "output" / "2026-05-31" / "bilibili" / "BV1same"
            source_dir.mkdir(parents=True)
            (source_dir / "BV1same.mp4").write_text("video", encoding="utf-8")
            (source_dir / "BV1same.jpg").write_text("cover", encoding="utf-8")
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "B站",
                        "channel": "B站市场部",
                        "content_identity_key": "B站市场部::B站::id::BV1same",
                        "content_id": "BV1same",
                        "content_url": "https://www.bilibili.com/video/BV1same/",
                        "title": "同一素材",
                        "period_end": "2026-06-04",
                        "spend": 3000,
                    }
                ]
            )
            old_job = build_asset_jobs("old-batch", top_content)[0]
            persist_harvester_asset_jobs(
                db_path,
                "old-batch",
                [old_job],
                status="succeeded",
                harvester_root=tmp_path / "harvester-THS",
                jobs_path=tmp_path / "old" / "jobs.jsonl",
                manifest_path=tmp_path / "old" / "manifest.json",
            )
            persist_harvester_asset_manifests(
                db_path,
                "old-batch",
                [
                    {
                        "job_id": old_job["job_id"],
                        "status": "succeeded",
                        "platform": "B站",
                        "asset_dir": str(source_dir),
                        "cover_path": str(source_dir / "BV1same.jpg"),
                        "video_path": str(source_dir / "BV1same.mp4"),
                        "screenshots": [str(source_dir / "BV1same.jpg")],
                        "metadata": {"content_type": "视频"},
                    }
                ],
            )

            reused = cache_existing_harvester_assets_for_batch(
                db_path,
                "new-batch",
                top_content,
                cache_root=tmp_path / ".runtime" / "top-assets",
                harvester_root=tmp_path / "harvester-THS",
                jobs_path=tmp_path / "new" / "jobs.jsonl",
                manifest_path=tmp_path / "new" / "manifest.json",
            )

            jobs = list_harvester_asset_jobs(db_path, batch_id="new-batch")
            manifests = list_harvester_asset_manifests(db_path, batch_id="new-batch")
            cached_dir = tmp_path / ".runtime" / "top-assets" / "bilibili" / "B站_id_BV1same"
            self.assertEqual(reused, 1)
            self.assertTrue(cached_dir.exists())
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs.iloc[0]["status"], "succeeded")
            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests.iloc[0]["asset_key"], "B站::id::BV1same")
            self.assertEqual(Path(manifests.iloc[0]["asset_dir"]).resolve(), cached_dir.resolve())
            self.assertEqual(Path(manifests.iloc[0]["video_path"]).resolve(), (cached_dir / "BV1same.mp4").resolve())
            self.assertTrue((cached_dir / "BV1same.mp4").exists())
            self.assertTrue((cached_dir / "BV1same.jpg").exists())
            self.assertIn("已复制到本项目缓存", manifests.iloc[0]["metadata_json"])
            entries = list_top_asset_cache_entries(db_path)
            self.assertGreater(int(entries.iloc[0]["size_bytes"]), 0)
            batch_manifest_text = (tmp_path / "new" / "manifest.json").read_text(encoding="utf-8")
            batch_manifest = json.loads(batch_manifest_text)
            self.assertEqual(Path(batch_manifest["items"][0]["asset_dir"]).resolve(), cached_dir.resolve())
            self.assertEqual(Path(batch_manifest["items"][0]["video_path"]).resolve(), (cached_dir / "BV1same.mp4").resolve())

    def test_cache_existing_harvester_assets_prefers_daily_cache_before_topn_capture(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            harvester_root = tmp_path / "harvester-THS"
            source_dir = harvester_root / "output" / "2026-06-11" / "douyin" / "7594830477777751338"
            source_dir.mkdir(parents=True)
            (source_dir / "7594830477777751338.mp4").write_text("video", encoding="utf-8")
            (source_dir / "001.jpg").write_text("frame", encoding="utf-8")
            (source_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "platformId": "douyin",
                        "id": "7594830477777751338",
                        "dir": str(source_dir),
                        "ok": True,
                        "videoPath": str(source_dir / "7594830477777751338.mp4"),
                        "framePaths": [str(source_dir / "001.jpg")],
                        "metadata": {"category_l1": "投教", "category_l2": "股票入门"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::id::7594830477777751338",
                        "content_id": "7594830477777751338",
                        "content_url": "https://www.douyin.com/video/7594830477777751338",
                        "title": "每日缓存素材",
                        "period_end": "2026-06-11",
                        "spend": 3000,
                    }
                ]
            )
            job = build_asset_jobs("batch-1", top_content)[0]

            reused = cache_existing_harvester_assets_for_batch(
                db_path,
                "batch-1",
                top_content,
                cache_root=tmp_path / ".runtime" / "top-assets",
                harvester_root=harvester_root,
                jobs_path=tmp_path / "jobs.jsonl",
                manifest_path=tmp_path / "manifest.json",
            )
            pending = build_asset_jobs_to_capture(db_path, "batch-1", top_content)
            manifests = list_harvester_asset_manifests(db_path, batch_id="batch-1")
            cached_dir = tmp_path / ".runtime" / "top-assets" / "douyin" / "抖音_id_7594830477777751338"

            self.assertEqual(reused, 1)
            self.assertEqual(pending, [])
            self.assertEqual(manifests.iloc[0]["asset_key"], "抖音::id::7594830477777751338")
            self.assertEqual(Path(manifests.iloc[0]["asset_dir"]).resolve(), cached_dir.resolve())
            self.assertTrue(cached_dir.exists())
            self.assertIn("harvester每日缓存", manifests.iloc[0]["metadata_json"])

    def test_cache_existing_harvester_assets_is_idempotent_when_manifest_already_points_to_ops_cache(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            cache_root = tmp_path / ".runtime" / "top-assets"
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::id::7594830477777751338",
                        "content_id": "7594830477777751338",
                        "content_url": "https://www.douyin.com/video/7594830477777751338",
                        "title": "已缓存素材",
                        "period_end": "2026-06-11",
                        "spend": 3000,
                    }
                ]
            )
            job = build_asset_jobs("batch-1", top_content)[0]
            cached_dir = cache_root / "douyin" / "抖音_id_7594830477777751338"
            cached_dir.mkdir(parents=True)
            (cached_dir / "7594830477777751338.mp4").write_text("video", encoding="utf-8")
            (cached_dir / "7594830477777751338.jpeg").write_text("cover", encoding="utf-8")
            persist_harvester_asset_jobs(
                db_path,
                "batch-1",
                [job],
                status="succeeded",
                harvester_root=tmp_path / "harvester-THS",
                jobs_path=tmp_path / "jobs.jsonl",
                manifest_path=tmp_path / "manifest.json",
            )
            persist_harvester_asset_manifests(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": job["job_id"],
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_dir": str(cached_dir),
                        "cover_path": str(cached_dir / "7594830477777751338.jpeg"),
                        "video_path": str(cached_dir / "7594830477777751338.mp4"),
                        "metadata": {"ops_cache_note": "复用已采集素材"},
                    }
                ],
            )

            reused = cache_existing_harvester_assets_for_batch(
                db_path,
                "batch-1",
                top_content,
                cache_root=cache_root,
                harvester_root=tmp_path / "harvester-THS",
                jobs_path=tmp_path / "jobs.jsonl",
                manifest_path=tmp_path / "manifest.json",
            )

            manifests = list_harvester_asset_manifests(db_path, batch_id="batch-1")
            self.assertEqual(reused, 1)
            self.assertTrue((cached_dir / "7594830477777751338.mp4").exists())
            self.assertTrue((cached_dir / "7594830477777751338.jpeg").exists())
            self.assertEqual(Path(manifests.iloc[0]["asset_dir"]).resolve(), cached_dir.resolve())
            self.assertEqual(Path(manifests.iloc[0]["cover_path"]).resolve(), (cached_dir / "7594830477777751338.jpeg").resolve())

    def test_cache_existing_harvester_assets_copies_current_batch_harvester_output_to_ops_cache(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "workflow.sqlite3"
            source_dir = tmp_path / "harvester-THS" / "output" / "2026-06-11" / "douyin" / "7594830477777751338"
            source_dir.mkdir(parents=True)
            (source_dir / "7594830477777751338.mp4").write_text("video", encoding="utf-8")
            (source_dir / "7594830477777751338.jpeg").write_text("cover", encoding="utf-8")
            top_content = pd.DataFrame(
                [
                    {
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::id::7594830477777751338",
                        "content_id": "7594830477777751338",
                        "content_url": "https://www.douyin.com/video/7594830477777751338",
                        "title": "本周期已采集素材",
                        "period_end": "2026-06-11",
                        "spend": 3000,
                    }
                ]
            )
            job = build_asset_jobs("batch-1", top_content)[0]
            persist_harvester_asset_jobs(
                db_path,
                "batch-1",
                [job],
                status="succeeded",
                harvester_root=tmp_path / "harvester-THS",
                jobs_path=tmp_path / "old" / "jobs.jsonl",
                manifest_path=tmp_path / "old" / "manifest.json",
            )
            persist_harvester_asset_manifests(
                db_path,
                "batch-1",
                [
                    {
                        "job_id": job["job_id"],
                        "status": "succeeded",
                        "platform": "抖音",
                        "asset_dir": str(source_dir),
                        "cover_path": str(source_dir / "7594830477777751338.jpeg"),
                        "video_path": str(source_dir / "7594830477777751338.mp4"),
                    }
                ],
            )

            reused = cache_existing_harvester_assets_for_batch(
                db_path,
                "batch-1",
                top_content,
                cache_root=tmp_path / ".runtime" / "top-assets",
                harvester_root=tmp_path / "harvester-THS",
                jobs_path=tmp_path / "new" / "jobs.jsonl",
                manifest_path=tmp_path / "new" / "manifest.json",
            )

            manifests = list_harvester_asset_manifests(db_path, batch_id="batch-1")
            cached_dir = tmp_path / ".runtime" / "top-assets" / "douyin" / "抖音_id_7594830477777751338"
            self.assertEqual(reused, 1)
            self.assertEqual(Path(manifests.iloc[0]["asset_dir"]).resolve(), cached_dir.resolve())
            self.assertTrue(cached_dir.exists())
            self.assertEqual(Path(manifests.iloc[0]["video_path"]).resolve(), (cached_dir / "7594830477777751338.mp4").resolve())

    def test_content_performance_items_merge_feishu_fields_and_cleaned_metrics(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::title_account::示例账号::标题",
                        "content_id": "dy-1",
                        "work_id": "dy-1",
                        "content_url": "https://www.douyin.com/video/dy-1",
                        "work_url": "https://www.douyin.com/video/dy-1",
                        "title": "标题",
                        "account": "",
                        "matched_account": "示例账号",
                        "matched_category_l1": "投教",
                        "matched_category_l2": "方法论",
                        "matched_content_type": "方法论",
                        "match_status": "已匹配",
                        "match_source": "标准标题",
                        "match_confidence": 0.95,
                        "spend": 1000,
                        "impressions": 10000,
                        "clicks": 100,
                        "activations": 10,
                        "first_pay_count": 2,
                        "activation_cost": 100,
                        "first_pay_cost": 500,
                        "ctr": 0.01,
                        "source_file": "raw.xlsx",
                        "source_sheet": "sheet1",
                        "source_row": 2,
                    },
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::title_account::示例账号::标题",
                        "content_id": "dy-1",
                        "work_id": "dy-1",
                        "content_url": "https://www.douyin.com/video/dy-1",
                        "title": "标题",
                        "account": "",
                        "matched_account": "示例账号",
                        "matched_category_l1": "投教",
                        "matched_category_l2": "方法论",
                        "matched_content_type": "方法论",
                        "match_status": "已匹配",
                        "match_source": "标准标题",
                        "match_confidence": 0.95,
                        "spend": 500,
                        "impressions": 5000,
                        "clicks": 50,
                        "activations": 5,
                        "first_pay_count": 1,
                        "activation_cost": 100,
                        "first_pay_cost": 500,
                        "ctr": 0.01,
                        "source_file": "raw.xlsx",
                        "source_sheet": "sheet2",
                        "source_row": 3,
                    },
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            persist_content_performance_items(db_path, "batch-1", canonical)
            items = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(len(items), 1)
            row = items.iloc[0]
            self.assertEqual(row["account"], "")
            self.assertEqual(row["category_l1"], "投教")
            self.assertEqual(row["category_l2"], "方法论")
            self.assertEqual(row["content_type"], "方法论")
            self.assertEqual(float(row["spend"]), 1500.0)
            self.assertEqual(float(row["impressions"]), 15000.0)
            self.assertEqual(float(row["activations"]), 15.0)
            self.assertEqual(float(row["first_pay_count"]), 3.0)
            self.assertEqual(int(row["merged_row_count"]), 2)

    def test_content_performance_items_use_matched_title_when_bilibili_title_is_bv_placeholder(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "B站",
                        "channel": "B站市场部",
                        "content_identity_key": "B站市场部::B站::id::BV1abcde2345",
                        "content_id": "BV1abcde2345",
                        "work_id": "BV1abcde2345",
                        "content_url": "https://www.bilibili.com/video/BV1abcde2345/",
                        "title": "BV1abcde2345",
                        "matched_ledger_title": "真实B站标题",
                        "matched_account": "投资号",
                        "category_l1": "不应保留",
                        "category_l2": "不应保留",
                        "content_category": "不应保留",
                        "matched_bilibili_content_type": "采访内容",
                        "matched_content_type": "采访内容",
                        "match_status": "已匹配",
                        "match_source": "BV号",
                        "match_confidence": 1.0,
                        "spend": 1000,
                        "impressions": 10000,
                        "clicks": 100,
                        "activations": 10,
                        "first_pay_count": 2,
                    }
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            items = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(len(items), 1)
            self.assertEqual(items.iloc[0]["title"], "真实B站标题")
            self.assertEqual(items.iloc[0]["category_l1"], "")
            self.assertEqual(items.iloc[0]["category_l2"], "")
            self.assertEqual(items.iloc[0]["bilibili_content_type"], "采访内容")

    def test_content_performance_items_clear_bilibili_type_for_douyin_and_xhs(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::id::dy-1",
                        "content_id": "dy-1",
                        "content_url": "https://www.douyin.com/video/dy-1",
                        "title": "抖音标题",
                        "matched_category_l1": "股友说",
                        "matched_category_l2": "股民教学",
                        "matched_bilibili_content_type": "不应保留",
                        "matched_content_type": "股民教学",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 10,
                        "first_pay_count": 2,
                    },
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "小红书商业化::小红书::id::note-1",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "小红书标题",
                        "matched_category_l1": "图文",
                        "matched_category_l2": "理财方法",
                        "matched_bilibili_content_type": "不应保留",
                        "matched_content_type": "理财方法",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 10,
                        "first_pay_count": 2,
                    },
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            items = list_content_performance_items(db_path, batch_id="batch-1").sort_values("platform").reset_index(drop=True)

            self.assertEqual(set(items["platform"]), {"小红书", "抖音"})
            self.assertTrue(items["bilibili_content_type"].eq("").all())
            self.assertEqual(items.loc[items["platform"].eq("抖音"), "category_l1"].iloc[0], "股友说")
            self.assertEqual(items.loc[items["platform"].eq("小红书"), "category_l2"].iloc[0], "理财方法")

    def test_content_performance_items_extract_tags_from_title_when_tags_missing(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::id::dy-1",
                        "content_id": "dy-1",
                        "content_url": "https://www.douyin.com/video/dy-1",
                        "title": "为什么说股市就是看清自己最好的地方？ #财经 #同花顺资讯 #股市",
                        "tags": "",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 10,
                        "first_pay_count": 2,
                    }
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            items = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(items.iloc[0]["title"], "为什么说股市就是看清自己最好的地方？")
            self.assertEqual(items.iloc[0]["tags"], "#财经 #同花顺资讯 #股市")

    def test_content_performance_items_backfill_account_from_local_asset_table(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            upsert_content_assets_from_feishu(
                db_path,
                "ledger-batch",
                pd.DataFrame(
                    [
                        {
                            "platform": "抖音",
                            "content_id": "dy-1",
                            "content_url": "https://www.douyin.com/video/dy-1",
                            "title": "投教标题",
                            "account": "投资号",
                        }
                    ]
                ),
            )
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "抖音",
                        "channel": "抖音商业化",
                        "content_identity_key": "抖音商业化::抖音::id::dy-1",
                        "content_id": "dy-1",
                        "content_url": "https://www.douyin.com/video/dy-1",
                        "title": "投教标题",
                        "account": "",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 10,
                        "first_pay_count": 2,
                    }
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            items = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(items.iloc[0]["account"], "投资号")

    def test_content_performance_items_backfill_xhs_fields_when_title_is_link(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            upsert_content_assets_from_feishu(
                db_path,
                "ledger-batch",
                pd.DataFrame(
                    [
                        {
                            "platform": "小红书",
                            "content_id": "note-1",
                            "content_url": "https://www.xiaohongshu.com/explore/note-1",
                            "title": "真实小红书标题",
                            "account": "福利官",
                            "category_l1": "图文",
                            "category_l2": "理财方法",
                            "content_type": "理财方法",
                        }
                    ]
                ),
            )
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "小红书商业化::小红书::id::note-1",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1?xsec_source=pc_ad_export",
                        "title": "https://www.xiaohongshu.com/explore/note-1?xsec_source=pc_ad_export",
                        "account": "",
                        "category_l1": "",
                        "category_l2": "",
                        "content_type": "",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 10,
                        "first_pay_count": 2,
                    }
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            items = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(items.iloc[0]["title"], "真实小红书标题")
            self.assertEqual(items.iloc[0]["account"], "福利官")
            self.assertEqual(items.iloc[0]["category_l1"], "图文")
            self.assertEqual(items.iloc[0]["category_l2"], "理财方法")
            self.assertEqual(items.iloc[0]["bilibili_content_type"], "")

    def test_content_performance_items_backfill_match_fields_by_platform_id(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "去重后会变化的明细键",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "https://www.xiaohongshu.com/explore/note-1",
                        "account": "",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 10,
                        "first_pay_count": 2,
                    }
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    insert into asset_match_results (
                        batch_id, period_start, period_end, platform, channel,
                        content_identity_key, content_id, title, matched_ledger_title,
                        content_url, match_status, match_source, match_key,
                        matched_category_l1, matched_category_l2,
                        matched_content_type, matched_account
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "batch-1",
                        "2026-06-01",
                        "2026-06-07",
                        "小红书",
                        "小红书商业化",
                        "上传源匹配键",
                        "note-1",
                        "",
                        "匹配表真实标题",
                        "https://www.xiaohongshu.com/explore/note-1",
                        "已匹配",
                        "作品ID",
                        "note-1",
                        "图文",
                        "理财方法",
                        "理财方法",
                        "福利官",
                    ),
                )
                conn.commit()

            items = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(items.iloc[0]["title"], "匹配表真实标题")
            self.assertEqual(items.iloc[0]["account"], "")
            self.assertEqual(items.iloc[0]["category_l1"], "图文")
            self.assertEqual(items.iloc[0]["category_l2"], "理财方法")

    def test_content_performance_items_clear_xhs_link_title_when_no_real_title(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            canonical = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "小红书商业化::小红书::id::note-1",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "https://www.xiaohongshu.com/explore/note-1",
                        "spend": 1000,
                        "impressions": 10000,
                        "activations": 10,
                        "first_pay_count": 2,
                    }
                ]
            )

            persist_content_performance_items(db_path, "batch-1", canonical)
            items = list_content_performance_items(db_path, batch_id="batch-1")

            self.assertEqual(items.iloc[0]["title"], "")
            self.assertEqual(items.iloc[0]["content_url"], "https://www.xiaohongshu.com/explore/note-1")

    def test_same_content_keeps_separate_period_performance_rows(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workflow.sqlite3"
            first_period = pd.DataFrame(
                [
                    {
                        "period_start": "2026-06-01",
                        "period_end": "2026-06-07",
                        "platform": "小红书",
                        "channel": "小红书商业化",
                        "content_identity_key": "小红书商业化::小红书::id::note-1",
                        "content_id": "note-1",
                        "content_url": "https://www.xiaohongshu.com/explore/note-1",
                        "title": "同一素材",
                        "account": "示例账号",
                        "matched_category_l1": "投教",
                        "matched_category_l2": "方法论",
                        "matched_content_type": "方法论",
                        "spend": 100,
                        "impressions": 1000,
                        "clicks": 10,
                        "activations": 1,
                        "first_pay_count": 0,
                    }
                ]
            )
            second_period = first_period.copy()
            second_period["period_start"] = "2026-06-08"
            second_period["period_end"] = "2026-06-14"
            second_period["spend"] = 300
            second_period["impressions"] = 3000
            second_period["activations"] = 3

            persist_content_performance_items(db_path, "batch-week-1", first_period)
            persist_content_performance_items(db_path, "batch-week-2", second_period)
            items = list_content_performance_items(db_path)

            self.assertEqual(len(items), 2)
            by_batch = items.set_index("batch_id")
            self.assertEqual(float(by_batch.loc["batch-week-1", "spend"]), 100.0)
            self.assertEqual(float(by_batch.loc["batch-week-2", "spend"]), 300.0)
            self.assertEqual(float(by_batch.loc["batch-week-2", "activations"]), 3.0)


if __name__ == "__main__":
    unittest.main()
