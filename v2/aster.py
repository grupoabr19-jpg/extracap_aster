from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from .config import Config


log = logging.getLogger("aster_v2")


class SessionExpired(RuntimeError):
    """The Aster application redirected the automation back to its login page."""


class AsterExtractor:
    """Download the report, recovering once from a displaced Aster session."""

    max_session_attempts = 2

    def __init__(self, config: Config):
        self.config = config

    def extract(self, reference_date: date) -> Path:
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = reference_date.strftime("%Y%m%d")
        last_session_error: SessionExpired | None = None

        # Each attempt gets a new browser context: Aster can invalidate a
        # session when this account authenticates in another browser.
        for attempt in range(1, self.max_session_attempts + 1):
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page: Page | None = None
                try:
                    page = browser.new_page(accept_downloads=True)
                    page.set_default_timeout(60_000)
                    return self._download(page, reference_date, output_dir, stamp)
                except SessionExpired as error:
                    last_session_error = error
                    if attempt < self.max_session_attempts:
                        log.warning(
                            "Sessão do Aster encerrada; fechando o navegador e autenticando novamente (%s/%s).",
                            attempt,
                            self.max_session_attempts,
                        )
                        continue
                    self._save_diagnostics(page, output_dir, stamp)
                    raise RuntimeError(
                        "A sessão do Aster foi encerrada novamente após uma nova autenticação."
                    ) from error
                except PlaywrightTimeoutError as error:
                    if self._session_was_lost(page) and attempt < self.max_session_attempts:
                        log.warning("Aster retornou ao login; iniciando um novo navegador.")
                        continue
                    self._save_diagnostics(page, output_dir, stamp)
                    raise RuntimeError(
                        "O Aster não concluiu a extração; capturas foram salvas em output"
                    ) from error
                except PlaywrightError:
                    if self._session_was_lost(page) and attempt < self.max_session_attempts:
                        log.warning("Aster encerrou a sessão; iniciando um novo navegador.")
                        continue
                    self._save_diagnostics(page, output_dir, stamp)
                    raise
                finally:
                    browser.close()

        raise RuntimeError("Não foi possível recuperar a sessão do Aster") from last_session_error

    def _download(self, page: Page, reference_date: date, output_dir: Path, stamp: str) -> Path:
        config = self.config
        page.goto(config.aster_url, wait_until="domcontentloaded")
        page.locator(config.username_selector).fill(config.aster_user)
        page.locator(config.password_selector).fill(config.aster_password)
        page.locator(config.login_selector).click()
        page.wait_for_url(lambda url: "/login" not in url.lower(), timeout=60_000)

        if config.aster_report_url:
            page.goto(config.aster_report_url, wait_until="domcontentloaded")
        self._raise_if_session_lost(page)
        if config.report_card_selector:
            page.locator(config.report_card_selector).last.click()
        if config.report_ready_selector:
            page.locator(config.report_ready_selector).wait_for(state="visible")
        self._raise_if_session_lost(page)

        formatted_date = reference_date.strftime("%d/%m/%Y")
        if config.start_selector:
            page.locator(config.start_selector).fill(formatted_date)
            page.locator(config.start_selector).press("Tab")
        if config.end_selector:
            page.locator(config.end_selector).fill(formatted_date)
            page.locator(config.end_selector).press("Tab")
        if not config.download_selector:
            raise ValueError("ASTER_REPORT_DOWNLOAD_SELECTOR é obrigatório na nova versão")

        with page.expect_download(timeout=90_000) as download_info:
            page.locator(config.download_selector).click()
        download = download_info.value
        suffix = Path(download.suggested_filename).suffix.lower() or ".csv"
        path = output_dir / f"resumo_comercial_{stamp}{suffix}"
        download.save_as(path)
        return path

    def _session_was_lost(self, page: Page | None) -> bool:
        if page is None or page.is_closed():
            return False
        if "/login" in page.url.lower():
            return True
        try:
            return page.locator(self.config.username_selector).is_visible(timeout=1_000)
        except PlaywrightError:
            return False

    def _raise_if_session_lost(self, page: Page) -> None:
        if self._session_was_lost(page):
            raise SessionExpired("Aster retornou à tela de login")

    @staticmethod
    def _save_diagnostics(page: Page | None, output_dir: Path, stamp: str) -> None:
        if page is None or page.is_closed():
            return
        try:
            page.screenshot(path=str(output_dir / f"aster_error_{stamp}.png"), full_page=True)
            (output_dir / f"aster_error_{stamp}.html").write_text(
                page.content(), encoding="utf-8"
            )
        except PlaywrightError:
            log.warning("Não foi possível salvar as capturas de diagnóstico do Aster.")
