import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import ensure_data_dir, get_database_url, get_settings
from app.db.session import init_db
from app.routers import api_router

settings = get_settings()
ensure_data_dir(get_database_url())
init_db()

_log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _log.info("접속: 채팅 UI  http://127.0.0.1:8000/ui")
    _log.info("접속: API 문서 http://127.0.0.1:8000/docs")
    _log.info("(다른 --port 를 쓰면 주소의 포트를 맞추세요)")
    if not (settings.supabase_jwt_secret or "").strip():
        _log.warning(
            "SUPABASE_JWT_SECRET 미설정 — Supabase Bearer 토큰은 서명 검증 없이 sub만 사용합니다. "
            "운영·스테이징에서는 대시보드 Settings > API > JWT Secret 을 반드시 설정하세요."
        )
    _du = (get_database_url() or "").lower()
    if (
        _du.startswith("postgres")
        and not (settings.fernet_key or "").strip()
        and not settings.user_api_keys_plaintext
    ):
        _log.warning(
            "FERNET_KEY 미설정 — 프로세스마다 임의 키로 암호화되어, 재시작·다른 인스턴스에서는 "
            "DB에 저장된 사용자 API 키를 복호화할 수 없습니다. cap/backend/.env 등에 "
            "고정 FERNET_KEY를 설정하거나, 임시로 USER_API_KEYS_PLAINTEXT=true 를 쓸 수 있습니다."
        )
    if settings.user_api_keys_plaintext:
        _log.warning(
            "USER_API_KEYS_PLAINTEXT=true — 사용자 LLM 키가 DB에 평문으로 저장됩니다. "
            "가능하면 false 로 두고 FERNET_KEY 로 암호화 저장하세요."
        )
    yield


app = FastAPI(
    title="지자체 행정 AI 업무지원 플랫폼 MVP",
    description="PRD/TRD/AI_SPEC 기반 API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/index.html", status_code=302)


static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")
