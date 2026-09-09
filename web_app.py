"""API HTTP para acionamento da automacao no Render."""
from datetime import datetime
import logging
import os
from threading import Lock, Thread
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from main import run
from business_calendar import previous_calendar_day

load_dotenv()
app = Flask(__name__)
state_lock = Lock()
state = {"state": "idle", "message": "Nenhuma execucao iniciada.", "reference_date": None, "started_at": None, "finished_at": None}

def authorized():
    expected = os.getenv("TRIGGER_TOKEN", "")
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(expected and supplied and supplied == expected)

def worker(reference_date):
    with state_lock: state.update(state="running", message="Atualizacao em andamento.", reference_date=reference_date.isoformat(), started_at=datetime.utcnow().isoformat() + "Z", finished_at=None)
    try:
        run(reference_date)
        with state_lock: state.update(state="success", message="Atualizacao concluida.")
    except Exception as error:
        logging.getLogger("aster").exception("Falha na execucao para %s", reference_date.isoformat())
        with state_lock: state.update(state="error", message=str(error))
    finally:
        with state_lock: state["finished_at"] = datetime.utcnow().isoformat() + "Z"

@app.get("/health")
def health():
    required = (
        "ASTER_URL", "ASTER_USERNAME", "ASTER_PASSWORD",
        "ASTER_USERNAME_SELECTOR", "ASTER_PASSWORD_SELECTOR",
        "ASTER_LOGIN_BUTTON_SELECTOR", "ASTER_REPORT_URL",
        "ASTER_REPORT_TABLE_SELECTOR", "SMTP_HOST", "SMTP_USERNAME",
        "SMTP_PASSWORD", "MAIL_FROM", "SHEETS_API_URL", "SHEETS_API_TOKEN",
        "TRIGGER_TOKEN",
    )
    missing = [name for name in required if not os.getenv(name)]
    return jsonify(status="ok" if not missing else "degraded", missing=missing)

@app.get("/status")
def status():
    if not authorized(): return jsonify(error="unauthorized"), 401
    with state_lock: return jsonify(dict(state))

@app.post("/run")
def trigger():
    if not authorized():
        return jsonify(error="unauthorized"), 401
    raw_date = request.json.get("reference_date") if isinstance(request.json, dict) else None
    reference_date = None
    if raw_date:
        from datetime import date
        try:
            reference_date = date.fromisoformat(raw_date)
        except ValueError:
            return jsonify(error="reference_date invalida"), 400
    selected_date = reference_date or previous_calendar_day()
    with state_lock:
        # A verificação e a reserva acontecem no mesmo lock; duas requisições
        # simultâneas não conseguem iniciar dois Playwrights em paralelo.
        if state["state"] == "running":
            return jsonify(error="already_running"), 409
        state.update(
            state="running",
            message="Atualizacao em andamento.",
            reference_date=selected_date.isoformat(),
            started_at=datetime.utcnow().isoformat() + "Z",
            finished_at=None,
        )
        response = {"state": state["state"], "message": state["message"]}
    Thread(target=worker, args=(selected_date,), daemon=True, name="aster-worker").start()
    return jsonify(response), 202

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
