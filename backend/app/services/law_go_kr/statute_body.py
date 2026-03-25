"""
법령 본문: lawSearch 응답의 법령상세링크(MST·efYd) 우선, 조문(JO)은 target=eflaw+MST+efYd+type=XML,
그 외 기존처럼 target=eflaw(ID) → law(ID) 폴백.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.law_go_kr.client import law_service_get, law_service_request
from app.services.law_go_kr.parse import (
    law_go_kr_json_looks_like_error,
    law_service_json_body_plain,
    parse_detail_link_query_params,
    xmlish_response_plain_text,
)


def _plain_len(data: Any | None, raw_body: str) -> int:
    if data is not None:
        return len(law_service_json_body_plain(data, max_chars=400_000))
    return len(xmlish_response_plain_text(raw_body, max_chars=400_000))


def _ok_body(
    *,
    st: int,
    data: Any | None,
    raw_body: str,
    min_plain_chars: int,
) -> bool:
    if st != 200:
        return False
    if data is not None and law_go_kr_json_looks_like_error(data):
        return False
    return _plain_len(data, raw_body) >= min_plain_chars


def fetch_statute_service_body(
    client: httpx.Client,
    *,
    service_url: str,
    oc: str,
    law_id: str,
    response_type: str,
    primary: str = "eflaw",
    fallback: str = "law",
    min_plain_chars: int = 200,
    service_extra: dict[str, str] | None = None,
    detail_link: str | None = None,
    mst: str | None = None,
    ef_yd: str | None = None,
) -> tuple[int, str, Any | None, str, str]:
    """
    반환: (http_status, raw_body, parsed_json_or_none, effective_target, request_url).

    - 법령상세링크가 있으면 그 쿼리를 기준으로 본문 조회( type 은 response_type 으로 덮어씀 ).
    - JO 가 있으면 가이드에 맞춰 target=eflaw, MST, efYd, JO — 먼저 type=XML, 필요 시 JSON 재시도.
    """
    primary = (primary or "eflaw").strip() or "eflaw"
    fallback = (fallback or "law").strip() or "law"
    su = service_url.rstrip("/")
    stype = (response_type or "JSON").strip().upper() or "JSON"
    oc_s = oc.strip()
    extra = dict(service_extra) if service_extra else {}
    jo = (extra.get("JO") or "").strip() or None

    def _from_link_full_body() -> tuple[int, str, Any | None, str, str] | None:
        if not (detail_link or "").strip():
            return None
        from_link = parse_detail_link_query_params(detail_link)
        if not from_link:
            return None
        params = dict(from_link)
        params["OC"] = oc_s
        params["type"] = stype
        params.pop("JO", None)
        if mst and "MST" not in params:
            params["MST"] = str(mst).strip()
        if ef_yd and "efYd" not in params:
            params["efYd"] = str(ef_yd).strip()
        st, body, data, url = law_service_get(client, service_url=su, params=params)
        tgt = str(params.get("target") or "law").strip() or "law"
        if _ok_body(st=st, data=data, raw_body=body, min_plain_chars=min_plain_chars):
            return st, body, data, tgt, url
        return None

    def _eflaw_jo_calls() -> tuple[int, str, Any | None, str, str] | None:
        if not jo:
            return None
        from_link = parse_detail_link_query_params(detail_link) if detail_link else {}
        mst_v = (from_link.get("MST") or mst or law_id or "").strip()
        ef_v = (from_link.get("efYd") or ef_yd or "").strip()
        if not mst_v or not ef_v:
            return None
        base_p = {
            "OC": oc_s,
            "target": "eflaw",
            "MST": mst_v,
            "efYd": ef_v,
            "JO": jo,
        }
        try_types: list[str] = []
        seen_t: set[str] = set()
        for t_try in ("XML", stype, "JSON"):
            if t_try not in seen_t:
                try_types.append(t_try)
                seen_t.add(t_try)
        for t_try in try_types:
            p = {**base_p, "type": t_try}
            st, body, data, url = law_service_get(client, service_url=su, params=p)
            if _ok_body(st=st, data=data, raw_body=body, min_plain_chars=min_plain_chars):
                return st, body, data, "eflaw", url
        return None

    # 1) 조문: eflaw + MST + efYd + JO (가이드 형식). 실패 시 아래로 진행.
    hit = _eflaw_jo_calls()
    if hit is not None:
        return hit

    # 2) 전체 본문: 검색 결과 법령상세링크(MST·efYd·target=law 등)
    hit = _from_link_full_body()
    if hit is not None:
        return hit

    # 3) 기존: ID 기반 eflaw → law (JO·기타 extra 유지)
    def _call_id(target: str) -> tuple[int, str, Any | None, str]:
        st, body, data, url = law_service_request(
            client,
            service_url=su,
            oc=oc_s,
            target=target,
            law_id=law_id,
            response_type=stype,
            extra=extra if extra else None,
        )
        return st, body, data, url

    if primary == fallback:
        st, body, data, url = _call_id(primary)
        return st, body, data, primary, url

    st, body, data, url = _call_id(primary)
    if _ok_body(st=st, data=data, raw_body=body, min_plain_chars=min_plain_chars):
        return st, body, data, primary, url

    st2, body2, data2, url2 = _call_id(fallback)
    return st2, body2, data2, fallback, url2
