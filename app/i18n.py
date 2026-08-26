from __future__ import annotations

from fastapi import Request

LANGS = ("cs", "en")
DEFAULT_LANG = "cs"
COOKIE = "lang"

STRINGS: dict[str, dict[str, str]] = {
    "cs": {
        "lang_label": "Jazyk",
        "lang_cs": "Česky",
        "lang_en": "English",
        "github": "GitHub",
        "footer": "Nezávislá synchronizace. Nesouvisí se Zepp Health ani s Liveloxem.",
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
        "settings_pending": "Napojení na Intervals.icu a Livelox zatím v rozhraní není. Až bude, sem dáte API klíč a propojíte Livelox. Zastavení přenosu a smazání účtu už teď funguje.",
        "account_label": "Účet",
        "sync_title": "Synchronizace",
        "sync_on": "Přenos tras je zapnutý. ZeppLox bude z Intervals.icu brát nové GPS aktivity a posílat je do Liveloxu.",
        "sync_off": "Přenos tras je vypnutý. Účet zůstává, ale nic se nestahuje ani nenahrává. Můžete ho znovu zapnout.",
        "sync_stop": "Zastavit synchronizaci",
        "sync_start": "Znovu zapnout synchronizaci",
        "sync_stopped": "Synchronizace je vypnutá.",
        "sync_started": "Synchronizace je znovu zapnutá.",
        "delete_title": "Smazat účet",
        "delete_lead": "Smaže se e-mail, uložené klíče a protokol z tohoto webu. Trasy, které už jsou v Liveloxu nebo v Intervals.icu, zůstanou. ZeppLox je odtud tahat přestane.",
        "delete_confirm": "Rozumím, že účet v ZeppLox nejde vzít zpět.",
        "delete_submit": "Smazat účet",
        "delete_need_confirm": "Zaškrtněte potvrzení, než účet smažete.",
        "error_csrf": "Neplatný formulář, zkuste to znovu.",
        "error_email": "Zadejte platnou e-mailovou adresu.",
        "error_otp_email": "Příliš mnoho pokusů na tento e-mail. Zkuste to za chvíli.",
        "error_otp_ip": "Příliš mnoho pokusů z této sítě. Zkuste to za chvíli.",
        "error_smtp": "E-mail s kódem se nepodařilo odeslat. Zkuste to znovu, nebo zkontrolujte SMTP.",
        "error_otp": "Neplatný nebo prošlý kód.",
        "privacy_title": "Soukromí",
    },
    "en": {
        "lang_label": "Language",
        "lang_cs": "Česky",
        "lang_en": "English",
        "github": "GitHub",
        "footer": "Independent sync. Not affiliated with Zepp Health or Livelox.",
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
        "settings_pending": "Intervals.icu and Livelox linking is not in the UI yet. When it is, you will paste an API key and connect Livelox here. Stopping the transfer and deleting the account already work.",
        "account_label": "Account",
        "sync_title": "Sync",
        "sync_on": "Track transfer is on. ZeppLox will take new GPS activities from Intervals.icu and send them to Livelox.",
        "sync_off": "Track transfer is off. Your account stays, but nothing is downloaded or uploaded. You can turn it back on.",
        "sync_stop": "Stop syncing",
        "sync_start": "Turn sync back on",
        "sync_stopped": "Sync is off.",
        "sync_started": "Sync is on again.",
        "delete_title": "Delete account",
        "delete_lead": "This removes your e-mail, stored keys and log from this site. Routes already in Livelox or Intervals.icu stay there. ZeppLox will stop fetching them.",
        "delete_confirm": "I understand that a ZeppLox account cannot be undone.",
        "delete_submit": "Delete account",
        "delete_need_confirm": "Tick the confirmation before deleting the account.",
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
