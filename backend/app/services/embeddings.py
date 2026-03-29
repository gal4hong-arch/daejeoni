"""OpenAI 임베딩 (벡터 검색용)."""

import hashlib
import json
import time

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import UserApiKey
from app.services.user_api_keys import read_user_api_key_stored

_EMBED_CACHE_TTL_SEC = 120.0
_EMBED_CACHE_MAX = 256
_EMBED_CACHE: dict[str, tuple[float, list[float]]] = {}


def _openai_for_embed(db: Session, user_id: str) -> OpenAI | None:
    s = get_settings()
    if s.openai_api_key:
        return OpenAI(api_key=s.openai_api_key)
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "openai"))
        .scalar_one_or_none()
    )
    if row:
        k = read_user_api_key_stored(row.encrypted_key)
        if k:
            return OpenAI(api_key=k)
    return None


def _cache_key(user_id: str, model: str, text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{user_id}:{model}:{h}"


def _cache_get(key: str) -> list[float] | None:
    now = time.time()
    row = _EMBED_CACHE.get(key)
    if not row:
        return None
    ts, vec = row
    if now - ts > _EMBED_CACHE_TTL_SEC:
        _EMBED_CACHE.pop(key, None)
        return None
    return vec


def _cache_set(key: str, vec: list[float]) -> None:
    _EMBED_CACHE[key] = (time.time(), vec)
    if len(_EMBED_CACHE) <= _EMBED_CACHE_MAX:
        return
    # FIFO 유사 정리(삽입 순서 dict 기준)
    drop_n = max(1, len(_EMBED_CACHE) - _EMBED_CACHE_MAX)
    for k in list(_EMBED_CACHE.keys())[:drop_n]:
        _EMBED_CACHE.pop(k, None)


def embed_text(
    db: Session,
    user_id: str,
    text: str,
    model: str = "text-embedding-3-small",
    meta_out: dict | None = None,
) -> list[float] | None:
    text = (text or "")[:8000]
    if not text.strip():
        return None
    t0 = time.perf_counter()
    ckey = _cache_key(user_id, model, text)
    cached = _cache_get(ckey)
    if cached:
        if meta_out is not None:
            meta_out.update({"cache_hit": True, "embed_ms": 0.0, "embed_model": model})
        return cached
    client = _openai_for_embed(db, user_id)
    if not client:
        return None
    try:
        r = client.embeddings.create(model=model, input=text)
        vec = list(r.data[0].embedding)
        _cache_set(ckey, vec)
        if meta_out is not None:
            meta_out.update(
                {
                    "cache_hit": False,
                    "embed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "embed_model": model,
                }
            )
        return vec
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
