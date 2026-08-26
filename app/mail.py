from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import Settings


def send_otp_email(settings: Settings, to_addr: str, code: str) -> None:
    body = (
        f"Váš kód k přihlášení do {settings.app_name} je: {code}\n\n"
        f"Platí {settings.otp_ttl_seconds // 60} minut. Pokud jste o kód nežádali, e-mail ignorujte.\n"
    )
    message = EmailMessage()
    message["Subject"] = f"Kód k přihlášení do {settings.app_name}"
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from}>"
    message["To"] = to_addr
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        if settings.smtp_starttls:
            smtp.starttls()
            smtp.ehlo()
        smtp.send_message(message)
