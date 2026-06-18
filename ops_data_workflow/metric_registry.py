"""Metric definitions for the lightweight recap data mart."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    label: str
    metric_type: str
    unit: str = ""
    formula: str = ""
    denominator: str = ""
    lower_is_better: bool = False
    delta_direction: str = "higher_better"


_METRICS = [
    MetricDefinition("spend", "消耗", "atomic", unit="元", lower_is_better=True, delta_direction="lower_better"),
    MetricDefinition("impressions", "曝光", "atomic", unit="次"),
    MetricDefinition("clicks", "点击", "atomic", unit="次"),
    MetricDefinition("activations", "激活", "atomic", unit="次"),
    MetricDefinition("first_pay_count", "付费", "atomic", unit="次"),
    MetricDefinition("ctr", "点击率", "derived", formula="clicks / impressions", denominator="impressions"),
    MetricDefinition(
        "activation_cost",
        "激活成本",
        "derived",
        unit="元",
        formula="spend / activations",
        denominator="activations",
        lower_is_better=True,
        delta_direction="lower_better",
    ),
    MetricDefinition(
        "first_pay_cost",
        "付费成本",
        "derived",
        unit="元",
        formula="spend / first_pay_count",
        denominator="first_pay_count",
        lower_is_better=True,
        delta_direction="lower_better",
    ),
    MetricDefinition(
        "first_pay_rate",
        "付费率",
        "derived",
        formula="first_pay_count / activations",
        denominator="activations",
    ),
    MetricDefinition(
        "content_value",
        "内容价值",
        "composite",
        formula="activations*m + first_pay_count*n",
    ),
    MetricDefinition(
        "value_per_spend",
        "单位消耗价值",
        "derived",
        formula="content_value / spend",
        denominator="spend",
    ),
    MetricDefinition(
        "value_share",
        "价值占比",
        "derived",
        formula="channel_content_value / total_content_value",
        denominator="total_content_value",
    ),
]


def list_metrics() -> list[MetricDefinition]:
    return list(_METRICS)


def get_metric(name: str) -> MetricDefinition:
    for metric in _METRICS:
        if metric.name == name:
            return metric
    raise KeyError(name)
