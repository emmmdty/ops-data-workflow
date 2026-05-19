"""Reference workbook loading and initialization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

import pandas as pd


REFERENCE_SHEETS = {
    "content_hierarchy": "内容类型分级表",
    "account_content_type": "账号内容类型对照表",
    "account_mapping": "账号映射表",
    "period_report_template": "周期报告模板",
    "field_mapping": "字段映射表",
    "processing_rules": "处理规则",
}


DEFAULT_ACCOUNT_MAPPING = pd.DataFrame(
    [
        {
            "渠道": "B站",
            "来源账号ID": "1622777305",
            "来源账号名": "",
            "实际账号": "同花顺投资",
            "映射来源": "默认维护",
            "说明": "B站导出账号为 Up主mid，已确认该 MID 对应同花顺投资。",
        }
    ]
)


DEFAULT_FIELD_MAPPING = pd.DataFrame(
    [
        {
            "来源文件": "*B站*",
            "Sheet": "sheet1",
            "来源字段": "Up主mid",
            "标准字段": "账号ID",
            "字段角色": "账号",
            "优先级": 1,
            "填充规则": "保留为账号ID，再通过账号映射表补实际账号名。",
        },
        {
            "来源文件": "*B站*",
            "Sheet": "sheet1",
            "来源字段": "视频BVID",
            "标准字段": "视频/笔记id",
            "字段角色": "标识",
            "优先级": 1,
            "填充规则": "缺失时使用视频AVID。",
        },
        {
            "来源文件": "*小红书*",
            "Sheet": "kos账户投放数据",
            "来源字段": "发布作者",
            "标准字段": "实际账号",
            "字段角色": "账号",
            "优先级": 1,
            "填充规则": "小红书账号字段默认为实际账号名。",
        },
        {
            "来源文件": "*抖音*",
            "Sheet": "Sheet2",
            "来源字段": "账号/账号名称/发布账号/达人名称",
            "标准字段": "实际账号",
            "字段角色": "账号",
            "优先级": 1,
            "填充规则": "抖音账号字段默认为实际账号名。",
        },
    ]
)


DEFAULT_CONTENT_HIERARCHY = pd.DataFrame(
    [
        {"渠道": "B站", "标签词": "", "二级栏目": "采访", "三级题材": "新手教学", "规则": "渠道固定规则"},
        {"渠道": "小红书/抖音", "标签词": "#同花顺资讯", "二级栏目": "资讯", "三级题材": "", "规则": "TAG权威映射"},
        {"渠道": "小红书/抖音", "标签词": "#同花顺股友说", "二级栏目": "股友说", "三级题材": "", "规则": "TAG权威映射"},
        {"渠道": "小红书/抖音", "标签词": "#同顺图解", "二级栏目": "图文", "三级题材": "", "规则": "TAG权威映射"},
        {"渠道": "小红书/抖音", "标签词": "#同顺盘点", "二级栏目": "盘点", "三级题材": "", "规则": "TAG权威映射"},
        {"渠道": "小红书/抖音", "标签词": "#问财问句", "二级栏目": "问财", "三级题材": "", "规则": "TAG权威映射"},
        {"渠道": "小红书/抖音", "标签词": "#同顺深度财经", "二级栏目": "长视频", "三级题材": "", "规则": "TAG权威映射"},
        {"渠道": "小红书/抖音", "标签词": "#同顺财商", "二级栏目": "财商动画", "三级题材": "", "规则": "TAG权威映射"},
        {"渠道": "小红书/抖音", "标签词": "#同花顺股民话题", "二级栏目": "社区话题", "三级题材": "", "规则": "TAG权威映射"},
    ]
)


DEFAULT_ACCOUNT_CONTENT_TYPE = pd.DataFrame(
    columns=["渠道", "实际账号", "二级栏目", "三级题材", "说明"]
)


DEFAULT_PERIOD_REPORT_TEMPLATE = pd.DataFrame(
    [
        {"段落": "整体结论", "提示词": "总结本周期渠道、栏目、题材和账号表现。"},
        {"段落": "渠道差异", "提示词": "比较各渠道投流效益综合评分和成本。"},
        {"段落": "题材建议", "提示词": "给出下周期栏目/题材和账号投放建议。"},
    ]
)


DEFAULT_PROCESSING_RULES = pd.DataFrame(
    [
        {"规则": "去重键", "取值": "渠道 + 视频/笔记id", "说明": "视频/笔记id 为空不自动去重。"},
        {"规则": "数值冲突阈值", "取值": "0.05", "说明": "相对差异大于 5% 时求和并标记人工审核。"},
        {"规则": "一级分类", "取值": "不启用", "说明": "不区分长视频、短视频、图文；渠道就是第一层分析维度。"},
        {"规则": "B站栏目题材", "取值": "采访 / 新手教学", "说明": "B站不做动态栏目题材分类，统一按固定值写入。"},
        {"规则": "小红书/抖音TAG", "取值": "TAG权威映射", "说明": "TAG命中时作为二级栏目来源，冲突进入人工审核。"},
    ]
)


REFERENCE_COLUMN_ALIASES = {
    "channel": "渠道",
    "source_account_id": "来源账号ID",
    "source_account_name": "来源账号名",
    "account": "实际账号",
    "mapping_source": "映射来源",
    "note": "说明",
    "source_file": "来源文件",
    "sheet": "Sheet",
    "source_column": "来源字段",
    "canonical_column": "标准字段",
    "field_role": "字段角色",
    "priority": "优先级",
    "fill_rule": "填充规则",
    "category_l1": "一级类型",
    "category_l2": "二级栏目",
    "category_l3": "三级题材",
    "tag": "标签词",
    "rule": "规则",
    "value": "取值",
    "section": "段落",
    "prompt": "提示词",
}

CANONICAL_COLUMN_ALIASES = {
    "渠道": "channel",
    "来源账号ID": "source_account_id",
    "来源账号名": "source_account_name",
    "实际账号": "account",
    "映射来源": "mapping_source",
    "说明": "note",
    "来源文件": "source_file",
    "Sheet": "sheet",
    "来源字段": "source_column",
    "标准字段": "canonical_column",
    "字段角色": "field_role",
    "优先级": "priority",
    "填充规则": "fill_rule",
    "一级类型": "category_l1",
    "二级栏目": "category_l2",
    "三级题材": "category_l3",
    "标签词": "tag",
    "规则": "rule",
    "取值": "value",
    "段落": "section",
    "提示词": "prompt",
}


@dataclass(frozen=True)
class ReferenceTables:
    path: Path
    tables: Mapping[str, pd.DataFrame]

    @property
    def account_mapping(self) -> pd.DataFrame:
        return to_canonical_reference_columns(self.tables.get("账号映射表", DEFAULT_ACCOUNT_MAPPING).copy())

    @property
    def account_content_type(self) -> pd.DataFrame:
        return to_canonical_reference_columns(self.tables.get("账号内容类型对照表", DEFAULT_ACCOUNT_CONTENT_TYPE).copy())

    @property
    def field_mapping(self) -> pd.DataFrame:
        return to_canonical_reference_columns(self.tables.get("字段映射表", DEFAULT_FIELD_MAPPING).copy())

    @property
    def content_hierarchy(self) -> pd.DataFrame:
        return to_canonical_reference_columns(self.tables.get("内容类型分级表", DEFAULT_CONTENT_HIERARCHY).copy())

    @property
    def processing_rules(self) -> pd.DataFrame:
        return to_canonical_reference_columns(self.tables.get("处理规则", DEFAULT_PROCESSING_RULES).copy())


def parse_period_from_raw_dir(raw_dir: Path) -> tuple[str, str]:
    """Parse YYYYMMDD-YYYYMMDD from a raw directory path."""
    name = Path(raw_dir).name
    match = re.fullmatch(r"(\d{8})-(\d{8})", name)
    if not match:
        raise ValueError(f"raw 目录名需为 YYYYMMDD-YYYYMMDD：{name}")
    return _format_yyyymmdd(match.group(1)), _format_yyyymmdd(match.group(2))


def load_reference_tables(path: Path = Path("config/reference_tables.xlsx")) -> ReferenceTables:
    path = Path(path)
    if not path.exists():
        _write_default_reference_tables(path)
    tables: dict[str, pd.DataFrame] = {}
    try:
        with pd.ExcelFile(path) as workbook:
            for sheet_name in workbook.sheet_names:
                tables[sheet_name] = _clean_table(pd.read_excel(workbook, sheet_name=sheet_name, dtype=object))
    except Exception:
        _write_default_reference_tables(path)
        with pd.ExcelFile(path) as workbook:
            for sheet_name in workbook.sheet_names:
                tables[sheet_name] = _clean_table(pd.read_excel(workbook, sheet_name=sheet_name, dtype=object))

    changed = False
    for sheet_name, frame in _default_tables().items():
        if sheet_name not in tables:
            tables[sheet_name] = frame.copy()
            changed = True
    if any(_has_english_reference_columns(frame) for frame in tables.values()):
        changed = True
    if changed:
        _write_tables(path, tables)
    return ReferenceTables(path=path, tables=tables)


def account_mapping_lookup(account_mapping: pd.DataFrame) -> dict[tuple[str, str], dict[str, str]]:
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    if account_mapping.empty:
        return lookup
    for _, row in account_mapping.fillna("").iterrows():
        channel = str(row.get("channel", "")).strip()
        source_account_id = _clean_identifier(row.get("source_account_id", ""))
        account = str(row.get("account", "")).strip()
        if not channel or not source_account_id or not account:
            continue
        lookup[(channel, source_account_id)] = {
            "account": account,
            "mapping_source": str(row.get("mapping_source", "账号映射表")).strip() or "账号映射表",
        }
    return lookup


def _write_default_reference_tables(path: Path) -> None:
    _write_tables(path, _default_tables())


def _write_tables(path: Path, tables: Mapping[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name in REFERENCE_SHEETS.values():
            to_display_reference_columns(tables.get(sheet_name, pd.DataFrame())).to_excel(
                writer,
                sheet_name=sheet_name,
                index=False,
            )


def _default_tables() -> dict[str, pd.DataFrame]:
    return {
        "内容类型分级表": DEFAULT_CONTENT_HIERARCHY.copy(),
        "账号内容类型对照表": DEFAULT_ACCOUNT_CONTENT_TYPE.copy(),
        "账号映射表": DEFAULT_ACCOUNT_MAPPING.copy(),
        "周期报告模板": DEFAULT_PERIOD_REPORT_TEMPLATE.copy(),
        "字段映射表": DEFAULT_FIELD_MAPPING.copy(),
        "处理规则": DEFAULT_PROCESSING_RULES.copy(),
    }


def _clean_table(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.astype(object).where(pd.notna(frame), "")


def to_canonical_reference_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns={column: CANONICAL_COLUMN_ALIASES.get(str(column), column) for column in frame.columns})


def to_display_reference_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns={column: REFERENCE_COLUMN_ALIASES.get(str(column), column) for column in frame.columns})


def _has_english_reference_columns(frame: pd.DataFrame) -> bool:
    return any(str(column) in REFERENCE_COLUMN_ALIASES for column in frame.columns)


def _format_yyyymmdd(value: str) -> str:
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"


def _clean_identifier(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text
