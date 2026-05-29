from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from ops_data_workflow.account_filters import apply_account_filters, load_account_filter_config


class AccountFilterTests(unittest.TestCase):
    def test_missing_config_uses_default_xiaohongshu_rules(self):
        with TemporaryDirectory() as tmp:
            config = load_account_filter_config(Path(tmp) / "missing.yml")

            included = config.evaluate("小红书商业化", "同顺股民社区")
            short_alias = config.evaluate("小红书商业化", "股民社区")
            excluded = config.evaluate("小红书商业化", "同花顺APP")
            blank = config.evaluate("小红书市场部", "")
            douyin_blank = config.evaluate("抖音市场部", "")
            douyin_short_alias = config.evaluate("抖音商业化", "股民社区")
            bilibili_mid = config.evaluate("B站", "", "1622777305")
            bilibili_short_alias = config.evaluate("B站", "投资号")

            self.assertTrue(included.included)
            self.assertEqual(included.normalized_account, "同顺股民社区")
            self.assertTrue(short_alias.included)
            self.assertEqual(short_alias.normalized_account, "同顺股民社区")
            self.assertFalse(excluded.included)
            self.assertEqual(excluded.reason, "不在小红书账号白名单")
            self.assertTrue(blank.included)
            self.assertFalse(blank.scoped)
            self.assertEqual(blank.reason, "")
            self.assertFalse(douyin_blank.scoped)
            self.assertTrue(douyin_blank.included)
            self.assertTrue(douyin_short_alias.included)
            self.assertFalse(douyin_short_alias.scoped)
            self.assertEqual(douyin_short_alias.normalized_account, "股民社区")
            self.assertTrue(bilibili_mid.included)
            self.assertFalse(bilibili_mid.scoped)
            self.assertTrue(bilibili_short_alias.included)
            self.assertFalse(bilibili_short_alias.scoped)
            self.assertEqual(bilibili_short_alias.normalized_account, "投资号")
            self.assertEqual(
                set(config.to_frame()["platform"]),
                {"小红书", "抖音", "B站"},
            )
            self.assertEqual(
                config.expected_accounts_by_platform()["小红书"],
                ["同花顺投资", "同顺股民社区", "同花顺理财", "同顺财经", "问财", "喵懂投资"],
            )
            blank_rule = config.to_frame()
            blank_rule = blank_rule[
                (blank_rule["platform"].eq("小红书")) & (blank_rule["rule_type"].eq("空账号策略"))
            ].iloc[0]
            self.assertTrue(blank_rule["included"])
            self.assertEqual(blank_rule["status"], "默认记录")

    def test_apply_filters_keeps_rows_without_raw_account_when_column_exists(self):
        with TemporaryDirectory() as tmp:
            config = load_account_filter_config(Path(tmp) / "missing.yml")
            canonical = pd.DataFrame(
                [
                    {
                        "channel": "小红书商业化",
                        "account_raw": "",
                        "account": "同花顺投资",
                        "author": "同花顺投资",
                        "spend": 10,
                    },
                    {
                        "channel": "小红书商业化",
                        "account_raw": "股民社区",
                        "account": "股民社区",
                        "author": "股民社区",
                        "spend": 20,
                    },
                    {
                        "channel": "B站",
                        "account_raw": "",
                        "account_id": "1622777305.0",
                        "account": "同花顺投资",
                        "author": "同花顺投资",
                        "spend": 30,
                    },
                    {
                        "channel": "抖音商业化",
                        "account_raw": "股民社区",
                        "account": "股民社区",
                        "author": "股民社区",
                        "spend": 40,
                    },
                ]
            )

            filtered, details = apply_account_filters(canonical, config)

            self.assertEqual(set(filtered["channel"]), {"小红书商业化", "B站", "抖音商业化"})
            xhs_rows = filtered[filtered["channel"].eq("小红书商业化")]
            bilibili = filtered[filtered["channel"].eq("B站")].iloc[0]
            douyin = filtered[filtered["channel"].eq("抖音商业化")].iloc[0]
            self.assertEqual(set(xhs_rows["account"]), {"同花顺投资", "同顺股民社区"})
            self.assertEqual(bilibili["account"], "同花顺投资")
            self.assertEqual(douyin["account"], "股民社区")
            self.assertTrue(details.empty)

    def test_three_platform_config_keeps_disabled_platforms_unfiltered(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "account_filters.yml"
            config_path.write_text(
                """
platforms:
  xiaohongshu:
    display_name: 小红书
    match_channels: [小红书]
    filter_enabled: true
    include_accounts: [股民社区]
    aliases:
      同顺股民社区: 股民社区
    exclude_blank: true
  douyin:
    display_name: 抖音
    match_channels: [抖音]
    filter_enabled: false
    include_accounts: [同花顺投资]
    aliases: {}
    exclude_blank: false
  bilibili:
    display_name: B站
    match_channels: [B站]
    filter_enabled: false
    include_accounts: [同花顺投资]
    aliases: {}
    exclude_blank: false
""".strip(),
                encoding="utf-8",
            )
            config = load_account_filter_config(config_path)
            canonical = pd.DataFrame(
                [
                    {"channel": "小红书商业化", "account": "同顺股民社区", "spend": 10},
                    {"channel": "小红书商业化", "account": "同花顺APP", "spend": 99},
                    {"channel": "抖音商业化", "account": "", "spend": 88},
                    {"channel": "B站", "account": "", "spend": 77},
                ]
            )

            filtered, details = apply_account_filters(canonical, config)

            self.assertEqual(set(filtered["channel"]), {"小红书商业化", "抖音商业化", "B站"})
            self.assertEqual(filtered.loc[filtered["channel"].eq("小红书商业化"), "account"].iloc[0], "股民社区")
            self.assertEqual(filtered.loc[filtered["channel"].eq("抖音商业化"), "account"].iloc[0], "")
            self.assertEqual(filtered.loc[filtered["channel"].eq("B站"), "account"].iloc[0], "")
            self.assertEqual(len(details), 1)
            self.assertEqual(details.iloc[0]["account_raw"], "同花顺APP")
            self.assertEqual(
                config.expected_accounts_by_platform(),
                {"小红书": ["股民社区"], "抖音": ["同花顺投资"], "B站": ["同花顺投资"]},
            )

    def test_apply_filters_normalizes_and_keeps_excluded_audit_rows(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "account_filters.yml"
            config_path.write_text(
                """
xiaohongshu:
  include_accounts:
    - 股民社区
    - 研习社
  aliases:
    同顺股民社区: 股民社区
    同花顺研习社: 研习社
  exclude_blank: true
""".strip(),
                encoding="utf-8",
            )
            config = load_account_filter_config(config_path)
            canonical = pd.DataFrame(
                [
                    {
                        "channel": "小红书商业化",
                        "account_raw": "同顺股民社区",
                        "account": "同顺股民社区",
                        "author": "同顺股民社区",
                        "source_file": "小红书商业化.xlsx",
                        "source_sheet": "Sheet1",
                        "source_row": 2,
                        "spend": 10,
                        "activations": 1,
                        "first_pay_count": 1,
                    },
                    {
                        "channel": "小红书商业化",
                        "account_raw": "同花顺ETF",
                        "account": "同花顺ETF",
                        "author": "同花顺ETF",
                        "source_file": "小红书商业化.xlsx",
                        "source_sheet": "Sheet1",
                        "source_row": 3,
                        "spend": 99,
                        "activations": 9,
                        "first_pay_count": 3,
                    },
                    {
                        "channel": "抖音商业化",
                        "account_raw": "",
                        "account": "",
                        "author": "",
                        "source_file": "抖音商业化.xlsx",
                        "source_sheet": "Sheet1",
                        "source_row": 2,
                        "spend": 100,
                        "activations": 10,
                        "first_pay_count": 4,
                    },
                ]
            )

            filtered, details = apply_account_filters(canonical, config)

            self.assertEqual(len(filtered), 2)
            xhs_row = filtered[filtered["channel"].eq("小红书商业化")].iloc[0]
            self.assertEqual(xhs_row["account"], "股民社区")
            self.assertEqual(xhs_row["author"], "股民社区")
            self.assertEqual(xhs_row["account_filter_status"], "已统计")
            self.assertEqual(len(details), 1)
            self.assertEqual(details.iloc[0]["account_raw"], "同花顺ETF")
            self.assertEqual(details.iloc[0]["normalized_account"], "同花顺ETF")
            self.assertEqual(details.iloc[0]["filter_reason"], "不在小红书账号白名单")


if __name__ == "__main__":
    unittest.main()
