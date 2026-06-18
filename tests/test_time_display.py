import unittest

import pandas as pd

from app import (
    _asset_cache_records_display,
    _local_content_assets_display,
    _recap_weight_updated_at_caption,
)
from ops_data_workflow.dashboard import format_beijing_datetime
from ops_data_workflow.reporting import localize_columns


class TimeDisplayTests(unittest.TestCase):
    def test_format_beijing_datetime_uses_compact_beijing_time(self):
        result = format_beijing_datetime("2026-05-19T01:02:03+00:00")

        self.assertEqual(result, "2026-05-19 09:02:03")

    def test_display_time_columns_use_compact_beijing_time(self):
        records = pd.DataFrame(
            [
                {
                    "status": "succeeded",
                    "platform": "小红书",
                    "asset_source": "harvester",
                    "has_cover": "有",
                    "has_video": "无",
                    "error_message": "",
                    "updated_at": "2026-06-18T05:38:59.985848+00:00",
                }
            ]
        )

        display = _asset_cache_records_display(records)

        self.assertEqual(display.iloc[0]["updated_at"], "2026-06-18 13:38:59")

    def test_localized_time_columns_use_compact_beijing_time(self):
        assets = pd.DataFrame(
            [
                {
                    "asset_key": "小红书::id::note-1",
                    "platform": "小红书",
                    "content_id": "note-1",
                    "title": "标题",
                    "updated_at": "2026-06-02T00:00:00+00:00",
                }
            ]
        )

        display = localize_columns(_local_content_assets_display(assets))

        self.assertEqual(display.iloc[0]["更新时间"], "2026-06-02 08:00:00")

    def test_weight_updated_at_caption_uses_compact_beijing_time(self):
        caption = _recap_weight_updated_at_caption("2026-06-18T05:38:59.985848+00:00")

        self.assertEqual(caption, "当前默认权重更新时间：2026-06-18 13:38:59")
