"""Extrai vendas do Aster, publica lancamentos e envia o relatorio."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional
import csv
import html
import json
import logging
import os
import shutil
import smtplib
import ssl
import sys
from threading import Thread
from time import monotonic
from urllib.parse import urlencode
from urllib.request import urlopen
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without python-dotenv
    def load_dotenv(*_args, **_kwargs):
        return False
try:
    from playwright.sync_api import Browser, Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without playwright
    Browser = Any
    Page = Any

    class PlaywrightError(RuntimeError):
        pass

    class PlaywrightTimeoutError(PlaywrightError):
        pass

    def sync_playwright(*_args, **_kwargs):
        raise ModuleNotFoundError("Playwright is not installed. Install it with 'pip install playwright'.")

from business_calendar import resolve_reference_date
from sales_parser import key, number, read_rows, read_sales_records, row_date
from sheets_writer import publish_from_env
from groq_client import GroqClient, sanitize_page_diagnostic

ROOT = Path(__file__).resolve().parent
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
EMAIL_TEMPLATE = ROOT / "corpo_de_email.json"

class NoReportData(RuntimeError):
    """Raised when the report loaded correctly but returned no rows."""

@dataclass(frozen=True)
class Settings:
    aster_url: str
    username: str
    password: str
    username_selector: str
    password_selector: str
    login_button_selector: str
    report_url: str
    report_ready_selector: str
    report_table_selector: str
    report_card_selector: str
    report_data_mode: str
    report_start_date_selector: str
    report_end_date_selector: str
    report_confirm_selector: str
    report_start_date: str
    report_end_date: str
    post_login_wait_ms: int
    navigation_timeout_ms: int
    headless: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_security: str
    smtp_timeout_seconds: int
    mail_from: str
    mail_to: list[str]
    mail_cc: list[str]
    mail_subject: str
    output_dir: Path
    log_dir: Path
    daily_comparison_enabled: bool
    working_days_remaining: int
    sales_vendor_column: str
    sales_quantity_column: str
    sales_date_column: str
    groq_enabled: bool
    groq_api_key: str
    groq_model: str
    groq_timeout_seconds: int
    groq_confidence_threshold: float
    report_download_selector: str = 'button[data-tip="Baixar XLSX"]'
    chromium_executable: str = ""

    @classmethod
    def from_env(cls, reference_date=None):
        def required(name):
            value = os.getenv(name, "").strip()
            if not value: raise ValueError(f"Variavel obrigatoria ausente: {name}")
            return value
        def items(name):
            return [x.strip() for x in os.getenv(name, "").replace(";", ",").split(",") if x.strip()]
        today = reference_date or datetime.now().date()
        report_data_mode = os.getenv("ASTER_REPORT_DATA_MODE", "date_balance").strip()
        default_start = today if report_data_mode == "date_balance" else today.replace(day=1)
        groq_model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip()
        if groq_model == "mixtral-8x7b-32768":
            groq_model = DEFAULT_GROQ_MODEL
        return cls(
            os.getenv("ASTER_URL", "https://aster.gruposps.com.br/Login").strip(), required("ASTER_USERNAME"), required("ASTER_PASSWORD"),
            os.getenv("ASTER_USERNAME_SELECTOR", "input#email||input[type=\"email\"]").strip(),
            os.getenv("ASTER_PASSWORD_SELECTOR", "input[placeholder=\"Senha\"]||input[type=\"password\"]").strip(),
            os.getenv("ASTER_LOGIN_BUTTON_SELECTOR", "button[type=\"submit\"]").strip(),
            required("ASTER_REPORT_URL"),
            os.getenv("ASTER_REPORT_READY_SELECTOR", "body"), required("ASTER_REPORT_TABLE_SELECTOR"),
            os.getenv("ASTER_REPORT_CARD_SELECTOR", "").strip(),
            report_data_mode,
            os.getenv("ASTER_REPORT_START_DATE_SELECTOR", "").strip(), os.getenv("ASTER_REPORT_END_DATE_SELECTOR", "").strip(),
            os.getenv("ASTER_REPORT_CONFIRM_SELECTOR", 'button:has-text("Confirmar")').strip(),
            os.getenv("ASTER_REPORT_START_DATE", "").strip() or default_start.strftime("%d/%m/%Y"),
            os.getenv("ASTER_REPORT_END_DATE", "").strip() or today.strftime("%d/%m/%Y"),
            int(os.getenv("ASTER_POST_LOGIN_WAIT_MS", "1000")), int(os.getenv("ASTER_NAVIGATION_TIMEOUT_MS", "30000")),
            os.getenv("ASTER_HEADLESS", "true").lower() in {"1", "true", "yes"},
            required("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), required("SMTP_USERNAME"),
            required("SMTP_PASSWORD"), os.getenv("SMTP_SECURITY", "starttls"),
            int(os.getenv("SMTP_TIMEOUT_SECONDS", "30")), required("MAIL_FROM"),
            items("MAIL_TO"), items("MAIL_CC"), os.getenv("MAIL_SUBJECT", "Extracao Aster ERP"),
            ROOT / os.getenv("OUTPUT_DIR", "output"), ROOT / os.getenv("LOG_DIR", "logs"),
            os.getenv("DAILY_COMPARISON_ENABLED", "true").lower() in {"1", "true", "yes"},
            int(os.getenv("DAILY_WORKING_DAYS_REMAINING", "0")), os.getenv("ASTER_SALES_VENDOR_COLUMN", ""),
            os.getenv("ASTER_SALES_QUANTITY_COLUMN", ""), os.getenv("ASTER_SALES_DATE_COLUMN", ""),
            os.getenv("GROQ_ENABLED", "true").lower() in {"1", "true", "yes"},
            os.getenv("GROQ_API_KEY", "").strip(),
            groq_model,
            int(os.getenv("GROQ_TIMEOUT_SECONDS", "30")),
            float(os.getenv("GROQ_CONFIDENCE_THRESHOLD", "0.85")),
            os.getenv("ASTER_REPORT_DOWNLOAD_SELECTOR", 'button[data-tip="Baixar XLSX"]').strip(),
            os.getenv("ASTER_CHROMIUM_EXECUTABLE", "").strip() or shutil.which("chromium") or shutil.which("google-chrome") or "",
        )

def configure_logging(directory):
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("aster")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        stream = logging.StreamHandler(sys.stdout); stream.setFormatter(formatter)
        file = logging.FileHandler(directory / "execucao.log", encoding="utf-8"); file.setFormatter(formatter)
        logger.addHandler(stream); logger.addHandler(file)
    return logger

def _save_diagnostic(page: Page, settings: Settings, stem: str, logger):
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(settings.output_dir / f"{stem}.png"), full_page=True)
    except Exception as error:
        logger.warning("Nao foi possivel salvar screenshot de diagnostico: %s", error)
    try:
        (settings.output_dir / f"{stem}.html").write_text(page.content(), encoding="utf-8")
    except Exception as error:
        logger.warning("Nao foi possivel salvar HTML de diagnostico: %s", error)


def _visible_locator(page: Page, configured: str, fallback: str, timeout: int):
    selectors = [item.strip() for item in configured.split("||") if item.strip()]
    if fallback and fallback not in selectors:
        selectors.append(fallback)
    last_error = None
    deadline = monotonic() + timeout / 1000
    while True:
        for selector in selectors:
            candidate = page.locator(selector).first
            try:
                remaining = max(100, int((deadline - monotonic()) * 1000))
                candidate.wait_for(state="visible", timeout=min(remaining, 1000))
                return candidate, selector
            except PlaywrightTimeoutError as error:
                last_error = error
        if monotonic() >= deadline:
            break
        page.wait_for_timeout(100)
    if last_error:
        raise last_error
    raise ValueError("Nenhum seletor configurado para o campo de login")


def _wait_for_login_form(page: Page, settings: Settings, logger):
    """Aguarda a montagem da SPA e recupera uma tela de login que ficou vazia."""
    deadline = monotonic() + settings.navigation_timeout_ms / 1000
    reloads = 0
    last_error = None
    while monotonic() < deadline:
        remaining = max(100, int((deadline - monotonic()) * 1000))
        try:
            username, username_selector = _visible_locator(
                page, settings.username_selector, 'input[type="email"]', min(remaining, 5000)
            )
            password, password_selector = _visible_locator(
                page, settings.password_selector, 'input[type="password"]', min(remaining, 5000)
            )
            return username, username_selector, password, password_selector
        except (PlaywrightTimeoutError, ValueError) as error:
            last_error = error

        if reloads >= 2 or monotonic() >= deadline:
            break
        reloads += 1
        logger.warning("Formulario de login ainda nao montou; recarregando a SPA (tentativa %s/2)", reloads)
        try:
            page.reload(wait_until="commit", timeout=min(remaining, 15000))
        except PlaywrightError as error:
            logger.warning("Recarregamento da tela de login falhou: %s", error)
        page.wait_for_timeout(500)

    raise last_error or ValueError("Formulario de login nao apareceu")


def _find_visible_element(page: Page, selector: str, timeout: int):
    """Encontra o primeiro elemento visível usando o seletor; evita pegar o primeiro invisível."""
    locator = page.locator(selector)
    
    # Tenta encontrar o primeiro elemento visível em 100ms de intervalo
    deadline = monotonic() + timeout / 1000
    while True:
        for i in range(locator.count()):
            try:
                element = locator.nth(i)
                if element.is_visible():
                    return element
            except Exception:
                pass
        
        if monotonic() >= deadline:
            raise TimeoutError(f"Nenhum elemento visível encontrado com seletor '{selector}' em {timeout}ms")
        
        page.wait_for_timeout(100)


def _find_report_date_field(page: Page, configured_selector: str, field: str, timeout: int, logger):
    """Resolve um filtro de data sem depender da ordem dos inputs da SPA."""
    legacy_selector = 'input[autocomplete="off"]'
    explicit = bool(configured_selector and not configured_selector.startswith(legacy_selector))
    candidates = page.locator(configured_selector if explicit else "input:not([type='hidden']), textarea")
    deadline = monotonic() + timeout / 1000
    wanted = "inicio inicial start" if field == "start_date" else "fim final end"
    opposite = "fim final end" if field == "start_date" else "inicio inicial start"
    while True:
        count = candidates.count()
        scored = []
        for index in range(count):
            candidate = candidates.nth(index)
            try:
                if not candidate.is_visible() or not candidate.is_editable():
                    continue
                metadata = candidate.evaluate("""el => {
                    const label = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
                    // Os rotulos do Aster ficam tres niveis acima do input,
                    // sem <label for>. Nao atravesse um grupo com outros campos.
                    let parent = el.parentElement;
                    for (let depth = 0; parent && depth < 5; depth++) {
                        if (parent.querySelectorAll('input,textarea').length !== 1) {
                            parent = null;
                            break;
                        }
                        if ((parent.innerText || '').trim()) break;
                        parent = parent.parentElement;
                    }
                    return {
                        type: (el.type || '').toLowerCase(),
                        name: el.name || '', id: el.id || '',
                        placeholder: el.placeholder || '',
                        aria: el.getAttribute('aria-label') || '',
                        title: el.title || '',
                        label: Array.from(el.labels || []).map(item => item.innerText).join(' ') || (label ? label.innerText : ''),
                        context: parent ? parent.innerText.slice(0, 160) : ''
                    };
                }""")
                text = key(" ".join(str(value) for value in metadata.values()))
                if "pesquis" in text or "search" in text:
                    continue
                own_text = key(" ".join(str(value) for name, value in metadata.items() if name != "context"))
                direction_text = own_text if any(token in own_text for token in (wanted + " " + opposite).split()) else text
                matches_wanted = any(token in direction_text for token in wanted.split())
                matches_opposite = any(token in direction_text for token in opposite.split())
                if matches_opposite and not matches_wanted:
                    continue
                score = 100 if metadata["type"] == "date" else 0
                if any(token in text for token in ("data", "date")):
                    score += 60
                if matches_wanted:
                    score += 50
                # Configuracao explicita permite controles textuais sem rotulo.
                # Descoberta automatica exige evidencia de data e do papel do campo.
                if explicit or (score >= 60 and matches_wanted and not matches_opposite):
                    scored.append((score, index, metadata))
            except Exception:
                continue
        if scored:
            best_score = max(item[0] for item in scored)
            best = [item for item in scored if item[0] == best_score]
            if len(best) == 1:
                score, index, metadata = best[0]
                logger.info("Campo %s resolvido por DOM: input[%d] score=%d type=%s name=%s placeholder=%s", field, index, score, metadata["type"], metadata["name"], metadata["placeholder"])
                return candidates.nth(index)
        if monotonic() >= deadline:
            raise ValueError(
                f"Filtro {field} ausente, nao editavel ou ambiguo. "
                "O formulario de datas do relatorio pode nao ter aberto; verifique o diagnostico e os seletores."
            )
        page.wait_for_timeout(250)


def _capture_page_diagnostic(page: Page, field: str, error: str) -> dict:
    """Captura diagnóstico sanitizado da página para enviar ao Groq."""
    try:
        inputs_info = page.evaluate("""() => {
            const inputs = document.querySelectorAll('input');
            return Array.from(inputs).map(el => ({
                type: el.type,
                name: el.name,
                id: el.id,
                placeholder: el.placeholder,
                autocomplete: el.autocomplete,
                visible: el.offsetParent !== null,
                ariaLabel: el.getAttribute('aria-label'),
                dataTestid: el.getAttribute('data-testid'),
            }));
        }""")
        
        visible_text = page.locator("body").inner_text(timeout=2000)[:1000]
        
        return {
            "url": page.url,
            "title": page.title(),
            "field": field,
            "error": error,
            "inputs": inputs_info,
            "visible_text": visible_text,
        }
    except Exception as e:
        logger = logging.getLogger("aster")
        logger.warning("Erro ao capturar diagnóstico: %s", e)
        return {"url": page.url, "title": page.title(), "error": str(e)}


def _find_with_groq_fallback(
    page: Page,
    settings: Settings,
    selector: str,
    field: str,
    timeout: int,
    logger,
    groq_client: Optional[GroqClient] = None,
) -> Any:
    """Tenta encontrar elemento; se falhar, usa Groq como fallback."""
    try:
        if field in {"start_date", "end_date"}:
            return _find_report_date_field(page, selector, field, timeout, logger)
        return _find_visible_element(page, selector, timeout)
    except (TimeoutError, ValueError) as e:
        logger.warning("Seletor '%s' não encontrou elemento visível: %s", selector, e)
        
        if not groq_client or not groq_client.enabled:
            raise
        
        # Capturar diagnóstico e consultar Groq
        diagnostic = _capture_page_diagnostic(page, field, str(e))
        logger.info("Consultando Groq para campo '%s'", field)
        
        groq_response = groq_client.ask_for_page_recovery(
            page_state="summary_report",
            error_message=str(e),
            page_diagnostic=diagnostic,
            field=field,
        )
        
        if not groq_response:
            raise ValueError(f"Campo {field} não encontrado e Groq não conseguiu ajudar")
        
        if groq_response.status == "blocked":
            raise ValueError(f"Groq determinou que o campo {field} não pode ser alcançado: {groq_response.reason}")
        
        # Se Groq sugeriu uma ação de wait, espera
        if groq_response.actions and groq_response.actions[0].get("type") == "wait":
            wait_seconds = 3
            logger.info("Groq recomendou esperar %d segundos", wait_seconds)
            page.wait_for_timeout(wait_seconds * 1000)
            return _find_with_groq_fallback(page, settings, selector, field, timeout, logger, groq_client)
        
        # Se Groq sugeriu um novo seletor, tenta usar
        new_selector = None
        for action in groq_response.actions:
            if action.get("type") in ["click", "fill"]:
                new_selector = action.get("target", {}).get("selector")
                if new_selector:
                    break
        
        if new_selector and groq_response.confidence >= settings.groq_confidence_threshold:
            logger.info("Groq sugeriu novo seletor com confidence %.2f: %s", groq_response.confidence, new_selector)
            try:
                if field in {"start_date", "end_date"}:
                    return _find_report_date_field(page, new_selector, field, timeout, logger)
                return _find_visible_element(page, new_selector, timeout)
            except (TimeoutError, ValueError) as retry_error:
                logger.error("Novo seletor também falhou: %s", retry_error)
                raise ValueError(f"Campo {field}: seletor original e Groq falharam") from e
        
        raise ValueError(f"Campo {field}: Groq não conseguiu sugerir um seletor confiável")


def _apply_report_dates(page: Page, settings: Settings, logger, groq_client=None):
    """Resolve os dois campos antes de preencher e confirma os valores aplicados."""
    def parse_date(value):
        for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, pattern).date()
            except ValueError:
                pass
        raise ValueError("Data de filtro invalida; use DD/MM/YYYY ou YYYY-MM-DD")

    start = parse_date(settings.report_start_date)
    end = parse_date(settings.report_end_date)
    if start > end:
        raise ValueError("Data inicial posterior a data final")
    timeout = min(settings.navigation_timeout_ms, 15000)
    resolved = []
    for name, selector, value in (
        ("start_date", settings.report_start_date_selector, start),
        ("end_date", settings.report_end_date_selector, end),
    ):
        control = _find_with_groq_fallback(page, settings, selector, name, timeout, logger, groq_client)
        resolved.append((control, value))
    first = resolved[0][0].element_handle(timeout=timeout)
    try:
        if resolved[1][0].evaluate("(el, other) => el === other", first):
            raise ValueError("Data inicial e final resolveram para o mesmo campo")
    finally:
        if first is not None:
            first.dispose()
    for control, value in resolved:
        expected = value.isoformat() if control.get_attribute("type") == "date" else value.strftime("%d/%m/%Y")
        _fill_report_date(control, expected, timeout)


def _fill_report_date(control, expected, timeout):
    control.click(timeout=timeout)
    control.press("Control+A", timeout=timeout)
    control.press("Backspace", timeout=timeout)
    control.type(expected, delay=30, timeout=timeout)
    control.evaluate(
        """(element, value) => {
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(element, value);
            for (const type of ['input', 'change', 'blur']) {
                element.dispatchEvent(new Event(type, { bubbles: true }));
            }
        }""",
        expected,
    )
    control.press("Tab", timeout=timeout)
    if control.input_value(timeout=timeout) != expected:
        raise ValueError("O campo de data nao manteve o valor preenchido")


def _open_report_page(page: Page, settings: Settings, logger):
    """Abre o cartao visivel em Tudo e confirma a rota, inclusive em nova aba."""
    from urllib.parse import urlsplit
    def is_report(tab):
        return urlsplit(tab.url).path.lower().startswith("/executereport/")
    if is_report(page):
        return page
    if not settings.report_card_selector:
        raise ValueError("Configure o cartao Resumo Comercial ou a URL do relatorio")
    all_tab = page.locator('button[data-tab-id="all"]')
    all_tab.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    all_tab.click()
    logger.info("Abrindo Resumo Comercial pela aba Tudo")
    card = _find_visible_element(page, settings.report_card_selector, settings.navigation_timeout_ms)
    # O titulo e o icone usam pointer-events:none; o cartao que contem
    # o botao de favoritos e o alvo real observado no Workspace.
    container = card.locator("xpath=ancestor::*[./button[contains(@aria-label, 'favorit')]][1]")
    target = container if container.count() and container.is_visible() else card
    previous_pages = set(page.context.pages)
    target.click(timeout=settings.navigation_timeout_ms)
    deadline = monotonic() + settings.navigation_timeout_ms / 1000
    while monotonic() < deadline:
        for tab in page.context.pages:
            if (tab is page or tab not in previous_pages) and is_report(tab):
                tab.wait_for_load_state("domcontentloaded", timeout=settings.navigation_timeout_ms)
                logger.info("Tela ExecuteReport confirmada")
                return tab
        page.wait_for_timeout(100)
    raise ValueError("O clique em Resumo Comercial nao abriu a tela ExecuteReport")


def _goto_with_retry(page: Page, url: str, settings: Settings, logger, label: str, wait_until="domcontentloaded"):
    last_error = None
    for attempt in range(1, 3):
        try:
            page.goto(url, wait_until=wait_until, timeout=settings.navigation_timeout_ms)
            return
        except PlaywrightTimeoutError as error:
            last_error = error
            logger.warning("Timeout ao abrir %s na tentativa %s; tentando novamente", label, attempt)
            try:
                page.evaluate("() => window.stop()")
            except PlaywrightError:
                pass
    raise last_error


def _report_has_no_records(page: Page, timeout_ms=1000) -> bool:
    try:
        return page.get_by_text("Nenhum registro encontrado").first.is_visible(timeout=timeout_ms)
    except (PlaywrightError, PlaywrightTimeoutError):
        return False


def login_and_extract(page: Page, settings: Settings, logger):
    page.set_default_timeout(settings.navigation_timeout_ms)
    page.set_default_navigation_timeout(settings.navigation_timeout_ms)
    page.on("console", lambda message: logger.info("Console do Aster [%s]: %s", message.type, message.text))
    page.on("pageerror", lambda error: logger.error("Erro JavaScript do Aster: %s", error))
    page.on("requestfailed", lambda request: logger.error("Requisicao falhou: %s - %s", request.url, request.failure))
    
    # Inicializar cliente Groq
    groq_client = None
    if settings.groq_enabled and settings.groq_api_key:
        groq_client = GroqClient(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            timeout=settings.groq_timeout_seconds,
            logger=logger,
        )
        logger.info("Cliente Groq inicializado")
    else:
        logger.info("Groq desabilitado ou não configurado")
    
    logger.info("Abrindo tela de login")
    logger.info("Iniciando navegacao para o Aster")
    try:
        _goto_with_retry(page, settings.aster_url, settings, logger, "tela de login", wait_until="commit")
    except PlaywrightTimeoutError:
        _save_diagnostic(page, settings, "aster_navigation_timeout", logger)
        raise ValueError("Timeout ao abrir a tela de login do Aster; diagnostico salvo em output/aster_navigation_timeout.*")

    # A página é uma SPA: DOMContentLoaded não significa que React já montou o formulário.
    # O Aster e uma SPA com WebSocket/polling; networkidle pode nunca ocorrer.
    # O estado funcional que importa aqui e o formulario visivel, resolvido logo abaixo.
    logger.info("Navegacao inicial confirmada; aguardando o formulario funcional")
    logger.info("Tela inicial carregada: url=%s title=%s", page.url, page.title())

    try:
        username, username_selector, password, password_selector = _wait_for_login_form(
            page, settings, logger
        )
    except (PlaywrightTimeoutError, ValueError) as error:
        body = page.locator("body").inner_text(timeout=3000)[:800]
        logger.error("Formulario nao apareceu: url=%s title=%s body=%s", page.url, page.title(), body)
        _save_diagnostic(page, settings, "aster_login_form_timeout", logger)
        raise ValueError(
            "Timeout aguardando formulario de login; a SPA nao exibiu os campos. "
            "Verifique output/aster_login_form_timeout.*"
        ) from error

    logger.info("Seletor de usuario visivel: %s (quantidade=%s)", username_selector, username.count())
    username.fill(settings.username)
    logger.info("Seletor de senha visivel: %s (quantidade=%s)", password_selector, password.count())
    password.fill(settings.password)

    login_button, button_selector = _visible_locator(
        page,
        settings.login_button_selector,
        'button[type="submit"]',
        settings.navigation_timeout_ms,
    )
    logger.info("Enviando login pelo seletor: %s", button_selector)
    login_button.click()

    # Aguarda tanto a mudança de rota quanto o marcador de uma sessão autenticada.
    try:
        page.wait_for_function(
            """() => {
                const url = location.href.toLowerCase();
                const hasReports = !!document.querySelector('button[data-tab-id=\"Reports\"]');
                const hasAuthenticatedRoute = !url.includes('/login') && !url.endsWith('/login');
                return hasAuthenticatedRoute || hasReports;
            }""",
            timeout=settings.navigation_timeout_ms,
        )
    except PlaywrightTimeoutError as error:
        logger.error("Login nao produziu estado autenticado: url=%s title=%s", page.url, page.title())
        _save_diagnostic(page, settings, "aster_login_failed", logger)
        raise ValueError("Login nao concluiu a transicao para a area autenticada; verifique output/aster_login_failed.*") from error

    logger.info("Login processado: url=%s title=%s", page.url, page.title())
    if "/login" in page.url.casefold():
        _save_diagnostic(page, settings, "aster_login_failed", logger)
        raise ValueError("Login retornou para /Login; credencial rejeitada, sessao expirada ou fluxo incompleto")

    logger.info("Abrindo relatorio configurado: %s", settings.report_url)
    try:
        _goto_with_retry(page, settings.report_url, settings, logger, "relatorio configurado", wait_until="commit")
        page.locator("body").wait_for(state="visible", timeout=min(settings.navigation_timeout_ms, 10000))
    except PlaywrightTimeoutError:
        logger.info("Corpo da tela autenticada nao ficou visivel no prazo")

    if settings.report_ready_selector and "/executereport/" not in page.url.lower():
        try:
            page.locator(settings.report_ready_selector).first.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
        except PlaywrightTimeoutError as error:
            if "/login" not in page.url.casefold():
                try:
                    page.locator("body").wait_for(state="visible", timeout=3000)
                    logger.info(
                        "Tela autenticada visivel sem seletor report_ready; continuando pela busca do cartao: url=%s title=%s",
                        page.url,
                        page.title(),
                    )
                except PlaywrightTimeoutError:
                    logger.error("Relatorio nao ficou pronto: url=%s title=%s", page.url, page.title())
                    _save_diagnostic(page, settings, "aster_report_ready_timeout", logger)
                    raise ValueError("A tela autenticada nao ficou pronta para o relatorio") from error
            else:
                logger.error("Relatorio nao ficou pronto: url=%s title=%s", page.url, page.title())
                _save_diagnostic(page, settings, "aster_report_ready_timeout", logger)
                raise ValueError("A tela autenticada nao ficou pronta para o relatorio") from error

    if "/login" in page.url.casefold():
        _save_diagnostic(page, settings, "aster_session_lost", logger)
        raise ValueError("A sessao voltou para /Login ao abrir o relatorio")

    try:
        page = _open_report_page(page, settings, logger)
    except (PlaywrightError, ValueError, TimeoutError):
        _save_diagnostic(page, settings, "aster_report_navigation_failed", logger)
        raise

    try:
        _apply_report_dates(page, settings, logger, groq_client)
    except (PlaywrightError, TimeoutError, ValueError) as error:
        logger.error("Nao foi possivel aplicar o periodo do relatorio: %s", error)
        _save_diagnostic(page, settings, "aster_report_date_filters_failed", logger)
        raise ValueError(
            "Filtros de data indisponiveis ou invalidos; verifique "
            "output/aster_report_date_filters_failed.*. O clique no cartao "
            "nao garante que o formulario do relatorio abriu."
        ) from error

    if settings.report_confirm_selector:
        logger.info("Aplicando periodo do relatorio")
        try:
            confirm = _find_visible_element(page, settings.report_confirm_selector, 15000)
            logger.info("Botao de confirmacao encontrado e visivel")
            confirm.click()
        except (TimeoutError, ValueError) as error:
            logger.error("Botao de confirmacao nao ficou visivel: %s", error)
            _save_diagnostic(page, settings, "aster_report_confirm_timeout", logger)
            raise ValueError("Botao de confirmacao nao encontrado") from error
    
    if _report_has_no_records(page, 3000):
        _save_diagnostic(page, settings, "aster_report_no_records", logger)
        raise NoReportData("Relatorio carregado sem registros para o periodo informado")

    table = page.locator(settings.report_table_selector).first
    try:
        table.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    except PlaywrightTimeoutError as error:
        if _report_has_no_records(page, 1000):
            _save_diagnostic(page, settings, "aster_report_no_records", logger)
            raise NoReportData("Relatorio carregado sem registros para o periodo informado") from error
        _save_diagnostic(page, settings, "aster_report_table_timeout", logger)
        raise ValueError("A tabela do relatorio nao apareceu; verifique os filtros e o seletor") from error
    if settings.report_download_selector:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        with page.expect_download(timeout=settings.navigation_timeout_ms) as event:
            _find_visible_element(page, settings.report_download_selector, settings.navigation_timeout_ms).click()
        download = event.value
        # O botao se chama XLSX, mas o Aster observado entrega um CSV.
        suffix = Path(download.suggested_filename).suffix.lower()
        if suffix not in {".csv", ".txt", ".xlsx", ".xlsm"}:
            raise ValueError("Formato de exportacao do relatorio nao suportado")
        attachment = settings.output_dir / f"aster_{datetime.now():%Y%m%d_%H%M%S_%f}{suffix}"
        download.save_as(str(attachment))
        logger.info("Exportacao do relatorio concluida: %s", attachment.name)
        return "", attachment
    return table.evaluate("element => element.outerHTML"), None

def html_to_csv(content, directory):
    from html.parser import HTMLParser
    class Parser(HTMLParser):
        def __init__(self): super().__init__(); self.rows=[]; self.row=None; self.text=[]
        def handle_starttag(self, tag, attrs):
            if tag == "tr": self.row = []
            if tag in {"td", "th"}: self.text = []
        def handle_data(self, data):
            if self.row is not None: self.text.append(data)
        def handle_endtag(self, tag):
            if tag in {"td", "th"} and self.row is not None: self.row.append(" ".join("".join(self.text).split()))
            if tag == "tr" and self.row is not None: self.rows.append(self.row); self.row = None
    parser = Parser(); parser.feed(content)
    if not parser.rows: raise ValueError("Tabela sem linhas")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"aster_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as file: csv.writer(file).writerows(parser.rows)
    return path


def _find_report_column(columns, configured, defaults):
    wanted = {key(x) for x in (configured.split(",") if configured else defaults)}
    return next((original for normalized, original in columns.items() if normalized in wanted), None)


def _date_balance_payload(path: Path, reference_date: date, vendor_column="", quantity_column=""):
    rows = read_rows(path)
    if not rows:
        raise ValueError("Relatorio de vendas vazio")
    columns = {key(name): name for name in rows[0] if name is not None}
    vendor_field = _find_report_column(columns, vendor_column, ("vendedor", "vendedor(a)", "consultor"))
    type_field = _find_report_column(columns, "", ("tipo", "canal"))
    region_field = _find_report_column(columns, "", ("regiao", "região"))
    segment_field = _find_report_column(columns, "", ("segmento",))
    quantity_field = _find_report_column(columns, quantity_column, ("quantidade", "qtd", "toneladas", "peso", "peso total", "volume", "vendido"))
    value_field = _find_report_column(columns, "", ("valor total", "valor", "faturamento"))
    if any(field is None for field in (vendor_field, type_field, region_field, segment_field, quantity_field, value_field)):
        raise ValueError("Colunas do Resumo Comercial nao encontradas para date_balance")
    payload_rows = []
    for row in rows:
        sale_type = " ".join(str(row.get(type_field) or "").split()).strip()
        if key(sale_type) != "varejo":
            continue
        vendor = " ".join(str(row.get(vendor_field) or "").split()).strip()
        if not vendor:
            raise ValueError("Vendedor vazio no relatorio")
        amount = number(row.get(quantity_field))
        payload_rows.append([
            reference_date.isoformat(),
            vendor,
            sale_type,
            " ".join(str(row.get(region_field) or "").split()).strip(),
            " ".join(str(row.get(segment_field) or "").split()).strip(),
            amount,
            " ".join(str(row.get(value_field) or "").split()).strip(),
        ])
    if not payload_rows:
        raise ValueError("Nenhuma linha VAREJO encontrada no Resumo Comercial")
    return ["Data", "Vendedor", "Tipo", "Regiao", "Segmento", "Peso do dia (kg)", "Valor total"], payload_rows


def _format_decimal(value):
    amount = Decimal(str(value))
    text = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return text


def _format_tons_from_kg(value):
    amount = Decimal(str(value)) / Decimal("1000")
    return _format_decimal(amount)


def _rank_vendor_amounts(rows, reference_date, scope):
    totals = {}
    month_prefix = reference_date.strftime("%Y-%m")
    for row in rows:
        current = row_date(row[0])
        if current is None:
            continue
        if scope == "daily" and current != reference_date:
            continue
        if scope == "month" and (
            not current.isoformat().startswith(month_prefix) or current > reference_date
        ):
            continue
        vendor = str(row[1] or "").strip()
        if not vendor:
            continue
        amount = number(row[5] if len(row) >= 6 else row[2])
        totals[vendor] = totals.get(vendor, Decimal("0")) + amount
    sort_key = lambda item: (-item[1], item[0].casefold())
    return sorted(totals.items(), key=sort_key)


def _summarize_vendor_rows(rows, reference_date, monthly_rows=None):
    return (
        _rank_vendor_amounts(rows, reference_date, "daily"),
        _rank_vendor_amounts(monthly_rows if monthly_rows is not None else rows, reference_date, "month"),
    )


def _ranking_lines(title, ranking):
    lines = [title]
    if not ranking:
        lines.append("- Sem registros.")
        return lines
    for index, (vendor, amount) in enumerate(ranking, start=1):
        lines.append(f"{index}. {vendor}: {_format_tons_from_kg(amount)} t")
    return lines


def _ranking_html(title, ranking):
    if not ranking:
        return f"<h2>{html.escape(title)}</h2><p>Sem registros.</p>"
    items = "".join(
        "<tr>"
        f"<td>{index}</td>"
        f"<td>{html.escape(vendor)}</td>"
        f"<td>{html.escape(_format_tons_from_kg(amount))} t</td>"
        "</tr>"
        for index, (vendor, amount) in enumerate(ranking, start=1)
    )
    return (
        f"<h2>{html.escape(title)}</h2>"
        "<table>"
        "<thead><tr><th>#</th><th>Vendedor</th><th>Tonelagem</th></tr></thead>"
        f"<tbody>{items}</tbody>"
        "</table>"
    )


def _load_email_template():
    if not EMAIL_TEMPLATE.exists():
        return {}
    try:
        content = EMAIL_TEMPLATE.read_text(encoding="utf-8").strip()
        return json.loads(content) if content else {}
    except json.JSONDecodeError as error:
        raise ValueError("corpo_de_email.json invalido") from error


def _sheet_values_for_email(logger):
    endpoint = os.getenv("SHEETS_API_URL", "").strip()
    token = os.getenv("SHEETS_API_TOKEN", "").strip()
    sheet_name = os.getenv("SHEETS_OUTPUT_TAB", "1_Lançamentos Diários")
    if not endpoint or not token:
        return []
    separator = "&" if "?" in endpoint else "?"
    url = endpoint + separator + urlencode({"token": token})
    try:
        with urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except Exception as error:
        logger.warning("Nao foi possivel buscar historico da planilha para o e-mail: %s", error)
        return []
    if payload.get("status") != "ok":
        logger.warning("Apps Script recusou leitura do historico para o e-mail: %s", payload.get("error"))
        return []
    for sheet in payload.get("sheets", []):
        if sheet.get("name") == sheet_name:
            return sheet.get("rows") or []
    logger.warning("Aba %s nao encontrada ao montar historico do e-mail", sheet_name)
    return []


def _email_rows_from_sheet_values(values):
    if not values:
        return []
    headers = [str(header or "") for header in values[0]]
    columns = {key(header): index for index, header in enumerate(headers)}

    def find(defaults):
        for candidate in defaults:
            if key(candidate) in columns:
                return columns[key(candidate)]
        return None

    date_index = find(("data",))
    vendor_index = find(("vendedor", "vendedor(a)", "consultor"))
    amount_index = find(("peso do dia (kg)", "peso acumulado (kg)", "peso", "peso total", "quantidade"))
    if date_index is None or vendor_index is None or amount_index is None:
        return []
    rows = []
    for source in values[1:]:
        current = row_date(source[date_index] if date_index < len(source) else "")
        vendor = str(source[vendor_index] if vendor_index < len(source) else "").strip()
        if current is None or not vendor:
            continue
        try:
            amount = number(source[amount_index] if amount_index < len(source) else "")
        except ValueError:
            continue
        rows.append([current.isoformat(), vendor, "", "", "", amount, ""])
    return rows


def _write_rendered_email_json(settings, reference_date, message, logger):
    text_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    payload = {
        "subject": message["Subject"],
        "from": message["From"],
        "to": [address.strip() for address in message["To"].split(",") if address.strip()],
        "cc": [address.strip() for address in (message.get("Cc") or "").split(",") if address.strip()],
        "text": text_part.get_content() if text_part else "",
        "html": html_part.get_content() if html_part else "",
    }
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = settings.output_dir / f"email_{reference_date.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("JSON do e-mail renderizado salvo: %s", path.name)
    return path


def build_email_message(settings, reference_date, data_rows, no_report_data=False, attachment=None, monthly_rows=None):
    template = _load_email_template()
    subject = template.get("subject") or settings.mail_subject
    intro = template.get("intro") or template.get("text") or "Relatorio Aster atualizado."
    footer = template.get("footer", "")
    ranking_url = template.get("ranking_url", "")
    link_label = template.get("ranking_link_label", "Abrir ranking geral")
    daily, monthly = _summarize_vendor_rows(data_rows, reference_date, monthly_rows)

    if no_report_data:
        body_lines = [
            intro,
            "",
            f"Nao foram encontrados registros no Aster para {reference_date.isoformat()}. Nenhuma linha foi publicada na planilha.",
            "",
        ]
        body_lines.extend(_ranking_lines(f"Resultado por vendedor em {reference_date.strftime('%d/%m/%Y')}", daily))
        body_lines.append("")
        body_lines.extend(_ranking_lines(f"Ranking acumulado desde 01/{reference_date:%m/%Y}", monthly))
        if ranking_url:
            body_lines.extend(["", f"{link_label}: {ranking_url}"])
        if footer:
            body_lines.extend(["", footer])
        body = "\n".join(body_lines)
    else:
        body_lines = [intro, ""]
        body_lines.extend(_ranking_lines(f"Resultado por vendedor em {reference_date.strftime('%d/%m/%Y')}", daily))
        body_lines.append("")
        body_lines.extend(_ranking_lines(f"Ranking acumulado desde 01/{reference_date:%m/%Y}", monthly))
        if ranking_url:
            body_lines.extend(["", f"{link_label}: {ranking_url}"])
        if footer:
            body_lines.extend(["", footer])
        body = "\n".join(body_lines)

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = ", ".join(settings.mail_to)
    message["Cc"] = ", ".join(settings.mail_cc)
    message["Subject"] = subject
    message.set_content(body)

    if no_report_data:
        html_body = (
            "<html><body>"
            f"<p>{html.escape(intro).replace(chr(10), '<br>')}</p>"
            f"<p>Nao foram encontrados registros no Aster para {reference_date.isoformat()}. "
            "Nenhuma linha foi publicada na planilha.</p>"
            f"{_ranking_html(f'Resultado por vendedor em {reference_date:%d/%m/%Y}', daily)}"
            f"{_ranking_html(f'Ranking acumulado desde 01/{reference_date:%m/%Y}', monthly)}"
        )
        if ranking_url:
            html_body += (
                '<p><a style="display:inline-block;padding:10px 14px;'
                'background:#1f7a9d;color:#ffffff;text-decoration:none;border-radius:4px" '
                f'href="{html.escape(ranking_url, quote=True)}">{html.escape(link_label)}</a></p>'
            )
        if footer:
            html_body += f"<p>{html.escape(footer).replace(chr(10), '<br>')}</p>"
        html_body += "</body></html>"
        message.add_alternative(html_body, subtype="html")
    else:
        html_body = (
            "<html><body>"
            f"<p>{html.escape(intro).replace(chr(10), '<br>')}</p>"
            f"{_ranking_html(f'Resultado por vendedor em {reference_date:%d/%m/%Y}', daily)}"
            f"{_ranking_html(f'Ranking acumulado desde 01/{reference_date:%m/%Y}', monthly)}"
        )
        if ranking_url:
            html_body += (
                '<p><a style="display:inline-block;padding:10px 14px;'
                'background:#1f7a9d;color:#ffffff;text-decoration:none;border-radius:4px" '
                f'href="{html.escape(ranking_url, quote=True)}">{html.escape(link_label)}</a></p>'
            )
        if footer:
            html_body += f"<p>{html.escape(footer).replace(chr(10), '<br>')}</p>"
        html_body += "</body></html>"
        message.add_alternative(html_body, subtype="html")

    if attachment:
        message.add_attachment(attachment.read_bytes(), maintype="application", subtype="octet-stream", filename=attachment.name)
    return message


def send_email(settings, message, logger):
    errors = []

    def _send():
        try:
            context = ssl.create_default_context()
            if settings.smtp_security == "ssl":
                with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=settings.smtp_timeout_seconds) as server:
                    server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
            else:
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as server:
                    server.starttls(context=context); server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
        except Exception as error:
            errors.append(error)

    worker = Thread(target=_send, daemon=True, name="smtp-send")
    worker.start()
    worker.join(settings.smtp_timeout_seconds)
    if worker.is_alive():
        raise TimeoutError(f"Timeout ao enviar e-mail apos {settings.smtp_timeout_seconds}s")
    if errors:
        raise errors[0]
    logger.info("E-mail enviado")

def run(reference_date=None):
    load_dotenv(ROOT / ".env")
    reference_date = resolve_reference_date(reference_date)
    settings = Settings.from_env(reference_date); logger = configure_logging(settings.log_dir); logger.info("Inicio da execucao para %s", reference_date.isoformat())
    with sync_playwright() as playwright:
        launch_args = [
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        # O container oficial normalmente roda como root no Render; nesse caso
        # o Chromium precisa de --no-sandbox para iniciar de forma determinística.
        geteuid = getattr(os, "geteuid", None)
        if callable(geteuid) and geteuid() == 0:
            launch_args.append("--no-sandbox")
        launch_kwargs = {"headless": settings.headless, "args": launch_args}
        chromium_executable = getattr(settings, "chromium_executable", "")
        if chromium_executable:
            launch_kwargs["executable_path"] = chromium_executable
            logger.info("Usando Chromium configurado em %s", chromium_executable)
        browser: Browser = playwright.chromium.launch(**launch_kwargs)
        # O Aster registra um Service Worker para uso offline. No job headless,
        # ele pode servir um shell vazio antes de a SPA montar o login.
        block_service_workers = os.getenv("ASTER_BLOCK_SERVICE_WORKERS", "false").lower() in {"1", "true", "yes"}
        context = browser.new_context(
            ignore_https_errors=False,
            service_workers="block" if block_service_workers else "allow",
        )
        logger.info("Service workers do Aster: %s", "bloqueados" if block_service_workers else "permitidos")
        page = context.new_page()
        try:
            no_report_data = False
            email_rows = []
            try:
                content, attachment = login_and_extract(page, settings, logger)
            except NoReportData as error:
                logger.info("%s", error)
                content, attachment = "", None
                no_report_data = True
            if attachment is None and content.startswith("<"): attachment = html_to_csv(content, settings.output_dir)
            if settings.daily_comparison_enabled:
                if no_report_data:
                    logger.info("Nenhuma publicacao na planilha: relatorio sem registros")
                    records = []
                elif not attachment:
                    raise ValueError("A carga exige dados do relatorio")
                elif settings.report_data_mode == "date_balance":
                    headers, rows = _date_balance_payload(
                        attachment, reference_date, settings.sales_vendor_column,
                        settings.sales_quantity_column,
                    )
                    logger.info("Publicando %s linhas na planilha em modo %s", len(rows), settings.report_data_mode)
                    publish_from_env(headers, rows, reference_date, settings.report_data_mode)
                    logger.info("Publicacao na planilha concluida")
                    email_rows = rows
                else:
                    records = read_sales_records(
                        attachment, reference_date, settings.sales_vendor_column,
                        settings.sales_quantity_column, settings.sales_date_column,
                        require_date=(settings.report_data_mode == "daily_rows"
                                      and row_date(settings.report_start_date) != row_date(settings.report_end_date)),
                    )
                    headers = ["Data", "Vendedor", "Peso do dia (kg)", "Observação"]
                    if settings.report_data_mode == "cumulative_by_seller":
                        totals = {}
                        for _, vendor, amount in records: totals[vendor] = totals.get(vendor, Decimal("0")) + amount
                        rows = [[reference_date.isoformat(), vendor, amount, f"Aster acumulado ate {reference_date.isoformat()}"] for vendor, amount in totals.items()]
                    else:
                        rows = [[current.isoformat(), vendor, amount, "Automacao Aster"] for current, vendor, amount in records]
                    email_rows = rows
                    logger.info("Publicando %s linhas na planilha em modo %s", len(rows), settings.report_data_mode)
                    publish_from_env(headers, rows, reference_date, settings.report_data_mode)
                    logger.info("Publicacao na planilha concluida")
            monthly_email_rows = _email_rows_from_sheet_values(_sheet_values_for_email(logger))
            if monthly_email_rows:
                logger.info("Historico mensal carregado para e-mail: %s linhas", len(monthly_email_rows))
            else:
                logger.info("Historico mensal indisponivel; e-mail usara somente a carga atual")
            message = build_email_message(
                settings,
                reference_date,
                email_rows,
                no_report_data,
                attachment,
                monthly_email_rows or None,
            )
            _write_rendered_email_json(settings, reference_date, message, logger)
            logger.info("Enviando e-mail para %s", ", ".join(settings.mail_to))
            try:
                send_email(settings, message, logger)
            except (smtplib.SMTPException, OSError, TimeoutError) as error:
                logger.error("Falha ao enviar e-mail apos publicar a planilha: %s", error)
                if os.getenv("MAIL_REQUIRED", "false").lower() in {"1", "true", "yes"}:
                    raise
        finally:
            context.close()
            browser.close()
    logger.info("Execucao concluida")

if __name__ == "__main__":
    try: run()
    except (ValueError, PlaywrightTimeoutError, smtplib.SMTPException, OSError, RuntimeError) as error:
        logging.basicConfig(level=logging.ERROR); logging.exception("Falha na execucao: %s", error); raise SystemExit(1) from error
