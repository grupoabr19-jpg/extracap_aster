"""Calculo do comparativo diario por vendedor."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

HEADERS = ["Data", "Regiao", "Lider Regional", "Segmento", "Vendedor", "Meta (kg)", "Vendido no dia (kg)", "Vendido acumulado (kg)", "% da meta", "Saldo (kg)", "Dias uteis restantes", "Necessario por dia (kg)"]

def _r(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def calculate_rows(targets, sales_by_vendor, accumulated_by_vendor, reference_date: date, working_days_remaining: int):
    if working_days_remaining < 1:
        raise ValueError("working_days_remaining deve ser maior que zero")
    rows = []
    for target in targets:
        daily = sales_by_vendor.get(target.vendor, Decimal("0"))
        accumulated = accumulated_by_vendor.get(target.vendor, daily)
        balance = max(target.target_kg - accumulated, Decimal("0"))
        percentage = accumulated / target.target_kg * 100 if target.target_kg else Decimal("0")
        rows.append([reference_date.isoformat(), target.region, target.leader, target.segment, target.vendor, float(_r(target.target_kg)), float(_r(daily)), float(_r(accumulated)), float(_r(percentage)), float(_r(balance)), working_days_remaining, float(_r(balance / working_days_remaining))])
    return rows
