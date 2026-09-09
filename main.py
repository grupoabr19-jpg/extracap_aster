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
            required("ASTER_URL"), required("ASTER_USERNAME"), required("ASTER_PASSWORD"),
            required("ASTER_USERNAME_SELECTOR"), required("ASTER_PASSWORD_SELECTOR"),
            required("ASTER_LOGIN_BUTTON_SELECTOR"), required("ASTER_REPORT_URL"),
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
            os.getenv("DAILY_COMPARISON_ENABLED", "false").lower() in {"1", "true", "yes"},
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

def login_and_extract(page: Page, settings: Settings, logger):
    page.set_default_timeout(settings.navigation_timeout_ms)
    page.set_default_navigation_timeout(settings.navigation_timeout_ms)
    page.on("console", lambda message: logger.info("Console do Aster [%s]: %s", message.type, message.text))
    page.on("pageerror", lambda error: logger.error("Erro JavaScript do Aster: %s", error))
    page.on("requestfailed", lambda request: logger.error("Requisicao falhou: %s - %s", request.url, request.failure))
    logger.info("Abrindo tela de login")
    logger.info("Iniciando navegacao para o Aster")
    try:
        page.goto(settings.aster_url, wait_until="commit", timeout=settings.navigation_timeout_ms)
    except PlaywrightTimeoutError:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(settings.output_dir / "aster_navigation_timeout.png"), full_page=True)
        (settings.output_dir / "aster_navigation_timeout.html").write_text(page.content(), encoding="utf-8")
        raise ValueError("Timeout ao abrir a tela de login do Aster; diagnostico salvo em output/aster_navigation_timeout.*")
    logger.info("Tela de login carregada: url=%s title=%s", page.url, page.title())
    logger.info("Preenchendo usuario")
    username = page.locator(settings.username_selector)
    logger.info("Aguardando campo de usuario: %s", settings.username_selector)
    try:
        username.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    except PlaywrightTimeoutError:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(settings.output_dir / "aster_login_form_timeout.png"), full_page=True)
        (settings.output_dir / "aster_login_form_timeout.html").write_text(page.content(), encoding="utf-8")
        logger.error("Formulario de login nao apareceu: url=%s title=%s body=%s", page.url, page.title(), page.locator("body").inner_text()[:500])
        raise ValueError("Timeout aguardando formulario de login; diagnostico salvo em output/aster_login_form_timeout.*")
    logger.info("Seletor de usuario encontrado: %s", username.count())
    username.fill(settings.username)
    logger.info("Preenchendo senha")
    password = page.locator(settings.password_selector)
    logger.info("Aguardando campo de senha: %s", settings.password_selector)
    password.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    logger.info("Seletor de senha encontrado: %s", password.count())
    password.fill(settings.password)
    logger.info("Enviando login")
    login_button = page.locator(settings.login_button_selector)
    logger.info("Aguardando botao de login: %s", settings.login_button_selector)
    login_button.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    logger.info("Seletor do botao de login encontrado: %s", login_button.count())
    login_button.click()
    try: page.wait_for_url(lambda url: "/login" not in url.casefold(), timeout=settings.navigation_timeout_ms)
    except PlaywrightTimeoutError: page.wait_for_timeout(settings.post_login_wait_ms)
    logger.info("Login processado: url=%s title=%s", page.url, page.title())
    if "/login" in page.url.casefold():
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(settings.output_dir / "aster_login_failed.png"), full_page=True)
        (settings.output_dir / "aster_login_failed.html").write_text(page.content(), encoding="utf-8")
        raise ValueError("Login nao concluido no Aster; diagnostico salvo em output/aster_login_failed.*")
    logger.info("Abrindo relatorio configurado")
    page.goto(settings.report_url, wait_until="domcontentloaded")
    page.locator(settings.report_ready_selector).wait_for(state="visible")
    reports = page.locator('button[data-tab-id="Reports"]')
    if reports.count(): reports.click()
    if settings.report_card_selector:
        card = page.locator(settings.report_card_selector).last
        card.wait_for(state="visible")
        card.locator("xpath=../..").click()
    if settings.report_start_date_selector:
        field = page.locator(settings.report_start_date_selector)
        field.fill(settings.report_start_date); field.press("Tab")
    if settings.report_end_date_selector:
        field = page.locator(settings.report_end_date_selector)
        field.fill(settings.report_end_date); field.press("Tab")
    if settings.report_confirm_selector:
        logger.info("Aplicando periodo do relatorio")
        confirm = page.locator(settings.report_confirm_selector)
        confirm.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
        confirm.click()
    table = page.locator(settings.report_table_selector)
    table.wait_for(state="visible")
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
        browser: Browser = playwright.chromium.launch(headless=settings.headless)
        try:
            content, attachment = login_and_extract(browser.new_page(), settings, logger)
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
        finally: browser.close()
    logger.info("Execucao concluida")

if __name__ == "__main__":
    try: run()
    except (ValueError, PlaywrightTimeoutError, smtplib.SMTPException, OSError, RuntimeError) as error:
        logging.basicConfig(level=logging.ERROR); logging.exception("Falha na execucao: %s", error); raise SystemExit(1) from error
