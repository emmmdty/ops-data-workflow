import unittest

import requests

from ops_data_workflow.external_context import fetch_external_context


class _FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


class ExternalContextTests(unittest.TestCase):
    def test_fetch_external_context_combines_holiday_market_and_policy_signals(self):
        def fake_get(url, **kwargs):
            if "holiday.ailcc.com" in url:
                return _FakeResponse(
                    {
                        "code": 0,
                        "holiday": {
                            "05-01": {"holiday": True, "name": "劳动节（休）", "date": "2026-05-01"},
                            "05-04": {"holiday": False, "name": "劳动节（调休）", "date": "2026-05-04"},
                        },
                    }
                )
            if "push2his.eastmoney.com" in url:
                return _FakeResponse(
                    {
                        "data": {
                            "name": "上证指数",
                            "klines": [
                                "2026-05-01,100,100,101,99,1,1,0,0,0,0",
                                "2026-05-07,100,102,103,99,1,1,0,2.0,0,0",
                            ],
                        }
                    }
                )
            if "csrc.gov.cn" in url:
                return _FakeResponse(
                    text="""
                    <html><body>
                    <a>证监会发布并购重组政策解读 2026-05-06</a>
                    <a>中国证监会优化市场制度 2026-05-03</a>
                    </body></html>
                    """
                )
            raise AssertionError(url)

        context = fetch_external_context("2026-05-01", "2026-05-07", request_get=fake_get)

        self.assertTrue(context.available)
        self.assertIn("劳动节", context.summary)
        self.assertIn("上证指数", context.summary)
        self.assertIn("+2", context.summary)
        self.assertIn("并购重组政策解读", context.summary)
        self.assertGreaterEqual(len(context.sources), 3)

    def test_fetch_external_context_falls_back_without_raising(self):
        def failing_get(url, **kwargs):
            raise requests.Timeout("network timeout")

        context = fetch_external_context("2026-05-01", "2026-05-07", request_get=failing_get)

        self.assertFalse(context.available)
        self.assertIn("未取到外部背景", context.summary)
        self.assertEqual(context.sources, [])


if __name__ == "__main__":
    unittest.main()
