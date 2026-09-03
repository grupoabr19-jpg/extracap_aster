"""Cliente para ler metas publicadas pelo Apps Script do Google Sheets."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class VendorTarget:
    region: str
    leader: str
    segment: str
    vendor: str
    target_tons: Decimal


def fetch_workbook(endpoint: str, token: str, timeout: int = 30) -> dict:
    url = f"{endpoint}?token={quote(token, safe='')}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            raise RuntimeError(
                "Apps Script retornou 404: publique um novo Web app e copie a URL terminada em /exec"
            ) from error
        raise RuntimeError(f"Apps Script retornou HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Nao foi possivel acessar o Apps Script: {error.reason}") from error
    if payload.get("error"):
        raise RuntimeError(f"Apps Script retornou erro: {payload['error']}")
    if not isinstance(payload.get("sheets"), list):
        raise ValueError("Resposta do Apps Script nao possui a lista sheets")
    return payload


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _key(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", _clean(value))
    return "".join(character for character in normalized if not unicodedata.combining(character)).casefold()


def _number(value: object) -> Decimal | None:
    text = _clean(value).replace("t", "").strip()
    if not text:
        return None
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _find_row(rows: list[list[object]], label: str) -> tuple[int, list[object]] | None:
    wanted = _key(label)
    for index, row in enumerate(rows):
        if any(_key(cell) == wanted for cell in row):
            return index, row
    return None


def _row_values_after_label(row: list[object], label: str) -> list[object]:
    wanted = _key(label)
    for index, cell in enumerate(row):
        if _key(cell) == wanted:
            return row[index + 1:]
    return row


def extract_vendor_targets(payload: dict) -> list[VendorTarget]:
    """Interpreta blocos com linhas REGIAO/LIDER/SEGMENTO/VENDEDOR/META.

    O parser procura uma linha VENDEDOR e usa as linhas de contexto acima dela
    e a primeira linha numerica abaixo dela. Isso evita depender de colunas
    fixas, classes HTML ou uma quantidade especifica de blocos.
    """
    targets: list[VendorTarget] = []
    for sheet in payload["sheets"]:
        rows = sheet.get("rows", [])
        vendor_row_info = _find_row(rows, "VENDEDOR")
        if not vendor_row_info:
            continue
        vendor_index, vendor_row = vendor_row_info
        numeric_row = next((row for row in rows[vendor_index + 1:] if sum(_number(cell) is not None for cell in row) >= 2), None)
        if numeric_row is None:
            continue

        def context(label: str) -> list[str]:
            found = _find_row(rows[:vendor_index], label)
            return [_clean(cell) for cell in _row_values_after_label(found[1], label)] if found else []

        regions = context("REGIAO")
        leaders = context("LIDER REGIONAL")
        segments = context("SEGMENTO")
        if not regions or not leaders or not segments:
            continue
        vendors = _row_values_after_label(vendor_row, "VENDEDOR")
        values = numeric_row[1:] if numeric_row and _number(numeric_row[0]) is None else numeric_row
        for column, vendor_value in enumerate(vendors):
            vendor = _clean(vendor_value)
            target = _number(values[column]) if column < len(values) else None
            if not vendor or target is None:
                continue
            targets.append(VendorTarget(
                region=regions[column] if column < len(regions) else "",
                leader=leaders[column] if column < len(leaders) else "",
                segment=segments[column] if column < len(segments) else "",
                vendor=vendor,
                target_tons=target,
            ))
    if not targets:
        raise ValueError("Nenhum bloco VENDEDOR com metas numericas foi encontrado")
    return targets


def extract_sheet_rows(payload: dict, sheet_name: str) -> list[list[object]]:
    """Retorna as linhas de uma aba pelo nome, sem depender da ordem das abas."""
    wanted = _key(sheet_name)
    for sheet in payload.get("sheets", []):
        if _key(sheet.get("name") or sheet.get("title") or sheet.get("sheetName")) != wanted:
            continue
        rows = sheet.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError(f"A aba {sheet_name!r} nao possui linhas validas")
        return [list(row) for row in rows if isinstance(row, list)]
    raise ValueError(f"Aba nao encontrada na resposta do Apps Script: {sheet_name}")


def load_workbook_from_env() -> dict:
    endpoint = os.getenv("SHEETS_API_URL", "").strip()
    token = os.getenv("SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token:
        raise ValueError("Defina SHEETS_API_URL e SHEETS_API_TOKEN no .env")
    return fetch_workbook(endpoint, token)


def load_targets_from_env() -> list[VendorTarget]:
    return extract_vendor_targets(load_workbook_from_env())
