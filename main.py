"""Extrai um relatorio do Aster ERP e envia o resultado por e-mail.

Os seletores do portal ficam em variaveis de ambiente para evitar acoplamento
com uma versao especifica da interface. Consulte README.md antes da primeira
execucao.
"""

from __future__ import annotations

import csv
import html
import logging
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from business_calendar import resolve_reference_date
from daily_comparison import DAILY_LOG_HEADERS, build_daily_log_rows
from sales_parser import read_sales_report
from sheets_client import extract_sheet_rows, extract_vendor_targets, load_workbook_from_env
from sheets_writer import output_tab_from_env, publish_from_env


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
    report_start_date: str
    report_end_date: str
    reference_date: date
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
    email_enabled: bool
    output_dir: Path
    log_dir: Path
    daily_comparison_enabled: bool
    working_days_remaining: int
    sales_vendor_column: str
    sales_quantity_column: str
    sales_date_column: str
    sales_accumulated_column: str

    @classmethod
    def from_env(cls, reference_date_override: date | None = None) -> "Settings":
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"Variavel obrigatoria ausente: {name}")
            return value

        def csv_list(name: str) -> list[str]:
            raw = os.getenv(name, "").replace(";", ",")
            return [item.strip() for item in raw.split(",") if item.strip()]

        reference_date = resolve_reference_date(reference_date_override)
        start_date = os.getenv("ASTER_REPORT_START_DATE", "").strip() or reference_date.replace(day=1).strftime("%d/%m/%Y")
        end_date = os.getenv("ASTER_REPORT_END_DATE", "").strip() or reference_date.strftime("%d/%m/%Y")
        email_enabled = os.getenv("EMAIL_ENABLED", "true").lower() in {"1", "true", "yes"}

        def email_value(name: str, default: str = "") -> str:
            return required(name) if email_enabled else os.getenv(name, default).strip()

        return cls(
            aster_url=required("ASTER_URL"), username=required("ASTER_USERNAME"),
            password=required("ASTER_PASSWORD"),
            username_selector=required("ASTER_USERNAME_SELECTOR"),
            password_selector=required("ASTER_PASSWORD_SELECTOR"),
            login_button_selector=required("ASTER_LOGIN_BUTTON_SELECTOR"),
            report_url=required("ASTER_REPORT_URL"),
            report_ready_selector=required("ASTER_REPORT_READY_SELECTOR"),
            report_table_selector=required("ASTER_REPORT_TABLE_SELECTOR"),
            report_download_selector=os.getenv("ASTER_REPORT_DOWNLOAD_SELECTOR", "").strip(),
            report_card_selector=os.getenv("ASTER_REPORT_CARD_SELECTOR", "").strip(),
            report_start_date_selector=os.getenv("ASTER_REPORT_START_DATE_SELECTOR", "").strip(),
            report_end_date_selector=os.getenv("ASTER_REPORT_END_DATE_SELECTOR", "").strip(),
            report_start_date=start_date,
            report_end_date=end_date,
            reference_date=reference_date,
            post_login_wait_ms=int(os.getenv("ASTER_POST_LOGIN_WAIT_MS", "1000")),
            navigation_timeout_ms=int(os.getenv("ASTER_NAVIGATION_TIMEOUT_MS", "30000")),
            headless=os.getenv("ASTER_HEADLESS", "true").lower() in {"1", "true", "yes"},
            smtp_host=email_value("SMTP_HOST"), smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=email_value("SMTP_USERNAME"), smtp_password=email_value("SMTP_PASSWORD"),
            smtp_security=os.getenv("SMTP_SECURITY", "starttls").lower(),
            mail_from=email_value("MAIL_FROM"), mail_to=csv_list("MAIL_TO"), mail_cc=csv_list("MAIL_CC"),
            mail_subject=os.getenv("MAIL_SUBJECT", "Extracao Aster ERP"),
            email_enabled=email_enabled,
            output_dir=ROOT / os.getenv("OUTPUT_DIR", "output"),
            log_dir=ROOT / os.getenv("LOG_DIR", "logs"),
            daily_comparison_enabled=os.getenv("DAILY_COMPARISON_ENABLED", "false").lower() in {"1", "true", "yes"},
            working_days_remaining=int(os.getenv("DAILY_WORKING_DAYS_REMAINING", "0")),
            sales_vendor_column=os.getenv("ASTER_SALES_VENDOR_COLUMN", "").strip(),
            sales_quantity_column=os.getenv("ASTER_SALES_QUANTITY_COLUMN", "").strip(),
            sales_date_column=os.getenv("ASTER_SALES_DATE_COLUMN", "").strip(),
            sales_accumulated_column=os.getenv("ASTER_SALES_ACCUMULATED_COLUMN", "").strip(),
        )


