"""Publica resultados calculados na planilha via Apps Script."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_OUTPUT_TAB = "1_Lançamentos Diários"


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    return value


def publish_rows(endpoint: str, token: str, sheet_name: str, headers: list[str], rows: list[list[object]], timeout: int = 30) -> dict:
    payload = {
        "token": token,
        "sheetName": sheet_name,
        "headers": headers,
        "rows": [[_json_value(value) for value in row] for row in rows],
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Falha HTTP ao publicar na planilha: {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"Nao foi possivel acessar o Apps Script: {error.reason}") from error
    if result.get("error"):
        raise RuntimeError(f"Apps Script recusou a publicacao: {result['error']}")
    return result


def publish_from_env(headers: list[str], rows: list[list[object]]) -> dict:
    endpoint = os.getenv("SHEETS_API_URL", "").strip()
    token = os.getenv("SHEETS_API_TOKEN", "").strip()
    sheet_name = output_tab_from_env()
    if not endpoint or not token:
        raise ValueError("Defina SHEETS_API_URL e SHEETS_API_TOKEN no .env")
    if not sheet_name:
        raise ValueError("SHEETS_OUTPUT_TAB nao pode ser vazio")
    return publish_rows(endpoint, token, sheet_name, headers, rows)


def output_tab_from_env() -> str:
    return os.getenv("SHEETS_OUTPUT_TAB", DEFAULT_OUTPUT_TAB).strip()
