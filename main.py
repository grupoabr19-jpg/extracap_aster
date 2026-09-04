"""Extrai vendas do Aster, publica lancamentos e envia o relatorio."""
from dataclasses import dataclass
from datetime import date, datetime
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
from daily_comparison import HEADERS, calculate_rows
from sales_parser import read_sales_report
from sheets_client import load_targets_from_env
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
    report_download_selector: str
    report_card_selector: str
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
    def from_env(cls):
        def required(name):
            value = os.getenv(name, "").strip()
            if not value: raise ValueError(f"Variavel obrigatoria ausente: {name}")
            return value
        def items(name):
            return [x.strip() for x in os.getenv(name, "").replace(";", ",").split(",") if x.strip()]
        today = datetime.now().date()
        return cls(
            required("ASTER_URL"), required("ASTER_USERNAME"), required("ASTER_PASSWORD"),
            required("ASTER_USERNAME_SELECTOR"), required("ASTER_PASSWORD_SELECTOR"),
            required("ASTER_LOGIN_BUTTON_SELECTOR"), required("ASTER_REPORT_URL"),
            os.getenv("ASTER_REPORT_READY_SELECTOR", "body"), required("ASTER_REPORT_TABLE_SELECTOR"),
            os.getenv("ASTER_REPORT_DOWNLOAD_SELECTOR", "").strip(), os.getenv("ASTER_REPORT_CARD_SELECTOR", "").strip(),
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
    logger.info("Abrindo tela de login")
    page.goto(settings.aster_url, wait_until="commit", timeout=settings.navigation_timeout_ms)
    logger.info("Tela de login carregada: %s", page.url)
    logger.info("Preenchendo usuario")
    username = page.locator(settings.username_selector)
    username.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    username.fill(settings.username)
    logger.info("Preenchendo senha")
    password = page.locator(settings.password_selector)
    password.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    password.fill(settings.password)
    logger.info("Enviando login")
    login_button = page.locator(settings.login_button_selector)
    login_button.wait_for(state="visible", timeout=settings.navigation_timeout_ms)
    login_button.click()
    try: page.wait_for_url(lambda url: "/login" not in url.casefold(), timeout=settings.navigation_timeout_ms)
    except PlaywrightTimeoutError: page.wait_for_timeout(settings.post_login_wait_ms)
    logger.info("Login processado: %s", page.url)
    if "/login" in page.url.casefold(): raise ValueError("Login nao concluido no Aster")
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
    if settings.report_download_selector:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        with page.expect_download() as info: page.locator(settings.report_download_selector).click()
        download = info.value
        path = settings.output_dir / f"aster_{datetime.now():%Y%m%d_%H%M%S}{Path(download.suggested_filename).suffix}"
        download.save_as(path)
        return f"Download gerado: {path.name}", path
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
    settings = Settings.from_env(); logger = configure_logging(settings.log_dir); logger.info("Inicio da execucao")
    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch(headless=settings.headless)
        try:
            content, attachment = login_and_extract(browser.new_page(), settings, logger)
            if attachment is None and content.startswith("<"): attachment = html_to_csv(content, settings.output_dir)
            if settings.daily_comparison_enabled:
                if not attachment: raise ValueError("Comparativo exige arquivo exportado")
                targets = load_targets_from_env(); ref = reference_date or date.today()
                daily, accumulated = read_sales_report(attachment, ref, [x.vendor for x in targets], settings.sales_vendor_column, settings.sales_quantity_column, settings.sales_date_column)
                publish_from_env(HEADERS, calculate_rows(targets, daily, accumulated, ref, settings.working_days_remaining))
            message = EmailMessage(); message["From"] = settings.mail_from; message["To"] = ", ".join(settings.mail_to); message["Cc"] = ", ".join(settings.mail_cc); message["Subject"] = settings.mail_subject; message.set_content("Relatorio Aster anexado.")
            if attachment: message.add_attachment(attachment.read_bytes(), maintype="application", subtype="octet-stream", filename=attachment.name)
            send_email(settings, message, logger)
        finally: browser.close()
    logger.info("Execucao concluida")

if __name__ == "__main__":
    try: run()
    except (ValueError, PlaywrightTimeoutError, smtplib.SMTPException, OSError, RuntimeError) as error:
        logging.basicConfig(level=logging.ERROR); logging.exception("Falha na execucao: %s", error); raise SystemExit(1) from error
