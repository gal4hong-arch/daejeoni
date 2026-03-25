from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/public")
def public_config() -> dict:
    """프론트에서 Supabase 클라이언트 초기화용(anon 키는 원래 공개 전제)."""
    s = get_settings()
    has_jwt_secret = bool((s.supabase_jwt_secret or "").strip())
    return {
        "supabase_url": s.supabase_url or "",
        "supabase_anon_key": s.supabase_anon_key or "",
        "demo_user_header_allowed": s.allow_demo_user_header,
        "jwt_verification_relaxed": not has_jwt_secret,
    }
