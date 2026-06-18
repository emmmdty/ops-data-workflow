from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from ops_data_workflow.minimax_recap import analyze_top_content_with_minimax


class FakeResponse:
    def __init__(self, payload: dict, *, ok: bool = True, status_code: int = 200) -> None:
        self.payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


class MiniMaxRecapTests(unittest.TestCase):
    def test_missing_minimax_config_fails_explicitly(self):
        with self.assertRaises(RuntimeError) as ctx:
            analyze_top_content_with_minimax({}, {}, env={}, env_path=None)

        self.assertIn("MINIMAX_API_KEY", str(ctx.exception))

    def test_analyzes_manifest_images_and_maps_json_result(self):
        with TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "cover.jpg"
            image_path.write_bytes(b"fake-jpeg")
            response = FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "category_l1": "投教",
                                        "category_l2": "方法论",
                                        "bilibili_content_type": "",
                                        "content_form": "图文",
                                        "summary": "强问题场景和明确转化承接。",
                                        "common_patterns": ["问题开头", "封面大字"],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }
            )
            session = FakeSession(response)

            result = analyze_top_content_with_minimax(
                {"platform": "小红书", "title": "为什么新手总是亏钱", "account": "示例账号"},
                {"cover_path": str(image_path), "metadata": {"category_l1": "旧分类"}},
                env={
                    "MINIMAX_API_KEY": "key",
                    "MINIMAX_BASE_URL": "https://api.minimaxi.com/v1",
                    "MINIMAX_MODEL": "MiniMax-M3",
                },
                session=session,
            )

        self.assertEqual(result["一级内容类型"], "投教")
        self.assertEqual(result["二级内容类型"], "方法论")
        self.assertEqual(result["内容形态"], "图文")
        self.assertIn("强问题场景", result["共性总结"])
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api.minimaxi.com/v1/chat/completions")
        payload = kwargs["json"]
        content = payload["messages"][1]["content"]
        self.assertTrue(any(item.get("type") == "image_url" for item in content))

    def test_limits_and_compresses_manifest_images_before_sending(self):
        with TemporaryDirectory() as tmp:
            from PIL import Image

            paths = []
            for index in range(5):
                image_path = Path(tmp) / f"image-{index}.png"
                Image.new("RGB", (1400, 900), color=(index * 20, 80, 120)).save(image_path)
                paths.append(str(image_path))
            response = FakeResponse(
                {
                    "choices": [
                        {"message": {"content": json.dumps({"一级内容类型": "图文"}, ensure_ascii=False)}}
                    ]
                }
            )
            session = FakeSession(response)

            analyze_top_content_with_minimax(
                {"platform": "小红书", "title": "压缩测试"},
                {"cover_path": paths[0], "screenshots": paths[1:]},
                env={"MINIMAX_API_KEY": "key"},
                session=session,
            )

        content = session.calls[0][1]["json"]["messages"][1]["content"]
        image_items = [item for item in content if item.get("type") == "image_url"]
        self.assertEqual(len(image_items), 3)
        for item in image_items:
            url = item["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/jpeg;base64,"))
            self.assertLess(len(url), 200_000)

    def test_normalizes_full_structured_json_fields_and_prompt_includes_metrics(self):
        response = FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "一级内容类型": "投教",
                                    "二级内容类型": "股票入门",
                                    "B站内容类型": "",
                                    "内容形态": "视频",
                                    "标题钩子": "问题钩子",
                                    "视觉结构": "口播加字幕",
                                    "信息密度": "高",
                                    "转化路径": "先讲痛点再引导下载",
                                    "可复用点": "强痛点开场",
                                    "不建议复用点": "不要夸张承诺",
                                    "下周期策略建议": "复用问题开场",
                                    "共性总结": "投教主题明确",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
        session = FakeSession(response)

        result = analyze_top_content_with_minimax(
            {
                "platform": "抖音",
                "title": "为什么新手总是亏钱",
                "account": "示例账号",
                "content_url": "https://www.douyin.com/video/1",
                "payload_json": json.dumps({"spend": 3000, "impressions": 100000}, ensure_ascii=False),
            },
            {"metadata": {"category_l1": "旧分类"}, "frames": []},
            env={"MINIMAX_API_KEY": "key"},
            session=session,
        )

        for field in [
            "一级内容类型",
            "二级内容类型",
            "B站内容类型",
            "内容形态",
            "标题钩子",
            "视觉结构",
            "信息密度",
            "转化路径",
            "可复用点",
            "不建议复用点",
            "下周期策略建议",
            "共性总结",
        ]:
            self.assertIn(field, result)
        self.assertEqual(result["标题钩子"], "问题钩子")
        prompt = session.calls[0][1]["json"]["messages"][1]["content"][0]["text"]
        self.assertIn("3000", prompt)
        self.assertIn("已有元数据", prompt)

    def test_extracts_json_object_when_model_wraps_content_with_reasoning_text(self):
        response = FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '<think>先判断类型</think>\n{"一级内容类型":"图文","二级内容类型":"理财方法","内容形态":"图文"}'
                        }
                    }
                ]
            }
        )
        session = FakeSession(response)

        result = analyze_top_content_with_minimax(
            {"platform": "小红书", "title": "K线基本形态"},
            {},
            env={"MINIMAX_API_KEY": "key"},
            session=session,
        )

        self.assertEqual(result["一级内容类型"], "图文")
        self.assertEqual(result["二级内容类型"], "理财方法")


if __name__ == "__main__":
    unittest.main()
