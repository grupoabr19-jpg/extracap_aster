"""Publicacao segura de cargas no Apps Script."""
import json, os
from decimal import Decimal
from datetime import date
import math
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

VALID_DATA_MODES = {"daily_rows", "cumulative_by_seller"}

def _json_value(value):
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, date): return value.isoformat()
    return value

def validate_payload(reference_date, data_mode, headers, rows):
    if not isinstance(reference_date, date): raise ValueError("reference_date invalida")
    if data_mode not in VALID_DATA_MODES: raise ValueError("data_mode invalido")
    if not headers or any(not isinstance(header, str) or not header for header in headers): raise ValueError("headers invalidos")
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(headers): raise ValueError("Todas as linhas devem ter o mesmo numero de colunas")
        if not str(row[1]).strip(): raise ValueError("Vendedor vazio")
        if not isinstance(row[2], (int, float, Decimal)) or not math.isfinite(float(row[2])): raise ValueError("Peso invalido")
        if row[0] > reference_date.isoformat(): raise ValueError("Linha com data posterior a reference_date")

def publish_rows(endpoint, token, sheet_name, reference_date, data_mode, headers, rows, timeout=30):
    validate_payload(reference_date, data_mode, headers, rows)
    payload = {"token": token, "sheetName": sheet_name, "referenceDate": reference_date.isoformat(), "dataMode": data_mode, "headers": headers, "rows": [[_json_value(v) for v in row] for row in rows]}
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response: result = json.load(response)
    except HTTPError as error: raise RuntimeError(f"Falha HTTP ao publicar: {error.code}") from error
    except URLError as error: raise RuntimeError(f"Falha ao acessar Apps Script: {error.reason}") from error
    if result.get("status") != "ok": raise RuntimeError(result.get("error", "Publicacao recusada"))
    return result

def publish_from_env(headers, rows, reference_date, data_mode="daily_rows"):
    endpoint, token = os.getenv("SHEETS_API_URL", "").strip(), os.getenv("SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token: raise ValueError("Defina SHEETS_API_URL e SHEETS_API_TOKEN")
    return publish_rows(endpoint, token, os.getenv("SHEETS_OUTPUT_TAB", "1_Lançamentos Diários"), reference_date, data_mode, headers, rows)
