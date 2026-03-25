from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_dev_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _dev_fernet
    key = get_settings().fernet_key.strip()
    if key:
        return Fernet(key.encode())
    if _dev_fernet is None:
        _dev_fernet = Fernet(Fernet.generate_key())
    return _dev_fernet


def encrypt_secret(plain: str) -> str:
    return get_fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    try:
        return get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
