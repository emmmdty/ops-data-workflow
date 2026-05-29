"""External context lookup for overview analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable

from bs4 import BeautifulSoup
import requests


HOLIDAY_URL_TEMPLATE = "https://holiday.ailcc.com/api/holiday/year/{year}"
MARKET_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
POLICY_URL = "https://www.csrc.gov.cn/csrc/c100039/common_list_2.shtml"
REQUEST_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class ExternalContext:
    summary: str
    sources: list[str]

    @property
    def available(self) -> bool:
        return bool(self.sources)


def fetch_external_context(
    period_start: str,
    period_end: str,
    *,
    request_get: Callable | None = None,
) -> ExternalContext:
    getter = request_get or requests.get
    start = _parse_date(period_start)
    end = _parse_date(period_end)
    if start is None or end is None:
        return ExternalContext(summary="未取到外部背景：周期日期无法识别。", sources=[])

    snippets: list[str] = []
    sources: list[str] = []
    for label, loader in [
        ("节假日", _fetch_holidays),
        ("行情", _fetch_market),
        ("政策", _fetch_policy),
    ]:
        try:
            text, source = loader(getter, start, end)
        except Exception:
            continue
        if text:
            snippets.append(f"{label}：{text}")
        if source:
            sources.append(source)

    if not snippets:
        return ExternalContext(summary="未取到外部背景。", sources=[])
    return ExternalContext(summary="；".join(snippets), sources=sources)


def _fetch_holidays(getter: Callable, start: date, end: date) -> tuple[str, str]:
    names: list[str] = []
    for year in range(start.year, end.year + 1):
        response = getter(HOLIDAY_URL_TEMPLATE.format(year=year), timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        holidays = payload.get("holiday", {}) if isinstance(payload, dict) else {}
        for item in holidays.values():
            day = _parse_date(item.get("date", "")) if isinstance(item, dict) else None
            if day is None or day < start or day > end:
                continue
            name = str(item.get("name", "")).strip()
            if name and name not in names:
                names.append(name)
    return ("、".join(names[:4]), HOLIDAY_URL_TEMPLATE.format(year=start.year)) if names else ("", "")


def _fetch_market(getter: Callable, start: date, end: date) -> tuple[str, str]:
    params = {
        "secid": "1.000001",
        "klt": "101",
        "fqt": "0",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "beg": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
    }
    response = getter(MARKET_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    klines = data.get("klines", []) if isinstance(data, dict) else []
    close_values = [_kline_close(line) for line in klines]
    close_values = [value for value in close_values if value is not None]
    if len(close_values) < 2:
        return "", ""
    change = (close_values[-1] - close_values[0]) / close_values[0] if close_values[0] else 0.0
    sign = "+" if change > 0 else ""
    name = str(data.get("name", "上证指数") or "上证指数")
    return f"{name}{sign}{_trim_percent(change)}", MARKET_URL


def _fetch_policy(getter: Callable, start: date, end: date) -> tuple[str, str]:
    response = getter(POLICY_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    soup = BeautifulSoup(response.text or "", "html.parser")
    titles: list[str] = []
    for text in _visible_link_texts(soup):
        if not _contains_period_date(text, start, end):
            continue
        clean = " ".join(text.split())
        if clean and clean not in titles:
            titles.append(clean)
        if len(titles) >= 3:
            break
    return ("；".join(titles), POLICY_URL) if titles else ("", "")


def _visible_link_texts(soup: BeautifulSoup) -> Iterable[str]:
    for link in soup.find_all("a"):
        text = link.get_text(" ", strip=True)
        if text:
            yield text


def _contains_period_date(text: str, start: date, end: date) -> bool:
    for pattern in ["%Y-%m-%d", "%Y/%m/%d"]:
        for token in str(text or "").replace("年", "-").replace("月", "-").replace("日", "").split():
            try:
                value = datetime.strptime(token.strip(), pattern).date()
            except ValueError:
                continue
            if start <= value <= end:
                return True
    return any(keyword in text for keyword in ["政策", "证监会", "市场", "制度"])


def _kline_close(line: object) -> float | None:
    parts = str(line or "").split(",")
    if len(parts) < 3:
        return None
    try:
        return float(parts[2])
    except ValueError:
        return None


def _trim_percent(value: float) -> str:
    text = f"{value * 100:.1f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _parse_date(value: object) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None
