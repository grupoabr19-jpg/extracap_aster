from __future__ import annotations

import argparse, logging
from datetime import date
from dotenv import load_dotenv
from v2.config import Config
from v2.service import run

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Aster V2 — Resumo Comercial")
    parser.add_argument("--date", dest="reference_date", help="Data do saldo no formato YYYY-MM-DD")
    args=parser.parse_args()
    cfg=Config.load(); d=cfg.reference_date(args.reference_date)
    print(run(d,cfg))
