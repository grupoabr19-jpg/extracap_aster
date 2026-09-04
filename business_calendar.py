"""Regras de data para execucoes agendadas."""
from datetime import date, timedelta

def previous_calendar_day(reference_date=None):
    return (reference_date or date.today()) - timedelta(days=1)

def last_business_day(reference_date=None, holidays=()):
    current = (reference_date or date.today()) - timedelta(days=1)
    holidays = set(holidays)
    while current.weekday() >= 5 or current.isoformat() in holidays: current -= timedelta(days=1)
    return current
