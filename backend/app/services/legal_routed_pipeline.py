"""
법령 체크(use_legal) 시: LLM으로 관련 법령 제목만 추출 → 제목별 lawSearch+lawService → 답변 근거로만 사용.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LegalSnapshot
from app.services.law_go_kr.jo_param import parse_law_service_jo_from_query
from app.services.law_go_kr.statute_body import fetch_statute_service_body
from app.services.law_go_kr.constants import DEFAULT_HEADERS
from app.services.law_go_kr.parse import (
    law_service_body_stats_for_debug,
    law_service_data_for_llm,
    law_service_json_title_hint,
    xmlish_response_plain_text,
)
from app.services.law_go_kr.relevance import extract_relevant_excerpts
from app.services.law_go_kr.types import LawQueryAnalysis, LegalFetchResult
from app.services.law_resolution import LawMatch, law_match_portal_url, search_law_from_api
from app.services.llm_client import chat_completion


_TITLE_SYSTEM = """너는 대한민국 법령·행정 실무를 돕는 보조 모델이다.
사용자의 한국어 질의를 읽고 (1) 무엇을 하려는지(의도), (2) 질의가 전제로 삼거나 답변에 필요한 법령이 무엇인지(지칭·근거 법령)를 판단한다.

규칙:
- 질의에 법령명·약칭·오타가 있으면, 국가법령정보센터에서 검색될 법령의 공식 명칭(법률/시행령/시행규칙 구분이 가능하면 구분)으로 정리한다.
- 질의가 특정 조항만 묻는 경우에도, 그 조항이 속한 상위 법령(본법·시행령 등)을 titles에 포함한다.
- 질의와 무관하거나 연관이 약한 법령은 넣지 않는다. 추측으로 목록을 늘리지 않는다. 관련이 명확한 법령만 최대 5개까지.
- 사용자가 "이 법이 뭐야"처럼 지시만 하고 법령명이 없으면, 문맥상 유일하게 특정 가능한 법령이 있을 때만 titles에 넣고, 불가하면 titles는 빈 배열로 둔다.
- 출력은 JSON 한 개만. 설명·마크다운·코드펜스 금지.

