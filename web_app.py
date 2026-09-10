from __future__ import annotations

import hmac, os, threading
from datetime import datetime
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from v2.config import Config
from v2.service import run

load_dotenv(); app=Flask(__name__); lock=threading.Lock(); status={"state":"idle","message":"Nenhuma execução iniciada."}

def auth():
    expected=os.getenv("TRIGGER_TOKEN",""); supplied=request.headers.get("Authorization","")
    return bool(expected and supplied.startswith("Bearer ") and hmac.compare_digest(supplied[7:],expected))

def work(raw_date):
    global status
    status={"state":"running","started_at":datetime.utcnow().isoformat()+"Z","reference_date":raw_date}
    try:
        result=run(Config.load().reference_date(raw_date or None),Config.load()); status={"state":"success",**result,"finished_at":datetime.utcnow().isoformat()+"Z"}
    except Exception as exc:
        status={"state":"error","message":f"{type(exc).__name__}: {exc}","finished_at":datetime.utcnow().isoformat()+"Z"}
    finally: lock.release()

@app.get("/health")
def health():
    required=["TRIGGER_TOKEN","ASTER_URL","ASTER_USERNAME","ASTER_PASSWORD","ASTER_USERNAME_SELECTOR","ASTER_PASSWORD_SELECTOR","ASTER_LOGIN_BUTTON_SELECTOR","ASTER_REPORT_URL","ASTER_REPORT_READY_SELECTOR","ASTER_REPORT_DOWNLOAD_SELECTOR","ASTER_REPORT_START_DATE_SELECTOR","ASTER_REPORT_END_DATE_SELECTOR","SHEETS_V2_URL","SHEETS_V2_TOKEN"]
    missing=[x for x in required if not os.getenv(x,"").strip()]
    return jsonify({"status":"configuration_required" if missing else "ok","missing":missing,"version":"v2"}), (503 if missing else 200)

@app.post("/run")
def trigger():
    if not auth(): return jsonify({"error":"unauthorized"}),401
    if not lock.acquire(blocking=False): return jsonify({"error":"already_running","status":status}),409
    body=request.get_json(silent=True) or {}; raw=str(body.get("reference_date") or "").strip() or None
    if raw:
        try: Config.load().reference_date(raw)
        except ValueError: lock.release(); return jsonify({"error":"invalid_reference_date"}),400
    threading.Thread(target=work,args=(raw,),daemon=True).start(); return jsonify({"status":"accepted","version":"v2"}),202

@app.get("/status")
def get_status():
    if not auth(): return jsonify({"error":"unauthorized"}),401
    return jsonify(status)
