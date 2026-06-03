from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from ops_data_workflow.channel_profiles import load_channel_profiles, render_channel_profiles_table
from ops_data_workflow.source_channels import infer_channel_from_path


class ChannelProfileConfigTests(unittest.TestCase):
    def test_default_profiles_cover_required_channels_and_fields(self):
        profiles = load_channel_profiles()

        self.assertEqual(
            [profile.channel for profile in profiles.active_profiles()],
            ["小红书商业化", "小红书市场部", "抖音市场部", "抖音商业化", "B站", "微信市场部", "微信商业化"],
        )
        for profile in profiles.active_profiles():
            self.assertTrue(profile.channel)
            self.assertTrue(profile.platform)
            self.assertTrue(profile.group)
            self.assertTrue(profile.filename_keywords)
            self.assertIsInstance(profile.field_aliases, dict)
            self.assertIsInstance(profile.account_filter_enabled, bool)
            self.assertTrue(profile.active)

    def test_filename_keywords_are_loaded_from_yaml_without_breaking_legacy_results(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "channel_profiles.yml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "channels": [
                            {
                                "channel": "小红书商业化",
                                "platform": "小红书",
                                "group": "小红书",
                                "filename_keywords": ["XHS投流"],
                                "field_aliases": {"标题": ["笔记标题"]},
                                "account_filter_enabled": True,
                                "active": True,
                            }
                        ]
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )

            self.assertEqual(infer_channel_from_path("XHS投流-5月.xlsx", config_path=config_path), "小红书商业化")

        self.assertEqual(infer_channel_from_path("腾讯（市场部）.xlsx"), "微信市场部")
        self.assertEqual(infer_channel_from_path("视频号商业化.xlsx"), "微信商业化")
        self.assertEqual(infer_channel_from_path("抖音达人.csv"), "达人数据")
        self.assertEqual(infer_channel_from_path("抖音期货.csv"), "抖音期货通")
        self.assertEqual(infer_channel_from_path("快手投放.xlsx"), "快手投放")

    def test_new_platform_can_be_added_by_configuration_only(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "channel_profiles.yml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "channels": [
                            {
                                "channel": "快手市场部",
                                "platform": "快手",
                                "group": "快手",
                                "filename_keywords": ["快手投放", "KS市场"],
                                "field_aliases": {"标题": ["作品标题"], "消耗": ["花费"]},
                                "account_filter_enabled": False,
                                "active": True,
                            }
                        ]
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )

            profiles = load_channel_profiles(config_path)

            self.assertEqual(infer_channel_from_path("KS市场-周报.xlsx", config_path=config_path), "快手市场部")
            self.assertEqual(profiles.field_aliases_for_channel("快手市场部"), {"标题": ["作品标题"], "消耗": ["花费"]})

    def test_disabled_profiles_do_not_match_filenames(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "channel_profiles.yml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "channels": [
                            {
                                "channel": "快手市场部",
                                "platform": "快手",
                                "group": "快手",
                                "filename_keywords": ["快手投放"],
                                "field_aliases": {},
                                "account_filter_enabled": False,
                                "active": False,
                            }
                        ]
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )

            self.assertEqual(infer_channel_from_path("快手投放.xlsx", config_path=config_path), "快手投放")

    def test_profiles_have_business_facing_table_for_reference_page(self):
        frame = render_channel_profiles_table(load_channel_profiles())

        self.assertEqual(
            list(frame.columns),
            ["渠道", "平台", "平台组", "文件名关键词", "字段别名", "账号过滤", "启用状态"],
        )
        self.assertIn("小红书商业化", frame["渠道"].tolist())
        self.assertIn("启用", frame["启用状态"].tolist())
        self.assertTrue(frame["文件名关键词"].str.len().gt(0).all())

    def test_app_reference_page_exposes_channel_profile_description(self):
        app_source = Path("app.py").read_text(encoding="utf-8")
        reference_source = app_source[
            app_source.index("def _page_reference_tables") : app_source.index("def _content_review_type_options")
        ]

        self.assertIn("load_channel_profiles", app_source)
        self.assertIn("render_channel_profiles_table", app_source)
        self.assertIn("渠道配置说明", reference_source)
        self.assertIn("channel_profiles.yml", reference_source)
        self.assertIn("字段别名优先于通用字段映射", reference_source)