def configure_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("aster_extracao")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        file_handler = logging.FileHandler(log_dir / "execucao.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(stream)
        logger.addHandler(file_handler)
    return logger


def login_and_extract(page: Page, settings: Settings, logger: logging.Logger) -> tuple[str, Path | None]:
    page.set_default_timeout(settings.navigation_timeout_ms)
    logger.info("Abrindo tela de login")
    page.goto(settings.aster_url, wait_until="domcontentloaded")
    page.locator(settings.username_selector).wait_for(state="visible")
    page.locator(settings.username_selector).fill(settings.username)
    page.locator(settings.password_selector).fill(settings.password)
    page.locator(settings.login_button_selector).click()
    try:
        page.wait_for_url(
            lambda current_url: "/login" not in current_url.casefold(),
            timeout=settings.navigation_timeout_ms,
        )
    except PlaywrightTimeoutError:
        page.wait_for_timeout(settings.post_login_wait_ms)
    if "/login" in page.url.casefold():
        raise ValueError("Login nao concluido: o Aster retornou para a tela de login. Verifique usuario, senha e ambiente.")

    logger.info("Abrindo relatorio configurado")
    page.goto(settings.report_url, wait_until="domcontentloaded")
    if "/login" in page.url.casefold():
        raise ValueError("Sessao do Aster nao autenticada: a URL do relatorio redirecionou para Login.")
    page.locator(settings.report_ready_selector).wait_for(state="visible")

    reports_tab = page.locator('button[data-tab-id="Reports"]')
    if reports_tab.count():
        logger.info("Abrindo aba Relatorios")
        reports_tab.click()

    if settings.report_card_selector:
        logger.info("Abrindo Resumo Comercial")
        card = page.locator(settings.report_card_selector)
        try:
            card.last.wait_for(state="visible")
        except PlaywrightTimeoutError:
            settings.output_dir.mkdir(parents=True, exist_ok=True)
            (settings.output_dir / "aster_debug.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(settings.output_dir / "aster_debug.png"), full_page=True)
            logger.error("Resumo Comercial nao encontrado. URL atual: %s; HTML e captura salvos em output/aster_debug.*", page.url)
            raise
        card.last.click()
    if settings.report_start_date_selector and settings.report_start_date:
        page.locator(settings.report_start_date_selector).fill(settings.report_start_date)
        page.locator(settings.report_start_date_selector).press("Tab")
    if settings.report_end_date_selector and settings.report_end_date:
        page.locator(settings.report_end_date_selector).fill(settings.report_end_date)
        page.locator(settings.report_end_date_selector).press("Tab")

    download_path: Path | None = None
    if settings.report_download_selector:
        logger.info("Acionando download do relatorio")
        with page.expect_download() as download_info:
            page.locator(settings.report_download_selector).click()
        download = download_info.value
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        download_path = settings.output_dir / f"aster_{datetime.now():%Y%m%d_%H%M%S}{Path(download.suggested_filename).suffix}"
        download.save_as(download_path)
        logger.info("Download gerado: %s", download_path.name)
        return f"Download gerado: {download_path.name}", download_path

    logger.info("Extraindo tabela HTML")
    table = page.locator(settings.report_table_selector)
    table.wait_for(state="visible")
    table_html = table.evaluate("element => element.outerHTML")
    return table_html, None


def html_to_csv(table_html: str, output_dir: Path) -> Path:
    """Converte uma tabela simples em CSV usando o parser HTML do navegador."""
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[list[str]] = []
            self.current: list[str] | None = None
            self.text: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "tr": self.current = []
            if tag in {"td", "th"}: self.text = []

        def handle_data(self, data: str) -> None:
            if self.current is not None: self.text.append(data)

        def handle_endtag(self, tag: str) -> None:
            if tag in {"td", "th"} and self.current is not None: self.current.append(" ".join("".join(self.text).split()))
            if tag == "tr" and self.current is not None:
                self.rows.append(self.current); self.current = None

    parser = TableParser(); parser.feed(table_html)
    if not parser.rows: raise ValueError("A tabela extraida nao possui linhas")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"aster_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        csv.writer(file).writerows(parser.rows)
    return path


def build_email(settings: Settings, content: str, attachment: Path | None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = ", ".join(settings.mail_to)
    if settings.mail_cc: message["Cc"] = ", ".join(settings.mail_cc)
    message["Subject"] = settings.mail_subject
    message.set_content("Relatorio Aster ERP anexado ou disponivel em HTML.")
    message.add_alternative(f"<html><body><h2>Relatorio Aster ERP</h2>{content if content.startswith('<') else html.escape(content)}</body></html>", subtype="html")
    if attachment:
        data = attachment.read_bytes()
        maintype, subtype = ("text", "csv") if attachment.suffix.lower() == ".csv" else ("application", "octet-stream")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=attachment.name)
    return message


def send_email(settings: Settings, message: EmailMessage, logger: logging.Logger) -> None:
    context = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as server:
            server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo(); server.starttls(context=context); server.ehlo()
            server.login(settings.smtp_username, settings.smtp_password); server.send_message(message)
    logger.info("E-mail enviado para %s", ", ".join(settings.mail_to))


def publish_daily_sales(settings: Settings, report_path: Path, logger: logging.Logger) -> None:
    if not settings.daily_comparison_enabled:
        return
    workbook = load_workbook_from_env()
    targets = extract_vendor_targets(workbook)
    output_tab = output_tab_from_env()
    existing_rows = extract_sheet_rows(workbook, output_tab)
    reference_date = settings.reference_date
    vendor_names = [target.vendor for target in targets]
    sales_by_vendor, accumulated_by_vendor = read_sales_report(
        report_path, reference_date, vendor_names,
        settings.sales_vendor_column, settings.sales_quantity_column,
        settings.sales_date_column, settings.sales_accumulated_column,
    )
    rows = build_daily_log_rows(existing_rows, targets, sales_by_vendor, reference_date)
    result = publish_from_env(DAILY_LOG_HEADERS, rows)
    logger.info(
        "Lancamentos diarios atualizados: %s vendedores, %s linhas historicas, resposta=%s",
        len(targets), len(rows), result,
    )


def run(reference_date_override: date | None = None) -> None:
    load_dotenv(ROOT / ".env")
    settings = Settings.from_env(reference_date_override)
    logger = configure_logging(settings.log_dir)
    logger.info("Inicio da execucao")
    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch(headless=settings.headless)
        try:
            page = browser.new_page()
            content, attachment = login_and_extract(page, settings, logger)
            if attachment is None and content.startswith("<"):
                attachment = html_to_csv(content, settings.output_dir)
            if settings.daily_comparison_enabled:
                if attachment is None:
                    raise ValueError("O comparativo diario precisa do download CSV do relatorio")
                publish_daily_sales(settings, attachment, logger)
            if settings.email_enabled:
                send_email(settings, build_email(settings, content, attachment), logger)
        finally:
            browser.close()
    logger.info("Execucao concluida")


if __name__ == "__main__":
    try:
        run()
    except (ValueError, PlaywrightTimeoutError, smtplib.SMTPException, OSError) as error:
        logging.basicConfig(level=logging.ERROR)
        logging.exception("Falha na execucao: %s", error)
        raise SystemExit(1) from error
