"""사용자별 DB 저장 API 키 — 기본 Fernet 암호화, USER_API_KEYS_PLAINTEXT=true 시 평문."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import UserApiKey
from app.services.crypto_keys import decrypt_secret, encrypt_secret


def _looks_like_fernet_ciphertext(s: str) -> bool:
    """복호화 실패한 값이 Fernet 암호문이면 평문 API 키로 쓰지 않는다."""
    t = (s or "").strip()
    return len(t) > 40 and t.startswith("gAAAAA")


def read_user_api_key_stored(stored: str | None) -> str | None:
    """DB 컬럼 encrypted_key 값 → 사용 가능한 평문 키."""
    if not stored:
        return None
    dec = decrypt_secret(stored)
    if (dec or "").strip():
        return dec.strip()
    s = get_settings()
    if not s.user_api_keys_plaintext:
        return None
    p = stored.strip()
    if not p:
        return None
    if _looks_like_fernet_ciphertext(p):
        return None
    return p


def store_user_api_key(plain: str) -> str:
    """저장용 문자열(암호화 또는 평문)."""
    s = get_settings()
    p = (plain or "").strip()
    if not p:
        return ""
    if s.user_api_keys_plaintext:
        return p
    return encrypt_secret(p)


def has_usable_stored_key(db: Session, user_id: str, provider: str) -> bool:
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == provider))
        .scalar_one_or_none()
    )
    if not row:
        return False
    plain = read_user_api_key_stored(row.encrypted_key)
    return bool((plain or "").strip())
