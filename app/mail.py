from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import Settings
from app.i18n import strings_for

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


def _otp_copy(settings: Settings, to_addr: str, code: str, *, lang: str, returning: bool) -> tuple[str, str, str]:
    t = strings_for(lang)
    minutes = max(1, settings.otp_ttl_seconds // 60)
    url = settings.app_base_url
    app = settings.app_name
    hello = t["mail_hello_back"] if returning else t["mail_hello_new"]
    hello = hello.format(app=app, email=to_addr)
    subject = t["mail_subject"].format(app=app)
    text = "\n".join(
        [
            hello,
            t["mail_dont_share"],
            "",
            code,
            "",
            t["mail_expires"].format(minutes=minutes),
            t["mail_open"].format(app=app, url=url),
            "",
            t["mail_mistake"],
            "",
            t["mail_signoff"].format(app=app),
            t["mail_footer"],
            "",
        ]
    )
    safe_hello = html.escape(hello)
    safe_share = html.escape(t["mail_dont_share"])
    safe_expires = html.escape(t["mail_expires"].format(minutes=minutes))
    safe_mistake = html.escape(t["mail_mistake"])
    safe_signoff = html.escape(t["mail_signoff"].format(app=app))
    safe_footer = html.escape(t["mail_footer"])
    safe_app = html.escape(app)
    safe_url = html.escape(url, quote=True)
    safe_code = html.escape(code)
    html_body = f"""\
<html>
  <body style="font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1c241c;line-height:1.45">
    <p>{safe_hello}</p>
    <p>{safe_share}</p>
    <p style="font-size:28px;letter-spacing:0.18em;font-family:ui-monospace,Consolas,monospace;font-weight:700">{safe_code}</p>
    <p>{safe_expires}</p>
    <p><a href="{safe_url}">{safe_app}</a><br>
       <span style="color:#5c6758">{safe_url}</span></p>
    <p>{safe_mistake}</p>
    <p>{safe_signoff}<br>
       <span style="color:#5c6758;font-size:0.9em">{safe_footer}</span></p>
  </body>
</html>
"""
    return subject, text, html_body


def send_otp_email(
    settings: Settings,
    to_addr: str,
    code: str,
    *,
    lang: str = "cs",
    returning: bool = False,
) -> None:
    subject, text, html_body = _otp_copy(settings, to_addr, code, lang=lang, returning=returning)
    if settings.smtp_is_console:
        log.warning("OTP for %s: %s", to_addr, code)
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from}>"
    message["To"] = to_addr
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")

    with _smtp_client(settings) as smtp:
        smtp.ehlo()
        if settings.smtp_encryption == "starttls":
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
