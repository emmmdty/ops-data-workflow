"""Content category helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

import yaml


DEFAULT_CATEGORY_RULES: Dict[str, List[str]] = {
    "股友说": ["股友说", "股民", "交易高手", "短线交易", "交易者", "炒股的人"],
    "资讯": ["板块", "涨停", "大涨", "官宣", "A股", "芯片", "指数", "主力"],
    "同花顺进行曲": ["同花顺进行曲", "真英雄", "伴奏", "合唱"],
    "大佬采访": ["采访", "冠军", "大佬", "孙辉", "陈小群", "鑫多多"],
    "盘点": ["盘点", "前十", "排行", "VS"],
    "财商动画": ["动画", "财商", "贫穷的人", "富有的人"],
    "问财问句": ["问财", "问句", "选股模型"],
    "社区话题": ["社区", "股民交流", "同花顺社区"],
    "励志语录": ["阶层跃迁", "纪律", "一事无成", "翻身", "先苦后甜"],
}


CATEGORY_TAG_MAP: Dict[str, str] = {
    "#同花顺资讯": "资讯",
    "#同花顺股友说": "股友说",
    "#同顺图解": "图文",
    "#同顺盘点": "盘点",
    "#问财问句": "问财问句",
    "#同顺深度财经": "长视频",
    "#同顺财商": "财商动画",
    "#同花顺股民话题": "社区话题",
}


def load_category_rules(config_path: Optional[Path] = None) -> Mapping[str, Iterable[str]]:
    if not config_path or not config_path.exists():
        return DEFAULT_CATEGORY_RULES

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    rules = data.get("categories", data)
    if not isinstance(rules, dict):
        return DEFAULT_CATEGORY_RULES
    return {str(name): [str(item) for item in keywords or []] for name, keywords in rules.items()}


def suggest_category(title: object, rules: Mapping[str, Iterable[str]]) -> str:
    text = "" if title is None else str(title)
    if not text.strip():
        return ""

    lower_text = text.lower()
    for category, keywords in rules.items():
        for keyword in keywords:
            token = str(keyword).strip()
            if token and token.lower() in lower_text:
                return str(category)
    return ""


def category_from_tags(text: object) -> str:
    content = "" if text is None else str(text)
    if not content.strip():
        return ""
    for tag, category in CATEGORY_TAG_MAP.items():
        if tag in content:
            return category
    return ""
