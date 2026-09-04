"""Regras de data para execucoes agendadas."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = "America/Sao_Paulo"

def resolve_reference_date(reference_date=None, timezone=BUSINESS_TIMEZONE):
    """Resolve a data da carga no fuso do negocio, nunca no UTC do Render."""
    if reference_date is not None:
        return reference_date
    return datetime.now(ZoneInfo(timezone)).date() - timedelta(days=1)

def previous_calendar_day(reference_date=None):
    return resolve_reference_date(reference_date)

def last_business_day(reference_date=None, holidays=()):
    current = (reference_date or date.today()) - timedelta(days=1)
    holidays = set(holidays)
    while current.weekday() >= 5 or current.isoformat() in holidays: current -= timedelta(days=1)
    return current
