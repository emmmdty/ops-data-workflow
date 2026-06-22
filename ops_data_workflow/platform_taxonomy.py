"""Platform content taxonomy shared with the harvester recap flow."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess


FALLBACK_DOUYIN_TAXONOMY: dict[str, set[str]] = {
    "股友说": {"股民教学", "股民优势", "股民洞察"},
    "财商动画": {"对比分析类", "历经磨难类"},
    "图文": {"市场热点行业盘点", "投资认知理财方法", "财富故事投资人物", "话题类内容"},
    "社区话题": {"股市段子互动", "股民情绪共鸣", "同花顺产品种草"},
    "说唱": set(),
    "长视频": set(),
    "盘点": {"资金盘面盘点", "行业品种产业链解析", "投资知识类盘点"},
}
FALLBACK_XHS_TAXONOMY: dict[str, set[str]] = {
    "图文": {"财富人物", "理财方法", "行业盘点", "互动话题"},
    "视频": {"股友说", "社区话题", "资讯", "说唱", "段子", "长视频"},
}
FALLBACK_BILIBILI_CONTENT_TYPES: set[str] = {"采访内容", "大佬生平", "新手教学指标教学", "海外搬运", "短视频"}

BILIBILI_TYPE_ALIASES = {
    "新手教学": "新手教学指标教学",
    "指标教学": "新手教学指标教学",
    "教学指标": "新手教学指标教学",
    "K线教学": "新手教学指标教学",
    "采访": "采访内容",
    "人物采访": "采访内容",
    "人物生平": "大佬生平",
    "生平": "大佬生平",
    "搬运": "海外搬运",
}


@dataclass(frozen=True)
class PlatformTaxonomy:
    douyin: dict[str, set[str]]
    xhs: dict[str, set[str]]
    bilibili: set[str]
    source: str = "fallback"


@dataclass(frozen=True)
class PlatformClassification:
    platform: str
    primary_type: str = ""
    secondary_type: str = ""
    bilibili_type: str = ""
    primary_valid: bool = False
    secondary_valid: bool = False
    bilibili_valid: bool = False

    @property
    def has_any_valid_type(self) -> bool:
        return self.primary_valid or self.secondary_valid or self.bilibili_valid


def load_harvester_taxonomy(harvester_root: Path) -> PlatformTaxonomy:
    root = Path(harvester_root).expanduser().resolve()
    platform_module = root / "src" / "ai" / "platform-taxonomies.mjs"
    douyin_module = root / "src" / "douyin-channel-type-classifier" / "taxonomy.mjs"
    if not platform_module.exists() or not douyin_module.exists():
        raise FileNotFoundError(f"harvester taxonomy files not found under {root}")
    script = """
const [platformModulePath, douyinModulePath] = process.argv.slice(1);
const { pathToFileURL } = await import("node:url");
const platformTaxonomies = await import(pathToFileURL(platformModulePath).href);
const douyinTaxonomy = await import(pathToFileURL(douyinModulePath).href);
const douyin = {};
for (const primary of douyinTaxonomy.DOUYIN_CHANNEL_PRIMARY_TYPES || []) {
  douyin[primary] = douyinTaxonomy.secondaryLabelsForPrimary(primary) || [];
}
const xhs = platformTaxonomies.XHS_TAXONOMY?.secondaryTypes || {};
const bilibili = platformTaxonomies.BILIBILI_PRIMARY_TYPES || [];
process.stdout.write(JSON.stringify({ douyin, xhs, bilibili }));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(platform_module), str(douyin_module)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    payload = json.loads(completed.stdout or "{}")
    return PlatformTaxonomy(
        douyin=_taxonomy_mapping(payload.get("douyin", {})),
        xhs=_taxonomy_mapping(payload.get("xhs", {})),
        bilibili={_clean_text(value) for value in payload.get("bilibili", []) if _clean_text(value)},
        source=str(root),
    )


def load_effective_taxonomy() -> PlatformTaxonomy:
    root = _default_harvester_root()
    try:
        taxonomy = load_harvester_taxonomy(root)
        if taxonomy.douyin and taxonomy.xhs and taxonomy.bilibili:
            return taxonomy
    except Exception:
        pass
    return PlatformTaxonomy(
        douyin={key: set(values) for key, values in FALLBACK_DOUYIN_TAXONOMY.items()},
        xhs={key: set(values) for key, values in FALLBACK_XHS_TAXONOMY.items()},
        bilibili=set(FALLBACK_BILIBILI_CONTENT_TYPES),
        source="fallback",
    )


