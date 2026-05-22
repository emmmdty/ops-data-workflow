"""Excel ingestion, normalization, category completion, and scoring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Dict, Iterable, List, Mapping, Optional

import pandas as pd

from .categories import category_from_tags, load_category_rules, suggest_category
from .reference_tables import (
    ReferenceTables,
    account_mapping_lookup,
    load_reference_tables,
)


TABULAR_SUFFIXES = {".csv", ".xls", ".xlsx"}

STANDARD_COLUMNS = [
    "platform",
    "platform_group",
    "channel",
    "period_start",
    "period_end",
    "content_id",
    "material_id",
    "title",
    "account_raw",
    "account_id",
    "account",
    "account_mapping_source",
    "author",
    "cover_url",
    "content_url",
    "category_l1",
    "category_l2",
    "category_l3",
    "category_source",
    "category_l2_source",
    "category_confidence",
    "review_status",
    "primary_category",
    "manual_category",
    "ai_category",
    "content_category",
    "category_status",
    "spend",
    "impressions",
    "clicks",
    "activations",
    "first_pay_count",
    "activation_cost",
    "first_pay_cost",
    "ctr",
    "activation_rate",
    "first_pay_rate",
    "dedupe_key",
    "merged_row_count",
    "conflict_details",
    "needs_manual_review",
    "review_reasons",
    "source_file",
    "source_sheet",
    "source_row",
    "source_file_hash",
    "duplicate_group_id",
    "review_action",
]

INTERNAL_COMPAT_COLUMNS = {"platform", "platform_group"}
NUMERIC_COLUMNS = ["spend", "impressions", "clicks", "activations", "first_pay_count"]


@dataclass(frozen=True)
class AnalysisData:
    canonical: pd.DataFrame
    category_summary: pd.DataFrame
    channel_summary: pd.DataFrame
    platform_summary: pd.DataFrame
    platform_category_summary: pd.DataFrame
    total_summary: pd.DataFrame
    raw_category_stats: pd.DataFrame
    pending_categories: pd.DataFrame
    account_audit: pd.DataFrame
    top_content_items: pd.DataFrame
    cover_metrics: pd.DataFrame
    data_quality: pd.DataFrame
    review_queue: pd.DataFrame
    preprocessing_report: pd.DataFrame
    duplicate_merge_details: pd.DataFrame
    conflict_retention_details: pd.DataFrame
    missing_value_details: pd.DataFrame
    reference_tables: ReferenceTables


EXPECTED_ACCOUNTS: Dict[str, List[str]] = {
    "小红书": [
        "同花顺投资",
        "同花顺股民社区",
        "同花顺理财",
        "同顺财经",
        "问财",
        "喵懂投资",
        "同花顺新手福利官",
    ],
    "抖音": [
        "同花顺投资",
        "同花顺股民社区",
        "同花顺财富",
        "同花顺财经",
        "同花顺问财",
        "喵懂投资",
        "同花顺期货通",
    ],
    "B站": ["同花顺投资"],
}

CategoryMatcher = Callable[[pd.DataFrame, list[str], Optional[Path]], Mapping[int, str]]
CategoryMappings = Mapping[str, Mapping[str, str]]


def analyze_input_dir(
    input_dir: Path,
    period_start: str,
    period_end: str,
    category_rules_path: Optional[Path] = None,
    *,
    env_path: Optional[Path] = None,
    category_matcher: Optional[CategoryMatcher] = None,
    category_mappings: Optional[CategoryMappings] = None,
    reference_tables_path: Optional[Path] = None,
) -> AnalysisData:
    input_dir = Path(input_dir)
    rules = load_category_rules(category_rules_path)
    references = load_reference_tables(reference_tables_path or Path("config/reference_tables.xlsx"))
    frames = _read_available_sources(input_dir, references)
    if not frames:
        raise FileNotFoundError("未找到可识别的渠道数据文件，请上传 Excel、CSV 或 zip。")
    raw_category_stats = collect_raw_category_stats(input_dir)
    canonical = pd.concat(frames, ignore_index=True)
    canonical["period_start"] = period_start
    canonical["period_end"] = period_end
    canonical = _apply_account_mappings(canonical, references)
    preprocessing = _preprocess_canonical(canonical)
    canonical = preprocessing["canonical"]
    canonical = _complete_categories(
        canonical,
        rules,
        references=references,
        env_path=env_path,
        category_matcher=category_matcher,
        category_mappings=category_mappings or {},
    )
    canonical = _derive_metrics(canonical)
    data_quality = _build_data_quality_report(canonical)
    preprocessing_report = _build_preprocessing_report(canonical, preprocessing, data_quality)
    review_queue = _build_review_queue(canonical)
    channel_summary = _summarize_channels(canonical)
    platform_summary = _summarize_platforms(canonical)
    platform_category_summary = _summarize_platform_categories(canonical)
    total_summary = _make_total_summary(canonical)
    category_summary = _summarize_categories(canonical)
    pending = canonical[canonical["content_category"].map(_is_blank)].copy()
    account_audit = _build_account_audit(canonical)
    top_content_items = _summarize_top_content(canonical)
    cover_metrics = _summarize_cover_metrics(canonical)
    return AnalysisData(
        canonical=canonical,
        category_summary=category_summary,
        channel_summary=channel_summary,
        platform_summary=platform_summary,
        platform_category_summary=platform_category_summary,
        total_summary=total_summary,
        raw_category_stats=raw_category_stats,
        pending_categories=pending,
        account_audit=account_audit,
        top_content_items=top_content_items,
        cover_metrics=cover_metrics,
        data_quality=data_quality,
        review_queue=review_queue,
        preprocessing_report=preprocessing_report,
        duplicate_merge_details=preprocessing["duplicate_merge_details"],
        conflict_retention_details=preprocessing["conflict_retention_details"],
        missing_value_details=preprocessing["missing_value_details"],
        reference_tables=references,
    )


def analyze_canonical_frame(
    canonical: pd.DataFrame,
    period_start: str,
    period_end: str,
    category_rules_path: Optional[Path] = None,
    *,
    env_path: Optional[Path] = None,
    category_matcher: Optional[CategoryMatcher] = None,
    category_mappings: Optional[CategoryMappings] = None,
    reference_tables_path: Optional[Path] = None,
) -> AnalysisData:
    rules = load_category_rules(category_rules_path)
    references = load_reference_tables(reference_tables_path or Path("config/reference_tables.xlsx"))
    prepared = canonical.copy()
    for column in STANDARD_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = ""
    prepared["period_start"] = period_start
    prepared["period_end"] = period_end
    prepared = _apply_account_mappings(prepared, references)
    preprocessing = _preprocess_canonical(prepared)
    prepared = preprocessing["canonical"]
    prepared = _complete_categories(
        prepared,
        rules,
        references=references,
        env_path=env_path,
        category_matcher=category_matcher,
        category_mappings=category_mappings or {},
    )
    prepared = _derive_metrics(prepared)
    data_quality = _build_data_quality_report(prepared)
    preprocessing_report = _build_preprocessing_report(prepared, preprocessing, data_quality)
    review_queue = _build_review_queue(prepared)
    channel_summary = _summarize_channels(prepared)
    platform_summary = _summarize_platforms(prepared)
    platform_category_summary = _summarize_platform_categories(prepared)
    total_summary = _make_total_summary(prepared)
    category_summary = _summarize_categories(prepared)
    pending = prepared[prepared["content_category"].map(_is_blank)].copy()
    account_audit = _build_account_audit(prepared)
    top_content_items = _summarize_top_content(prepared)
    cover_metrics = _summarize_cover_metrics(prepared)
    return AnalysisData(
        canonical=prepared,
        category_summary=category_summary,
        channel_summary=channel_summary,
        platform_summary=platform_summary,
        platform_category_summary=platform_category_summary,
        total_summary=total_summary,
        raw_category_stats=pd.DataFrame(columns=["source_file", "sheet", "raw_field", "value", "count"]),
        pending_categories=pending,
        account_audit=account_audit,
        top_content_items=top_content_items,
        cover_metrics=cover_metrics,
        data_quality=data_quality,
        review_queue=review_queue,
        preprocessing_report=preprocessing_report,
        duplicate_merge_details=preprocessing["duplicate_merge_details"],
        conflict_retention_details=preprocessing["conflict_retention_details"],
        missing_value_details=preprocessing["missing_value_details"],
        reference_tables=references,
    )


def _read_available_sources(input_dir: Path, references: ReferenceTables) -> List[pd.DataFrame]:
    frames: List[pd.DataFrame] = []
    used: set[Path] = set()
    reader_specs = [
        (["B站"], _read_bilibili),
        (["小红书", "市场部"], _read_xiaohongshu_market),
        (["小红书"], _read_xiaohongshu),
        (["抖音", "达人"], _read_douyin_generic),
        (["抖音", "商业化"], _read_douyin_commercial),
        (["抖音", "市场部"], _read_douyin_market),
        (["微信", "市场部"], _read_social_market),
        (["腾讯", "市场部"], _read_social_market),
    ]
    for tokens, reader in reader_specs:
        try:
            path = _find_file(input_dir, tokens)
        except FileNotFoundError:
            continue
        try:
            frame = reader(path)
        except Exception as exc:
            raise ValueError(f"{path.name} 解析失败：{exc}") from exc
        if not frame.empty:
            frames.append(frame)
            used.add(path.resolve())

    for path in _iter_tabular_files(input_dir):
        if path.resolve() in used or path.name.startswith("~$"):
            continue
        frame = _read_optional_source(path, references)
        if frame is not None and not frame.empty:
            frames.append(frame)
    return frames


def _read_optional_source(path: Path, references: ReferenceTables) -> Optional[pd.DataFrame]:
    name = path.name
    if "抖音" in name:
        return _read_douyin_generic(path)
    if "小红书" in name and "市场部" in name:
        return _read_xiaohongshu_market(path)
    if "小红书" in name:
        return _read_xiaohongshu(path)
    if "B站" in name:
        return _read_bilibili(path)
    if "微信" in name or "腾讯" in name:
        return _read_social_market(path)
    columns = _read_first_available_columns(path)
    if {"视频BVID", "花费", "展示量"}.issubset(columns) or {"视频AVID", "应用激活数"}.issubset(columns):
        return _read_bilibili(path)
    if {"笔记ID", "消费", "展现量"}.issubset(columns):
        return _read_xiaohongshu(path)
    if {"视频标题", "消耗", "展示数"}.issubset(columns):
        return _read_douyin_generic(path)
    if {"创意名称", "花费"}.issubset(columns) or {"链接", "花费"}.issubset(columns):
        return _read_social_market(path)
    if path.suffix.lower() in {".xls", ".xlsx"}:
        return _read_generic_channel(path, references)
    return None


def _find_file(input_dir: Path, tokens: Iterable[str]) -> Path:
    candidates = []
    for path in _iter_tabular_files(input_dir):
        name = path.name
        if all(token in name for token in tokens):
            candidates.append(path)
    if not candidates:
        joined = " / ".join(tokens)
        raise FileNotFoundError(f"未找到包含 {joined} 的平台数据文件")
    return sorted(candidates)[0]


def _read_bilibili(path: Path) -> pd.DataFrame:
    raw = _read_named_or_first_matching_sheet(path, "sheet1", ["视频BVID", "视频bvid", "花费", "求和项:总花费"])
    return _standardize(
        raw,
        platform="B站",
        platform_group="B站",
        channel="B站",
        source_file=path.name,
        fields={
            "content_id": ["视频BVID", "视频bvid", "视频AVID", "视频avid"],
            "material_id": ["素材中心id", "素材中心ID", "视频BVID", "视频bvid"],
            "title": ["视频标题"],
            "account_id": ["Up主mid", "UID", "uid", "mid"],
            "account": ["Up主名称", "UP主名称", "UP主昵称", "账号名称"],
            "cover_url": ["素材url"],
            "content_url": ["视频链接", "素材url"],
            "primary_category": [],
            "spend": ["花费", "总花费", "求和项:总花费"],
            "impressions": ["展示量", "求和项:展示量"],
            "clicks": ["点击量", "求和项:点击量"],
            "activations": ["应用激活数", "求和项:应用激活数"],
            "first_pay_count": ["应用内付费", "应用内首次付费次数", "求和项:应用内首次付费次数"],
        },
    )


def _read_xiaohongshu(path: Path) -> pd.DataFrame:
    raw = _read_named_or_first_matching_sheet(path, "kos账户投放数据", ["笔记ID", "消费"])
    if _is_csv(path):
        content_map = pd.DataFrame()
    else:
        try:
            content_map = _read_table(path, sheet_name="内容表格", header=1)
        except Exception:
            content_map = pd.DataFrame()
    if {"笔记ID", "内容类型"}.issubset(content_map.columns):
        mapping = (
            content_map[["笔记ID", "内容类型"]]
            .dropna(subset=["笔记ID"])
            .drop_duplicates(subset=["笔记ID"], keep="first")
            .rename(columns={"内容类型": "内容类型_映射"})
        )
        raw = raw.merge(mapping, on="笔记ID", how="left")
        raw["内容类别_解析"] = _first_non_blank(raw, ["内容分类", "内容类型", "内容类型_映射"])
        raw["类别来源_解析"] = raw.apply(_xiaohongshu_category_status, axis=1)
    else:
        raw["内容类别_解析"] = _first_non_blank(raw, ["内容分类", "内容类型"])
        raw["类别来源_解析"] = ""

    return _standardize(
        raw,
        platform="小红书商业化",
        platform_group="小红书",
        channel="小红书商业化",
        source_file=path.name,
        fields={
            "content_id": ["笔记ID"],
            "material_id": ["笔记ID"],
            "title": ["标题"],
            "account_id": ["作者ID", "用户ID", "小红书号", "账号ID"],
            "account": ["发布作者"],
            "cover_url": ["封面", "封面图", "图片链接"],
            "content_url": ["笔记链接"],
            "primary_category": ["类型"],
            "manual_category": ["内容类别_解析"],
            "category_status": ["类别来源_解析"],
            "spend": ["消费"],
            "impressions": ["展现量"],
            "clicks": ["点击量"],
            "activations": ["激活数"],
            "first_pay_count": ["首次付费次数"],
        },
    )


def _read_xiaohongshu_market(path: Path) -> pd.DataFrame:
    raw = _read_first_sheet_with_columns(path, ["消费"])
    return _standardize(
        raw,
        platform="小红书市场部",
        platform_group="小红书",
        channel="小红书市场部",
        source_file=path.name,
        fields={
            "content_id": ["笔记/素材ID", "笔记ID", "素材ID", "计划ID", "链接", "笔记/素材链接"],
            "material_id": ["笔记/素材ID", "素材ID", "计划ID"],
            "title": ["标题", "笔记标题", "创意名称", "计划名称", "笔记/素材链接"],
            "account_id": ["作者ID", "用户ID", "账号ID"],
            "account": ["发布作者", "账号", "账号名称"],
            "cover_url": ["封面", "封面图", "图片链接"],
            "content_url": ["笔记链接", "笔记/素材链接", "链接"],
            "primary_category": ["类型", "营销诉求"],
            "manual_category": ["内容分类", "内容类型"],
            "spend": ["消费", "消耗", "花费"],
            "impressions": ["展现量", "曝光次数", "展示数"],
            "clicks": ["点击量", "点击次数", "点击数"],
            "activations": ["激活数", "激活数(转化时间)", "APP激活次数"],
            "first_pay_count": ["首次付费次数", "首次付费次数(转化时间)", "付费次数"],
        },
    )


def _read_douyin_commercial(path: Path) -> pd.DataFrame:
    raw = _read_named_or_first_matching_sheet(path, "Sheet2", ["视频标题", "消耗"])
    return _standardize_douyin(raw, path.name, "抖音商业化")


def _read_douyin_market(path: Path) -> pd.DataFrame:
    raw = _read_named_or_first_matching_sheet(path, "Sheet2", ["视频标题", "消耗"])
    return _standardize_douyin(raw, path.name, "抖音市场部")


def _read_douyin_generic(path: Path) -> pd.DataFrame:
    raw = _read_first_sheet_with_columns(path, ["视频标题", "消耗"])
    stem = path.stem
    if "市场部" in stem:
        channel = "抖音市场部"
    elif "达人" in stem:
        channel = "抖音达人内容"
    elif "商业化" in stem:
        channel = "抖音商业化"
    elif "期货" in stem:
        channel = "抖音期货通"
    else:
        channel = stem
    return _standardize_douyin(raw, path.name, channel)


def _read_social_market(path: Path) -> pd.DataFrame:
    raw = _read_first_non_empty_sheet(path)
    channel = _social_market_channel(path.stem)
    return _standardize(
        raw,
        platform=channel,
        platform_group=channel.replace("市场部", ""),
        channel=channel,
        source_file=path.name,
        fields={
            "content_id": ["内容ID", "创意ID", "计划ID", "链接", "落地页", "创意名称"],
            "material_id": ["素材ID", "创意ID", "计划ID", "链接", "创意名称"],
            "title": ["创意名称", "标题", "内容标题", "链接", "落地页"],
            "account_id": ["账号ID", "账户ID"],
            "account": ["账号", "账号名称", "账户名称"],
            "cover_url": ["封面", "封面图", "图片链接"],
            "content_url": ["链接", "落地页"],
            "primary_category": ["营销诉求", "优化目标"],
            "manual_category": ["内容分类", "内容类型"],
            "spend": ["花费", "消费", "消耗"],
            "impressions": ["曝光次数", "展现量", "展示数"],
            "clicks": ["点击次数", "点击量", "点击数"],
            "activations": ["APP激活次数", "激活数", "注册次数"],
            "first_pay_count": ["注册次数", "注册次数（点击归因）", "付费次数", "首次付费次数"],
        },
    )


def _read_generic_channel(path: Path, references: ReferenceTables) -> pd.DataFrame:
    raw = _read_first_non_empty_sheet(path)
    if raw.empty:
        return pd.DataFrame()
    channel = path.stem
    fields = {
        "content_id": ["内容ID", "内容id", "视频id", "视频ID", "笔记ID", "作品ID", "id", "ID"],
        "material_id": ["素材ID", "素材id", "素材中心id", "内容ID", "视频id", "笔记ID", "链接", "创意名称"],
        "title": ["标题", "视频标题", "内容标题", "笔记标题", "创意名称", "链接"],
        "account_id": ["账号ID", "账号id", "作者ID", "用户ID", "uid", "UID", "mid"],
        "account": ["账号", "账号名称", "发布账号", "达人名称", "作者", "发布作者"],
        "cover_url": ["封面", "封面图", "图片链接", "视频封面图", "素材url"],
        "content_url": ["链接", "内容链接", "视频链接", "笔记链接", "素材url"],
        "primary_category": ["类型", "一级类型", "一级素材形式"],
        "manual_category": ["内容类型", "内容分类", "二级栏目", "最终内容类别"],
        "category_l3": ["三级题材", "题材"],
        "spend": ["消耗", "消费", "花费", "spend"],
        "impressions": ["展示数", "展示量", "展现量", "曝光量", "impressions"],
        "clicks": ["点击数", "点击量", "clicks"],
        "activations": ["激活数", "应用激活数", "APP激活次数", "activations"],
        "first_pay_count": ["付费次数", "首次付费次数", "应用内付费", "注册次数", "注册次数（点击归因）"],
    }
    return _standardize(
        raw,
        platform=channel,
        platform_group=channel,
        channel=channel,
        source_file=path.name,
        fields=fields,
    )


def _read_first_sheet_with_columns(path: Path, required_columns: Iterable[str]) -> pd.DataFrame:
    if _is_csv(path):
        return _read_table(path)
    required = set(required_columns)
    last_frame = pd.DataFrame()
    with pd.ExcelFile(path) as workbook:
        for sheet_name in workbook.sheet_names:
            frame = _read_table(path, sheet_name=sheet_name)
            last_frame = frame
            if required.issubset(set(frame.columns)):
                return frame
    return last_frame


def _read_first_non_empty_sheet(path: Path) -> pd.DataFrame:
    if _is_csv(path):
        return _read_table(path)
    last_frame = pd.DataFrame()
    with pd.ExcelFile(path) as workbook:
        for sheet_name in workbook.sheet_names:
            frame = _read_table(path, sheet_name=sheet_name)
            last_frame = frame
            if not frame.dropna(how="all").empty:
                return frame
    return last_frame


def _read_named_or_first_matching_sheet(
    path: Path,
    preferred_sheet: str,
    required_columns: Iterable[str],
) -> pd.DataFrame:
    if _is_csv(path):
        return _read_table(path)
    try:
        return _read_table(path, sheet_name=preferred_sheet)
    except Exception:
        return _read_first_sheet_with_columns(path, required_columns)


def _read_first_available_columns(path: Path) -> set[str]:
    try:
        if _is_csv(path):
            return set(_read_table(path, nrows=0).columns.astype(str))
        with pd.ExcelFile(path) as workbook:
            for sheet_name in workbook.sheet_names:
                try:
                    columns = set(_read_table(path, sheet_name=sheet_name, nrows=0).columns.astype(str))
                except Exception:
                    continue
                if columns:
                    return columns
    except Exception:
        return set()
    return set()


def _standardize_douyin(raw: pd.DataFrame, source_file: str, channel: str) -> pd.DataFrame:
    return _standardize(
        raw,
        platform=channel,
        platform_group="抖音",
        channel=channel,
        source_file=source_file,
        fields={
            "content_id": ["视频id", "视频链接"],
            "material_id": ["素材ID", "视频链接"],
            "title": ["视频标题", "视频链接"],
            "account_id": ["账号ID", "账号id", "抖音号", "达人ID", "作者ID", "uid", "UID"],
            "account": ["账号", "账号名称", "发布账号", "达人名称"],
            "cover_url": ["视频封面图"],
            "content_url": ["视频链接"],
            "primary_category": [],
            "manual_category": ["内容类型"],
            "spend": ["消耗"],
            "impressions": ["展示数"],
            "clicks": ["点击数"],
            "activations": ["激活数"],
            "first_pay_count": ["付费次数", "付费数"],
        },
    )


def _social_market_channel(stem: str) -> str:
    name = re.sub(r"[（）()\s_-]+", "", str(stem or ""))
    if "腾讯" in name:
        return "腾讯市场部"
    if "微信" in name:
        return "微信市场部"
    return stem


def _standardize(
    raw: pd.DataFrame,
    platform: str,
    platform_group: str,
    channel: str,
    source_file: str,
    fields: Mapping[str, List[str]],
) -> pd.DataFrame:
    normalized = pd.DataFrame(index=raw.index)
    normalized["platform"] = platform
    normalized["platform_group"] = platform_group
    normalized["channel"] = channel
    normalized["period_start"] = ""
    normalized["period_end"] = ""
    normalized["source_file"] = source_file

    for output, candidates in fields.items():
        normalized[output] = _first_non_blank(raw, candidates)

    if "manual_category" not in normalized.columns and "content_category" in normalized.columns:
        normalized["manual_category"] = normalized["content_category"]

    for column in STANDARD_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = ""
    normalized["account_raw"] = normalized["account"].map(lambda value: "" if _is_blank(value) else str(value).strip())
    normalized["author"] = normalized["author"].where(~normalized["author"].map(_is_blank), normalized["account"])

    for column in NUMERIC_COLUMNS:
        normalized[column] = normalized[column].map(parse_number)

    normalized["content_id"] = normalized["content_id"].fillna("").astype(str)
    normalized["material_id"] = normalized["material_id"].fillna("").astype(str)
    normalized["title"] = normalized["title"].fillna("").astype(str)
    normalized["account_id"] = normalized["account_id"].map(_clean_identifier)
    normalized["account"] = normalized["account"].map(lambda value: "" if _is_blank(value) else str(value))
    normalized["account_mapping_source"] = normalized["account"].map(
        lambda value: "原始账号字段" if not _is_blank(value) else ""
    )
    normalized["author"] = normalized["author"].map(lambda value: "" if _is_blank(value) else str(value))
    normalized["author"] = normalized["author"].where(~normalized["author"].map(_is_blank), normalized["account"])
    normalized = normalized[normalized[["title", "content_id", "material_id"]].ne("").any(axis=1)]
    raw_extra = _raw_extra_columns(raw, fields, source_file)
    if not raw_extra.empty:
        normalized = pd.concat([normalized.reset_index(drop=True), raw_extra.loc[normalized.index].reset_index(drop=True)], axis=1)
    return _ordered_columns(normalized)


def _raw_extra_columns(raw: pd.DataFrame, fields: Mapping[str, List[str]], source_file: str) -> pd.DataFrame:
    mapped_columns: set[str] = set()
    for candidates in fields.values():
        mapped_columns.update(candidates)
    extras = raw[[column for column in raw.columns if column not in mapped_columns]].copy()
    if extras.empty:
        return extras
    renamed = {}
    for column in extras.columns:
        renamed[column] = f"raw__{Path(source_file).stem}__{column}"
    return extras.rename(columns=renamed)


def _ordered_columns(frame: pd.DataFrame) -> pd.DataFrame:
    standard = [column for column in STANDARD_COLUMNS if column in frame.columns]
    extras = [column for column in frame.columns if column not in standard]
    return frame[standard + extras]


def _apply_account_mappings(canonical: pd.DataFrame, references: ReferenceTables) -> pd.DataFrame:
    canonical = canonical.copy()
    lookup = account_mapping_lookup(references.account_mapping)
    for column in ["account_raw", "account_mapping_source", "account_id", "account", "author"]:
        if column not in canonical.columns:
            canonical[column] = ""

    for index, row in canonical.iterrows():
        account = "" if _is_blank(row.get("account")) else str(row.get("account")).strip()
        account_id = _clean_identifier(row.get("account_id"))
        channel = "" if _is_blank(row.get("channel")) else str(row.get("channel")).strip()
        if account:
            canonical.at[index, "account"] = account
            canonical.at[index, "account_mapping_source"] = canonical.at[index, "account_mapping_source"] or "原始账号字段"
            canonical.at[index, "author"] = row.get("author") if not _is_blank(row.get("author")) else account
            continue
        mapped = lookup.get((channel, account_id))
        if mapped:
            canonical.at[index, "account"] = mapped["account"]
            canonical.at[index, "author"] = mapped["account"]
            canonical.at[index, "account_mapping_source"] = mapped["mapping_source"]
        elif channel == "B站" and account_id:
            canonical.at[index, "account_mapping_source"] = "未匹配"
    return canonical


def _preprocess_canonical(canonical: pd.DataFrame) -> dict[str, pd.DataFrame]:
    canonical = canonical.copy()
    for column in ["dedupe_key", "merged_row_count", "conflict_details", "needs_manual_review", "review_reasons"]:
        if column not in canonical.columns:
            canonical[column] = "" if column not in {"merged_row_count", "needs_manual_review"} else 0
    canonical["dedupe_key"] = canonical.apply(_dedupe_key, axis=1)
    canonical["merged_row_count"] = pd.to_numeric(canonical["merged_row_count"], errors="coerce").fillna(0).astype(int)
    canonical["merged_row_count"] = canonical["merged_row_count"].where(canonical["merged_row_count"].gt(0), 1)
    canonical["conflict_details"] = canonical["conflict_details"].fillna("").astype(str)
    canonical["needs_manual_review"] = canonical["needs_manual_review"].fillna(False).astype(bool)
    canonical["review_reasons"] = canonical["review_reasons"].fillna("").astype(str)

    rows: list[pd.Series] = []
    duplicate_rows: list[dict[str, object]] = []
    conflict_rows: list[dict[str, object]] = []

    dedupeable = canonical["dedupe_key"].astype(str).str.strip().ne("")
    for _, group in canonical[dedupeable].groupby("dedupe_key", sort=False, dropna=False):
        merged, conflicts = _merge_duplicate_group(group)
        rows.append(merged)
        if len(group) > 1:
            duplicate_rows.append(
                {
                    "dedupe_key": merged["dedupe_key"],
                    "channel": merged.get("channel", ""),
                    "content_id": merged.get("content_id", ""),
                    "merged_row_count": int(len(group)),
                    "source_files": _join_unique_nonblank(group["source_file"]),
                    "material_ids": _join_unique_nonblank(group["material_id"]),
                }
            )
        conflict_rows.extend(conflicts)

    for _, row in canonical[~dedupeable].iterrows():
        rows.append(row)

    if rows:
        result = pd.DataFrame(rows).reset_index(drop=True)
    else:
        result = canonical
    result = _mark_manual_review_reasons(result)
    missing_details = _build_missing_value_details(result)
    return {
        "canonical": _ordered_columns(result),
        "duplicate_merge_details": pd.DataFrame(
            duplicate_rows,
            columns=["dedupe_key", "channel", "content_id", "merged_row_count", "source_files", "material_ids"],
        ),
        "conflict_retention_details": pd.DataFrame(
            conflict_rows,
            columns=["dedupe_key", "channel", "content_id", "column", "values", "action", "relative_difference"],
        ),
        "missing_value_details": missing_details,
    }


def _dedupe_key(row: pd.Series) -> str:
    channel = "" if _is_blank(row.get("channel")) else str(row.get("channel")).strip()
    content_id = "" if _is_blank(row.get("content_id")) else str(row.get("content_id")).strip()
    if not channel:
        return ""
    if content_id:
        return f"{channel}::id::{content_id}"
    title = "" if _is_blank(row.get("title")) else re.sub(r"\s+", "", str(row.get("title")).strip()).lower()
    if not title:
        return ""
    return f"{channel}::title::{title}"


def _merge_duplicate_group(group: pd.DataFrame) -> tuple[pd.Series, list[dict[str, object]]]:
    merged = group.iloc[0].copy()
    conflicts: list[dict[str, object]] = []
    merged["merged_row_count"] = int(len(group))
    if len(group) == 1:
        return merged, conflicts

    conflict_columns: list[str] = []
    for column in NUMERIC_COLUMNS:
        values = pd.to_numeric(group[column], errors="coerce").dropna()
        if values.empty:
            merged[column] = float("nan")
            continue
        unique_values = list(dict.fromkeys(float(value) for value in values))
        if len(unique_values) <= 1:
            merged[column] = unique_values[0]
            continue
        relative_difference = _relative_difference(unique_values)
        if relative_difference > 0.05:
            merged[column] = float(sum(unique_values))
            action = "sum"
        else:
            merged[column] = unique_values[0]
            action = "first_non_blank"
        conflict_columns.append(column)
        conflicts.append(
            {
                "dedupe_key": merged.get("dedupe_key", ""),
                "channel": merged.get("channel", ""),
                "content_id": merged.get("content_id", ""),
                "column": column,
                "values": " | ".join(_format_number(value) for value in unique_values),
                "action": action,
                "relative_difference": relative_difference,
            }
        )

    for column in group.columns:
        if column in NUMERIC_COLUMNS or column in {"merged_row_count", "conflict_details", "needs_manual_review", "review_reasons"}:
            continue
        if column == "dedupe_key":
            merged[column] = group[column].iloc[0]
        else:
            merged[column] = _first_non_blank_value(group[column])
    existing_reasons: list[str] = []
    for value in group.get("review_reasons", pd.Series(dtype=object)).tolist():
        existing_reasons.extend(_split_reasons(value))
    existing_conflicts = [
        str(value).strip()
        for value in group.get("conflict_details", pd.Series(dtype=object)).tolist()
        if not _is_blank(value)
    ]
    if conflict_columns:
        existing_conflicts.extend(f"{item['column']}={item['values']}->{item['action']}" for item in conflicts)
        merged["needs_manual_review"] = True
        existing_reasons.append("数值冲突")
    elif bool(group.get("needs_manual_review", pd.Series(dtype=bool)).astype(bool).any()):
        merged["needs_manual_review"] = True
    merged["conflict_details"] = "; ".join(dict.fromkeys(existing_conflicts))
    merged["review_reasons"] = "；".join(dict.fromkeys(reason for reason in existing_reasons if reason))
    return merged, conflicts


def _relative_difference(values: list[float]) -> float:
    positives = [abs(value) for value in values if value != 0]
    if not positives:
        return 0.0
    return (max(values) - min(values)) / min(positives)


def _mark_manual_review_reasons(canonical: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical.copy()
    for index, row in canonical.iterrows():
        reasons = _split_reasons(row.get("review_reasons", ""))
        if _is_blank(row.get("content_id")):
            reasons.append("内容ID缺失")
        if _is_blank(row.get("account")) and str(row.get("channel", "")).strip() == "B站" and not _is_blank(row.get("account_id")):
            reasons.append("账号映射缺失")
        if not _is_blank(row.get("conflict_details")):
            reasons.append("数值冲突")
        unique_reasons = []
        for reason in reasons:
            if reason and reason not in unique_reasons:
                unique_reasons.append(reason)
        canonical.at[index, "review_reasons"] = "；".join(unique_reasons)
        canonical.at[index, "needs_manual_review"] = bool(unique_reasons)
    return canonical


def _split_reasons(value: object) -> list[str]:
    if _is_blank(value):
        return []
    return [token.strip() for token in re.split(r"[;；]", str(value)) if token.strip()]


def _first_non_blank_value(series: pd.Series) -> str:
    for value in series:
        if not _is_blank(value):
            return str(value).strip()
    return ""


def _build_missing_value_details(canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    key_columns = ["content_id", "material_id", "title", "account", "category_l1", "category_l2"]
    for column in key_columns:
        if column not in canonical.columns:
            continue
        mask = canonical[column].map(_is_blank)
        for _, row in canonical[mask].iterrows():
            rows.append(
                {
                    "channel": row.get("channel", ""),
                    "content_id": row.get("content_id", ""),
                    "material_id": row.get("material_id", ""),
                    "title": row.get("title", ""),
                    "missing_column": column,
                    "action": "保留为空并进入质量扫描",
                }
            )
    return pd.DataFrame(
        rows,
        columns=["channel", "content_id", "material_id", "title", "missing_column", "action"],
    )


def _build_preprocessing_report(
    canonical: pd.DataFrame,
    preprocessing: Mapping[str, pd.DataFrame],
    data_quality: pd.DataFrame,
) -> pd.DataFrame:
    duplicate_details = preprocessing["duplicate_merge_details"]
    conflict_details = preprocessing["conflict_retention_details"]
    missing_details = preprocessing["missing_value_details"]
    rows = [
        {
            "metric": "标准化后行数",
            "value": int(len(canonical)),
            "count": int(len(canonical)),
            "total": int(len(canonical)),
            "status": "完成",
            "note": "标准化并完成渠道内去重后的 canonical 行数。",
        },
        {
            "metric": "重复合并组数",
            "value": int(len(duplicate_details)),
            "count": int(len(duplicate_details)),
            "total": int(len(canonical)),
            "status": "需复核" if not duplicate_details.empty else "通过",
            "note": "按 channel + content_id 合并，content_id 为空不合并。",
        },
        {
            "metric": "冲突保留字段数",
            "value": int(len(conflict_details)),
            "count": int(len(conflict_details)),
            "total": int(len(canonical)),
            "status": "需复核" if not conflict_details.empty else "通过",
            "note": "数值冲突相对差异大于 5% 时求和，否则首个非空值，全部冲突值保留。",
        },
        {
            "metric": "缺失值明细数",
            "value": int(len(missing_details)),
            "count": int(len(missing_details)),
            "total": int(len(canonical)),
            "status": "需处理" if not missing_details.empty else "通过",
            "note": "关键字段缺失保留为空，并进入人工审核或质量扫描。",
        },
    ]
    if not data_quality.empty:
        rows.extend(data_quality.to_dict(orient="records"))
    return pd.DataFrame(rows, columns=["metric", "value", "count", "total", "status", "note"])


def _format_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _first_non_blank(raw: pd.DataFrame, candidates: Iterable[str]) -> pd.Series:
    result = pd.Series([pd.NA] * len(raw), index=raw.index, dtype="object")
    for column in candidates:
        if column not in raw.columns:
            continue
        values = raw[column].astype("object")
        mask = result.map(_is_blank) & ~values.map(_is_blank)
        result.loc[mask] = values.loc[mask]
    return result


def _xiaohongshu_category_status(row: pd.Series) -> str:
    if not _is_blank(row.get("内容分类")):
        return "人工标记"
    if not _is_blank(row.get("内容类型")):
        return "人工标记"
    if not _is_blank(row.get("内容类型_映射")):
        return "人工标记"
    return ""


def _complete_categories(
    canonical: pd.DataFrame,
    rules: Mapping[str, Iterable[str]],
    *,
    references: ReferenceTables,
    env_path: Optional[Path] = None,
    category_matcher: Optional[CategoryMatcher] = None,
    category_mappings: Optional[CategoryMappings] = None,
) -> pd.DataFrame:
    canonical = canonical.copy()
    raw_category = canonical["manual_category"].where(~canonical["manual_category"].map(_is_blank), "")
    canonical["primary_category"] = ""
    canonical["category_confidence"] = 0.0

    canonical["manual_category"] = ""
    canonical["ai_category"] = ""
    canonical["content_category"] = ""
    canonical["category_l2_source"] = ""
    _apply_fixed_channel_categories(canonical)
    has_category = raw_category.astype(str).str.strip().ne("")
    writable_manual = has_category & canonical["content_category"].map(_is_blank)
    canonical.loc[writable_manual, "manual_category"] = raw_category.loc[writable_manual].astype(str).str.strip()
    canonical.loc[writable_manual, "content_category"] = raw_category.loc[writable_manual].astype(str).str.strip()
    canonical.loc[writable_manual, "category_status"] = "人工标记"
    canonical.loc[writable_manual, "category_confidence"] = 1.0

    if category_mappings:
        _apply_category_mappings(canonical, category_mappings)

    _apply_account_content_type_mappings(canonical, references.account_content_type)

    tag_category = canonical["title"].map(category_from_tags)
    has_tag_category = tag_category.astype(str).str.strip().ne("")
    _apply_tag_categories(canonical, tag_category, has_tag_category)

    unmatched_tag_category = has_tag_category & canonical["content_category"].map(_is_blank)
    canonical.loc[unmatched_tag_category, "ai_category"] = tag_category.loc[unmatched_tag_category]
    canonical.loc[unmatched_tag_category, "content_category"] = tag_category.loc[unmatched_tag_category]
    canonical.loc[unmatched_tag_category, "category_status"] = "TAG匹配"
    canonical.loc[unmatched_tag_category, "category_confidence"] = 0.95

    blank_tag_ai = has_tag_category & canonical["ai_category"].map(_is_blank)
    canonical.loc[blank_tag_ai, "ai_category"] = tag_category.loc[blank_tag_ai]

    keyword_category = canonical["title"].map(lambda title: suggest_category(title, rules))
    has_keyword_category = keyword_category.astype(str).str.strip().ne("") & canonical["content_category"].map(_is_blank)
    canonical.loc[has_keyword_category, "ai_category"] = keyword_category.loc[has_keyword_category]
    canonical.loc[has_keyword_category, "content_category"] = keyword_category.loc[has_keyword_category]
    canonical.loc[has_keyword_category, "category_status"] = "标题关键词匹配"
    canonical.loc[has_keyword_category, "category_confidence"] = 0.75

    category_library = _build_category_library(raw_category, tag_category, keyword_category, canonical["content_category"])
    pending = canonical[canonical["content_category"].map(_is_blank)]
    if category_library and not pending.empty:
        matcher = category_matcher or _default_category_matcher
        for channel, group in pending.groupby("channel", dropna=False):
            scoped_library = _category_library_for_channel(canonical, str(channel), category_library)
            matched = matcher(group.copy(), scoped_library, env_path)
            for index, category in matched.items():
                if index not in canonical.index:
                    continue
                normalized = str(category).strip()
                if normalized not in scoped_library:
                    continue
                canonical.at[index, "ai_category"] = normalized
                canonical.at[index, "content_category"] = normalized
                canonical.at[index, "category_status"] = "DeepSeek匹配"
                canonical.at[index, "category_confidence"] = 0.65

    _fill_missing_secondary_categories(canonical)

    still_missing = canonical["content_category"].map(_is_blank)
    canonical.loc[still_missing, "category_status"] = "未匹配"
    canonical.loc[~still_missing & canonical["category_status"].map(_is_blank), "category_status"] = "人工标记"
    canonical.loc[~still_missing & canonical["category_confidence"].eq(0.0), "category_confidence"] = 1.0
    canonical["primary_category"] = ""
    canonical["category_l1"] = ""
    canonical["category_l2"] = canonical["content_category"].fillna("").astype(str)
    canonical["category_l3"] = canonical["category_l3"].where(~canonical["category_l3"].map(_is_blank), canonical["title"])
    canonical["category_source"] = canonical["category_status"].fillna("").astype(str)
    missing_l2_source = canonical["category_l2_source"].map(_is_blank)
    canonical.loc[missing_l2_source, "category_l2_source"] = canonical.loc[missing_l2_source, "category_status"]
    canonical["review_status"] = canonical.apply(_review_status, axis=1)
    canonical = _mark_category_review_reasons(canonical)
    return canonical


def _apply_fixed_channel_categories(canonical: pd.DataFrame) -> None:
    bilibili = canonical["channel"].fillna("").astype(str).str.strip().eq("B站")
    if not bilibili.any():
        return
    canonical.loc[bilibili, "manual_category"] = ""
    canonical.loc[bilibili, "ai_category"] = "B站全部"
    canonical.loc[bilibili, "content_category"] = "B站全部"
    canonical.loc[bilibili, "category_status"] = "渠道固定规则"
    canonical.loc[bilibili, "category_l2_source"] = "渠道固定规则"
    canonical.loc[bilibili, "category_confidence"] = 1.0


def _fill_missing_secondary_categories(canonical: pd.DataFrame) -> None:
    missing = canonical["content_category"].map(_is_blank)
    non_bilibili = ~canonical["channel"].fillna("").astype(str).str.strip().eq("B站")
    candidates = canonical[missing & non_bilibili]
    if candidates.empty:
        return

    known = canonical[~canonical["content_category"].map(_is_blank) & non_bilibili].copy()
    if known.empty:
        return

    account_lookup = _majority_category_lookup(known, ["channel", "account"])
    topic_lookup = _majority_category_lookup(known, ["channel", "category_l3"])
    channel_lookup = _single_category_lookup(known, ["channel"])

    for index, row in candidates.iterrows():
        category = ""
        source = ""
        account_key = _lookup_key(row, ["channel", "account"])
        if account_key in account_lookup:
            category = account_lookup[account_key]
            source = "同账号栏目补全"
        if not category:
            topic_key = _lookup_key(row, ["channel", "category_l3"])
            if topic_key in topic_lookup:
                category = topic_lookup[topic_key]
                source = "同题材栏目补全"
        if not category:
            channel_key = _lookup_key(row, ["channel"])
            if channel_key in channel_lookup:
                category = channel_lookup[channel_key]
                source = "同渠道栏目补全"
        if not category:
            continue
        canonical.at[index, "ai_category"] = category
        canonical.at[index, "content_category"] = category
        canonical.at[index, "category_status"] = source
        canonical.at[index, "category_l2_source"] = source
        canonical.at[index, "category_confidence"] = 0.55


def _majority_category_lookup(known: pd.DataFrame, key_columns: list[str]) -> dict[tuple[str, ...], str]:
    lookup: dict[tuple[str, ...], str] = {}
    for key, group in known.groupby(key_columns, dropna=False):
        normalized_key = key if isinstance(key, tuple) else (key,)
        clean_key = tuple("" if pd.isna(value) else str(value).strip() for value in normalized_key)
        if any(not value for value in clean_key):
            continue
        counts = group["content_category"].fillna("").astype(str).str.strip().value_counts()
        counts = counts[counts.index != ""]
        if counts.empty:
            continue
        if len(counts) == 1 or counts.iloc[0] > counts.iloc[1]:
            lookup[clean_key] = str(counts.index[0])
    return lookup


def _single_category_lookup(known: pd.DataFrame, key_columns: list[str]) -> dict[tuple[str, ...], str]:
    lookup: dict[tuple[str, ...], str] = {}
    for key, group in known.groupby(key_columns, dropna=False):
        normalized_key = key if isinstance(key, tuple) else (key,)
        clean_key = tuple("" if pd.isna(value) else str(value).strip() for value in normalized_key)
        if any(not value for value in clean_key):
            continue
        values = group["content_category"].fillna("").astype(str).str.strip()
        unique = [value for value in values.unique() if value]
        if len(unique) == 1:
            lookup[clean_key] = unique[0]
    return lookup


def _lookup_key(row: pd.Series, columns: list[str]) -> tuple[str, ...]:
    return tuple("" if pd.isna(row.get(column, "")) else str(row.get(column, "")).strip() for column in columns)


def _apply_tag_categories(canonical: pd.DataFrame, tag_category: pd.Series, has_tag_category: pd.Series) -> None:
    channel = canonical["channel"].fillna("").astype(str)
    tag_scoped = has_tag_category & (
        channel.str.contains("小红书", na=False) | channel.str.contains("抖音", na=False)
    )
    if not tag_scoped.any():
        return

    for index in canonical.index[tag_scoped]:
        tag_value = str(tag_category.loc[index]).strip()
        current = "" if _is_blank(canonical.at[index, "content_category"]) else str(canonical.at[index, "content_category"]).strip()
        if current and current != tag_value:
            reasons = _split_reasons(canonical.at[index, "review_reasons"])
            reasons.append(f"TAG分类与原始分类冲突：{tag_value} / {current}")
            canonical.at[index, "review_reasons"] = "；".join(dict.fromkeys(reason for reason in reasons if reason))
            canonical.at[index, "needs_manual_review"] = True
            canonical.at[index, "conflict_details"] = _join_non_blank(
                [canonical.at[index, "conflict_details"], f"TAG分类={tag_value}; 原始分类={current}"]
            )
        canonical.at[index, "ai_category"] = tag_value
        canonical.at[index, "content_category"] = tag_value
        canonical.at[index, "category_status"] = "TAG匹配"
        canonical.at[index, "category_confidence"] = 0.95


def _apply_account_content_type_mappings(canonical: pd.DataFrame, account_content_type: pd.DataFrame) -> None:
    if account_content_type.empty:
        return
    required = {"channel", "account", "category_l2"}
    if not required.issubset(set(account_content_type.columns)):
        return
    mapping: dict[tuple[str, str], Mapping[str, str]] = {}
    for _, row in account_content_type.fillna("").iterrows():
        channel = str(row.get("channel", "")).strip()
        account = str(row.get("account", "")).strip()
        category_l2 = str(row.get("category_l2", "")).strip()
        if not channel or not account or not category_l2:
            continue
        mapping[(channel, account)] = {
            "category_l1": str(row.get("category_l1", "")).strip(),
            "category_l2": category_l2,
            "category_l3": str(row.get("category_l3", "")).strip(),
        }
    if not mapping:
        return

    for index, row in canonical.iterrows():
        if not _is_blank(row.get("content_category")):
            continue
        key = (str(row.get("channel", "")).strip(), str(row.get("account", "")).strip())
        item = mapping.get(key)
        if not item:
            continue
        canonical.at[index, "manual_category"] = item["category_l2"]
        canonical.at[index, "content_category"] = item["category_l2"]
        canonical.at[index, "category_status"] = "账号内容类型对照"
        canonical.at[index, "category_confidence"] = 1.0
        if item["category_l3"]:
            canonical.at[index, "category_l3"] = item["category_l3"]


def _mark_category_review_reasons(canonical: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical.copy()
    for index, row in canonical.iterrows():
        reasons = _split_reasons(row.get("review_reasons", ""))
        if row.get("review_status") in {"待审核", "待复核"}:
            reasons.append("分类待复核")
        unique_reasons = []
        for reason in reasons:
            if reason and reason not in unique_reasons:
                unique_reasons.append(reason)
        canonical.at[index, "review_reasons"] = "；".join(unique_reasons)
        canonical.at[index, "needs_manual_review"] = bool(unique_reasons)
    return canonical


def _apply_category_mappings(canonical: pd.DataFrame, category_mappings: CategoryMappings) -> None:
    for index, row in canonical.iterrows():
        if not _is_blank(row.get("content_category")):
            continue
        mapping = _lookup_category_mapping(row, category_mappings)
        if not mapping:
            continue
        l2 = str(mapping.get("category_l2", "")).strip()
        l3 = str(mapping.get("category_l3", "")).strip()
        if l2:
            canonical.at[index, "manual_category"] = l2
            canonical.at[index, "content_category"] = l2
            canonical.at[index, "category_status"] = "历史审核映射"
            canonical.at[index, "category_confidence"] = 1.0
        if l3:
            canonical.at[index, "category_l3"] = l3


def _lookup_category_mapping(
    row: pd.Series,
    category_mappings: CategoryMappings,
) -> Optional[Mapping[str, str]]:
    for key in _category_mapping_keys(row):
        mapping = category_mappings.get(key)
        if mapping:
            return mapping
    return None


def _category_mapping_keys(row: pd.Series) -> list[str]:
    keys: list[str] = []
    for column in ["content_id", "material_id", "title"]:
        value = "" if pd.isna(row.get(column, "")) else str(row.get(column, "")).strip()
        if value:
            keys.append(f"{column}:{value}")
    return keys


def _review_status(row: pd.Series) -> str:
    if _is_blank(row.get("category_l2")):
        return "待审核"
    source = str(row.get("category_source", "")).strip()
    if source in {"人工标记", "历史审核映射"}:
        return "已确认"
    confidence = parse_number(row.get("category_confidence"))
    if not pd.isna(confidence) and confidence >= 0.9:
        return "已确认"
    return "待复核"


def _default_category_matcher(
    items: pd.DataFrame,
    category_library: list[str],
    env_path: Optional[Path],
) -> Mapping[int, str]:
    from .ai import match_missing_categories

    return match_missing_categories(items, category_library, env_path)


def _build_category_library(*series_list: pd.Series) -> list[str]:
    values: list[str] = []
    for series in series_list:
        clean = series.where(~series.map(_is_blank), "").astype(str).str.strip()
        for value in clean:
            if value and value not in values:
                values.append(value)
    return values


def _category_library_for_channel(canonical: pd.DataFrame, channel: str, fallback: list[str]) -> list[str]:
    channel_name = str(channel).strip()
    scoped = canonical[canonical["channel"].fillna("").astype(str).str.strip().eq(channel_name)]
    library = _build_category_library(scoped["content_category"]) if not scoped.empty else []
    return library or fallback


def _derive_metrics(canonical: pd.DataFrame) -> pd.DataFrame:
    canonical = canonical.copy()
    canonical["ctr"] = _safe_divide(canonical["clicks"], canonical["impressions"])
    canonical["activation_rate"] = _safe_divide(canonical["activations"], canonical["clicks"])
    canonical["first_pay_rate"] = _safe_divide(canonical["first_pay_count"], canonical["activations"])
    canonical["activation_cost"] = _safe_divide(canonical["spend"], canonical["activations"])
    canonical["first_pay_cost"] = _safe_divide(canonical["spend"], canonical["first_pay_count"])
    return canonical


def _read_channel_totals(path: Path) -> pd.DataFrame:
    preview = _read_table(path, sheet_name=0, header=None, nrows=20)
    header_row = None
    for idx, row in preview.iterrows():
        if row.astype(str).str.strip().eq("渠道").any():
            header_row = int(idx)
            break
    if header_row is None:
        raise ValueError(f"{path.name} 中未找到渠道汇总表头")

    raw = _read_table(path, sheet_name=0, header=header_row)
    raw = raw.dropna(axis=1, how="all")
    if "渠道" not in raw.columns:
        channel_col = next((col for col in raw.columns if str(col).strip() == "渠道"), None)
        if channel_col is None:
            raise ValueError(f"{path.name} 中未找到渠道列")
        raw = raw.rename(columns={channel_col: "渠道"})
    keep = [column for column in ["渠道", "消耗", "激活", "付费"] if column in raw.columns]
    totals = raw[keep].copy()
    totals = totals[~totals["渠道"].map(_is_blank)]
    totals = totals.rename(
        columns={
            "渠道": "channel",
            "消耗": "spend_total",
            "激活": "activations_total",
            "付费": "first_pay_count_total",
        }
    )
    for column in ["spend_total", "activations_total", "first_pay_count_total"]:
        if column in totals.columns:
            totals[column] = totals[column].map(parse_number)
    return totals


def _summarize_channels(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby("channel", as_index=False)
        .agg(
            platform=("platform", "first"),
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values("spend", ascending=False)
    )
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    return summary.reset_index(drop=True)


def _summarize_platforms(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby("channel", as_index=False)
        .agg(
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values("spend", ascending=False)
    )
    total_spend = _sum_or_zero(summary["spend"])
    total_activations = _sum_or_zero(summary["activations"])
    total_first_pay = _sum_or_zero(summary["first_pay_count"])
    summary["spend_share"] = summary["spend"] / total_spend if total_spend else 0.0
    summary["activation_share"] = summary["activations"] / total_activations if total_activations else 0.0
    summary["first_pay_share"] = summary["first_pay_count"] / total_first_pay if total_first_pay else 0.0
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_rate"] = _safe_divide(summary["activations"], summary["clicks"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    return summary.reset_index(drop=True)


def _summarize_platform_categories(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby(["channel", "account", "content_category", "category_l3"], as_index=False)
        .agg(
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values(["channel", "activations"], ascending=[True, False])
    )
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_rate"] = _safe_divide(summary["activations"], summary["clicks"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    summary["category_display"] = summary["content_category"].fillna("").astype(str)
    summary = _add_scoring_columns(summary)
    return summary.sort_values(["channel", "overall_score"], ascending=[True, False]).reset_index(drop=True)


def _summarize_categories(canonical: pd.DataFrame) -> pd.DataFrame:
    summary = (
        canonical.groupby(["content_category", "category_l3"], as_index=False)
        .agg(
            channel_count=("channel", "nunique"),
            account_count=("account", lambda values: values.replace("", pd.NA).dropna().nunique()),
            item_count=("content_id", "count"),
            spend=("spend", _sum_or_blank),
            impressions=("impressions", _sum_or_blank),
            clicks=("clicks", _sum_or_blank),
            activations=("activations", _sum_or_blank),
            first_pay_count=("first_pay_count", _sum_or_blank),
        )
        .sort_values("activations", ascending=False)
    )
    summary["ctr"] = _safe_divide(summary["clicks"], summary["impressions"])
    summary["activation_cost"] = _safe_divide(summary["spend"], summary["activations"])
    summary["first_pay_cost"] = _safe_divide(summary["spend"], summary["first_pay_count"])
    summary["first_pay_rate"] = _safe_divide(summary["first_pay_count"], summary["activations"])
    summary["category_display"] = summary["content_category"].fillna("").astype(str)
    summary = _add_scoring_columns(summary)
    return summary.sort_values("overall_score", ascending=False).reset_index(drop=True)


def _add_scoring_columns(summary: pd.DataFrame) -> pd.DataFrame:
    summary = summary.copy()
    summary["heat_score"] = (
        0.45 * _rank_pct(summary["impressions"])
        + 0.35 * _rank_pct(summary["clicks"])
        + 0.20 * _rank_pct(summary["ctr"])
    )
    inverse_activation_cost = 1 - _rank_pct(summary["activation_cost"].replace([float("inf")], pd.NA))
    summary["acquisition_score"] = (
        0.35 * _rank_pct(summary["activations"])
        + 0.25 * _rank_pct(summary["first_pay_count"])
        + 0.15 * inverse_activation_cost
        + 0.15 * _rank_pct(summary["first_pay_rate"])
        + 0.10 * summary["heat_score"]
    )
    summary["overall_score"] = (summary["acquisition_score"] * 100).round(2)
    return summary


def _make_total_summary(canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    total_spend = _sum_or_zero(canonical["spend"])
    total_activations = _sum_or_zero(canonical["activations"])
    total_first_pay = _sum_or_zero(canonical["first_pay_count"])

    for channel, group in canonical.groupby("channel", sort=False):
        rows.append(_total_row(channel, group, total_spend, total_activations, total_first_pay))
    rows.append(_total_row("总计", canonical, total_spend, total_activations, total_first_pay))
    return pd.DataFrame(rows)


def _total_row(
    channel: str,
    group: pd.DataFrame,
    total_spend: float,
    total_activations: float,
    total_first_pay: float,
) -> Dict[str, float]:
    spend = _sum_or_zero(group["spend"])
    activations = _sum_or_zero(group["activations"])
    first_pay = _sum_or_zero(group["first_pay_count"])
    impressions = _sum_or_zero(group["impressions"])
    clicks = _sum_or_zero(group["clicks"])
    pending = group[group["content_category"].map(_is_blank)]
    pending_spend = _sum_or_zero(pending["spend"])
    return {
        "channel": channel,
        "spend": spend,
        "spend_share": spend / total_spend if total_spend else 0.0,
        "impressions": impressions,
        "clicks": _sum_or_blank(group["clicks"]),
        "ctr": clicks / impressions if impressions and not pd.isna(clicks) else float("nan"),
        "activations": activations,
        "activation_share": activations / total_activations if total_activations else 0.0,
        "activation_cost": spend / activations if activations else 0.0,
        "first_pay_count": first_pay,
        "first_pay_share": first_pay / total_first_pay if total_first_pay else 0.0,
        "first_pay_cost": spend / first_pay if first_pay else 0.0,
        "first_pay_rate": first_pay / activations if activations else 0.0,
        "item_count": float(len(group)),
        "pending_item_count": float(len(pending)),
        "pending_spend": pending_spend,
        "pending_spend_share": pending_spend / spend if spend else 0.0,
        "secondary_category_count": float(group["content_category"].replace("", pd.NA).dropna().nunique()),
    }


def collect_raw_category_stats(input_dir: Path) -> pd.DataFrame:
    specs = [
        ("B站.xlsx", "sheet1", 0),
        ("小红书商业化.xlsx", "kos账户投放数据", 0),
        ("小红书商业化.xlsx", "内容表格", 1),
        ("抖音商业化.xlsx", "Sheet2", 0),
        ("抖音商业化.xlsx", "Sheet1", 3),
        ("抖音市场部.xlsx", "Sheet2", 0),
    ]
    rows = []
    for file_name, sheet_name, header in specs:
        try:
            path = _find_file(Path(input_dir), [Path(file_name).stem])
        except FileNotFoundError:
            continue
        try:
            frame = _read_table(path, sheet_name=sheet_name, header=header)
        except Exception:
            continue
        for raw_field in ["类型", "内容分类", "内容类型"]:
            if raw_field not in frame.columns:
                continue
            values = frame[raw_field].dropna().astype(str).str.strip()
            values = values[values.ne("") & values.ne("0") & values.str.lower().ne("nan")]
            for value, count in values.value_counts().items():
                rows.append(
                    {
                        "source_file": path.name,
                        "sheet": sheet_name,
                        "raw_field": raw_field,
                        "value": value,
                        "count": int(count),
                    }
                )
    return pd.DataFrame(rows, columns=["source_file", "sheet", "raw_field", "value", "count"])


def _build_data_quality_report(canonical: pd.DataFrame) -> pd.DataFrame:
    total = int(len(canonical))
    rows = []

    def add_rate(metric: str, mask: pd.Series, note: str) -> None:
        count = int(mask.sum())
        rows.append(
            {
                "metric": metric,
                "value": count / total if total else 0.0,
                "count": count,
                "total": total,
                "status": "需处理" if count else "通过",
                "note": note,
            }
        )

    add_rate("二级分类缺失率", canonical["content_category"].map(_is_blank), "二级分类对应当前内容类型，是推荐分析的主分类。")
    add_rate("素材ID缺失率", canonical["material_id"].map(_is_blank), "素材ID缺失会影响按素材追踪。")
    add_rate("内容ID缺失率", canonical["content_id"].map(_is_blank), "内容ID缺失会影响去重和历史复用。")
    add_rate("标题缺失率", canonical["title"].map(_is_blank), "标题缺失会影响AI分类和三级题材暂代。")
    add_rate("账号/作者缺失率", canonical["account"].map(_is_blank), "账号缺失会影响账号维度筛选。")

    impressions = pd.to_numeric(canonical["impressions"], errors="coerce").fillna(0.0)
    clicks = pd.to_numeric(canonical["clicks"], errors="coerce").fillna(0.0)
    spend = pd.to_numeric(canonical["spend"], errors="coerce").fillna(0.0)
    activations = pd.to_numeric(canonical["activations"], errors="coerce").fillna(0.0)
    anomalies = [
        ("展示为0但点击大于0", impressions.eq(0.0) & clicks.gt(0.0), "展示为0时点击率无法解释，需要回查原表。"),
        ("消耗为0但激活大于0", spend.eq(0.0) & activations.gt(0.0), "消耗为0时成本类指标无法解释，需要回查原表。"),
        ("消耗小于0", spend.lt(0.0), "负消耗通常来自退款或导出异常，需人工确认。"),
    ]
    for metric, mask, note in anomalies:
        count = int(mask.sum())
        rows.append(
            {
                "metric": metric,
                "value": float(count),
                "count": count,
                "total": total,
                "status": "需处理" if count else "通过",
                "note": note,
            }
        )
    return pd.DataFrame(rows, columns=["metric", "value", "count", "total", "status", "note"])


def _build_review_queue(canonical: pd.DataFrame) -> pd.DataFrame:
    queue = canonical[
        canonical["review_status"].isin(["待审核", "待复核"])
        | canonical["content_category"].map(_is_blank)
        | canonical["needs_manual_review"].astype(bool)
    ].copy()
    if queue.empty:
        return pd.DataFrame(
            columns=[
                "review_status",
                "needs_manual_review",
                "review_reasons",
                "channel",
                "title",
                "account_id",
                "account_raw",
                "account",
                "account_mapping_source",
                "content_id",
                "material_id",
                "dedupe_key",
                "merged_row_count",
                "conflict_details",
                "category_l2",
                "category_l3",
                "category_source",
                "category_confidence",
                "spend",
                "activations",
                "activation_cost",
                "source_file",
                "source_sheet",
                "source_row",
                "source_file_hash",
                "duplicate_group_id",
                "review_action",
            ]
        )
    columns = [
        "review_status",
        "needs_manual_review",
        "review_reasons",
        "channel",
        "title",
        "account_id",
        "account_raw",
        "account",
        "account_mapping_source",
        "content_id",
        "material_id",
        "dedupe_key",
        "merged_row_count",
        "conflict_details",
        "category_l2",
        "category_l3",
        "category_source",
        "category_confidence",
        "spend",
        "activations",
        "activation_cost",
        "source_file",
        "source_sheet",
        "source_row",
        "source_file_hash",
        "duplicate_group_id",
        "review_action",
    ]
    return queue.sort_values(["spend", "activations"], ascending=[False, False])[columns].reset_index(drop=True)


def _build_account_audit(canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    canonical = canonical.copy()
    canonical["account"] = canonical["account"].fillna("").astype(str).str.strip()
    for platform, expected_accounts in EXPECTED_ACCOUNTS.items():
        if platform == "抖音":
            platform_items = canonical[canonical["channel"].astype(str).str.contains("抖音", na=False)]
        elif platform == "小红书":
            platform_items = canonical[canonical["channel"].astype(str).str.contains("小红书", na=False)]
        else:
            platform_items = canonical[canonical["channel"].eq(platform)]
        observed = platform_items["account"].replace("", pd.NA).dropna()
        observed_set = set(observed.astype(str))
        for expected_account in expected_accounts:
            count = int(platform_items["account"].eq(expected_account).sum())
            rows.append(
                {
                    "channel": platform,
                    "expected_account": expected_account,
                    "status": "已覆盖" if count else "缺失",
                    "observed_count": count,
                    "matched_account": expected_account if count else "",
                }
            )
        for account in sorted(observed_set - set(expected_accounts)):
            rows.append(
                {
                    "channel": platform,
                    "expected_account": "",
                    "status": "异常账号",
                    "observed_count": int(platform_items["account"].eq(account).sum()),
                    "matched_account": account,
                }
            )
    return pd.DataFrame(
        rows,
        columns=["channel", "expected_account", "status", "observed_count", "matched_account"],
    )


def _summarize_top_content(canonical: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for channel, group in canonical.groupby("channel", sort=False):
        ranked = group.sort_values("spend", ascending=False).head(15).copy()
        spend_threshold = pd.to_numeric(group["spend"], errors="coerce").quantile(0.75)
        activation_median = pd.to_numeric(group["activations"], errors="coerce").median()
        cost_median = pd.to_numeric(group["activation_cost"], errors="coerce").median()
        for _, item in ranked.iterrows():
            rows.append(
                {
                    "channel": channel,
                    "title": item.get("title", ""),
                    "account_id": item.get("account_id", ""),
                    "account": item.get("account", ""),
                    "manual_category": item.get("manual_category", ""),
                    "ai_category": item.get("ai_category", ""),
                    "content_category": item.get("content_category", ""),
                    "spend": item.get("spend", 0.0),
                    "impressions": item.get("impressions", 0.0),
                    "clicks": item.get("clicks", 0.0),
                    "activations": item.get("activations", 0.0),
                    "first_pay_count": item.get("first_pay_count", 0.0),
                    "activation_cost": item.get("activation_cost", pd.NA),
                    "first_pay_cost": item.get("first_pay_cost", pd.NA),
                    "ctr": item.get("ctr", pd.NA),
                    "cover_url": item.get("cover_url", ""),
                    "content_url": item.get("content_url", ""),
                    "performance_flag": _content_performance_flag(
                        item, spend_threshold, activation_median, cost_median
                    ),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "channel",
            "title",
            "account_id",
            "account",
            "manual_category",
            "ai_category",
            "content_category",
            "spend",
            "impressions",
            "clicks",
            "activations",
            "first_pay_count",
            "activation_cost",
            "first_pay_cost",
            "ctr",
            "cover_url",
            "content_url",
            "performance_flag",
        ],
    )


def _content_performance_flag(
    item: pd.Series,
    spend_threshold: float,
    activation_median: float,
    cost_median: float,
) -> str:
    spend = parse_number(item.get("spend"))
    activations = parse_number(item.get("activations"))
    first_pay = parse_number(item.get("first_pay_count"))
    activation_cost = parse_number(item.get("activation_cost"))
    if first_pay > 0 and spend >= spend_threshold:
        return "爆款候选"
    if activations > 0 and not pd.isna(activation_cost) and activation_cost <= cost_median:
        return "高转化低成本"
    if spend >= spend_threshold and activations <= activation_median:
        return "高消耗低转化"
    return "常规观察"


def _summarize_cover_metrics(canonical: pd.DataFrame) -> pd.DataFrame:
    scoped = canonical[
        canonical["channel"].eq("B站") | canonical["channel"].astype(str).str.contains("小红书", na=False)
    ].copy()
    if scoped.empty:
        return pd.DataFrame(
            columns=[
                "channel",
                "title",
                "account_id",
                "account",
                "manual_category",
                "ai_category",
                "content_category",
                "cover_url",
                "content_url",
                "spend",
                "impressions",
                "clicks",
                "ctr",
                "activations",
                "activation_cost",
            ]
        )
    columns = [
        "channel",
        "title",
        "account_id",
        "account",
        "manual_category",
        "ai_category",
        "content_category",
        "cover_url",
        "content_url",
        "spend",
        "impressions",
        "clicks",
        "ctr",
        "activations",
        "activation_cost",
    ]
    return scoped.sort_values("spend", ascending=False)[columns].head(50).reset_index(drop=True)


def _rank_pct(series: pd.Series) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if len(clean) == 0:
        return clean
    return clean.rank(pct=True, method="average")


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator = pd.to_numeric(numerator, errors="coerce").astype(float)
    denominator = pd.to_numeric(denominator, errors="coerce").astype(float)
    result = pd.Series(pd.NA, index=numerator.index, dtype="Float64")
    mask = denominator.ne(0.0)
    result.loc[mask] = numerator.loc[mask] / denominator.loc[mask]
    return result


def _sum_or_blank(series: pd.Series) -> float:
    total = pd.to_numeric(series, errors="coerce").sum(min_count=1)
    return float(total) if not pd.isna(total) else float("nan")


def _sum_or_zero(series: pd.Series) -> float:
    total = pd.to_numeric(series, errors="coerce").sum(min_count=1)
    return float(total) if not pd.isna(total) else 0.0


def _join_non_blank(values: Iterable[object]) -> str:
    tokens = [str(value).strip() for value in values if not _is_blank(value)]
    return " / ".join(tokens)


def _join_unique_nonblank(series: pd.Series) -> str:
    values: list[str] = []
    for value in series:
        if _is_blank(value):
            continue
        text = str(value).strip()
        if text and text not in values:
            values.append(text)
    return "、".join(values)


def _clean_identifier(value: object) -> str:
    if _is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _is_blank(value: object) -> bool:
    if value is None or pd.isna(value):
        return True
    return str(value).strip() == ""


def _iter_tabular_files(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in Path(input_dir).rglob("*")
        if path.is_file() and path.suffix.lower() in TABULAR_SUFFIXES and not path.name.startswith("~$")
    )


def _is_csv(path: Path) -> bool:
    return Path(path).suffix.lower() == ".csv"


def _read_table(
    path: Path,
    sheet_name: object = 0,
    header: object = 0,
    nrows: Optional[int] = None,
) -> pd.DataFrame:
    path = Path(path)
    if _is_csv(path):
        last_error: Optional[Exception] = None
        for encoding in ["utf-8-sig", "utf-8", "gbk"]:
            try:
                return pd.read_csv(path, header=header, nrows=nrows, encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        if last_error:
            raise last_error
        return pd.read_csv(path, header=header, nrows=nrows)
    return pd.read_excel(path, sheet_name=sheet_name, header=header, nrows=nrows)


def parse_number(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "/":
        return float("nan")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return float("nan")
    number = float(match.group(0))
    suffix = text[match.end() : match.end() + 1].lower()
    if suffix in {"w", "万"}:
        number *= 10000
    elif suffix in {"y", "亿"}:
        number *= 100000000
    return number
