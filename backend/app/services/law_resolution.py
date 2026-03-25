"""
LLM 답변 기반 법령명 정규화, lawSearch.do 단일 매칭, 시행령·시행규칙 연동, 하단 '관련 법령' 블록 생성.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.services.legal_adapter import (
    DEFAULT_HEADERS,
    DEFAULT_LAW_SEARCH_URL,
    _is_plausible_law_id_scalar,
    _key_looks_like_law_id_field,
)

# ---------------------------------------------------------------------------
# 별칭 → 정식 법령명 (확장: 동일 dict에 키만 추가)
# ---------------------------------------------------------------------------
LAW_ALIAS_MAP: dict[str, str] = {
    "국가계약법": "국가를 당사자로 하는 계약에 관한 법률",
    "지방계약법": "지방자치단체를 당사자로 하는 계약에 관한 법률",
}


class LawSearchError(Exception):
    """법제처 검색 API 실패·비JSON 등."""


@dataclass
class LawMatch:
    law_name: str
    law_id: str
    law_type: str  # "법" | "시행령" | "시행규칙" | "기타"
    detail_link: str | None = None  # lawSearch 응답 법령상세링크(상대·절대)
    mst: str | None = None  # 법령일련번호 — lawService MST
    ef_yd: str | None = None  # 시행일자(YYYYMMDD) — lawService efYd


def _norm_compact(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip())


# --- [1] LLM 텍스트에서 법령명 후보 추출 -------------------------------------


def extract_law_names(text: str) -> list[str]:
    """
    LLM 응답 등에서 법령명·약칭 후보를 순서 유지·중복 제거로 추출.
    """
    if not (text or "").strip():
        return []
    seen: set[str] = set()
    out: list[str] = []

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) < 3 or s in seen:
            return
        seen.add(s)
        out.append(s)

    for pat in (
        r"([가-힣0-9·\s]{2,85}?(?:법률|시행령|시행규칙))",
        r"([가-힣0-9·]{2,55}법)(?!\s*원)",
    ):
        for m in re.finditer(pat, text):
            add(m.group(1))

    for alias in sorted(LAW_ALIAS_MAP.keys(), key=len, reverse=True):
        if alias in text:
            add(alias)

    return out


# --- [1] 별칭 → 정식 명칭 ---------------------------------------------------


def normalize_law_name(name: str) -> str:
    """별칭 매핑 테이블을 적용한 정식 법령명(또는 원문)."""
    t = (name or "").strip()
    if not t:
        return t
    if t in LAW_ALIAS_MAP:
        return LAW_ALIAS_MAP[t]
    compact = re.sub(r"\s+", "", t)
    for k, v in LAW_ALIAS_MAP.items():
        if re.sub(r"\s+", "", k) == compact:
            return v
    return t


def classify_law_type(name: str) -> str:
    """표시·필터용: 법 / 시행령 / 시행규칙 / 기타."""
    n = (name or "").strip()
    if "시행규칙" in n:
        return "시행규칙"
    if "시행령" in n:
        return "시행령"
    if "법률" in n or (n.endswith("법") and "시행" not in n and "규칙" not in n):
        return "법"
    return "기타"


def _law_display_name(d: dict[str, Any]) -> str:
    return str(
        d.get("법령명한글")
        or d.get("법령명")
        or d.get("lawNm")
        or d.get("법령명_한글")
        or d.get("법령명약칭")
        or d.get("행정규칙명")
        or d.get("admRulNm")
        or ""
    ).strip()


def _law_id_from_dict(d: dict[str, Any]) -> str:
    """포털·중복 제거용: 법령ID(lsiSeq 계열) 우선, 없으면 일련번호(MST)."""
    for k in (
        "법령ID",
        "lsiSeq",
        "lsi_seq",
        "lawId",
        "LAW_ID",
        "법령일련번호",
        "MST",
        "법령MST",
        "admRulSeq",
        "admRulId",
        "자치법규일련번호",
        "행정규칙ID",
    ):
        if k not in d:
            continue
        v = d.get(k)
        if _is_plausible_law_id_scalar(v):
            return str(v).strip()
    for k, v in d.items():
        if _key_looks_like_law_id_field(k) and _is_plausible_law_id_scalar(v):
            return str(v).strip()
    return ""


def _law_match_from_hit_dict(d: dict[str, Any]) -> LawMatch | None:
    name = _law_display_name(d)
    lid = _law_id_from_dict(d)
    if not name or not lid:
        return None
    dl = d.get("법령상세링크")
    dl_s = str(dl).strip() if dl is not None and str(dl).strip() else None
    mst_v = d.get("법령일련번호")
    mst_s = str(mst_v).strip() if mst_v is not None and str(mst_v).strip() else None
    ef = d.get("시행일자")
    ef_s = str(ef).strip() if ef is not None and str(ef).strip() else None
    return LawMatch(name, lid, classify_law_type(name), detail_link=dl_s, mst=mst_s, ef_yd=ef_s)


def _iter_law_hit_dicts(node: Any) -> Any:
    if isinstance(node, dict):
        m = _law_match_from_hit_dict(node)
        if m is not None:
            yield m
        for v in node.values():
            yield from _iter_law_hit_dicts(v)
    elif isinstance(node, list):
        for it in node:
            yield from _iter_law_hit_dicts(it)


def collect_law_hits_from_search_json(data: Any) -> list[LawMatch]:
    """lawSearch.do JSON에서 (법령명, ID, 유형) 목록을 중복 제거해 수집.

    aiSearch 응답은 ``법령조문`` 배열 순서가 관련도가 높으므로, 해당 목록을 먼저 넣고
    나머지는 트리 순회로 보충한다.
    """
    seen: set[str] = set()
    out: list[LawMatch] = []
    if isinstance(data, dict):
        ais = data.get("aiSearch")
        if isinstance(ais, dict):
            arts = ais.get("법령조문")
            if isinstance(arts, list):
                for art in arts:
                    if not isinstance(art, dict):
                        continue
                    m = _law_match_from_hit_dict(art)
                    if m is None or m.law_id in seen:
                        continue
                    seen.add(m.law_id)
                    out.append(m)
    for h in _iter_law_hit_dicts(data):
        if h.law_id in seen:
            continue
        seen.add(h.law_id)
        out.append(h)
    return out


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm_compact(a), _norm_compact(b)).ratio()


def pick_best_law(
    hits: list[LawMatch],
    target_name: str,
    *,
    user_context: str | None = None,
) -> LawMatch | None:
    """
    검색 결과 중 1건만 선택.
    1) 정규화된 질의와 법령명이 동일(공백 무시) 우선 — 동률이면 타입 '법' 우선
    2) 전체 후보에서 제안 제목·(있으면) 사용자 질의 대비 유사도 최대 — 낮으면 None

    시행령·시행규칙·부령 등은 aiSearch 상위에 자주 나오므로, '법'만 두고 비교하지 않는다.
    """
    if not hits:
        return None
    want = _norm_compact(normalize_law_name(target_name))
    exact = [h for h in hits if _norm_compact(h.law_name) == want]
    if exact:
        laws = [h for h in exact if h.law_type == "법"]
        return laws[0] if laws else exact[0]

    norm_title = normalize_law_name(target_name)
    uc = (user_context or "").strip()[:400]

    def match_score(h: LawMatch) -> float:
        s = _similarity(h.law_name, norm_title)
        if uc:
            s = max(s, _similarity(h.law_name, uc))
        return s

    best = max(hits, key=match_score)
    if match_score(best) < 0.38:
        return None
    return best


def _law_search_params(
    *,
    oc: str,
    query: str,
    target: str,
    search_mode: str,
) -> dict[str, str]:
    return {
        "OC": oc,
        "target": target,
        "type": "JSON",
        "query": query.strip()[:200],
        "search": search_mode,
        "display": "20",
        "page": "1",
    }


# --- [2] API로 정확한 법령 1건 ----------------------------------------------


def _pick_from_ai_search_hits(
    hits: list[LawMatch],
    query_norm: str,
    *,
    user_context: str | None = None,
) -> LawMatch | None:
    """지능형 검색(aiSearch) 결과: 제목·사용자 질의 유사도 우선, 없으면 API 정렬 첫 건."""
    if not hits:
        return None
    picked = pick_best_law(hits, query_norm, user_context=user_context)
    if picked:
        return picked
    return hits[0]


def search_law_from_api(
    name: str,
    *,
    oc: str,
    search_url: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 22.0,
    search_debug: list[dict[str, Any]] | None = None,
    user_query: str | None = None,
) -> LawMatch | None:
    """
    1) 법령정보지식베이스 지능형 법령검색: lawSearch.do target=aiSearch (search 0·2 우선)
    2) 폴백: 기존 target=law (법령명 문자열 검색)
    """
    if not oc or not (name or "").strip():
        return None
    base = (search_url or DEFAULT_LAW_SEARCH_URL).strip()
    qn = normalize_law_name(name.strip())

    def _preview_text(t: str, n: int = 1500) -> str:
        t = t or ""
        if len(t) <= n:
            return t
        return t[: n - 1].rstrip() + "…"

    def run(c: httpx.Client) -> LawMatch | None:
        last_err: LawSearchError | None = None

        # --- 지능형 법령검색 API (aiSearch): 키워드·자연어 질의에 적합 ---
        for sm in ("0", "2", "1", "3"):
            params = {
                "OC": oc.strip(),
                "target": "aiSearch",
                "type": "JSON",
                "query": qn.strip()[:200],
                "search": sm,
                "display": "20",
                "page": "1",
            }
            step: dict[str, Any] = {
                "phase": "lawSearch.do",
                "target": "aiSearch",
                "search": sm,
                "query": qn[:200],
                "note": "법령정보지식베이스 지능형 법령검색",
            }
            try:
                r = c.get(base.rstrip("/"), params=params)
                step["request_url"] = str(r.url)
                step["http_status"] = r.status_code
                raw = r.text or ""
                step["http_response_len"] = len(raw)
                step["http_response_preview"] = _preview_text(raw, 2500)
                if r.status_code != 200:
                    if search_debug is not None:
                        search_debug.append(step)
                    last_err = LawSearchError(f"HTTP {r.status_code}")
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    step["error"] = "not_json"
                    if search_debug is not None:
                        search_debug.append(step)
                    last_err = LawSearchError("not_json")
                    continue
            except OSError as e:
                step["error"] = str(e)[:300]
                if search_debug is not None:
                    search_debug.append(step)
                last_err = LawSearchError(str(e))
                continue
            hits = collect_law_hits_from_search_json(data)
            step["hit_count"] = len(hits)
            picked = (
                _pick_from_ai_search_hits(hits, qn, user_context=user_query)
                if hits
                else None
            )
            if picked:
                step["picked_law_id"] = picked.law_id
                step["picked_law_name"] = picked.law_name
                if picked.mst:
                    step["picked_law_mst"] = picked.mst
            if search_debug is not None:
                search_debug.append(step)
            if picked:
                return picked

        # --- 폴백: 일반 lawSearch (법령명 검색) ---
        for sm in ("1", "0", "2"):
            params = _law_search_params(oc=oc, query=qn, target="law", search_mode=sm)
            step = {
                "phase": "lawSearch.do",
                "target": "law",
                "search": sm,
                "query": qn[:200],
            }
            try:
                r = c.get(base.rstrip("/"), params=params)
                step["request_url"] = str(r.url)
                step["http_status"] = r.status_code
                raw = r.text or ""
                step["http_response_len"] = len(raw)
                step["http_response_preview"] = _preview_text(raw, 2500)
                if r.status_code != 200:
                    if search_debug is not None:
                        search_debug.append(step)
                    last_err = LawSearchError(f"HTTP {r.status_code}")
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    step["error"] = "not_json"
                    if search_debug is not None:
                        search_debug.append(step)
                    last_err = LawSearchError("not_json")
                    continue
            except OSError as e:
                step["error"] = str(e)[:300]
                if search_debug is not None:
                    search_debug.append(step)
                last_err = LawSearchError(str(e))
                continue
            hits = collect_law_hits_from_search_json(data)
            step["hit_count"] = len(hits)
            picked = pick_best_law(hits, qn, user_context=user_query) if hits else None
            if picked:
                step["picked_law_id"] = picked.law_id
                step["picked_law_name"] = picked.law_name
                if picked.mst:
                    step["picked_law_mst"] = picked.mst
            if search_debug is not None:
                search_debug.append(step)
            if not hits or not picked:
                continue
            return picked
        if last_err:
            return None
        return None

    if client is not None:
        return run(client)
    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as c:
        return run(c)


# --- [3] 시행령·시행규칙 ----------------------------------------------------


def find_related_laws(
    base_law: LawMatch,
    *,
    oc: str,
    search_url: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 22.0,
) -> list[LawMatch]:
    """
    기준 법(본법) 명칭으로 '… 시행령', '… 시행규칙'을 각각 검색해
    존재·타입 일치·명칭 연관성 있는 것만 반환.
    """
    if not oc or base_law.law_type != "법":
        return []
    base = (search_url or DEFAULT_LAW_SEARCH_URL).strip()
    out: list[LawMatch] = []

    def collect(c: httpx.Client) -> None:
        for suffix, want in ((" 시행령", "시행령"), (" 시행규칙", "시행규칙")):
            q = base_law.law_name + suffix
            m = search_law_from_api(q, oc=oc, search_url=base, client=c, timeout=timeout)
            if not m or m.law_type != want:
                continue
            if _norm_compact(base_law.law_name) not in _norm_compact(m.law_name) and _similarity(
                m.law_name, q
            ) < 0.55:
                continue
            if all(x.law_id != m.law_id for x in out):
                out.append(m)

    if client is not None:
        collect(client)
    else:
        with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as c:
            collect(c)
    return out


# --- [4] 최종 블록 + 링크 -----------------------------------------------------


def build_law_links_output(main_law: LawMatch, related: list[LawMatch]) -> str:
    """
    답변 하단용 고정 포맷. 본법 1줄 필수, 시행령·규칙은 있을 때만, 중복 제거.
    """
    lines = ["", "📘 관련 법령", f"- [법] {main_law.law_name}"]
    seen_ids = {main_law.law_id}
    seen_names = {_norm_compact(main_law.law_name)}

    for r in related:
        if r.law_id in seen_ids or _norm_compact(r.law_name) in seen_names:
            continue
        if r.law_type == "시행령":
            tag = "[시행령]"
        elif r.law_type == "시행규칙":
            tag = "[시행규칙]"
        else:
            continue
        lines.append(f"- {tag} {r.law_name}")
        seen_ids.add(r.law_id)
        seen_names.add(_norm_compact(r.law_name))
    return "\n".join(lines)


def law_portal_url(lsi_seq: str) -> str:
    """국가법령정보센터 본문 링크. 가능하면 법령일련번호(MST)를 lsiSeq 로 넘긴다."""
    return f"https://www.law.go.kr/lsInfoP.do?lsiSeq={quote(str(lsi_seq).strip(), safe='')}"


def law_match_portal_url(m: LawMatch) -> str:
    """검색 매칭 결과로 포털 링크 — 일련번호(MST)가 있으면 제목·본문과 가장 잘 맞는다."""
    seq = (m.mst or m.law_id or "").strip()
    return law_portal_url(seq)


def build_resolved_law_debug_links(main: LawMatch, related: list[LawMatch]) -> list[dict[str, str]]:
    """legal_debug.links 를 정제된 항목으로 덮어쓸 때 사용."""
    links: list[dict[str, str]] = [{"label": f"[법] {main.law_name}", "url": law_match_portal_url(main)}]
    for r in related:
        if r.law_type == "시행령":
            links.append({"label": f"[시행령] {r.law_name}", "url": law_match_portal_url(r)})
        elif r.law_type == "시행규칙":
            links.append({"label": f"[시행규칙] {r.law_name}", "url": law_match_portal_url(r)})
    return links


# --- [5] 전체 흐름 -----------------------------------------------------------


def resolve_laws_for_answer_text(
    answer_text: str,
    *,
    oc: str | None = None,
    search_url: str | None = None,
    timeout: float = 25.0,
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    """
    LLM 답변에 '관련 법령' 블록·정제 링크·메타를 붙이기 위한 일괄 처리.

    Returns:
        appendix: 답변 아래에 붙일 문자열(없으면 "")
        links: UI용 {label, url}
        meta: legal_debug에 병합할 필드
    """
    meta: dict[str, Any] = {"law_resolution": True}
    if not oc:
        meta["error"] = "no_oc"
        return "", [], meta

    names = extract_law_names(answer_text)
    meta["extracted_names"] = names[:12]
    if not names:
        meta["error"] = "no_law_names_in_answer"
        return "", [], meta

    base_u = (search_url or get_settings().law_go_kr_base_url or DEFAULT_LAW_SEARCH_URL).strip()

    main: LawMatch | None = None
    related: list[LawMatch] = []
    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        for raw in names[:5]:
            norm = normalize_law_name(raw)
            m = search_law_from_api(norm, oc=oc, search_url=base_u, client=client, timeout=timeout)
            if not m:
                continue
            if m.law_type == "법":
                main = m
                break
            if main is None:
                main = m

        if main and main.law_type != "법":
            stripped = re.sub(r"\s*시행규칙\s*$", "", main.law_name)
            stripped = re.sub(r"\s*시행령\s*$", "", stripped).strip()
            if stripped and stripped != main.law_name:
                parent = search_law_from_api(stripped, oc=oc, search_url=base_u, client=client, timeout=timeout)
                if parent and parent.law_type == "법":
                    main = parent

        if not main or main.law_type != "법":
            meta["error"] = "no_parent_law_match"
            return "", [], meta

        related = find_related_laws(main, oc=oc, search_url=base_u, client=client, timeout=timeout)

    meta["main_law"] = {"lawName": main.law_name, "lawId": main.law_id, "lawType": main.law_type}

    meta["related_laws"] = [
        {"lawName": r.law_name, "lawId": r.law_id, "lawType": r.law_type} for r in related
    ]

    appendix = build_law_links_output(main, related)
    links = build_resolved_law_debug_links(main, related)
    return appendix, links, meta
