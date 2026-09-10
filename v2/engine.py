from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re

from .report_parser import Sale, key
from .sheets import Target

HEADERS=["Data","Vendedor","Peso do dia (kg)","Observação","Região (automática)","Faturamento (R$)"]
EPOCH=date(1899,12,30)

def parse_date(v: object) -> date|None:
    if isinstance(v,(int,float,Decimal)): return EPOCH+timedelta(days=int(v))
    t=str(v or "").strip()
    for f in ("%d/%m/%Y","%Y-%m-%d","%d-%m-%Y"):
        try:return datetime.strptime(t[:10],f).date()
        except ValueError: pass
    return None

def round2(x: Decimal)->Decimal:return x.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
def serial(d: date)->int:return (d-EPOCH).days

def money(v: object) -> Decimal:
    text=re.sub(r"[^0-9,.-]", "", str(v or ""))
    if "," in text and "." in text: text=text.replace(".","").replace(",", ".")
    elif "," in text: text=text.replace(",", ".")
    return Decimal(text or "0")

def automatic_rows(sales: list[Sale], targets: list[Target], d: date) -> list[list[object]]:
    """Creates only the Aster-owned rows that may safely be replaced on a rerun."""
    target_by={key(t.vendor):t for t in targets}; conflicts=[]; output=[]; automated_marker=f"ASTER - saldo de {d:%d/%m/%Y}"
    for s in sales:
        region=target_by.get(key(s.vendor)).region if key(s.vendor) in target_by else s.region
        output.append([serial(d),s.vendor,float(round2(s.weight_kg)),automated_marker,region,float(round2(s.value_brl))])
    return output


def prepare_rows(existing: list[list[object]], sales: list[Sale], targets: list[Target], d: date, conflict_policy: str="skip_manual") -> tuple[list[list[object]],list[str]]:
    """Builds the projected sheet used for calculations without changing manual rows."""
    conflicts=[]; output=[]
    for idx, raw in enumerate(existing[1:] if existing and key(existing[0][0])=="data" else existing, start=2):
        row=list(raw)+[""]*max(0,6-len(raw)); row=row[:6]
        if not any(str(x or "").strip() for x in row): continue
        rd=parse_date(row[0]); vendor=str(row[1] or "").strip(); obs=str(row[3] or "")
        if rd is None or not vendor: conflicts.append(f"linha {idx} inválida"); continue
        if rd==d and key(vendor) in {key(s.vendor) for s in sales} and not obs.upper().startswith("ASTER - SALDO DE"):
            conflicts.append(f"{vendor}: já possui lançamento manual em {d:%d/%m/%Y}")
            if conflict_policy=="fail": raise ValueError("Conflito manual: "+conflicts[-1])
        # remove apenas a fotografia automática anterior da mesma data/vendedor
        if rd==d and obs.upper().startswith("ASTER - SALDO DE"):
            continue
        output.append(row)
    return [HEADERS]+output + automatic_rows(sales, targets, d),conflicts

def aggregate(rows: list[list[object]], d: date, targets: list[Target]) -> tuple[dict[str,Decimal],dict[str,Decimal],dict[str,Decimal]]:
    daily_w=defaultdict(Decimal); monthly_w=defaultdict(Decimal); monthly_v=defaultdict(Decimal)
    month=d.replace(day=1)
    for row in rows[1:] if rows and key(rows[0][0])=="data" else rows:
        if len(row)<6: continue
        rd=parse_date(row[0]); vendor=str(row[1] or "").strip()
        if not rd or not vendor: continue
        w=money(row[2]); v=money(row[5])
        if rd==d: daily_w[vendor]+=w
        if month<=rd<=d: monthly_w[vendor]+=w; monthly_v[vendor]+=v
    return daily_w,monthly_w,monthly_v

def rankings(monthly_w: dict[str,Decimal], targets: list[Target]) -> list[dict]:
    out=[]
    for t in targets:
        w=monthly_w.get(t.vendor,Decimal(0)); meta=t.monthly_kg
        out.append({"vendor":t.vendor,"region":t.region,"weight_kg":w,"target_kg":meta,"percent":(w/meta if meta else Decimal(0))})
    return sorted(out,key=lambda x:(-x["percent"],-x["weight_kg"],x["vendor"]))

def region_rankings(items:list[dict])->list[dict]:
    grouped=defaultdict(lambda:[Decimal(0),Decimal(0)])
    for x in items: grouped[x["region"]][0]+=x["weight_kg"]; grouped[x["region"]][1]+=x["target_kg"]
    out=[{"region":r,"weight_kg":v[0],"target_kg":v[1],"percent":v[0]/v[1] if v[1] else Decimal(0)} for r,v in grouped.items()]
    return sorted(out,key=lambda x:(-x["percent"],x["region"]))
