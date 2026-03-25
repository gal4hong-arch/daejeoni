"""supabase-py 클라이언트. .env의 SUPABASE_URL, SUPABASE_ANON_KEY 사용."""

from __future__ import annotations

from supabase import Client, create_client

from app.config import get_settings

_client: Client | None = None


def get_supabase_client() -> Client | None:
    """URL·키가 모두 있으면 싱글톤 Client, 없으면 None."""
    global _client
    s = get_settings()
    url = (s.supabase_url or "").strip()
    key = (s.supabase_anon_key or "").strip()
    if not url or not key:
        return None
    if _client is None:
        _client = create_client(url, key)
    return _client


def reset_supabase_client_for_tests() -> None:
    global _client
    _client = None
