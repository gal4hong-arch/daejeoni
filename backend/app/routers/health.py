from fastapi import APIRouter

from app.supabase_client import get_supabase_client

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    sb = get_supabase_client()
    return {
        "status": "ok",
        "supabase": "connected" if sb is not None else "not_configured",
    }
