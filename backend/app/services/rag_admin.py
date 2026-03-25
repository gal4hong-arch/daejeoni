"""RAG 전역 공유: 설정된 관리자 이메일이 등록한 문서는 모든 사용자 검색 풀에 포함."""

from __future__ import annotations

from app.config import get_settings


def rag_admin_email_set() -> set[str]:
    raw = (get_settings().rag_admin_emails or "").strip()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_rag_admin_email(email: str | None) -> bool:
    if not email or not (email := email.strip().lower()):
        return False
    return email in rag_admin_email_set()
