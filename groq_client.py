"""Cliente Groq para fallback em automação Playwright com Aster ERP."""
import json
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
import requests


class StatePage(str, Enum):
    """Estados da página durante navegação."""
    LOGIN = "login"
    WORKSPACE = "workspace"
    REPORTS = "reports"
    SUMMARY_REPORT = "summary_report"
    DATE_FILTERS = "date_filters"
    TABLE_READY = "table_ready"
    LOADING = "loading"
    ERROR = "error"
    UNKNOWN = "unknown"


class ActionType(str, Enum):
    """Tipos de ação permitidos do Groq."""
    WAIT = "wait"
    CLICK = "click"
    FILL = "fill"
    EXTRACT = "extract"
    STOP = "stop"


class FieldType(str, Enum):
    """Campos alvos de ações."""
    START_DATE = "start_date"
    END_DATE = "end_date"
    CONFIRM = "confirm"
    REPORT_CARD = "report_card"
    TABLE = "table"
    NONE = "none"


@dataclass
class GroqAction:
    """Ação individual permitida do Groq."""
    action_type: str
    selector: Optional[str] = None
    text: Optional[str] = None
    field: str = "none"
    value: Optional[str] = None
    expected_after: Optional[str] = None

    def validate(self) -> bool:
        """Valida se a ação é segura para executar."""
        if self.action_type not in [e.value for e in ActionType]:
            return False
        if self.selector and self._is_dangerous_selector(self.selector):
            return False
        if self.value and self._is_sensitive_value(self.value):
            return False
        return True

    @staticmethod
    def _is_dangerous_selector(selector: str) -> bool:
        """Rejeita seletores com código malicioso."""
        dangerous_patterns = [
            r"javascript:",
            r"on\w+\s*=",
            r"<script",
            r"eval",
            r"exec",
            r"\$\(",
            r"window\.",
            r"document\.write",
        ]
        selector_lower = selector.lower()
        return any(re.search(pattern, selector_lower) for pattern in dangerous_patterns)

    @staticmethod
    def _is_sensitive_value(value: str) -> bool:
        """Rejeita valores sensíveis."""
        sensitive_patterns = [
            r"password",
            r"token",
            r"Bearer",
            r"Authorization",
            r"cookie",
            r"session",
        ]
        value_lower = value.lower()
        return any(re.search(pattern, value_lower) for pattern in sensitive_patterns)


@dataclass
class GroqResponse:
    """Resposta estruturada do Groq."""
    status: str  # ok|retry|blocked|ambiguous|not_found
    confidence: float
    reason: str
    page_state: str
    actions: list[dict]
    stop_reason: Optional[str] = None

    def is_valid(self) -> bool:
        """Valida se a resposta atende aos critérios."""
        if self.status not in ["ok", "retry", "blocked", "ambiguous", "not_found"]:
            return False
        if not 0 <= self.confidence <= 1:
            return False
        if self.confidence < 0.85 and self.status != "retry":
            return False
        return True


