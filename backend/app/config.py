from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv

_backend_root = Path(__file__).resolve().parent.parent
load_dotenv(_backend_root / ".env")
load_dotenv()


class Settings:
    database_url: str
    fernet_key: str
    openai_api_key: str
    legal_api_base_url: str
    law_go_kr_oc: str
    law_go_kr_base_url: str
    law_go_kr_target: str
    law_go_kr_target_fallback: str
    law_go_kr_timeout: float
    law_go_kr_service_url: str
    law_go_kr_service_type: str
    law_go_kr_service_max_ids: int
    law_go_kr_service_fetch: bool
    law_go_kr_extended_sources: bool
    law_go_kr_body_target: str
    law_go_kr_body_target_fallback: str
    system_fallback_model: str
    supabase_url: str
    supabase_anon_key: str
    supabase_jwt_secret: str
    allow_demo_user_header: bool

    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./data/platform.db")
        self.fernet_key = os.getenv("FERNET_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.legal_api_base_url = os.getenv("LEGAL_API_BASE_URL", "")
        self.law_go_kr_oc = os.getenv("LAW_GO_KR_OC", "")
        self.law_go_kr_base_url = os.getenv("LAW_GO_KR_BASE_URL", "https://www.law.go.kr/DRF/lawSearch.do")
        self.law_go_kr_target = os.getenv("LAW_GO_KR_TARGET", "aiSearch")
        self.law_go_kr_target_fallback = os.getenv("LAW_GO_KR_TARGET_FALLBACK", "law")
        try:
            self.law_go_kr_timeout = float(os.getenv("LAW_GO_KR_TIMEOUT", "25"))
        except ValueError:
            self.law_go_kr_timeout = 25.0
        self.law_go_kr_service_url = os.getenv(
            "LAW_GO_KR_SERVICE_URL", "https://www.law.go.kr/DRF/lawService.do"
        )
        self.law_go_kr_service_type = os.getenv("LAW_GO_KR_SERVICE_TYPE", "JSON").strip() or "JSON"
        try:
            self.law_go_kr_service_max_ids = int(os.getenv("LAW_GO_KR_SERVICE_MAX_IDS", "2"))
        except ValueError:
            self.law_go_kr_service_max_ids = 2
        self.law_go_kr_service_max_ids = max(0, min(self.law_go_kr_service_max_ids, 5))
        self.law_go_kr_service_fetch = os.getenv("LAW_GO_KR_SERVICE_FETCH", "true").lower() not in (
            "0",
            "false",
            "no",
        )
        self.law_go_kr_extended_sources = os.getenv("LAW_GO_KR_EXTENDED_SOURCES", "true").lower() not in (
            "0",
            "false",
            "no",
        )
        self.law_go_kr_body_target = os.getenv("LAW_GO_KR_BODY_TARGET", "eflaw").strip() or "eflaw"
        self.law_go_kr_body_target_fallback = os.getenv("LAW_GO_KR_BODY_TARGET_FALLBACK", "law").strip() or "law"
        self.system_fallback_model = os.getenv("SYSTEM_FALLBACK_MODEL", "gpt-4o-mini")
        self.supabase_url = os.getenv("SUPABASE_URL", "")
        self.supabase_anon_key = os.getenv("SUPABASE_ANON_KEY", "")
        self.supabase_jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "")
        self.allow_demo_user_header = os.getenv("ALLOW_DEMO_USER_HEADER", "").lower() in (
            "1",
            "true",
            "yes",
        )
        # 쉼표 구분. 이 이메일로 RAG 소스 등록 시 전 사용자에게 문서·청크 검색에 포함.
        self.rag_admin_emails = os.getenv("RAG_ADMIN_EMAILS", "gal4hong@gmail.com").strip()
        try:
            _rmb = int(os.getenv("RAG_PDF_MAX_BYTES", str(48 * 1024 * 1024)))
        except ValueError:
            _rmb = 48 * 1024 * 1024
        # 8MB ~ 200MB 클램프 (리버스 프록시 한도는 별도 설정)
        self.rag_pdf_max_bytes = max(8 * 1024 * 1024, min(_rmb, 200 * 1024 * 1024))
        self.rag_outline_chunk_enabled = os.getenv("RAG_OUTLINE_CHUNK", "true").lower() not in (
            "0",
            "false",
            "no",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_data_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        if path != ":memory:":
            p = Path(path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
