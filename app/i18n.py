from __future__ import annotations

from fastapi import Request

LANGS = ("cs", "en")
DEFAULT_LANG = "cs"
COOKIE = "lang"

STRINGS: dict[str, dict[str, str]] = {
    "cs": {
        "tagline": "Zepp → Livelox",
        "lang_label": "Jazyk",
        "lang_cs": "Česky",
        "lang_en": "English",
        "footer": "Samostatný most. Nesouvisí se Zepp Health ani s Liveloxem.",
        "privacy_link": "Soukromí",
        "login_cta": "Přihlásit / registrovat",
        "home_link": "Úvod",
        "overview": "Přehled",
        "settings": "Nastavení",
        "logout": "Odhlásit",
        "back_home": "← Úvod",
        "back_overview": "← Přehled",
        "login_title": "Přihlášení",
        "login_lead": "Pošleme jednorázový kód na e-mail. Heslo v {app} není — první ověření účet založí.",
        "email_label": "E-mail",
        "send_code": "Poslat kód",
        "verify_title": "Zadejte kód",
        "verify_lead": "Kód platí několik minut a lze ho použít jen jednou.",
        "code_label": "Šestimístný kód",
        "verify_submit": "Přihlásit",
        "overview_ok": "Přihlášení funguje. Propojení Intervals.icu → Livelox přijde v dalším kroku.",
        "settings_pending": "Napojení na Intervals.icu a Livelox zatím není zapnuté. Až ověříme, že přihlášení a Docker běží, doplníme API klíč, OAuth a synchronizaci.",
        "account_label": "Účet",
        "error_csrf": "Neplatný formulář, zkuste to znovu.",
        "error_email": "Zadejte platnou e-mailovou adresu.",
        "error_otp_email": "Příliš mnoho pokusů na tento e-mail. Zkuste to za chvíli.",
        "error_otp_ip": "Příliš mnoho pokusů z této sítě. Zkuste to za chvíli.",
        "error_smtp": "E-mail s kódem se nepodařilo odeslat. Zkuste to znovu, nebo zkontrolujte SMTP.",
        "error_otp": "Neplatný nebo prošlý kód.",
        "privacy_title": "Soukromí",
    },
    "en": {
        "tagline": "Zepp → Livelox",
        "lang_label": "Language",
        "lang_cs": "Česky",
        "lang_en": "English",
        "footer": "An independent bridge. Not affiliated with Zepp Health or Livelox.",
        "privacy_link": "Privacy",
        "login_cta": "Sign in / register",
        "home_link": "Home",
        "overview": "Overview",
        "settings": "Settings",
        "logout": "Sign out",
        "back_home": "← Home",
        "back_overview": "← Overview",
        "login_title": "Sign in",
        "login_lead": "We send a one-time code to your e-mail. {app} has no password — the first successful code creates the account.",
        "email_label": "E-mail",
        "send_code": "Send code",
        "verify_title": "Enter the code",
        "verify_lead": "The code is valid for a few minutes and can be used only once.",
        "code_label": "Six-digit code",
        "verify_submit": "Sign in",
        "overview_ok": "Sign-in works. Connecting Intervals.icu → Livelox comes in the next step.",
        "settings_pending": "Intervals.icu and Livelox linking is not on yet. Once sign-in and Docker are proven, we will add the API key, OAuth, and sync.",
        "account_label": "Account",
        "error_csrf": "Invalid form, please try again.",
        "error_email": "Enter a valid e-mail address.",
        "error_otp_email": "Too many attempts for this e-mail. Try again in a little while.",
        "error_otp_ip": "Too many attempts from this network. Try again in a little while.",
        "error_smtp": "The login code could not be sent. Try again, or check SMTP.",
        "error_otp": "Invalid or expired code.",
        "privacy_title": "Privacy",
    },
}


def resolve_lang(request: Request) -> str:
    query = (request.query_params.get("lang") or "").lower()
    if query in LANGS:
        return query
    cookie = (request.cookies.get(COOKIE) or "").lower()
    if cookie in LANGS:
        return cookie
    for part in (request.headers.get("accept-language") or "").split(","):
        code = part.split(";")[0].strip().lower()
        if code.startswith("en"):
            return "en"
        if code.startswith("cs") or code.startswith("sk"):
            return "cs"
    return DEFAULT_LANG


def strings_for(lang: str) -> dict[str, str]:
    return STRINGS.get(lang, STRINGS[DEFAULT_LANG])
