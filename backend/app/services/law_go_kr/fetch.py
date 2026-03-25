"""
국가법령정보 OPEN API 통합 조회.

1) 질의에서 법령·규칙·조례 키워드 후보 추출( parse.law_search_query_variants + candidates )
2) lawSearch.do: aiSearch/law + 현행법령(eflaw) + 행정규칙(admrul) + (선택) 자치법규(ordin)
3) lawService.do 본문 + 질의 토큰 기반 발췌(relevance)
4) 링크: parse.extract_law_link_entries
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.db.models import LegalSnapshot
from app.services.law_go_kr.candidates import wants_administrative_rule_search, wants_ordinance_search
from app.services.law_go_kr.client import law_search_request, law_service_request
from app.services.law_go_kr.jo_param import parse_law_service_jo_from_query
from app.services.law_go_kr.statute_body import fetch_statute_service_body
from app.services.law_go_kr.constants import DEFAULT_HEADERS
from app.services.law_go_kr.parse import (
    extract_law_ids_from_many,
    extract_law_link_entries,
    find_law_hit_service_context,
    law_search_query_variants,
    law_service_body_stats_for_debug,
    law_service_data_for_llm,
    law_service_json_title_hint,
    response_to_llm_text,
    search_json_hit_titles,
    search_json_total_count_hint,
    xmlish_response_plain_text,
)
from app.services.law_go_kr.relevance import extract_relevant_excerpts
from app.services.law_go_kr.types import LegalFetchResult

# lawService.do target (검색 API target 과 동일한 경우가 많음)
_LAW_SERVICE_LAW = "law"
_LAW_SERVICE_ADMRUL = "admrul"
_LAW_SERVICE_ORDIN = "ordin"


def _roots_from_entries(entries: list[tuple[Any, str]]) -> list[Any]:
    return [e[0] for e in entries if e[0] is not None]


def _collect_tagged_service_ids(entries: list[tuple[Any, str]], *, limit: int) -> list[tuple[str, str]]:
    """검색 응답별로 추출한 ID에 lawService target 을 붙인다. 법령 ID를 행정규칙/자치법규보다 우선."""
    seen: set[str] = set()
    law_pairs: list[tuple[str, str]] = []
    other_pairs: list[tuple[str, str]] = []
    per_cap = max(limit * 4, 8)
    for root, svc_tgt in entries:
        if root is None:
            continue
        ids = extract_law_ids_from_many([root], per_cap)
        for lid in ids:
            if lid in seen:
                continue
            seen.add(lid)
            if svc_tgt == _LAW_SERVICE_LAW:
                law_pairs.append((lid, _LAW_SERVICE_LAW))
            else:
                other_pairs.append((lid, svc_tgt))
    ordered = law_pairs + other_pairs
    return ordered[:limit]


def _service_label(svc_target: str) -> str:
    return {"law": "법령", "admrul": "행정규칙", "ordin": "자치법규"}.get(svc_target, svc_target)


def _snapshot(db: Session, *, topic_session_id: str | None, query: str, payload: dict) -> None:
    db.add(
        LegalSnapshot(
            topic_session_id=topic_session_id,
            query=query,
            response_json=json.dumps(payload, ensure_ascii=False),
        )
    )


def run_law_go_kr_fetch(
    db: Session,
    *,
    topic_session_id: str | None,
    query: str,
    oc: str,
    search_url: str,
    target_primary: str,
    target_fallback: str,
    timeout: float,
    service_url: str,
    service_type: str,
    service_max_ids: int,
    service_fetch: bool,
    extended_sources: bool = True,
    statute_body_target: str = "eflaw",
    statute_body_fallback: str = "law",
) -> LegalFetchResult:
    q = (query or "").strip()
    if not q:
        payload: dict[str, Any] = {"source": "law.go.kr", "error": "empty_query"}
        raw = json.dumps(payload, ensure_ascii=False)
        _snapshot(db, topic_session_id=topic_session_id, query=query, payload=payload)
        return LegalFetchResult(
            text="검색어가 비어 있습니다.",
            raw_json=raw,
            ok=False,
            warning="법령 검색어가 없습니다.",
            debug={
                "requested": True,
                "mode": "law.go.kr",
                "summary": "법제처 API: 검색어 없음 — lawSearch.do 호출 안 함",
                "search": {"called": False, "ok": False, "http_status": None, "url": search_url.rstrip("/")},
                "service": {"attempted": 0, "ok": 0, "ids": []},
                "links": [],
            },
        )

    attempts: list[dict[str, Any]] = []
    service_attempts: list[dict[str, Any]] = []
    supplementary_meta: list[dict[str, Any]] = []
    extended_meta: list[dict[str, Any]] = []
    tagged_ids: list[tuple[str, str]] = []
    law_ids: list[str] = []
    scan_entries: list[tuple[Any, str]] = []
    scan_roots: list[Any] = []
    search_log: list[dict[str, Any]] = []

    def one_call(
        client: httpx.Client,
        base_url: str,
        target: str,
        extra: dict[str, str],
        query_override: str | None = None,
    ) -> tuple[int, str, Any | None]:
        q_use = (query_override if query_override is not None else q).strip()
        params_flat = {k: str(v) for k, v in extra.items()}
        if not q_use:
            attempts.append({"target": target, "error": "empty_query", "params_extra": extra})
            search_log.append(
                {
                    "endpoint": "lawSearch.do",
                    "target": target,
                    "error": "empty_query",
                    "params": params_flat,
                }
            )
            return -1, "", None
        try:
            st, body, data, search_req_url = law_search_request(
                client,
                base_url=base_url,
                oc=oc,
                target=target,
                extra={**extra, "query": q_use},
                query=None,
                response_type="JSON",
            )
            attempts.append(
                {
                    "target": target,
                    "query": q_use[:200],
                    "status": st,
                    "params_extra": extra,
                    "request_url": search_req_url,
                }
            )
            log_row: dict[str, Any] = {
                "endpoint": "lawSearch.do",
                "target": target,
                "query": q_use[:200],
                "http_status": st,
                "request_url": search_req_url,
                "params": {**params_flat, "query": q_use[:200]},
            }
            if isinstance(data, dict):
                tc = search_json_total_count_hint(data)
                if tc is not None:
                    log_row["total_count"] = tc
                hits = search_json_hit_titles(data, limit=8)
                if hits:
                    log_row["hit_titles"] = hits
            elif st == 200 and data is None:
                log_row["note"] = "json_parse_fail"
            if (body or "").strip():
                bl = len(body)
                log_row["http_response_preview"] = body[:2500] + ("…" if bl > 2500 else "")
                log_row["http_response_len"] = bl
            search_log.append(log_row)
            return st, body, data
        except Exception as e:
            attempts.append({"target": target, "query": q_use[:200], "error": str(e), "params_extra": extra})
            search_log.append(
                {
                    "endpoint": "lawSearch.do",
                    "target": target,
                    "query": q_use[:200],
                    "http_status": None,
                    "error": str(e)[:400],
                    "params": {**params_flat, "query": q_use[:200]},
                }
            )
            return -1, str(e), None

    status, body, data = -1, "", None
    base_search = search_url.rstrip("/")
    _MAX_SUPP = 22

    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        status, body, data = one_call(
            client, base_search, target_primary, {"search": "0", "display": "20", "page": "1"}
        )
        if status != 200 or data is None:
            status2, body2, data2 = one_call(
                client, base_search, target_fallback, {"search": "2", "display": "20", "page": "1"}
            )
            if status2 == 200 and data2 is not None:
                status, body, data = status2, body2, data2
            elif data is None and data2 is not None:
                status, body, data = status2, body2, data2

        scan_entries = []
        if data is not None:
            scan_entries.append((data, _LAW_SERVICE_LAW))

        need_id = max(1, service_max_ids)

        def append_extended() -> None:
            nonlocal scan_entries
            if not extended_sources or len(attempts) >= _MAX_SUPP:
                return
            variants = law_search_query_variants(q, max_variants=3)
            q_short = variants[0] if variants else q[:80]

            for label, target, extra in (
                ("eflaw_name", "eflaw", {"nw": "3", "search": "1", "display": "12", "page": "1"}),
                ("eflaw_body", "eflaw", {"nw": "3", "search": "2", "display": "8", "page": "1"}),
            ):
                if len(attempts) >= _MAX_SUPP:
                    break
                st_e, _, dt_e = one_call(client, base_search, target, extra, q_short)
                extended_meta.append({"phase": label, "target": target, "status": st_e, "query": q_short[:100]})
                if st_e == 200 and dt_e is not None:
                    scan_entries.append((dt_e, _LAW_SERVICE_LAW))

            if (wants_administrative_rule_search(q) or extended_sources) and len(attempts) < _MAX_SUPP:
                st_a, _, dt_a = one_call(
                    client,
                    base_search,
                    "admrul",
                    {"mobileYn": "Y", "display": "10", "page": "1"},
                    q_short[:100],
                )
                extended_meta.append(
                    {"phase": "admrul", "target": "admrul", "status": st_a, "query": q_short[:100]}
                )
                if st_a == 200 and dt_a is not None:
                    scan_entries.append((dt_a, _LAW_SERVICE_ADMRUL))

            if wants_ordinance_search(q) and len(attempts) < _MAX_SUPP:
                st_o, _, dt_o = one_call(
                    client,
                    base_search,
                    "ordin",
                    {"mobileYn": "Y", "display": "8", "page": "1"},
                    q_short[:100],
                )
                extended_meta.append({"phase": "ordin", "target": "ordin", "status": st_o, "query": q_short[:100]})
                if st_o == 200 and dt_o is not None:
                    scan_entries.append((dt_o, _LAW_SERVICE_ORDIN))

        append_extended()

        scan_roots = _roots_from_entries(scan_entries)

        if status == 200 and not extract_law_ids_from_many(scan_roots, need_id):
            for nq in law_search_query_variants(q, max_variants=6):
                broke = False
                for sch in ("1", "0", "2", "3"):
                    if len(attempts) >= _MAX_SUPP:
                        break
                    st_b, _bd_b, dt = one_call(
                        client,
                        base_search,
                        "law",
                        {"search": sch, "display": "15", "page": "1"},
                        nq,
                    )
                    supplementary_meta.append(
                        {
                            "phase": "law_name_match",
                            "query": nq[:120],
                            "target": "law",
                            "search": sch,
                            "status": st_b,
                            "json": dt is not None,
                        }
                    )
                    if st_b == 200 and dt is not None:
                        scan_entries.append((dt, _LAW_SERVICE_LAW))
                        scan_roots = _roots_from_entries(scan_entries)
                        if extract_law_ids_from_many(scan_roots, need_id):
                            broke = True
                            break
                if broke:
                    break
            if not extract_law_ids_from_many(scan_roots, need_id):
                for nq in law_search_query_variants(q, max_variants=4):
                    if len(attempts) >= _MAX_SUPP:
                        break
                    st_b, _bd_b, dt = one_call(
                        client,
                        base_search,
                        target_primary,
                        {"search": "0", "display": "15", "page": "1"},
                        nq,
                    )
                    supplementary_meta.append(
                        {
                            "phase": "ai_short_query",
                            "query": nq[:120],
                            "target": target_primary,
                            "search": "0",
                            "status": st_b,
                            "json": dt is not None,
                        }
                    )
                    if st_b == 200 and dt is not None:
                        scan_entries.append((dt, _LAW_SERVICE_LAW))
                        scan_roots = _roots_from_entries(scan_entries)
                        if extract_law_ids_from_many(scan_roots, need_id):
                            break

        tagged_ids = _collect_tagged_service_ids(scan_entries, limit=service_max_ids) if scan_entries else []
        law_ids = [p[0] for p in tagged_ids]

        detail_blocks: list[str] = []
        if (
            service_fetch
            and service_max_ids > 0
            and (service_url or "").strip()
            and status == 200
            and tagged_ids
        ):
            stype = (service_type or "JSON").strip().upper() or "JSON"
            su = (service_url or "").strip().rstrip("/")
            jo_q = parse_law_service_jo_from_query(q)
            fetch_extra: dict[str, str] | None = {"JO": jo_q} if jo_q else None
            for lid, svc_target in tagged_ids:
                try:
                    svc_req_url = ""
                    if svc_target == _LAW_SERVICE_LAW:
                        hit_ctx = find_law_hit_service_context(scan_roots, lid)
                        st, svc_body, svc_data, eff_tgt, svc_req_url = fetch_statute_service_body(
                            client,
                            service_url=su,
                            oc=oc,
                            law_id=lid,
                            response_type=stype,
                            primary=statute_body_target,
                            fallback=statute_body_fallback,
                            service_extra=fetch_extra,
                            detail_link=hit_ctx.get("detail_link"),
                            mst=hit_ctx.get("mst"),
                            ef_yd=hit_ctx.get("ef_yd"),
                        )
                    else:
                        st, svc_body, svc_data, svc_req_url = law_service_request(
                            client,
                            service_url=su,
                            oc=oc,
                            target=svc_target,
                            law_id=lid,
                            response_type=stype,
                        )
                        eff_tgt = svc_target
                    slab = _service_label(svc_target)
                    svc_row: dict[str, Any] = {
                        "endpoint": "lawService.do",
                        "id": lid,
                        "status": st,
                        "type": stype,
                        "target": eff_tgt,
                        "target_label_ko": slab if eff_tgt != "eflaw" else "현행법령 본문(eflaw)",
                        "request_url": svc_req_url,
                    }
                    if jo_q:
                        svc_row["JO"] = jo_q
                    if st == 200 and svc_data is not None:
                        th = law_service_json_title_hint(svc_data)
                        if th:
                            svc_row["title"] = th
                        svc_row.update(law_service_body_stats_for_debug(svc_data))
                    elif st == 200 and (svc_body or "").strip():
                        xp = xmlish_response_plain_text(svc_body, max_chars=120_000)
                        if len(xp) >= 80:
                            svc_row["body_plain_len"] = len(xp)
                            svc_row["body_preview"] = xp[:900]
                            svc_row["preview_note"] = "XML/HTML 근사 평문"
                    elif (svc_body or "").strip():
                        rl = len(svc_body)
                        svc_row["raw_http_body_len"] = rl
                        svc_row["raw_http_body_preview"] = svc_body[:2000] + ("…" if rl > 2000 else "")
                    if (svc_body or "").strip():
                        rl = len(svc_body)
                        svc_row["http_response_preview"] = svc_body[:2500] + ("…" if rl > 2500 else "")
                        svc_row["http_response_len"] = rl
                    service_attempts.append(svc_row)
                    if st == 200:
                        if svc_data is not None:
                            detail_blocks.append(
                                f"=== lawService.do 본문 ({slab}, target={eff_tgt}, ID={lid}, type={stype}) ===\n"
                                + law_service_data_for_llm(svc_data, max_chars=10000)
                            )
                            ex = extract_relevant_excerpts(svc_data, q, max_excerpts=4, max_chars_each=850)
                            if ex:
                                detail_blocks.append(
                                    f"=== 질의 관련 본문 발췌 ({slab} ID={lid}) ===\n" + "\n---\n".join(ex)
                                )
                        elif (svc_body or "").strip():
                            xp = xmlish_response_plain_text(svc_body, max_chars=10000)
                            detail_blocks.append(
                                f"=== lawService.do ({slab} ID={lid}, type={stype}, XML·HTML 근사 평문) ===\n"
                                + (xp if len(xp) >= 40 else (svc_body or "")[:10000])
                            )
                        else:
                            detail_blocks.append(
                                f"=== lawService.do ({slab} ID={lid}, type={stype}, 비JSON 원문 일부) ===\n"
                                + (svc_body or "")[:10000]
                            )
                    else:
                        detail_blocks.append(
                            f"=== lawService.do {slab} ID={lid} HTTP {st} ===\n{(svc_body or '')[:2000]}"
                        )
                except Exception as e:
                    service_attempts.append(
                        {
                            "endpoint": "lawService.do",
                            "id": lid,
                            "error": str(e)[:400],
                            "target": svc_target,
                            "target_label_ko": _service_label(svc_target),
                        }
                    )
                    detail_blocks.append(f"=== lawService.do ID={lid} 오류 ===\n{str(e)[:500]}")

    payload = {
        "source": "law.go.kr",
        "search_endpoint": base_search,
        "service_endpoint": (service_url or "").strip() or None,
        "law_ids_used": law_ids,
        "law_service_plan": [{"id": a, "target": b} for a, b in tagged_ids],
        "attempts": attempts,
        "service_attempts": service_attempts,
        "http_status": status,
        "supplementary_searches": supplementary_meta,
        "extended_searches": extended_meta,
        "scan_root_count": len(scan_entries),
        "openapi_guide": "https://open.law.go.kr/LSO/openApi/guideList.do",
    }

    if status != 200:
        payload["error"] = "http_error"
        payload["body_preview"] = (body or "")[:4000]
        raw = json.dumps(payload, ensure_ascii=False)
        _snapshot(db, topic_session_id=topic_session_id, query=query, payload=payload)
        links = extract_law_link_entries(scan_roots if scan_roots else None, [], q, limit=6)
        return LegalFetchResult(
            text=response_to_llm_text(payload),
            raw_json=raw,
            ok=False,
            warning=f"법령 검색 API HTTP 오류 (status={status}).",
            debug={
                "requested": True,
                "mode": "law.go.kr",
                "summary": f"법제처 lawSearch.do 실패 (HTTP {status}) — 실제 API 미응답",
                "search": {"called": True, "ok": False, "http_status": status, "url": base_search},
                "search_steps": search_log,
                "body_fetches": service_attempts,
                "service": {
                    "attempted": len(service_attempts),
                    "ok": sum(1 for a in service_attempts if a.get("status") == 200),
                    "ids": list(law_ids),
                },
                "links": links,
            },
        )

    if data is None and not scan_roots:
        payload["error"] = "not_json"
        payload["body_preview"] = (body or "")[:8000]
        raw = json.dumps(payload, ensure_ascii=False)
        _snapshot(db, topic_session_id=topic_session_id, query=query, payload=payload)
        links = extract_law_link_entries(None, law_ids, q, limit=8)
        return LegalFetchResult(
            text=(body or "")[:12000],
            raw_json=raw,
            ok=True,
            warning="검색 응답 JSON 파싱 실패 — 원문 일부만 반영(보조 검색도 없음).",
            debug={
                "requested": True,
                "mode": "law.go.kr",
                "summary": "법제처 lawSearch.do(HTTP 200) — JSON 없음·보조 검색 실패, 원문만 반영",
                "search": {"called": True, "ok": True, "http_status": status, "url": base_search, "json_ok": False},
                "service": {
                    "attempted": len(service_attempts),
                    "ok": sum(1 for a in service_attempts if a.get("status") == 200),
                    "ids": list(law_ids),
                },
                "name_match_attempts": len(supplementary_meta),
                "search_steps": search_log,
                "body_fetches": service_attempts,
                "links": links,
            },
        )

    if data is None and scan_roots:
        data = scan_roots[-1]

    payload["data"] = data
    raw = json.dumps(payload, ensure_ascii=False)
    _snapshot(db, topic_session_id=topic_session_id, query=query, payload=payload)

    err_hint = None
    if isinstance(data, dict):
        for key in ("resultMsg", "errMsg", "message", "RESULT_MSG"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                err_hint = v.strip()
                break

    search_text = response_to_llm_text(data) if data is not None else ""
    if supplementary_meta and len(scan_roots) > 1:
        search_text += "\n\n【보조 검색 JSON (법제처 목록·법령명·약칭 매칭)】\n"
        search_text += response_to_llm_text(scan_roots[-1], max_chars=9000)
    if extended_meta:
        search_text += "\n\n【다중 소스 검색(현행법령·행정규칙·조례 등)】\n"
        search_text += json.dumps(extended_meta, ensure_ascii=False, indent=2)[:2500]

    full_text = search_text
    if detail_blocks:
        full_text = "【법령 검색·본문·발췌】\n" + search_text + "\n\n" + "\n\n".join(detail_blocks)

    ok = True
    warning = None
    if err_hint and any(x in err_hint for x in ("오류", "실패", "error", "Error", "인증", "OC")):
        ok = False
        warning = f"법령 API 응답 메시지: {err_hint[:500]}"
    elif detail_blocks and law_ids:
        warning = "다중 검색 + lawService 본문 + 질의 발췌를 반영했습니다."

    svc_ok = sum(1 for a in service_attempts if a.get("status") == 200)
    links = extract_law_link_entries(scan_roots, law_ids, q, limit=12)
    ext_h = f" 확장검색 {len(extended_meta)}건." if extended_meta else ""
    if service_fetch and (service_url or "").strip() and law_ids:
        if supplementary_meta or extended_meta:
            sum_line = (
                f"법제처 API:{ext_h} 보조 {len(supplementary_meta)}회 — "
                f"ID {len(law_ids)}개 → lawService {svc_ok}/{len(law_ids)}건"
            )
        else:
            sum_line = f"lawSearch.do 성공(HTTP {status}) + lawService.do 본문 {svc_ok}/{len(law_ids)}건 성공"
    elif service_fetch and (service_url or "").strip() and not law_ids:
        sup_h = f" 보조 {len(supplementary_meta)}회." if supplementary_meta else ""
        sum_line = f"lawSearch.do 성공(HTTP {status}) —{ext_h}{sup_h} 법령 ID 추출 실패, 본문 API 미호출"
    else:
        sum_line = f"lawSearch.do 성공(HTTP {status}) — 본문 조회 비활성 또는 URL 없음"
    if err_hint and not ok:
        sum_line = f"법제처 API 응답 경고: {err_hint[:120]}"

    return LegalFetchResult(
        text=full_text[:20000],
        raw_json=raw,
        ok=ok,
        warning=warning,
        debug={
            "requested": True,
            "mode": "law.go.kr",
            "summary": sum_line,
            "search": {
                "called": True,
                "ok": status == 200 and data is not None,
                "http_status": status,
                "url": base_search,
                "json_ok": True,
            },
            "service": {
                "attempted": len(service_attempts),
                "ok": svc_ok,
                "ids": list(law_ids),
                "endpoint": (service_url or "").strip() or None,
            },
            "name_match_attempts": len(supplementary_meta),
            "extended_searches": extended_meta,
            "search_steps": search_log,
            "body_fetches": service_attempts,
            "links": links,
        },
    )
