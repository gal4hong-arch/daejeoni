"""
법제처 OPEN API (DRF) 엔드포인트·target 요약.

상세 파라미터·출력 필드는 가이드 각 항목을 따른다:
https://open.law.go.kr/LSO/openApi/guideList.do

이 모듈은 `LawGoKrClient.law_search` / `law_service`에 임의 query string을 넘길 수 있게 하되,
가이드에 대응하는 target·엔드포인트를 문서화한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LawSearchTargetSpec:
    """lawSearch.do 용 target 한 건."""

    target: str
    title_ko: str
    notes: str
    common_params: tuple[str, ...] = (
        "OC",
        "target",
        "type",
        "query",
        "search",
        "display",
        "page",
        "sort",
    )


# 가이드 목록과 대응(명칭은 가이드 표기에 맞춤; target 값은 API 명세 기준).
LAW_SEARCH_TARGET_SPECS: tuple[LawSearchTargetSpec, ...] = (
    LawSearchTargetSpec(
        "eflaw",
        "현행법령(시행일) 목록 조회",
        "nw=3 현행, search=1 법령명 / 2 본문검색, efYd·date·org·knd 등 가이드 참고",
    ),
    LawSearchTargetSpec(
        "law",
        "법령 목록 조회",
        "일반 법령 검색. search 모드에 따라 법령명·본문 등 (가이드별 상이)",
    ),
    LawSearchTargetSpec(
        "elaw",
        "영문법령 목록 조회",
        "영문 법령 검색 (가이드: lsEngListGuide 등)",
    ),
    LawSearchTargetSpec(
        "aiSearch",
        "AI 법령 검색",
        "지능형 검색용 target (기존 앱 기본값으로 많이 사용)",
    ),
    LawSearchTargetSpec(
        "admrul",
        "행정규칙 목록 조회",
        "훈령·예규·고시 등. mobileYn=Y 등 가이드(mobAdmrulListguide) 참고",
    ),
    LawSearchTargetSpec(
        "ordin",
        "자치법규(조례·규칙) 목록 조회",
        "lawSearch.do target=ordin. 모바일 가이드(mobOrdinListGuide) 등에서 mobileYn=Y·org·knd 등 선택. "
        "본문은 lawService.do target=ordin & ID=…",
    ),
)


@dataclass(frozen=True)
class LawServiceTargetSpec:
    """lawService.do (또는 동일 계열) 본문 조회."""

    target: str
    title_ko: str
    id_param: str
    notes: str


LAW_SERVICE_TARGET_SPECS: tuple[LawServiceTargetSpec, ...] = (
    LawServiceTargetSpec(
        "eflaw",
        "현행법령 본문(시행일)",
        "ID",
        "가이드: target=eflaw, ID 또는 MST+efYd. 앱은 ID 우선 후 law 폴백",
    ),
    LawServiceTargetSpec("law", "법령 본문", "ID", "법령ID(일련번호)로 본문 JSON/XML"),
    LawServiceTargetSpec("admrul", "행정규칙 본문", "ID", "행정규칙 ID — 응답 스키마는 가이드 확인"),
    LawServiceTargetSpec("ordin", "자치법규 본문", "ID", "자치법규 ID — 가이드 확인"),
)


def list_search_targets() -> list[str]:
    return [s.target for s in LAW_SEARCH_TARGET_SPECS]


def spec_for_search_target(target: str) -> LawSearchTargetSpec | None:
    t = (target or "").strip()
    for s in LAW_SEARCH_TARGET_SPECS:
        if s.target == t:
            return s
    return None
