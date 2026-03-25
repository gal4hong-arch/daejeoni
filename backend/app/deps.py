"""FastAPI 공통 의존성."""

from fastapi import HTTPException
from supabase import Client

from app.supabase_client import get_supabase_client


def require_supabase() -> Client:
    client = get_supabase_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Supabase가 설정되지 않았습니다. .env에 SUPABASE_URL, SUPABASE_ANON_KEY를 설정하세요.",
        )
    return client
