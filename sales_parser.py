"""Leitura da exportacao de vendas do Aster."""

from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _key(value: object) -> str:
    text = " ".join(str(value or "").replace("\xa0", " ").split()).strip()
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def _number(value: object) -> Decimal:
    text = str(value or "").strip().replace("t", "").replace("T", "")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        raise ValueError("valor numerico vazio")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"valor numerico invalido: {value!r}") from error


def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _aliases(value: str, defaults: tuple[str, ...]) -> set[str]:
    configured = [item for item in value.split(",") if item.strip()]
    return {_key(item) for item in (configured or defaults)}


def read_sales_report(
    path: Path,
    reference_date: date,
    vendor_names: list[str],
    vendor_column: str = "",
    quantity_column: str = "",
    date_column: str = "",
    accumulated_column: str = "",
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Retorna venda do dia e acumulado, agrupados pelos vendedores das metas.

    O acumulado e a soma das vendas ate a data de referencia. Se a exportacao
    ja trouxer uma coluna de acumulado, usa o maior valor encontrado por vendedor.
    """
    if path.suffix.lower() in {".csv", ".txt"}:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            sample = file.read(4096)
            file.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(file, dialect=dialect))
    elif path.suffix.lower() in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError as error:
            raise ValueError("Instale openpyxl para ler a exportacao Excel do Aster") from error
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            values = list(sheet.values)
        finally:
            workbook.close()
        if not values:
            rows = []
        else:
            headers = [str(value or "") for value in values[0]]
            rows = [dict(zip(headers, row)) for row in values[1:]]
    else:
        raise ValueError("O comparativo exige uma exportacao CSV ou XLSX do Aster")

    if not rows or not rows[0]:
        raise ValueError(f"Relatorio de vendas vazio ou sem cabecalho: {path.name}")
    columns = { _key(name): name for name in rows[0].keys() if name is not None }

    def find(configured: str, defaults: tuple[str, ...], required: bool) -> str | None:
        wanted = _aliases(configured, defaults)
        for key, original in columns.items():
            if key in wanted:
                return original
        if required:
            raise ValueError(f"Coluna nao encontrada no relatorio: {', '.join(defaults)}")
        return None

    vendor_field = find(vendor_column, ("vendedor", "vendedor(a)", "salesperson", "consultor"), True)
    quantity_field = find(quantity_column, ("quantidade", "qtd", "toneladas", "peso", "volume", "vendido"), True)
    date_field = find(date_column, ("data", "data venda", "dt venda", "emissao", "data emissao"), False)
    accumulated_field = find(accumulated_column, ("vendido acumulado", "acumulado", "acumulado (t)"), False)

    names = {_key(name): name for name in vendor_names}
    daily = {name: Decimal("0") for name in vendor_names}
    accumulated = {name: Decimal("0") for name in vendor_names}
    explicit_accumulated: dict[str, Decimal] = {}

    for row in rows:
        raw_vendor = row.get(vendor_field or "", "")
        canonical = names.get(_key(raw_vendor))
        if canonical is None:
            continue
        row_date = _date(row.get(date_field or "", "")) if date_field else reference_date
        if row_date is None:
            raise ValueError(f"Data invalida no relatorio para o vendedor {raw_vendor!r}")
        if row_date > reference_date:
            continue
        quantity = _number(row.get(quantity_field or "", ""))
        accumulated[canonical] += quantity
        if row_date == reference_date:
            daily[canonical] += quantity
        if accumulated_field:
            explicit = _number(row.get(accumulated_field, ""))
            explicit_accumulated[canonical] = max(explicit_accumulated.get(canonical, Decimal("0")), explicit)

    accumulated.update(explicit_accumulated)
    return daily, accumulated
