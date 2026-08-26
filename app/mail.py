from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import Settings

log = logging.getLogger("zepplox.mail")


def _smtp_client(settings: Settings) -> smtplib.SMTP:
    timeout = 30
    if settings.smtp_encryption == "ssl":
        return smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    return smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)


def send_otp_email(settings: Settings, to_addr: str, code: str) -> None:
    body = (
        f"Váš kód k přihlášení do {settings.app_name} je: {code}\n\n"
        f"Platí {settings.otp_ttl_seconds // 60} minut. Pokud jste o kód nežádali, e-mail ignorujte.\n"
    )
    if settings.smtp_is_console:
        log.warning("OTP for %s: %s", to_addr, code)
        return

    message = EmailMessage()
    message["Subject"] = f"Kód k přihlášení do {settings.app_name}"
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from}>"
    message["To"] = to_addr
    message.set_content(body)

    with _smtp_client(settings) as smtp:
        smtp.ehlo()
        if settings.smtp_encryption == "starttls":
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
