"""Leitura da exportacao CSV/XLSX do Resumo Comercial."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import csv
import re
import unicodedata

def key(value):
    text = unicodedata.normalize("NFKD", " ".join(str(value or "").split()))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()

def number(value):
    text = re.sub(r"[^0-9,.-]", "", str(value or "").replace("kg", "").replace("KG", ""))
    if not text:
        raise ValueError("Quantidade vazia")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Quantidade invalida: {value!r}") from error

def row_date(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    text = str(value or "").strip()
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try: return datetime.strptime(text[:10], pattern).date()
        except ValueError: pass
    return None

def read_rows(path):
    if path.suffix.lower() in {".csv", ".txt"}:
        with path.open(encoding="utf-8-sig", newline="") as file:
            sample = file.read(4096)
            file.seek(0)
            try: dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
            except csv.Error: dialect = csv.excel
            return list(csv.DictReader(file, dialect=dialect))
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try: values = list(workbook.active.values)
        finally: workbook.close()
        return [dict(zip([str(v or "") for v in values[0]], row)) for row in values[1:]] if values else []
    raise ValueError("O relatorio precisa ser CSV ou XLSX")

def read_sales_report(path: Path, reference_date: date, vendor_names, vendor_column="", quantity_column="", date_column=""):
    rows = read_rows(path)
    if not rows: raise ValueError("Relatorio de vendas vazio")
    columns = {key(name): name for name in rows[0] if name is not None}
    def find(configured, defaults):
        wanted = {key(x) for x in (configured.split(",") if configured else defaults)}
        return next((original for normalized, original in columns.items() if normalized in wanted), None)
    vendor_field = find(vendor_column, ("vendedor", "vendedor(a)", "consultor"))
    quantity_field = find(quantity_column, ("quantidade", "qtd", "toneladas", "peso", "peso total", "volume", "vendido"))
    date_field = find(date_column, ("data", "data venda", "dt venda", "emissao"))
    if not vendor_field or not quantity_field:
        raise ValueError("Colunas Vendedor e Quantidade nao encontradas no relatorio")
    names = {key(name): name for name in vendor_names}
    daily = {name: Decimal("0") for name in vendor_names}
    accumulated = {name: Decimal("0") for name in vendor_names}
    for row in rows:
        canonical = names.get(key(row.get(vendor_field)))
        if canonical is None: continue
        current = row_date(row.get(date_field)) if date_field else reference_date
        if current is None: raise ValueError(f"Data invalida para {canonical}")
        if current > reference_date: continue
        amount = number(row.get(quantity_field))
        accumulated[canonical] += amount
        if current == reference_date: daily[canonical] += amount
    return daily, accumulated

def read_sales_records(path: Path, reference_date: date, vendor_column="", quantity_column="", date_column=""):
    """Retorna as linhas do relatorio sem calcular deltas no Render."""
    rows = read_rows(path)
    if not rows:
        raise ValueError("Relatorio de vendas vazio")
    columns = {key(name): name for name in rows[0] if name is not None}
    def find(configured, defaults):
        wanted = {key(x) for x in (configured.split(",") if configured else defaults)}
        return next((original for normalized, original in columns.items() if normalized in wanted), None)
    vendor_field = find(vendor_column, ("vendedor", "vendedor(a)", "consultor"))
    quantity_field = find(quantity_column, ("quantidade", "qtd", "toneladas", "peso", "peso total", "volume", "vendido"))
    date_field = find(date_column, ("data", "data venda", "dt venda", "emissao"))
    if not vendor_field or not quantity_field:
        raise ValueError("Colunas Vendedor e Quantidade nao encontradas no relatorio")
    records = []
    for row in rows:
        vendor = " ".join(str(row.get(vendor_field) or "").split()).strip()
        if not vendor:
            raise ValueError("Vendedor vazio no relatorio")
        current = row_date(row.get(date_field)) if date_field else reference_date
        if current is None:
            raise ValueError(f"Data invalida para {vendor}")
        if current > reference_date:
            continue
        records.append((current, vendor, number(row.get(quantity_field))))
    if not records:
        raise ValueError("Nenhum lancamento valido encontrado no relatorio")
    return records