def sanitize_page_diagnostic(page_content: dict) -> dict:
    """Remove dados sensíveis antes de enviar ao Groq."""
    sensitive_keys = {
        "password",
        "token",
        "authorization",
        "cookie",
        "session",
        "api_key",
        "secret",
        "email",
        "username",
    }

    def clean_dict(obj):
        if isinstance(obj, dict):
            return {
                k: "***REDACTED***" if k.lower() in sensitive_keys else clean_dict(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [clean_dict(item) for item in obj]
        elif isinstance(obj, str):
            if len(obj) > 500:
                return obj[:500] + "..."
            return obj
        return obj

    sanitized = clean_dict(page_content)

    # Remover localStorage, sessionStorage, scripts
    for key in ["localStorage", "sessionStorage", "scripts", "csrf_token"]:
        sanitized.pop(key, None)

    return sanitized


class GroqClient:
    """Cliente para interagir com Groq de forma segura."""

    ALLOWED_ACTIONS = {ActionType.WAIT, ActionType.CLICK, ActionType.FILL, ActionType.EXTRACT, ActionType.STOP}
    MAX_ROUNDS = 3
    CONFIDENCE_THRESHOLD = 0.85

    def __init__(self, api_key: Optional[str] = None, model: str = "mixtral-8x7b-32768", timeout: int = 30, logger: Optional[logging.Logger] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.logger = logger or logging.getLogger("groq")
        self.enabled = bool(self.api_key)
        self.rounds = 0

        if not self.enabled:
            self.logger.warning("Groq desabilitado: GROQ_API_KEY não configurada")

    def ask_for_page_recovery(
        self,
        page_state: str,
        error_message: str,
        page_diagnostic: dict,
        field: str = "none",
    ) -> Optional[GroqResponse]:
        """Pergunta ao Groq como recuperar de um erro de seletor."""
        if not self.enabled:
            self.logger.info("Groq desabilitado; pulando recuperação")
            return None

        if self.rounds >= self.MAX_ROUNDS:
            self.logger.error("Máximo de rodadas Groq (%d) excedido", self.MAX_ROUNDS)
            return None

        self.rounds += 1

        diagnostic = sanitize_page_diagnostic(page_diagnostic)

        prompt = f"""Você é um especialista em automação de navegador com Playwright.
O bot está falhando ao encontrar um elemento na página do ASTER ERP.

Estado atual: {page_state}
Campo esperado: {field}
Erro: {error_message}

Diagnóstico sanitizado:
{json.dumps(diagnostic, indent=2, ensure_ascii=False)[:2000]}

REGRAS OBRIGATÓRIAS:
1. Responda APENAS em JSON válido, sem Markdown ou explicação
2. Confidence deve estar entre 0 e 1
3. Ações de preenchimento só são permitidas se confidence >= 0.85
4. Seletores não podem conter JavaScript ou código perigoso
5. Nunca sugira JavaScript arbitrário, apenas seletores CSS ou XPath seguros
6. Se não encontrar uma solução clara, retorne status="blocked"
7. Máximo 3 ações por resposta

Responda com JSON neste formato exato:
{{
  "status": "ok|retry|blocked|ambiguous|not_found",
  "confidence": 0.0,
  "reason": "descrição breve",
  "page_state": "{page_state}",
  "actions": [
    {{
      "type": "wait|click|fill|extract|stop",
      "target": {{"selector": "...", "field": "{field}"}},
      "value": "apenas para fill, não sensível",
      "expected_after": "condição observável"
    }}
  ],
  "stop_reason": null
}}
"""

        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1024,
                },
                timeout=self.timeout,
            )

            if response.status_code != 200:
                self.logger.error("Groq retornou status %d: %s", response.status_code, response.text[:200])
                return None

            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            if not content:
                self.logger.error("Resposta Groq vazia")
                return None

            # Limpar Markdown se presente
            if content.startswith("```"):
                content = re.sub(r"```(?:json)?\n?", "", content).strip()

            groq_data = json.loads(content)

            groq_response = GroqResponse(
                status=groq_data.get("status", "blocked"),
                confidence=float(groq_data.get("confidence", 0)),
                reason=groq_data.get("reason", ""),
                page_state=groq_data.get("page_state", page_state),
                actions=groq_data.get("actions", []),
                stop_reason=groq_data.get("stop_reason"),
            )

            if not groq_response.is_valid():
                self.logger.warning("Resposta Groq inválida: confidence=%.2f status=%s", groq_response.confidence, groq_response.status)
                return None

            # Validar cada ação
            for action_data in groq_response.actions:
                action = GroqAction(
                    action_type=action_data.get("type", "stop"),
                    selector=action_data.get("target", {}).get("selector"),
                    field=action_data.get("target", {}).get("field", "none"),
                    value=action_data.get("value"),
                )
                if not action.validate():
                    self.logger.error("Ação inválida ou perigosa: %s", action)
                    return None

            self.logger.info("Groq respondeu: status=%s confidence=%.2f actions=%d", groq_response.status, groq_response.confidence, len(groq_response.actions))
            return groq_response

        except json.JSONDecodeError as e:
            self.logger.error("Erro ao decodificar JSON do Groq: %s", e)
            return None
        except requests.RequestException as e:
            self.logger.error("Erro ao chamar Groq: %s", e)
            return None
        except Exception as e:
            self.logger.error("Erro inesperado no cliente Groq: %s", e)
            return None

    def reset_rounds(self):
        """Reseta contador de rodadas para novo ciclo."""
        self.rounds = 0
