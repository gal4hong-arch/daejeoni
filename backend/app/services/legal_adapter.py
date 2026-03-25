"""
법령 조회 진입점.

실제 국가법령정보 API 호출·파싱은 `app.services.law_go_kr` 패키지에서 수행한다.

가이드: https://open.law.go.kr/LSO/openApi/guideList.do
"""

from __future__ import annotations

import json

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LegalSnapshot
from app.services.law_go_kr.constants import (
    DEFAULT_HEADERS,
    DEFAULT_LAW_SEARCH_URL,
    DEFAULT_LAW_SERVICE_URL,
    OPENAPI_GUIDE_LIST_URL,
)
from app.services.law_go_kr.fetch import run_law_go_kr_fetch
from app.services.law_go_kr.parse import law_search_query_variants, portal_search_url
from app.services.law_go_kr.types import LegalFetchResult

# law_resolution·테스트 호환 (비공개 심볼)
from app.services.law_go_kr.parse import (  # noqa: F401
    is_plausible_law_id_scalar as _is_plausible_law_id_scalar,
)
from app.services.law_go_kr.parse import (  # noqa: F401
    key_looks_like_law_id_field as _key_looks_like_law_id_field,
)


def _snapshot(
    db: Session,
    *,
    topic_session_id: str | None,
    query: str,
    payload: dict,
) -> None:
    db.add(
        LegalSnapshot(
            topic_session_id=topic_session_id,
            query=query,
            response_json=json.dumps(payload, ensure_ascii=False),
        )
    )


def _fetch_custom_proxy(
    db: Session,
    *,
    topic_session_id: str | None,
    query: str,
    base: str,
) -> LegalFetchResult:
    payload: dict = {"query": query, "stub": False, "proxy": base}
    try:
        with httpx.Client(timeout=20.0, headers=DEFAULT_HEADERS) as client:
            r = client.get(f"{base}/search", params={"q": query})
        if r.status_code == 200:
            payload = {"live": True, "body": r.text[:8000], "proxy": base}
        else:
            payload = {"live": False, "status": r.status_code, "body": r.text[:2000], "proxy": base}
    except Exception as e:
        payload = {"live": False, "error": str(e), "proxy": base}
        raw = json.dumps(payload, ensure_ascii=False)
        _snapshot(db, topic_session_id=topic_session_id, query=query, payload=payload)
        return LegalFetchResult(
            text="",
            raw_json=raw,
            ok=False,
            warning=f"법령 프록시 오류: {e}",
            debug={
                "requested": True,
                "mode": "proxy",
                "summary": f"법령 LEGAL_API_BASE_URL 프록시 오류: {e}",
                "search": {"called": True, "ok": False, "proxy": base},
                "service": {"attempted": 0, "ok": 0, "ids": []},
                "links": [{"label": "국가법령정보센터 검색", "url": portal_search_url(query)}],
            },
        )
    raw = json.dumps(payload, ensure_ascii=False)
    _snapshot(db, topic_session_id=topic_session_id, query=query, payload=payload)
    text = payload.get("body") or json.dumps(payload, ensure_ascii=False)[:1500]
    ok = "error" not in payload
    warn = None if ok else "법령 조회에 실패했습니다. 내부 문서만 근거로 답변합니다."
    return LegalFetchResult(
        text=text,
        raw_json=raw,
        ok=ok,
        warning=warn,
        debug={
            "requested": True,
            "mode": "proxy",
            "summary": "법령 LEGAL_API_BASE_URL 프록시 호출 성공" if ok else "법령 프록시 HTTP/응답 오류",
            "search": {"called": True, "ok": ok, "proxy": base, "http_status": payload.get("status")},
            "service": {"attempted": 0, "ok": 0, "ids": []},
            "links": [{"label": "국가법령정보센터 검색", "url": portal_search_url(query)}],
        },
    )


def fetch_legal(db: Session, *, topic_session_id: str | None, query: str) -> LegalFetchResult:
    """
    우선순위:
    1) LAW_GO_KR_OC 설정 시 → law_go_kr.fetch (다중 target 검색 + 본문 + 발췌)
    2) LEGAL_API_BASE_URL 설정 시 → {base}/search?q=...
    3) 없으면 스텁
    """
    settings = get_settings()
    oc = (getattr(settings, "law_go_kr_oc", None) or "").strip()
    search_url = (getattr(settings, "law_go_kr_base_url", None) or DEFAULT_LAW_SEARCH_URL).strip()
    target_primary = (getattr(settings, "law_go_kr_target", None) or "aiSearch").strip() or "aiSearch"
    target_fallback = (getattr(settings, "law_go_kr_target_fallback", None) or "law").strip() or "law"
    timeout = float(getattr(settings, "law_go_kr_timeout", None) or 25.0)
    service_url = (getattr(settings, "law_go_kr_service_url", None) or DEFAULT_LAW_SERVICE_URL).strip()
    service_type = (getattr(settings, "law_go_kr_service_type", None) or "JSON").strip() or "JSON"
    service_max_ids = int(getattr(settings, "law_go_kr_service_max_ids", None) or 2)
    service_fetch = bool(getattr(settings, "law_go_kr_service_fetch", True))
    extended_sources = bool(getattr(settings, "law_go_kr_extended_sources", True))
    body_target = (getattr(settings, "law_go_kr_body_target", None) or "eflaw").strip() or "eflaw"
    body_fallback = (getattr(settings, "law_go_kr_body_target_fallback", None) or "law").strip() or "law"

    if oc:
        return run_law_go_kr_fetch(
            db,
            topic_session_id=topic_session_id,
            query=query,
            oc=oc,
            search_url=search_url,
            target_primary=target_primary,
            target_fallback=target_fallback,
            timeout=timeout,
            service_url=service_url,
            service_type=service_type,
            service_max_ids=service_max_ids,
            service_fetch=service_fetch,
            extended_sources=extended_sources,
            statute_body_target=body_target,
            statute_body_fallback=body_fallback,
        )

    base = settings.legal_api_base_url.strip().rstrip("/")
    if base:
        return _fetch_custom_proxy(db, topic_session_id=topic_session_id, query=query, base=base)

    payload = {
        "stub": True,
        "message": f"LAW_GO_KR_OC 미설정 — 국가법령정보 API를 쓰려면 .env에 OC를 넣으세요. 가이드: {OPENAPI_GUIDE_LIST_URL}",
    }
    raw = json.dumps(payload, ensure_ascii=False)
    _snapshot(db, topic_session_id=topic_session_id, query=query, payload=payload)
    return LegalFetchResult(
        text=payload["message"],
        raw_json=raw,
        ok=True,
        warning="법령 실조회 스텁 — OC 설정 후 실제 연동됩니다.",
        debug={
            "requested": True,
            "mode": "stub",
            "summary": "법제처 API 미사용 — LAW_GO_KR_OC 미설정(스텁 안내만, LLM·내부문서만 근거 가능)",
            "search": {"called": False, "ok": False, "http_status": None},
            "service": {"attempted": 0, "ok": 0, "ids": []},
            "links": [
                {"label": "OPEN API 가이드", "url": OPENAPI_GUIDE_LIST_URL},
                {"label": "국가법령정보센터 검색", "url": portal_search_url(query)},
            ],
        },
    )


__all__ = [
    "DEFAULT_HEADERS",
    "DEFAULT_LAW_SEARCH_URL",
    "DEFAULT_LAW_SERVICE_URL",
    "LegalFetchResult",
    "fetch_legal",
    "law_search_query_variants",
    "portal_search_url",
]
