from __future__ import annotations

import html, smtplib, ssl
from email.message import EmailMessage
from decimal import Decimal
from .config import Config

def br(x: Decimal, suffix="") -> str: return f"{x:,.2f}".replace(",","X").replace(".",",").replace("X",".")+suffix
def pct(x: Decimal)->str:return f"{x*100:.1f}".replace(".",",")+"%"

def build_message(c:Config,d, sales, daily_w, items, regions, total_weight, total_value, daily_target, sheet_url=""):
    top_day=sorted(sales,key=lambda s:(-s.weight_kg,s.vendor))
    def table_day():
        rows="".join(f"<tr><td>{i}</td><td>{html.escape(s.vendor)}</td><td>{br(s.weight_kg,' kg')}</td><td>{br(s.value_brl,' R$')}</td></tr>" for i,s in enumerate(top_day,1))
        return f"<table><tr><th>#</th><th>Vendedor</th><th>Tonelagem</th><th>Faturamento</th></tr>{rows}</table>"
    def table_rank():
        rows="".join(f"<tr><td>{i}</td><td>{html.escape(x['vendor'])}</td><td>{html.escape(x['region'])}</td><td>{br(x['weight_kg'],' kg')}</td><td>{pct(x['percent'])}</td></tr>" for i,x in enumerate(items,1))
        return f"<table><tr><th>#</th><th>Vendedor</th><th>Região</th><th>Acumulado</th><th>% da meta</th></tr>{rows}</table>"
    region_rows="".join(f"<tr><td>{i}</td><td>{html.escape(x['region'])}</td><td>{br(x['weight_kg'],' kg')}</td><td>{pct(x['percent'])}</td></tr>" for i,x in enumerate(regions,1))
    total_pct=total_weight/daily_target if daily_target else Decimal(0)
    subject=f"Meta Varejo - Resultado de {d:%d/%m/%Y}"
    html_body=f"""<html><body><p>Olá, pessoal.</p><p>A tabela <b>Meta Varejo - Tonelada por Dia</b> foi atualizada com os dados mais recentes.</p><p>O ranking já está disponível para acompanhamento da evolução individual dos vendedores, considerando o desempenho em tonelagem e o atingimento das respectivas metas.</p><h3>Totais do último dia processado</h3><p><b>Tonelagem:</b> {br(total_weight,' kg')} &nbsp; <b>Faturamento:</b> {br(total_value,' R$')}<br><b>Meta diária total:</b> {br(daily_target,' kg')} &nbsp; <b>Atingimento:</b> {pct(total_pct)}</p><h3>Resultado por vendedor em {d:%d/%m/%Y}</h3>{table_day()}<h3>Resultado por região em {d:%d/%m/%Y}</h3><table><tr><th>#</th><th>Região</th><th>Tonelagem</th><th>% da meta</th></tr>{region_rows}</table><h3>Ranking acumulado desde {d.replace(day=1):%d/%m/%Y}</h3>{table_rank()}<p><a href="{html.escape(sheet_url)}">Acessar ranking geral</a></p><p>Atenciosamente,<br>Inteligência de Mercado e Marketing<br>Grupo ABR</p></body></html>"""
    text=f"Olá, pessoal.\n\nA tabela Meta Varejo - Tonelada por Dia foi atualizada.\n\nTotais: {br(total_weight,' kg')} | Faturamento: {br(total_value,' R$')} | Meta diária: {br(daily_target,' kg')} | Atingimento: {pct(total_pct)}\n\nResultado por vendedor em {d:%d/%m/%Y}:\n"+"\n".join(f"{i}. {s.vendor} — {br(s.weight_kg,' kg')}" for i,s in enumerate(top_day,1))+f"\n\nRanking acumulado desde {d.replace(day=1):%d/%m/%Y}:\n"+"\n".join(f"{i}. {x['vendor']} ({x['region']}) — {br(x['weight_kg'],' kg')} — {pct(x['percent'])}" for i,x in enumerate(items,1))+"\n\nAtenciosamente,\nInteligência de Mercado e Marketing\nGrupo ABR"
    msg=EmailMessage(); msg["From"]=c.mail_from; msg["To"]=', '.join(c.mail_to); 
    if c.mail_cc: msg["Cc"]=', '.join(c.mail_cc)
    msg["Subject"]=subject; msg.set_content(text); msg.add_alternative(html_body,subtype="html"); return msg

def send(c:Config,msg:EmailMessage):
    ctx=ssl.create_default_context()
    if c.smtp_port==465:
        with smtplib.SMTP_SSL(c.smtp_host,c.smtp_port,context=ctx) as s: s.login(c.smtp_user,c.smtp_password); s.send_message(msg)
    else:
        with smtplib.SMTP(c.smtp_host,c.smtp_port) as s: s.starttls(context=ctx); s.login(c.smtp_user,c.smtp_password); s.send_message(msg)
