"""
법제처 DRF HTTP 클라이언트.

lawSearch.do / lawService.do 에 OC·target·type 및 가이드의 모든 선택 파라미터를 그대로 전달할 수 있다.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.services.law_go_kr.constants import DEFAULT_HEADERS


def law_search_request(
    client: httpx.Client,
    *,
    base_url: str,
    oc: str,
    target: str,
    extra: dict[str, str] | None = None,
    query: str | None = None,
    response_type: str = "JSON",
) -> tuple[int, str, Any | None, str]:
    """
    lawSearch.do 호출. extra에 display, page, sort, nw, search, org, knd, efYd 등 가이드 변수를 넣는다.
    query가 있으면 params에 query= 로 포함. 반환 마지막은 실제 요청 URL(브라우저·curl 재현용).
    """
    params: dict[str, str] = {
        "OC": oc.strip(),
        "target": target.strip(),
        "type": (response_type or "JSON").strip().upper() or "JSON",
    }
    if query is not None:
        params["query"] = query.strip()
    if extra:
        for k, v in extra.items():
            if v is not None and str(v).strip() != "":
                params[k] = str(v).strip()
    r = client.get(base_url.rstrip("/"), params=params)
    body = r.text
    request_url = str(r.url)
    data: Any = None
    ctype = (r.headers.get("content-type") or "").lower()
    if params["type"] == "JSON" or "json" in ctype or body.strip().startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
    return r.status_code, body, data, request_url


def law_service_request(
    client: httpx.Client,
    *,
    service_url: str,
    oc: str,
    target: str,
    law_id: str,
    response_type: str = "JSON",
    extra: dict[str, str] | None = None,
) -> tuple[int, str, Any | None, str]:
    """lawService.do — target=law|admrul|ordin 등, ID 파라미터는 API 명세상 보통 ID. 반환 마지막은 실제 요청 URL."""
    params: dict[str, str] = {
        "OC": oc.strip(),
        "target": target.strip(),
        "type": (response_type or "JSON").strip().upper() or "JSON",
        "ID": law_id.strip(),
    }
    if extra:
        for k, v in extra.items():
            if v is not None and str(v).strip() != "":
                params[k] = str(v).strip()
    r = client.get(service_url.rstrip("/"), params=params)
    body = r.text
    request_url = str(r.url)
    data: Any = None
    t = (response_type or "").strip().upper()
    ctype = (r.headers.get("content-type") or "").lower()
    if t == "JSON" or "json" in ctype or body.strip().startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
    return r.status_code, body, data, request_url


def law_service_get(
    client: httpx.Client,
    *,
    service_url: str,
    params: dict[str, str],
) -> tuple[int, str, Any | None, str]:
    """
    lawService.do 임의 쿼리(MST·efYd·target=eflaw 등). 빈 값 키는 전송하지 않음.
    반환 마지막은 실제 요청 URL.
    """
    clean: dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        s = str(v).strip()
        if s == "":
            continue
        clean[str(k).strip()] = s
    r = client.get(service_url.rstrip("/"), params=clean)
    body = r.text
    request_url = str(r.url)
    data: Any = None
    t = (clean.get("type") or "").strip().upper()
    ctype = (r.headers.get("content-type") or "").lower()
    if t == "JSON" or "json" in ctype or body.strip().startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
    return r.status_code, body, data, request_url


def default_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True)
