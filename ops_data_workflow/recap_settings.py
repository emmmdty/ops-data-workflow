"""Persisted settings for high-value recap calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .storage import get_recap_setting_values, upsert_recap_setting_values


DEFAULT_ACTIVATION_WEIGHT = 1.0
DEFAULT_FIRST_PAY_WEIGHT = 1.0


@dataclass(frozen=True)
class RecapSettings:
    activation_weight: float
    first_pay_weight: float
    updated_at: str = ""


def get_recap_settings(db_path: Path) -> RecapSettings:
    values = get_recap_setting_values(db_path)
    return RecapSettings(
        activation_weight=_float(values.get("activation_weight"), DEFAULT_ACTIVATION_WEIGHT),
        first_pay_weight=_float(values.get("first_pay_weight"), DEFAULT_FIRST_PAY_WEIGHT),
        updated_at=str(values.get("updated_at", "") or ""),
    )


def update_recap_settings(db_path: Path, *, activation_weight: float, first_pay_weight: float) -> RecapSettings:
    updated_at = datetime.now(timezone.utc).isoformat()
    values = {
        "activation_weight": float(activation_weight),
        "first_pay_weight": float(first_pay_weight),
        "updated_at": updated_at,
    }
    upsert_recap_setting_values(db_path, values)
    return RecapSettings(
        activation_weight=float(activation_weight),
        first_pay_weight=float(first_pay_weight),
        updated_at=updated_at,
    )


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default
