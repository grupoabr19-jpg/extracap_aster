"""Leitura de metas e historico via Apps Script."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json, os, re, unicodedata
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

@dataclass(frozen=True)
class VendorTarget:
    region: str
    leader: str
    segment: str
    vendor: str
    target_kg: Decimal

def _clean(value): return " ".join(str(value or "").replace("\xa0", " ").split()).strip()
def _key(value):
    text = unicodedata.normalize("NFKD", _clean(value))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()
def _number(value):
    text = re.sub(r"[^0-9,.-]", "", _clean(value).replace("kg", "").replace("KG", ""))
    if not text: return None
    if "," in text and "." in text: text = text.replace(".", "").replace(",", ".")
    elif "," in text: text = text.replace(",", ".")
    try: return Decimal(text)
    except InvalidOperation: return None

def fetch_workbook(endpoint, token, timeout=30):
    request = Request(f"{endpoint}?token={quote(token, safe='')}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response: payload = json.load(response)
    except HTTPError as error: raise RuntimeError(f"Apps Script retornou HTTP {error.code}") from error
    except URLError as error: raise RuntimeError(f"Nao foi possivel acessar o Apps Script: {error.reason}") from error
    if payload.get("error") or payload.get("status") == "error": raise RuntimeError(payload.get("error", "Apps Script retornou erro"))
    if not isinstance(payload.get("sheets"), list): raise ValueError("Resposta sem lista sheets")
    return payload

def extract_vendor_targets(payload):
    targets = []
    for sheet in payload["sheets"]:
        rows = sheet.get("rows", [])
        for index, row in enumerate(rows):
            positions = {_key(value): i for i, value in enumerate(row)}
            if "vendedor" not in positions: continue
            vendor_start = positions["vendedor"]
            numeric = next((candidate for candidate in rows[index + 1:] if sum(_number(v) is not None for v in candidate) >= 1), None)
            if numeric is None: continue
            context = {}
            for label in ("regiao", "lider regional", "segmento"):
                found = next((r for r in rows[:index] if any(_key(v) == label for v in r)), [])
                pos = next((i for i, v in enumerate(found) if _key(v) == label), None)
                context[label] = [_clean(v) for v in found[pos + 1:]] if pos is not None else []
            vendors = row[vendor_start + 1:]
            values = numeric[1:] if _number(numeric[0]) is None else numeric
            for column, raw_vendor in enumerate(vendors):
                value = _number(values[column]) if column < len(values) else None
                if not _clean(raw_vendor) or value is None: continue
                targets.append(VendorTarget(context["regiao"][column] if column < len(context["regiao"]) else "", context["lider regional"][column] if column < len(context["lider regional"]) else "", context["segmento"][column] if column < len(context["segmento"]) else "", _clean(raw_vendor), value))
    if not targets: raise ValueError("Nenhuma meta de vendedor foi encontrada")
    return targets

def load_targets_from_env():
    endpoint, token = os.getenv("SHEETS_API_URL", "").strip(), os.getenv("SHEETS_API_TOKEN", "").strip()
    if not endpoint or not token: raise ValueError("Defina SHEETS_API_URL e SHEETS_API_TOKEN")
    return extract_vendor_targets(fetch_workbook(endpoint, token))
