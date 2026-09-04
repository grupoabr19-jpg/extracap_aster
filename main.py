"""Extrai vendas do Aster, publica lancamentos e envia o relatorio."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
import csv
import html
import logging
import os
import smtplib
import ssl
import sys
from dotenv import load_dotenv
from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
from business_calendar import resolve_reference_date
from sales_parser import read_sales_records
from sheets_writer import publish_from_env

ROOT = Path(__file__).resolve().parent

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

    @classmethod
    def from_env(cls, reference_date=None):
        def required(name):
            value = os.getenv(name, "").strip()
            if not value: raise ValueError(f"Variavel obrigatoria ausente: {name}")
            return value
        def items(name):
            return [x.strip() for x in os.getenv(name, "").replace(";", ",").split(",") if x.strip()]
        today = reference_date or datetime.now().date()
        return cls(
            os.getenv("ASTER_URL", "https://aster.gruposps.com.br/Login").strip(), required("ASTER_USERNAME"), required("ASTER_PASSWORD"),
            os.getenv("ASTER_USERNAME_SELECTOR", "input#email||input[type=\"email\"]").strip(),
            os.getenv("ASTER_PASSWORD_SELECTOR", "input[placeholder=\"Senha\"]||input[type=\"password\"]").strip(),
            os.getenv("ASTER_LOGIN_BUTTON_SELECTOR", "button[type=\"submit\"]").strip(),
            required("ASTER_REPORT_URL"),
            os.getenv("ASTER_REPORT_READY_SELECTOR", "body"), required("ASTER_REPORT_TABLE_SELECTOR"),
            os.getenv("ASTER_REPORT_CARD_SELECTOR", "").strip(),
            os.getenv("ASTER_REPORT_DATA_MODE", "daily_rows").strip(),
            os.getenv("ASTER_REPORT_START_DATE_SELECTOR", "").strip(), os.getenv("ASTER_REPORT_END_DATE_SELECTOR", "").strip(),
            os.getenv("ASTER_REPORT_CONFIRM_SELECTOR", 'button:has-text("Confirmar")').strip(),
            os.getenv("ASTER_REPORT_START_DATE", "").strip() or today.replace(day=1).strftime("%d/%m/%Y"),
            os.getenv("ASTER_REPORT_END_DATE", "").strip() or today.strftime("%d/%m/%Y"),
            int(os.getenv("ASTER_POST_LOGIN_WAIT_MS", "1000")), int(os.getenv("ASTER_NAVIGATION_TIMEOUT_MS", "30000")),
            os.getenv("ASTER_HEADLESS", "true").lower() in {"1", "true", "yes"},
            required("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), required("SMTP_USERNAME"),
            required("SMTP_PASSWORD"), os.getenv("SMTP_SECURITY", "starttls"), required("MAIL_FROM"),
            items("MAIL_TO"), items("MAIL_CC"), os.getenv("MAIL_SUBJECT", "Extracao Aster ERP"),
            ROOT / os.getenv("OUTPUT_DIR", "output"), ROOT / os.getenv("LOG_DIR", "logs"),
            os.getenv("DAILY_COMPARISON_ENABLED", "true").lower() in {"1", "true", "yes"},
            int(os.getenv("DAILY_WORKING_DAYS_REMAINING", "0")), os.getenv("ASTER_SALES_VENDOR_COLUMN", ""),
            os.getenv("ASTER_SALES_QUANTITY_COLUMN", ""), os.getenv("ASTER_SALES_DATE_COLUMN", ""),
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
    for selector in selectors:
        candidate = page.locator(selector).first
        try:
            candidate.wait_for(state="visible", timeout=min(timeout, 5000))
            return candidate, selector
        except PlaywrightTimeoutError as error:
            last_error = error
    if last_error:
        raise last_error
    raise ValueError("Nenhum seletor configurado para o campo de login")


def _find_visible_element(page: Page, selector: str, timeout: int):
    """Encontra o primeiro elemento visível usando o seletor; evita pegar o primeiro invisível."""
    locator = page.locator(selector)
    count = locator.count()
    if not count:
        raise ValueError(f"Nenhum elemento encontrado com seletor: {selector}")
    
    # Tenta encontrar o primeiro elemento visível em 100ms de intervalo
    deadline_ms = page.locator("body").evaluate("() => Date.now()") + timeout
    while True:
        for i in range(count):
            try:
                element = locator.nth(i)
                if element.is_visible():
                    return element
            except Exception:
                pass
        
        elapsed_ms = page.locator("body").evaluate("() => Date.now()") - deadline_ms + timeout
        if elapsed_ms >= timeout:
            raise TimeoutError(f"Nenhum elemento visível encontrado com seletor '{selector}' em {timeout}ms")
        
        page.wait_for_timeout(100)


def login_and_extract(page: Page, settings: Settings, logger):
    page.set_default_timeout(settings.navigation_timeout_ms)
    page.set_default_navigation_timeout(settings.navigation_timeout_ms)
    page.on("console", lambda message: logger.info("Console do Aster [%s]: %s", message.type, message.text))
    page.on("pageerror", lambda error: logger.error("Erro JavaScript do Aster: %s", error))
    page.on("requestfailed", lambda request: logger.error("Requisicao falhou: %s - %s", request.url, request.failure))
    logger.info("Abrindo tela de login")
    logger.info("Iniciando navegacao para o Aster")
    try:
        page.goto(settings.aster_url, wait_until="domcontentloaded", timeout=settings.navigation_timeout_ms)
    except PlaywrightTimeoutError:
        _save_diagnostic(page, settings, "aster_navigation_timeout", logger)
        raise ValueError("Timeout ao abrir a tela de login do Aster; diagnostico salvo em output/aster_navigation_timeout.*")

    # A página é uma SPA: DOMContentLoaded não significa que React já montou o formulário.
    try:
        page.wait_for_load_state("networkidle", timeout=min(settings.navigation_timeout_ms, 15000))
    except PlaywrightTimeoutError:
        logger.info("A rede do Aster nao ficou ociosa; continuando pela presenca do formulario")
    logger.info("Tela inicial carregada: url=%s title=%s", page.url, page.title())

    try:
        username, username_selector = _visible_locator(
            page,
            settings.username_selector,
            'input[type="email"]',
            settings.navigation_timeout_ms,
        )
        password, password_selector = _visible_locator(
            page,
            settings.password_selector,
            'input[type="password"]',
            settings.navigation_timeout_ms,
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
                const hasLogin = !!document.querySelector('input[type=\"email\"], input[type=\"password\"]');
                const hasReports = !!document.querySelector('button[data-tab-id=\"Reports\"]');
                return (!url.includes('/login') && !hasLogin) || hasReports;
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
        page.goto(settings.report_url, wait_until="domcontentloaded", timeout=settings.navigation_timeout_ms)
        page.wait_for_load_state("networkidle", timeout=min(settings.navigation_timeout_ms, 15000))
    except PlaywrightTimeoutError:
        logger.info("Relatorio nao ficou ocioso; continuando pela interface visivel")

    if settings.report_ready_selector:
        try:
            page.locator(settings.report_ready_selector).first.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
        except PlaywrightTimeoutError as error:
            logger.error("Relatorio nao ficou pronto: url=%s title=%s", page.url, page.title())
            _save_diagnostic(page, settings, "aster_report_ready_timeout", logger)
            raise ValueError("A tela autenticada nao ficou pronta para o relatorio") from error

    if "/login" in page.url.casefold():
        _save_diagnostic(page, settings, "aster_session_lost", logger)
        raise ValueError("A sessao voltou para /Login ao abrir o relatorio")

    reports = page.locator('button[data-tab-id="Reports"]').first
    if reports.count():
        logger.info("Abrindo aba Reports")
        reports.click()
    if settings.report_card_selector:
        logger.info("Abrindo cartao de relatorio: %s", settings.report_card_selector)
        clicked = False
        deadline_ms = min(settings.navigation_timeout_ms, 30000)
        started_at = page.locator("body").evaluate("() => Date.now()")
        last_match_count = 0
        while not clicked:
            matches = page.locator(settings.report_card_selector)
            last_match_count = matches.count()
            for index in range(last_match_count - 1, -1, -1):
                candidate = matches.nth(index)
                ancestors = []
                try:
                    ancestors.append(candidate.locator(
                        "xpath=ancestor-or-self::*[@role='button' or self::button or self::a or @tabindex='0'][1]"
                    ))
                except Exception:
                    pass
                for depth in range(1, 7):
                    ancestors.append(candidate.locator("xpath=" + "/".join([".."] * depth)))

                for ancestor in ancestors:
                    if ancestor.count() and ancestor.first.is_visible():
                        target = ancestor.first
                        logger.info("Clicando no alvo visivel do cartao (match=%s)", index)
                        target.click()
                        clicked = True
                        break
                if clicked:
                    break
                if candidate.is_visible():
                    logger.info("Clicando no texto visivel do cartao (match=%s)", index)
                    candidate.click()
                    clicked = True
                    break

            if clicked:
                break
            elapsed_ms = page.locator("body").evaluate("started => Date.now() - started", started_at)
            if elapsed_ms >= deadline_ms:
                break
            page.wait_for_timeout(250)

        logger.info("Correspondencias do cartao: %s", last_match_count)
        if not clicked:
            logger.error("Cartao de relatorio nao ficou clicavel: url=%s title=%s", page.url, page.title())
            _save_diagnostic(page, settings, "aster_report_card_timeout", logger)
            raise ValueError("O texto do cartao existe, mas nenhum container clicavel ficou visivel")
    
    # Capturar diagnóstico imediatamente após clique no cartão
    try:
        _save_diagnostic(page, settings, "aster_after_card_click", logger)
        logger.info("URL após clique: %s", page.url)
        logger.info("Título após clique: %s", page.title())
        
        # Listar todos os inputs e seus atributos
        inputs_info = page.evaluate("""() => {
            const inputs = document.querySelectorAll('input');
            return Array.from(inputs).map(el => ({
                type: el.type,
                name: el.name,
                id: el.id,
                placeholder: el.placeholder,
                autocomplete: el.autocomplete,
                value: el.value,
                visible: el.offsetParent !== null,
                className: el.className,
                ariaLabel: el.getAttribute('aria-label'),
                dataTestid: el.getAttribute('data-testid'),
            }));
        }""")
        logger.info("Inputs encontrados: %d", len(inputs_info))
        for idx, inp in enumerate(inputs_info):
            logger.info("Input[%d]: type=%s name=%s placeholder=%s autocomplete=%s visible=%s", 
                       idx, inp['type'], inp['name'], inp['placeholder'], inp['autocomplete'], inp['visible'])
        
        # Procurar elementos com texto contendo "Data"
        data_elements = page.evaluate("""() => {
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                null,
                false
            );
            const results = [];
            let node;
            while (node = walker.nextNode()) {
                if (node.textContent.toLowerCase().includes('data') && node.textContent.trim().length < 50) {
                    const el = node.parentElement;
                    results.push({
                        text: node.textContent.trim(),
                        tag: el.tagName,
                        id: el.id,
                        className: el.className,
                        visible: el.offsetParent !== null,
                    });
                }
            }
            return results;
        }""")
        logger.info("Elementos com texto 'Data': %d", len(data_elements))
        for elem in data_elements[:10]:  # Limitar a 10 primeiros
            logger.info("  Elemento: text='%s' tag=%s visible=%s", elem['text'], elem['tag'], elem['visible'])
    except Exception as diag_error:
        logger.warning("Erro ao capturar diagnóstico pós-clique: %s", diag_error)
    
    if settings.report_start_date_selector:
        try:
            field = _find_visible_element(page, settings.report_start_date_selector, 15000)
            logger.info("Campo de data inicial encontrado e visivel")
            field.fill(settings.report_start_date)
            field.press("Tab")
        except (TimeoutError, ValueError) as error:
            logger.error("Campo de data inicial nao ficou visivel: %s", error)
            _save_diagnostic(page, settings, "aster_report_start_date_timeout", logger)
            raise ValueError("Campo de data inicial nao encontrado ou invisivel") from error
    
    if settings.report_end_date_selector:
        try:
            field = _find_visible_element(page, settings.report_end_date_selector, 15000)
            logger.info("Campo de data final encontrado e visivel")
            field.fill(settings.report_end_date)
            field.press("Tab")
        except (TimeoutError, ValueError) as error:
            logger.error("Campo de data final nao ficou visivel: %s", error)
            _save_diagnostic(page, settings, "aster_report_end_date_timeout", logger)
            raise ValueError("Campo de data final nao encontrado ou invisivel") from error
    
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
    
    table = page.locator(settings.report_table_selector).first
    try:
        table.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    except PlaywrightTimeoutError as error:
        _save_diagnostic(page, settings, "aster_report_table_timeout", logger)
        raise ValueError("A tabela do relatorio nao apareceu; verifique os filtros e o seletor") from error
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

def send_email(settings, message, logger):
    context = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as server:
            server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls(context=context); server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
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
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            launch_args.append("--no-sandbox")
        browser: Browser = playwright.chromium.launch(
            headless=settings.headless,
            args=launch_args,
        )
        context = browser.new_context(ignore_https_errors=False)
        page = context.new_page()
        try:
            content, attachment = login_and_extract(page, settings, logger)
            if attachment is None and content.startswith("<"): attachment = html_to_csv(content, settings.output_dir)
            if settings.daily_comparison_enabled:
                if not attachment: raise ValueError("A carga exige dados do relatorio")
                records = read_sales_records(attachment, reference_date, settings.sales_vendor_column, settings.sales_quantity_column, settings.sales_date_column)
                headers = ["Data", "Vendedor", "Peso do dia (kg)", "Observação"]
                if settings.report_data_mode == "cumulative_by_seller":
                    totals = {}
                    for _, vendor, amount in records: totals[vendor] = totals.get(vendor, Decimal("0")) + amount
                    rows = [[reference_date.isoformat(), vendor, amount, f"Aster acumulado ate {reference_date.isoformat()}"] for vendor, amount in totals.items()]
                else:
                    rows = [[current.isoformat(), vendor, amount, "Automacao Aster"] for current, vendor, amount in records]
                publish_from_env(headers, rows, reference_date, settings.report_data_mode)
            message = EmailMessage(); message["From"] = settings.mail_from; message["To"] = ", ".join(settings.mail_to); message["Cc"] = ", ".join(settings.mail_cc); message["Subject"] = settings.mail_subject; message.set_content("Relatorio Aster anexado.")
            if attachment: message.add_attachment(attachment.read_bytes(), maintype="application", subtype="octet-stream", filename=attachment.name)
            send_email(settings, message, logger)
        finally:
            context.close()
            browser.close()
    logger.info("Execucao concluida")

if __name__ == "__main__":
    try: run()
    except (ValueError, PlaywrightTimeoutError, smtplib.SMTPException, OSError, RuntimeError) as error:
        logging.basicConfig(level=logging.ERROR); logging.exception("Falha na execucao: %s", error); raise SystemExit(1) from error
