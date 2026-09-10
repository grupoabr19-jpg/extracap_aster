from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Target:
    vendor: str
    region: str
    monthly_kg: Decimal
    working_days: int


def key(value: object) -> str:
    text = unicodedata.normalize("NFKD", " ".join(str(value or "").split()).strip())
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def num(value: object) -> Decimal:
    text = re.sub(r"[^0-9,.-]", "", str(value or ""))
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    return Decimal(text or "0")


def sheet(payload: dict, name: str) -> list[list[object]]:
    for item in payload.get("sheets", []):
        if key(item.get("name")) == key(name):
            return item.get("rows", [])
    raise ValueError(f"Aba não encontrada: {name}")


def load(endpoint: str, token: str) -> dict:
    request = Request(endpoint + "?token=" + quote(token, safe=""), headers={"Accept": "application/json"})
    with urlopen(request, timeout=45) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def targets_from(payload: dict) -> list[Target]:
    rows, targets = sheet(payload, "4_Metas"), []
    for row in rows[1:]:
        if len(row) >= 8 and str(row[4] or "").strip():
            targets.append(Target(str(row[4]).strip(), str(row[1] or "").strip(), num(row[5]), int(num(row[6]))))
    if not targets:
        raise ValueError("Nenhuma meta encontrada na aba 4_Metas")
    return targets


def existing_daily(payload: dict) -> list[list[object]]:
    return sheet(payload, "1_Lançamentos Diários")


def publish(endpoint: str, token: str, reference_date: date, rows: list[list[object]], dry_run: bool) -> dict:
    if dry_run:
        return {"status": "dry_run", "rows": len(rows)}
    payload = {"token": token, "action": "upsert_daily", "sheetName": "1_Lançamentos Diários", "referenceDate": reference_date.isoformat(), "rows": rows}
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result
