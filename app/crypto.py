from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().app_encryption_key.encode("utf-8")
    return Fernet(key)


def encrypt_str(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_str(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted. Check APP_ENCRYPTION_KEY.") from exc
