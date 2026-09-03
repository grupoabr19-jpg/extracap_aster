"""Regras de data para a automacao comercial."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def parse_non_working_dates(raw: str) -> set[date]:
    dates: set[date] = set()
    for item in raw.replace(";", ",").split(","):
        value = item.strip()
        if not value:
            continue
        parsed = None
        for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(value, pattern).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(
                f"Data invalida em NON_WORKING_DATES: {value!r}. "
                "Use AAAA-MM-DD ou DD/MM/AAAA."
            )
        dates.add(parsed)
    return dates


def is_business_day(value: date, non_working_dates: set[date]) -> bool:
    return value.weekday() < 5 and value not in non_working_dates


def previous_business_day(
    current_date: date,
    non_working_dates: set[date] | None = None,
) -> date:
    holidays = non_working_dates or set()
    candidate = current_date - timedelta(days=1)
    while not is_business_day(candidate, holidays):
        candidate -= timedelta(days=1)
    return candidate


def resolve_reference_date(explicit_date: date | None = None) -> date:
    if explicit_date is not None:
        return explicit_date
    timezone_name = os.getenv("BUSINESS_TIMEZONE", "America/Sao_Paulo").strip()
    try:
        current_date = datetime.now(ZoneInfo(timezone_name)).date()
    except Exception as error:
        raise ValueError(f"BUSINESS_TIMEZONE invalido: {timezone_name}") from error
    holidays = parse_non_working_dates(os.getenv("NON_WORKING_DATES", ""))
    mode = os.getenv("REFERENCE_DATE_MODE", "previous_business_day").strip().casefold()
    if mode == "today":
        candidate = current_date
        while not is_business_day(candidate, holidays):
            candidate -= timedelta(days=1)
        return candidate
    if mode != "previous_business_day":
        raise ValueError(
            "REFERENCE_DATE_MODE deve ser previous_business_day ou today"
        )
    return previous_business_day(current_date, holidays)
