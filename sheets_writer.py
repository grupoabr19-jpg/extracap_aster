"""Publicacao segura de linhas no Apps Script."""
import json, os
from decimal import Decimal
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def publish_rows(endpoint, token, sheet_name, headers, rows, timeout=30):
    payload = {"token": token, "sheetName": sheet_name, "headers": headers, "rows": [[float(v) if isinstance(v, Decimal) else v for v in row] for row in rows]}
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response: result = json.load(response)
    except HTTPError as error: raise RuntimeError(f"Falha HTTP ao publicar: {error.code}") from error
    except URLError as error: raise RuntimeError(f"Falha ao acessar Apps Script: {error.reason}") from error
    if result.get("error") or result.get("status") == "error": raise RuntimeError(result.get("error", "Publicacao recusada"))
    return result

def publish_from_env(headers, rows):
    endpoint, token = os.getenv("SHEETS_API_URL", "").strip(), os.getenv("SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token: raise ValueError("Defina SHEETS_API_URL e SHEETS_API_TOKEN")
    return publish_rows(endpoint, token, os.getenv("SHEETS_OUTPUT_TAB", "1_Lançamentos Diários"), headers, rows)