def _default_harvester_root() -> Path:
    configured = os.environ.get("HARVESTER_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "harvester-THS"


def _taxonomy_mapping(value: object) -> dict[str, set[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, set[str]] = {}
    for raw_primary, raw_secondaries in value.items():
        primary = _clean_text(raw_primary)
        if not primary:
            continue
        secondaries = raw_secondaries if isinstance(raw_secondaries, list) else []
        result[primary] = {_clean_text(item) for item in secondaries if _clean_text(item)}
    return result


def normalize_platform_label(value: object) -> str:
    text = _clean_text(value)
    lowered = text.lower()
    if "抖音" in text or "douyin" in lowered:
        return "抖音"
    if "小红书" in text or "xhs" in lowered or "xiaohongshu" in lowered:
        return "小红书"
    if "B站" in text or "b站" in text or "哔哩" in text or "bilibili" in lowered:
        return "B站"
    return text


def normalize_platform_classification(
    platform: object,
    *,
    category_l1: object = "",
    category_l2: object = "",
    bilibili_content_type: object = "",
    content_type: object = "",
) -> PlatformClassification:
    platform_label = normalize_platform_label(platform)
    l1 = _clean_text(category_l1)
    l2 = _clean_text(category_l2)
    compatible_type = _clean_text(content_type)
    if platform_label == "抖音":
        return _normalize_two_level(platform_label, DOUYIN_TAXONOMY, l1, l2, compatible_type)
    if platform_label == "小红书":
        return _normalize_two_level(platform_label, XHS_TAXONOMY, l1, l2, compatible_type)
    if platform_label == "B站":
        bilibili_type = _normalize_bilibili_type(
            bilibili_content_type,
            content_type,
            category_l1,
            category_l2,
        )
        return PlatformClassification(
            platform=platform_label,
            bilibili_type=bilibili_type,
            bilibili_valid=bool(bilibili_type),
        )
    return PlatformClassification(platform=platform_label)


def _normalize_two_level(
    platform: str,
    taxonomy: dict[str, set[str]],
    primary: str,
    secondary: str,
    compatible_type: str,
) -> PlatformClassification:
    secondary_lookup = _secondary_to_primary(taxonomy)
    primary_type = primary if primary in taxonomy else ""
    secondary_candidates = [secondary, compatible_type]
    secondary_type = ""
    for candidate in secondary_candidates:
        if not candidate:
            continue
        if primary_type and candidate in taxonomy[primary_type]:
            secondary_type = candidate
            break
        inferred_primary = secondary_lookup.get(candidate)
        if inferred_primary:
            primary_type = primary_type or inferred_primary
            if primary_type == inferred_primary:
                secondary_type = candidate
                break
    if not primary_type and compatible_type in taxonomy:
        primary_type = compatible_type
    if primary_type and not taxonomy[primary_type]:
        secondary_type = ""
    return PlatformClassification(
        platform=platform,
        primary_type=primary_type,
        secondary_type=secondary_type,
        primary_valid=bool(primary_type),
        secondary_valid=bool(secondary_type),
    )


def _normalize_bilibili_type(*values: object) -> str:
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        if text in BILIBILI_CONTENT_TYPES:
            return text
        alias = BILIBILI_TYPE_ALIASES.get(text)
        if alias:
            return alias
    return ""


def _secondary_to_primary(taxonomy: dict[str, set[str]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for primary, secondaries in taxonomy.items():
        for secondary in secondaries:
            mapping.setdefault(secondary, primary)
    return mapping


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


_EFFECTIVE_TAXONOMY = load_effective_taxonomy()
DOUYIN_TAXONOMY: dict[str, set[str]] = _EFFECTIVE_TAXONOMY.douyin
XHS_TAXONOMY: dict[str, set[str]] = _EFFECTIVE_TAXONOMY.xhs
BILIBILI_CONTENT_TYPES: set[str] = _EFFECTIVE_TAXONOMY.bilibili
