"""Baixa metas do Apps Script e salva uma copia JSON para auditoria."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from sheets_client import fetch_workbook, extract_vendor_targets
import os


ROOT = Path(__file__).resolve().parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    endpoint = os.environ["SHEETS_API_URL"]
    token = os.environ["SHEETS_API_TOKEN"]
    payload = fetch_workbook(endpoint, token)
    targets = extract_vendor_targets(payload)

    output_dir = ROOT / os.getenv("OUTPUT_DIR", "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "planilha_raw.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = [
        {
            "regiao": target.region,
            "lider": target.leader,
            "segmento": target.segment,
            "vendedor": target.vendor,
            "meta_tons": str(target.target_tons),
        }
        for target in targets
    ]
    (output_dir / "metas_vendedores.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{len(targets)} metas extraidas")
    for target in targets:
        print(f"{target.vendor}: {target.target_tons} t")


if __name__ == "__main__":
    main()
