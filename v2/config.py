from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

@dataclass(frozen=True)
class Config:
    timezone: str; aster_url: str; aster_report_url: str; aster_user: str; aster_password: str
    username_selector: str; password_selector: str; login_selector: str; report_ready_selector: str
    report_card_selector: str; download_selector: str; start_selector: str; end_selector: str
    sheets_url: str; sheets_token: str; trigger_token: str; spreadsheet_url: str
    email_enabled: bool; smtp_host: str; smtp_port: int; smtp_user: str; smtp_password: str
    mail_from: str; mail_to: tuple[str, ...]; mail_cc: tuple[str, ...]; dry_run: bool; conflict_policy: str; output_dir: str

    @classmethod
    def load(cls) -> "Config":
        def s(name: str, default: str = "") -> str: return os.getenv(name, default).strip()
        def b(name: str, default: bool = False) -> bool: return s(name, str(default)).lower() in {"1", "true", "yes", "on"}
        def emails(name: str) -> tuple[str, ...]: return tuple(value.strip() for value in s(name).replace(";", ",").split(",") if value.strip())
        return cls(s("BUSINESS_TIMEZONE", "America/Sao_Paulo"), s("ASTER_URL"), s("ASTER_REPORT_URL"), s("ASTER_USERNAME"), s("ASTER_PASSWORD"), s("ASTER_USERNAME_SELECTOR"), s("ASTER_PASSWORD_SELECTOR"), s("ASTER_LOGIN_BUTTON_SELECTOR"), s("ASTER_REPORT_READY_SELECTOR"), s("ASTER_REPORT_CARD_SELECTOR"), s("ASTER_REPORT_DOWNLOAD_SELECTOR"), s("ASTER_REPORT_START_DATE_SELECTOR"), s("ASTER_REPORT_END_DATE_SELECTOR"), s("SHEETS_V2_URL"), s("SHEETS_V2_TOKEN"), s("TRIGGER_TOKEN"), s("SPREADSHEET_URL"), b("EMAIL_ENABLED"), s("SMTP_HOST"), int(s("SMTP_PORT", "587") or 587), s("SMTP_USERNAME"), s("SMTP_PASSWORD"), s("MAIL_FROM"), emails("MAIL_TO"), emails("MAIL_CC"), b("DRY_RUN", True), s("ASTER_CONFLICT_POLICY", "skip_manual"), s("OUTPUT_DIR", "output"))

    def reference_date(self, override: str | None = None) -> date:
        return datetime.strptime(override, "%Y-%m-%d").date() if override else datetime.now(ZoneInfo(self.timezone)).date() - timedelta(days=1)

    def validate_for_run(self) -> None:
        required = {"ASTER_URL": self.aster_url, "ASTER_USERNAME": self.aster_user, "ASTER_PASSWORD": self.aster_password, "ASTER_USERNAME_SELECTOR": self.username_selector, "ASTER_PASSWORD_SELECTOR": self.password_selector, "ASTER_LOGIN_BUTTON_SELECTOR": self.login_selector, "ASTER_REPORT_URL": self.aster_report_url, "ASTER_REPORT_READY_SELECTOR": self.report_ready_selector, "ASTER_REPORT_DOWNLOAD_SELECTOR": self.download_selector, "ASTER_REPORT_START_DATE_SELECTOR": self.start_selector, "ASTER_REPORT_END_DATE_SELECTOR": self.end_selector, "SHEETS_V2_URL": self.sheets_url, "SHEETS_V2_TOKEN": self.sheets_token}
        missing = [name for name, value in required.items() if not value]
        if missing: raise ValueError("Configuração ausente: " + ", ".join(missing))
        if self.email_enabled:
            mail = {"SMTP_HOST": self.smtp_host, "SMTP_USERNAME": self.smtp_user, "SMTP_PASSWORD": self.smtp_password, "MAIL_FROM": self.mail_from, "MAIL_TO": self.mail_to}
            missing_mail = [name for name, value in mail.items() if not value]
            if missing_mail: raise ValueError("Configuração de e-mail ausente: " + ", ".join(missing_mail))
        if self.conflict_policy not in {"skip_manual", "fail"}: raise ValueError("ASTER_CONFLICT_POLICY deve ser skip_manual ou fail")
