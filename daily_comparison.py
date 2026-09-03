"""Calculos do comparativo e do historico diario por vendedor."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


HEADERS = [
    "Data", "Regiao", "Lider Regional", "Segmento", "Vendedor",
    "Meta (t)", "Vendido no dia (t)", "Vendido acumulado (t)",
    "% da meta", "Saldo (t)", "Dias uteis restantes", "Necessario por dia (t)",
]

DAILY_LOG_HEADERS = [
    "Data",
    "Vendedor",
    "Peso do dia (kg)",
    "Observação",
    "Região (automática)",
]

GOOGLE_SHEETS_EPOCH = date(1899, 12, 30)


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _key(value: object) -> str:
    text = " ".join(str(value or "").replace("\xa0", " ").split()).strip()
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def _sheet_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            return GOOGLE_SHEETS_EPOCH.fromordinal(
                GOOGLE_SHEETS_EPOCH.toordinal() + int(Decimal(str(value)))
            )
        except (ValueError, InvalidOperation):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _sheet_number(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value or "").strip()
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        return Decimal("0")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Peso invalido na aba de lancamentos: {value!r}") from error


def _date_serial(value: date) -> int:
    """Retorna o numero de serie de data usado pelo Google Sheets."""
    return (value - GOOGLE_SHEETS_EPOCH).days


def build_daily_log_rows(
    existing_rows: list[list[object]],
    targets: list[object],
    sales_by_vendor: dict[str, Decimal],
    reference_date: date,
) -> list[list[object]]:
    """Atualiza somente o dia corrente e preserva todo o historico anterior.

    A publicacao do Apps Script substitui o conteudo da aba. Por isso, este
    metodo normaliza as linhas existentes, remove apenas as linhas do dia para
    vendedores cadastrados e inclui a fotografia mais recente do Aster.
    """
    rows = existing_rows
    if rows and [_key(cell) for cell in rows[0][:5]] == [_key(cell) for cell in DAILY_LOG_HEADERS]:
        rows = rows[1:]

    target_keys = {_key(target.vendor) for target in targets}
    result: list[list[object]] = []
    for row_number, raw_row in enumerate(rows, start=2):
        row = list(raw_row[:5]) + [""] * max(0, 5 - len(raw_row))
        if not any(str(value or "").strip() for value in row):
            continue
        row_date = _sheet_date(row[0])
        vendor = " ".join(str(row[1] or "").split()).strip()
        if row_date is None or not vendor:
            raise ValueError(
                f"Linha {row_number} invalida em 1_Lancamentos Diarios: data e vendedor sao obrigatorios"
            )
        if row_date == reference_date and _key(vendor) in target_keys:
            continue
        result.append([
            _date_serial(row_date),
            vendor,
            float(_sheet_number(row[2])),
            str(row[3] or "").strip(),
            str(row[4] or "").strip(),
        ])

    observation = f"Atualizado automaticamente pelo Aster em {reference_date:%d/%m/%Y}"
    for target in targets:
        sold_tons = sales_by_vendor.get(target.vendor, Decimal("0"))
        result.append([
            _date_serial(reference_date),
            target.vendor,
            float(_rounded(sold_tons * Decimal("1000"))),
            observation,
            target.region,
        ])
    return result


def calculate_rows(targets: list[object], sales_by_vendor: dict[str, Decimal], accumulated_by_vendor: dict[str, Decimal], reference_date: date, working_days_remaining: int) -> list[list[object]]:
    if working_days_remaining < 1:
        raise ValueError("working_days_remaining deve ser maior que zero")
    rows: list[list[object]] = []
    for target in targets:
        sold_today = sales_by_vendor.get(target.vendor, Decimal("0"))
        accumulated = accumulated_by_vendor.get(target.vendor, sold_today)
        balance = max(target.target_tons - accumulated, Decimal("0"))
        percentage = (accumulated / target.target_tons * Decimal("100")) if target.target_tons else Decimal("0")
        daily_required = balance / Decimal(working_days_remaining)
        rows.append([
            reference_date.isoformat(), target.region, target.leader, target.segment, target.vendor,
            float(_rounded(target.target_tons)), float(_rounded(sold_today)), float(_rounded(accumulated)),
            float(_rounded(percentage)), float(_rounded(balance)), working_days_remaining, float(_rounded(daily_required)),
        ])
    return rows
