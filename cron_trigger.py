"""Aciona o servico web no horario programado pelo Render."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(url: str, token: str, method: str, data: bytes | None = None) -> tuple[int, dict]:
    request = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=180) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            detail = {"error": f"HTTP {error.code}"}
        return error.code, detail
    except URLError as error:
        raise RuntimeError(f"Servico de automacao indisponivel: {error.reason}") from error


def main() -> None:
    base_url = os.environ["AUTOMATION_URL"].rstrip("/")
    token = os.environ["TRIGGER_TOKEN"]
    status_code, result = _request(
        base_url + "/run", token, "POST", data=b"{}"
    )
    if status_code not in {202, 409}:
        raise RuntimeError(f"Acionamento recusado: HTTP {status_code} {result}")
    if status_code == 409 and result.get("error") != "already_running":
        raise RuntimeError(f"Resposta inesperada do servico: {result}")

    deadline = time.monotonic() + int(os.getenv("AUTOMATION_TIMEOUT_SECONDS", "1200"))
    while time.monotonic() < deadline:
        time.sleep(15)
        status_code, current = _request(base_url + "/status", token, "GET")
        if status_code != 200:
            raise RuntimeError(f"Falha ao consultar status: HTTP {status_code} {current}")
        state = current.get("state")
        if state == "success":
            print(f"Atualizacao diaria concluida: {current.get('message')}")
            return
        if state == "error":
            raise RuntimeError(f"Atualizacao falhou: {current.get('message')}")
    raise TimeoutError("A atualizacao nao terminou dentro do prazo configurado")


if __name__ == "__main__":
    main()
