from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass(frozen=True)
class Sale:
    vendor_raw: str
    vendor: str
    kind: str
    region: str
    segment: str
    weight_kg: Decimal
    value_brl: Decimal


def key(value: object) -> str:
    text = " ".join(str(value or "").replace("\xa0", " ").split()).strip()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def number(value: object) -> Decimal:
    text = re.sub(r"[^0-9,.-]", "", str(value or "").strip())
    if not text:
        return Decimal("0")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Número inválido: {value!r}") from exc


def parse_csv(path: Path) -> list[Sale]:
    """Lê e valida o Resumo Comercial exportado pelo Aster."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"
        rows = list(csv.DictReader(handle, dialect=dialect))
    if not rows:
        raise ValueError("Resumo Comercial vazio")

    columns = {key(column): column for column in rows[0] if column}
    required = {
        "vendedor": "Vendedor", "tipo": "Tipo", "regiao": "Região",
        "segmento": "Segmento", "peso total": "Peso total", "valor total": "Valor total",
    }
    missing = [label for normalized, label in required.items() if normalized not in columns]
    if missing:
        raise ValueError("Colunas ausentes no Resumo Comercial: " + ", ".join(missing))

    sales: list[Sale] = []
    for index, row in enumerate(rows, start=2):
        fields = {label: " ".join(str(row.get(columns[normalized], "") or "").split()) for normalized, label in required.items()}
        empty = [label for label, value in fields.items() if not value]
        if empty:
            raise ValueError(f"Linha {index} inválida; campos vazios: {', '.join(empty)}")
        weight, value = number(fields["Peso total"]), number(fields["Valor total"])
        if weight < 0 or value < 0:
            raise ValueError(f"Linha {index} inválida; peso e valor não podem ser negativos")
        sales.append(Sale(fields["Vendedor"], fields["Vendedor"], fields["Tipo"], fields["Região"], fields["Segmento"], weight, value))
    return sales


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", key(value)) if len(token) > 1 and token not in {"vare", "vcorp", "vendedor"}}


def canonical_vendor(raw: str, configured: list[str]) -> str:
    exact = {key(name): name for name in configured}
    raw_key = key(raw)
    if raw_key in exact:
        return exact[raw_key]
    raw_tokens = _tokens(raw)
    candidates = [vendor for vendor in configured if _tokens(vendor) and _tokens(vendor) <= raw_tokens]
    return candidates[0] if len(candidates) == 1 else raw


def normalize_sales(sales: list[Sale], configured_vendors: list[str]) -> list[Sale]:
    """Associa nomes e consolida linhas repetidas do mesmo vendedor."""
    grouped: dict[str, list[Sale]] = defaultdict(list)
    for sale in sales:
        vendor = canonical_vendor(sale.vendor_raw, configured_vendors)
        grouped[vendor].append(Sale(sale.vendor_raw, vendor, sale.kind, sale.region, sale.segment, sale.weight_kg, sale.value_brl))
    return [
        Sale(items[0].vendor_raw, vendor, items[0].kind, items[0].region, items[0].segment,
             sum((item.weight_kg for item in items), Decimal("0")), sum((item.value_brl for item in items), Decimal("0")))
        for vendor, items in sorted(grouped.items(), key=lambda entry: key(entry[0]))
    ]
