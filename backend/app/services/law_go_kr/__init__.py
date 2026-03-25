"""
국가법령정보 OPEN API (법제처 DRF) 전용 패키지.

- `client`: lawSearch.do / lawService.do 범용 HTTP (가이드의 요청 변수를 그대로 전달 가능)
- `registry`: 가이드에 대응하는 target·엔드포인트 요약
- `fetch`: 채팅 연동용 통합 조회(다중 target 검색 + 본문 + 질의 발췌 + 링크)
- `parse`: JSON에서 ID·링크 추출, 검색어 변형

가이드 목록: https://open.law.go.kr/LSO/openApi/guideList.do
"""

from app.services.law_go_kr.client import law_search_request, law_service_request
from app.services.law_go_kr.constants import (
    DEFAULT_HEADERS,
    DEFAULT_LAW_SEARCH_URL,
    DEFAULT_LAW_SERVICE_URL,
    OPENAPI_GUIDE_LIST_URL,
)
from app.services.law_go_kr.fetch import run_law_go_kr_fetch
from app.services.law_go_kr.registry import LAW_SEARCH_TARGET_SPECS, list_search_targets
from app.services.law_go_kr.types import LegalFetchResult

__all__ = [
    "DEFAULT_HEADERS",
    "DEFAULT_LAW_SEARCH_URL",
    "DEFAULT_LAW_SERVICE_URL",
    "OPENAPI_GUIDE_LIST_URL",
    "LAW_SEARCH_TARGET_SPECS",
    "LegalFetchResult",
    "law_search_request",
    "law_service_request",
    "list_search_targets",
    "run_law_go_kr_fetch",
]
