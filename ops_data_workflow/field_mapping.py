"""Config-backed source field mapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd
import yaml


DEFAULT_FIELD_MAPPING_PATH = Path(__file__).resolve().parents[1] / "config" / "field_mapping.yml"
ALLOWED_FIELD_ROLES = frozenset(
    {
        "identity",
        "account",
        "context",
        "content",
        "additive_metric",
        "derived_metric_raw",
        "engagement",
    }
)
REQUIRED_INTERNAL_FIELDS = frozenset(
    {
        "title",
        "content_id",
        "content_id_fallback",
        "material_id",
        "account",
        "account_id",
        "content_url",
        "cover_url",
        "source_time",
        "duration",
        "content_form",
        "manual_category",
        "spend",
        "impressions",
        "clicks",
        "activations",
        "first_pay_count",
        "activation_cost_raw",
        "first_pay_cost_raw",
        "ctr_raw",
        "activation_rate_raw",
        "first_pay_rate_raw",
        "likes",
        "comments",
        "favorites",
        "follows",
    }
)


@dataclass(frozen=True)
class FieldMappingField:
    display: str
    internal: str
    role: str
    source_columns: tuple[str, ...]


@dataclass(frozen=True)
class HeaderCoverage:
    mapped: set[str]
    ignored: set[str]
    unmapped: set[str]


@dataclass(frozen=True)
class FieldMapping:
    fields: tuple[FieldMappingField, ...]
    passthrough_fields: Mapping[str, tuple[str, ...]]
    ignored_fields: frozenset[str]
    invalid_content_values: frozenset[str]
    graphic_values: frozenset[str]
    video_values: frozenset[str]
    default_content_form_by_channel: Mapping[str, str]

    def field_by_internal(self, internal: str) -> FieldMappingField:
        for field in self.fields:
            if field.internal == internal:
                return field
        raise KeyError(internal)

    def source_columns_for(self, internal: str) -> list[str]:
        if internal in self.passthrough_fields:
            return list(self.passthrough_fields[internal])
        return list(self.field_by_internal(internal).source_columns)

    def fields_for_source(self, _source_kind: str = "") -> dict[str, list[str]]:
        fields = {field.internal: list(field.source_columns) for field in self.fields}
        fields.update({internal: list(columns) for internal, columns in self.passthrough_fields.items()})
        return fields

    @property
    def mapped_source_columns(self) -> set[str]:
        columns: set[str] = set()
        for field in self.fields:
            columns.update(field.source_columns)
        for source_columns in self.passthrough_fields.values():
            columns.update(source_columns)
        return columns

    @property
    def additive_metric_columns(self) -> set[str]:
        columns: set[str] = set()
        for field in self.fields:
            if field.role == "additive_metric":
                columns.update(field.source_columns)
        return columns

    @property
    def metric_columns(self) -> set[str]:
        columns: set[str] = set()
        for field in self.fields:
            if field.role in {"additive_metric", "derived_metric_raw"}:
                columns.update(field.source_columns)
        return columns

    @property
    def identity_columns(self) -> set[str]:
        columns: set[str] = set()
        for field in self.fields:
            if field.role == "identity":
                columns.update(field.source_columns)
        return columns

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for field in self.fields:
            rows.append(
                {
                    "统一字段": field.display,
                    "标准字段": field.internal,
                    "字段角色": field.role,
                    "Excel字段": "、".join(field.source_columns),
                }
            )
        return pd.DataFrame(rows, columns=["统一字段", "标准字段", "字段角色", "Excel字段"])


def load_field_mapping(path: Path | str | None = None) -> FieldMapping:
    config_path = Path(path) if path is not None else DEFAULT_FIELD_MAPPING_PATH
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    passthrough_fields = {
        str(internal).strip(): tuple(str(column).strip() for column in columns if str(column).strip())
        for internal, columns in (data.get("passthrough_fields", {}) or {}).items()
        if str(internal).strip()
    }
    fields = tuple(
        FieldMappingField(
            display=str(item["display"]).strip(),
            internal=str(item["internal"]).strip(),
            role=str(item.get("role", "")).strip(),
            source_columns=tuple(str(column).strip() for column in item.get("source_columns", []) if str(column).strip()),
        )
        for item in data.get("fields", [])
    )
    mapping = FieldMapping(
        fields=fields,
        passthrough_fields=passthrough_fields,
        ignored_fields=frozenset(str(value).strip() for value in data.get("ignored_fields", []) if str(value).strip()),
        invalid_content_values=frozenset(
            str(value).strip() for value in data.get("invalid_content_values", []) if str(value).strip()
        ),
        graphic_values=frozenset(str(value).strip() for value in data.get("graphic_values", []) if str(value).strip()),
        video_values=frozenset(str(value).strip() for value in data.get("video_values", []) if str(value).strip()),
        default_content_form_by_channel={
            str(key).strip(): str(value).strip()
            for key, value in (data.get("default_content_form_by_channel", {}) or {}).items()
            if str(key).strip() and str(value).strip()
        },
    )
    _validate_mapping(mapping)
    return mapping


def mapped_or_ignored_headers(headers: Iterable[object], mapping: FieldMapping | None = None) -> HeaderCoverage:
    field_mapping = mapping or load_field_mapping()
    mapped_columns = field_mapping.mapped_source_columns
    mapped: set[str] = set()
    ignored: set[str] = set()
    unmapped: set[str] = set()
    for header in headers:
        text = str(header).strip()
        if not text:
            continue
        if text in mapped_columns:
            mapped.add(text)
        elif _is_ignored_header(text, field_mapping.ignored_fields):
            ignored.add(text)
        else:
            unmapped.add(text)
    return HeaderCoverage(mapped=mapped, ignored=ignored, unmapped=unmapped)


def standardize_content_type(row: pd.Series, mapping: FieldMapping | None = None) -> str:
    field_mapping = mapping or load_field_mapping()
    for column in field_mapping.source_columns_for("manual_category") + ["manual_category"]:
        value = _clean_content_value(row.get(column, ""), field_mapping)
        if value:
            return value
    return ""


def standardize_content_form(row: pd.Series, channel: str = "", mapping: FieldMapping | None = None) -> str:
    field_mapping = mapping or load_field_mapping()
    content_type = ""
    for column in field_mapping.source_columns_for("manual_category") + ["manual_category"]:
        value = _clean_content_value(row.get(column, ""), field_mapping)
        if value in field_mapping.graphic_values:
            return "图文"
        if value and not content_type:
            content_type = value
    for column in ["content_form", "类型"]:
        form = _normalize_form_value(row.get(column, ""), field_mapping)
        if form:
            return form
    if content_type:
        return "视频"
    default_form = field_mapping.default_content_form_by_channel.get(str(channel).strip(), "")
    return _normalize_form_value(default_form, field_mapping)


def _validate_mapping(mapping: FieldMapping) -> None:
    internals = [field.internal for field in mapping.fields]
    if len(internals) != len(set(internals)):
        raise ValueError("field_mapping.yml contains duplicate internal fields")
    missing = sorted(REQUIRED_INTERNAL_FIELDS - set(internals))
    if missing:
        raise ValueError(f"field_mapping.yml missing required fields: {', '.join(missing)}")
    passthrough_internals = list(mapping.passthrough_fields)
    if len(passthrough_internals) != len(set(passthrough_internals)):
        raise ValueError("field_mapping.yml contains duplicate passthrough fields")
    overlap = sorted(set(internals).intersection(passthrough_internals))
    if overlap:
        raise ValueError(f"field_mapping.yml passthrough fields overlap mapped fields: {', '.join(overlap)}")
    for field in mapping.fields:
        if not field.display or not field.internal:
            raise ValueError("field_mapping.yml fields must define display and internal")
        if field.role not in ALLOWED_FIELD_ROLES:
            raise ValueError(f"field_mapping.yml field {field.internal} has invalid role: {field.role}")
        if not field.source_columns:
            raise ValueError(f"{field.internal} must map at least one source column")
        if len(field.source_columns) != len(set(field.source_columns)):
            raise ValueError(f"{field.internal} contains duplicate source columns")
    for internal, source_columns in mapping.passthrough_fields.items():
        if not source_columns:
            raise ValueError(f"{internal} must map at least one source column")
        if len(source_columns) != len(set(source_columns)):
            raise ValueError(f"{internal} contains duplicate source columns")
    conflicts = sorted(mapping.mapped_source_columns.intersection(mapping.ignored_fields))
    if conflicts:
        raise ValueError(f"field_mapping.yml fields are both mapped and ignored: {', '.join(conflicts)}")
    if not mapping.invalid_content_values:
        raise ValueError("field_mapping.yml invalid_content_values must not be empty")
    if not mapping.graphic_values:
        raise ValueError("field_mapping.yml graphic_values must not be empty")
    if not mapping.video_values:
        raise ValueError("field_mapping.yml video_values must not be empty")


def _clean_content_value(value: object, mapping: FieldMapping) -> str:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    if not text:
        return ""
    if text in mapping.invalid_content_values or text.lower() in mapping.invalid_content_values:
        return ""
    return text


def _normalize_form_value(value: object, mapping: FieldMapping) -> str:
    text = _clean_content_value(value, mapping)
    if not text:
        return ""
    if text in mapping.graphic_values:
        return "图文"
    if text in mapping.video_values:
        return "视频"
    return ""


def _is_ignored_header(header: str, ignored_fields: Iterable[str]) -> bool:
    for ignored in ignored_fields:
        if header == ignored or header.startswith(ignored):
            return True
    return False