출력 형식(키 이름을 정확히 지킬 것):
{
  "intent_summary": "한 문장으로 사용자가 원하는 것(예: 조문 열람, 요건 확인, 절차 안내, 비교 질문 등)",
  "law_focus": "질의가 핵심적으로 걸고 있는 주제(예: 국가계약 체결, 개인정보 동의)",
  "titles": ["법제처 검색에 넣을 공식 법령명1", "…"],
  "notes_for_search": "검색 힌트(약칭→정식명, 후보 우선순위 등). 불필요하면 빈 문자열"
}
- 해당 법령을 특정할 수 없으면 titles는 []이고, intent_summary·law_focus는 여전히 채운다."""

_TITLE_CACHE_TTL_SEC = 180.0
_TITLE_CACHE_MAX = 256
_TITLE_CACHE: dict[str, tuple[float, list[str], str, LawQueryAnalysis | None]] = {}


def _strip_json_block(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
        if m:
            t = m.group(1).strip()
    return t


def _parse_law_route_from_llm(raw: str) -> tuple[list[str], LawQueryAnalysis | None]:
    t = _strip_json_block(raw)
    if not t:
        return [], None
    try:
        d = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        return [], None
    if not isinstance(d, dict):
        return [], None

    arr = d.get("titles")
    titles: list[str] = []
    if isinstance(arr, list):
        for x in arr:
            if isinstance(x, str):
                s = re.sub(r"\s+", " ", x).strip()
                if len(s) >= 3 and s not in titles:
                    titles.append(s)
        titles = titles[:5]

    def _s(key: str, max_len: int = 800) -> str:
        v = d.get(key)
        if isinstance(v, str):
            return v.strip()[:max_len]
        return ""

    analysis = LawQueryAnalysis(
        intent_summary=_s("intent_summary", 500),
        law_focus=_s("law_focus", 500),
        notes_for_search=_s("notes_for_search", 800),
    )
    return titles, analysis


def _title_cache_key(user_id: str, model: str, user_message: str) -> str:
    norm = re.sub(r"\s+", " ", (user_message or "").strip().lower())[:1200]
    return f"{user_id}:{model}:{norm}"


def _title_cache_get(key: str) -> tuple[list[str], str, LawQueryAnalysis | None] | None:
    row = _TITLE_CACHE.get(key)
    if not row:
        return None
    ts, titles, raw, analysis = row
    if time.time() - ts > _TITLE_CACHE_TTL_SEC:
        _TITLE_CACHE.pop(key, None)
        return None
    return titles, raw, analysis


def _title_cache_set(
    key: str, titles: list[str], raw: str, analysis: LawQueryAnalysis | None
) -> None:
    _TITLE_CACHE[key] = (time.time(), list(titles), raw, analysis)
    if len(_TITLE_CACHE) <= _TITLE_CACHE_MAX:
        return
    drop_n = max(1, len(_TITLE_CACHE) - _TITLE_CACHE_MAX)
    for k in list(_TITLE_CACHE.keys())[:drop_n]:
        _TITLE_CACHE.pop(k, None)


def propose_relevant_law_titles(
    db: Session,
    *,
    user_id: str,
    model: str,
    user_message: str,
    llm_meta_out: dict | None = None,
) -> tuple[list[str], str, LawQueryAnalysis | None]:
    """1단계: 질의 의도·초점·법령 공식 제목 목록을 LLM에 요청."""
    ckey = _title_cache_key(user_id, model, user_message)
    cached = _title_cache_get(ckey)
    if cached is not None:
        if llm_meta_out is not None:
            llm_meta_out.update({"cache_hit": True, "llm_ms": 0.0, "provider": "cache"})
        return cached
    user_block = (
        f"사용자 질문:\n{user_message.strip()}\n\n"
        "위 질문에 대해:\n"
        "1) 사용자의 의도를 intent_summary에 한 문장으로 적고,\n"
        "2) 다루는 주제를 law_focus에 적고,\n"
        "3) 이 질문에 답하거나 질문이 전제로 삼는 법령·시행령·시행규칙의 공식 명칭을 titles에 넣으며,\n"
        "4) 법제처 lawSearch에 유리한 힌트가 있으면 notes_for_search에 적어라.\n\n"
        "반드시 지정된 JSON 형식만 출력하라."
    )
    try:
        raw = chat_completion(
            db,
            user_id=user_id,
            model=model,
            system=_TITLE_SYSTEM,
            user=user_block,
            temperature=0.1,
            max_tokens=1024,
            meta_out=llm_meta_out,
        )
    except Exception as e:
        return [], f"(제목 추출 실패: {e})", None
    titles, analysis = _parse_law_route_from_llm(raw)
    _title_cache_set(ckey, titles, raw, analysis)
    return titles, raw, analysis


def _label_for_match(m: LawMatch) -> str:
    if m.law_type == "시행령":
        return "[시행령]"
    if m.law_type == "시행규칙":
        return "[시행규칙]"
    return "[법]"


def fetch_legal_bodies_for_titles(
    db: Session,
    *,
    topic_session_id: str | None,
    user_query: str,
    titles: list[str],
    oc: str,
    timeout: float,
    service_max_ids: int,
) -> tuple[LegalFetchResult, list[dict[str, str]]]:
    """
    2단계: 제목별 lawSearch(1건 매칭) → lawService 본문 + 질의 발췌.
    반환 used_refs: 답변 근거로 실제 본문을 가져온 법령만 (링크용).
    """
    settings = get_settings()
    t0 = time.perf_counter()
    service_url = (settings.law_go_kr_service_url or "").strip().rstrip("/")
    stype = (settings.law_go_kr_service_type or "JSON").strip().upper() or "JSON"
    search_url = (settings.law_go_kr_base_url or "").strip()
    body_primary = (getattr(settings, "law_go_kr_body_target", None) or "eflaw").strip() or "eflaw"
    body_fallback = (getattr(settings, "law_go_kr_body_target_fallback", None) or "law").strip() or "law"

    search_log: list[dict[str, Any]] = []
    body_fetches: list[dict[str, Any]] = []
    used_refs: list[dict[str, str]] = []
    detail_blocks: list[str] = []
    seen_ids: set[str] = set()
    matches_ordered: list[LawMatch] = []

    if not oc.strip():
        payload = {"error": "no_oc", "titles": titles}
        return (
            LegalFetchResult(
                text="LAW_GO_KR_OC 미설정으로 법령 본문을 조회할 수 없습니다.",
                raw_json=json.dumps(payload, ensure_ascii=False),
                ok=True,
                warning="법령 OC 미설정",
                debug={"mode": "law_routed", "search_steps": [], "body_fetches": [], "links": []},
            ),
            [],
        )

    if not titles:
        return (
            LegalFetchResult(
                text="질문에 대해 LLM이 특정한 관련 법령 제목이 없어 본문 조회를 하지 않았습니다.",
                raw_json=json.dumps({"titles": []}, ensure_ascii=False),
                ok=True,
                warning=None,
                debug={
                    "mode": "law_routed",
                    "summary": "관련 법령 제목 없음 — 본문 조회 생략",
                    "search_steps": [],
                    "body_fetches": [],
                    "links": [],
                },
            ),
            [],
        )

    limit = max(1, min(service_max_ids, 5))
    try:
        timebox_sec = float(getattr(settings, "law_go_kr_timebox_sec", 12.0) or 12.0)
    except (TypeError, ValueError):
        timebox_sec = 12.0
    timebox_sec = max(3.0, min(timebox_sec, 40.0))

    jo_param = parse_law_service_jo_from_query(user_query)
    service_extra: dict[str, str] | None = {"JO": jo_param} if jo_param else None

    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        for i, title in enumerate(titles):
            if time.perf_counter() - t0 > timebox_sec:
                search_log.append(
                    {
                        "endpoint": "lawSearch.do",
                        "phase": "timebox",
                        "note": f"법령 검색 timebox 초과({timebox_sec:.1f}s)로 중단",
                    }
                )
                break
            if len(matches_ordered) >= limit:
                break
            row: dict[str, Any] = {
                "endpoint": "lawSearch.do",
                "phase": "title_match",
                "index": i + 1,
                "proposed_title": title[:200],
                "http_status": None,
            }
            search_dbg: list[dict[str, Any]] = []
            uq = (user_query or "").strip()
            m = search_law_from_api(
                title.strip(),
                oc=oc,
                search_url=search_url,
                client=client,
                timeout=timeout,
                search_debug=search_dbg,
                user_query=uq[:500] if uq else None,
            )
            row["search_attempts"] = search_dbg
            if m:
                row["http_status"] = 200
                row["matched_name"] = m.law_name
                row["matched_id"] = m.law_id
                row["matched_type"] = m.law_type
                if m.mst:
                    row["matched_mst"] = m.mst
                if m.ef_yd:
                    row["matched_efYd"] = m.ef_yd
                if m.detail_link:
                    row["matched_detail_link"] = m.detail_link[:500]
            else:
                row["note"] = "매칭 실패"
                search_log.append(row)
                continue
            dedup_key = (m.mst or m.law_id).strip()
            if dedup_key in seen_ids:
                row["note"] = "중복 MST/ID 스킵"
                search_log.append(row)
                continue
            seen_ids.add(dedup_key)
            matches_ordered.append(m)
            search_log.append(row)

        if not matches_ordered or not service_url:
            payload = {
                "titles": titles,
                "matches": [{"name": x.law_name, "id": x.law_id} for x in matches_ordered],
                "service_skipped": not bool(service_url),
            }
            db.add(
                LegalSnapshot(
                    topic_session_id=topic_session_id,
                    query=user_query,
                    response_json=json.dumps(payload, ensure_ascii=False),
                )
            )
            sum_line = "법령 매칭만 수행" if matches_ordered else "매칭된 법령 없음"
            if not service_url:
                sum_line += " — lawService URL 없음"
            return (
                LegalFetchResult(
                    text=json.dumps(payload, ensure_ascii=False, indent=2)[:8000],
                    raw_json=json.dumps(payload, ensure_ascii=False),
                    ok=bool(matches_ordered),
                    warning=None if matches_ordered else "법령 본문 API URL이 없습니다.",
                    debug={
                        "mode": "law_routed",
                        "summary": sum_line,
                        "search_steps": search_log,
                        "body_fetches": [],
                        "links": [],
                    },
                ),
                [],
            )

        for m in matches_ordered:
            if time.perf_counter() - t0 > timebox_sec:
                body_fetches.append(
                    {
                        "endpoint": "lawService.do",
                        "phase": "timebox",
                        "note": f"법령 본문 조회 timebox 초과({timebox_sec:.1f}s)로 중단",
                    }
                )
                break
            slab = _label_for_match(m)
            try:
                st, svc_body, svc_data, eff_tgt, svc_req_url = fetch_statute_service_body(
                    client,
                    service_url=service_url,
                    oc=oc.strip(),
                    law_id=m.law_id,
                    response_type=stype,
                    primary=body_primary,
                    fallback=body_fallback,
                    service_extra=service_extra,
                    detail_link=m.detail_link,
                    mst=m.mst,
                    ef_yd=m.ef_yd,
                )
                th = law_service_json_title_hint(svc_data) if isinstance(svc_data, dict) else None
                bf_row: dict[str, Any] = {
                    "endpoint": "lawService.do",
                    "id": m.law_id,
                    "status": st,
                    "type": stype,
                    "target": eff_tgt,
                    "target_label_ko": "현행법령 본문(eflaw)"
                    if eff_tgt == "eflaw"
                    else "법령",
                    "title": th or m.law_name,
                    "request_url": svc_req_url,
                }
                if jo_param:
                    bf_row["JO"] = jo_param
                if (svc_body or "").strip():
                    rl = len(svc_body)
                    bf_row["http_response_preview"] = svc_body[:2500] + ("…" if rl > 2500 else "")
                    bf_row["http_response_len"] = rl
                if st == 200 and svc_data is not None:
                    bf_row.update(law_service_body_stats_for_debug(svc_data))
                elif st == 200 and (svc_body or "").strip():
                    xp = xmlish_response_plain_text(svc_body, max_chars=120_000)
                    if len(xp) >= 80:
                        bf_row["body_plain_len"] = len(xp)
                        bf_row["body_preview"] = xp[:900]
                        bf_row["preview_note"] = "XML/HTML 근사 평문"
                    else:
                        rl = len(svc_body)
                        bf_row["raw_http_body_len"] = rl
                        bf_row["raw_http_body_preview"] = (
                            (svc_body[:2000] + ("…" if rl > 2000 else "")) if rl else ""
                        )
                elif (svc_body or "").strip():
                    rl = len(svc_body)
                    bf_row["raw_http_body_len"] = rl
                    bf_row["raw_http_body_preview"] = (
                        (svc_body[:2000] + ("…" if rl > 2000 else "")) if rl else ""
                    )
                body_fetches.append(bf_row)
                if st == 200 and svc_data is not None:
                    detail_blocks.append(
                        f"=== 근거 법령 본문: {m.law_name} ({slab}, ID={m.law_id}) ===\n"
                        + law_service_data_for_llm(svc_data, max_chars=48000)
                    )
                    ex = extract_relevant_excerpts(svc_data, user_query, max_excerpts=5, max_chars_each=900)
                    if ex:
                        detail_blocks.append(
                            f"=== 질의 관련 발췌: {m.law_name} ===\n" + "\n---\n".join(ex)
                        )
                    used_refs.append(
                        {
                            "title": m.law_name,
                            "label": f"{slab} {m.law_name}".strip(),
                            "url": law_match_portal_url(m),
                            "law_id": m.law_id,
                            "law_mst": m.mst,
                        }
                    )
                elif st == 200 and (svc_body or "").strip():
                    xp = xmlish_response_plain_text(svc_body, max_chars=48000)
                    if len(xp) >= 80:
                        detail_blocks.append(
                            f"=== 근거 법령 본문(XML): {m.law_name} ({slab}, ID={m.law_id}) ===\n" + xp
                        )
                        used_refs.append(
                            {
                                "title": m.law_name,
                                "label": f"{slab} {m.law_name}".strip(),
                                "url": law_match_portal_url(m),
                                "law_id": m.law_id,
                                "law_mst": m.mst,
                            }
                        )
                else:
                    detail_blocks.append(
                        f"=== lawService {m.law_name} ID={m.law_id} HTTP {st} ===\n{(svc_body or '')[:2500]}"
                    )
            except Exception as e:
                err_row: dict[str, Any] = {
                    "endpoint": "lawService.do",
                    "id": m.law_id,
                    "error": str(e)[:400],
                    "target": "law",
                }
                if jo_param:
                    err_row["JO"] = jo_param
                body_fetches.append(err_row)

    full_text = (
        "【이번 답변에 사용할 법령 본문(제목 기준 조회)】\n"
        + (("\n\n".join(detail_blocks)) if detail_blocks else "(본문 없음)")
    )[:60000]

    links = [{"label": r["label"], "url": r["url"]} for r in used_refs]
    payload = {
        "mode": "law_routed",
        "user_query": user_query[:500],
        "input_titles": titles,
        "search_steps": search_log,
        "body_fetches": body_fetches,
        "used_law_ids": [r["law_id"] for r in used_refs],
        "timebox_sec": timebox_sec,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    if jo_param:
        payload["JO"] = jo_param
    db.add(
        LegalSnapshot(
            topic_session_id=topic_session_id,
            query=user_query,
            response_json=json.dumps(payload, ensure_ascii=False),
        )
    )

    svc_ok = sum(1 for b in body_fetches if b.get("status") == 200)
    sum_line = f"제목기반: 제안 {len(titles)}건 → 매칭 {len(matches_ordered)}건 → 본문 {svc_ok}/{len(matches_ordered)}건"

    return (
        LegalFetchResult(
            text=full_text,
            raw_json=json.dumps(payload, ensure_ascii=False),
            ok=bool(used_refs),
            warning=None if used_refs else "매칭·본문 조회 결과가 비어 있습니다.",
            debug={
                "requested": True,
                "mode": "law_routed",
                "summary": sum_line,
                "search": {"called": True, "ok": True, "http_status": 200, "url": search_url},
                "service": {
                    "attempted": len(body_fetches),
                    "ok": svc_ok,
                    "ids": [r["law_id"] for r in used_refs],
                    "endpoint": service_url,
                },
                "search_steps": search_log,
                "body_fetches": body_fetches,
                "links": links,
            },
        ),
        used_refs,
    )


def build_appendix_for_used_refs(refs: list[dict[str, str]]) -> str:
    """답변 본문 하단 텍스트 블록(링크는 legal_debug.links)."""
    if not refs:
        return ""
    lines = ["", "📘 참고 법령 (이번 답변 근거)"]
    for r in refs:
        lines.append(f"- {r['label']}")
    return "\n".join(lines)
