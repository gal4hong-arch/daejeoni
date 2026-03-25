"""OpenAI 임베딩 (벡터 검색용)."""

import json

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import UserApiKey
from app.services.crypto_keys import decrypt_secret


def _openai_for_embed(db: Session, user_id: str) -> OpenAI | None:
    s = get_settings()
    if s.openai_api_key:
        return OpenAI(api_key=s.openai_api_key)
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "openai"))
        .scalar_one_or_none()
    )
    if row:
        k = decrypt_secret(row.encrypted_key)
        if k:
            return OpenAI(api_key=k)
    return None


def embed_text(db: Session, user_id: str, text: str, model: str = "text-embedding-3-small") -> list[float] | None:
    text = (text or "")[:8000]
    if not text.strip():
        return None
    client = _openai_for_embed(db, user_id)
    if not client:
        return None
    try:
        r = client.embeddings.create(model=model, input=text)
        return list(r.data[0].embedding)
    except Exception:
        return None


def embedding_to_json(vec: list[float] | None) -> str | None:
    if not vec:
        return None
    return json.dumps(vec, ensure_ascii=False)


def json_to_embedding(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return [float(x) for x in v] if isinstance(v, list) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
