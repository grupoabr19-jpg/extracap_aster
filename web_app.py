"""Endpoint protegido para acionar a automacao pelo Google Sheets."""

from __future__ import annotations

import hmac
import os
import threading
from datetime import datetime

from flask import Flask, jsonify, request

from business_calendar import resolve_reference_date
from main import run


app = Flask(__name__)
_run_lock = threading.Lock()
_status_lock = threading.Lock()
_status: dict[str, object] = {
    "state": "idle",
    "message": "Nenhuma execucao iniciada desde o ultimo deploy.",
}


def _authorized() -> bool:
    expected = os.getenv("TRIGGER_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    if not expected or not supplied.startswith("Bearer "):
        return False
    return hmac.compare_digest(supplied[7:], expected)


def _set_status(**values: object) -> None:
    with _status_lock:
        _status.update(values)


def _execute(reference_date) -> None:
    started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    _set_status(
        state="running",
        started_at=started_at,
        finished_at=None,
        reference_date=reference_date.isoformat() if reference_date else None,
        message="Atualizacao em andamento.",
    )
    try:
        run(reference_date_override=reference_date)
    except Exception as error:
        _set_status(
            state="error",
            finished_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            message=f"{type(error).__name__}: {error}",
        )
    else:
        _set_status(
            state="success",
            finished_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            message="Planilha atualizada com sucesso.",
        )
    finally:
        _run_lock.release()


@app.get("/health")
def health():
    required = (
        "TRIGGER_TOKEN",
        "ASTER_URL",
        "ASTER_USERNAME",
        "ASTER_PASSWORD",
        "ASTER_USERNAME_SELECTOR",
        "ASTER_PASSWORD_SELECTOR",
        "ASTER_LOGIN_BUTTON_SELECTOR",
        "ASTER_REPORT_URL",
        "ASTER_REPORT_READY_SELECTOR",
        "ASTER_REPORT_TABLE_SELECTOR",
        "SHEETS_API_URL",
        "SHEETS_API_TOKEN",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        return jsonify({"status": "configuration_required", "missing": missing}), 503
    return jsonify({"status": "ok"}), 200


@app.post("/run")
def trigger_run():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _run_lock.acquire(blocking=False):
        return jsonify({"error": "already_running", **_status}), 409

    body = request.get_json(silent=True) or {}
    raw_date = str(body.get("reference_date") or "").strip()
    try:
        reference_date = resolve_reference_date(
            datetime.strptime(raw_date, "%Y-%m-%d").date() if raw_date else None
        )
    except ValueError as error:
        _run_lock.release()
        return jsonify({"error": "invalid_reference_date", "message": str(error)}), 400

    thread = threading.Thread(
        target=_execute,
        args=(reference_date,),
        name="aster-update",
        daemon=True,
    )
    thread.start()
    return jsonify({"status": "accepted", "message": "Atualizacao iniciada."}), 202


@app.get("/status")
def status():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    with _status_lock:
        return jsonify(dict(_status))
