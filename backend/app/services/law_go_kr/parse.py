"""법제처 JSON/XML 응답 파싱: ID 추출, 포털 링크, LLM용 텍스트화."""

from __future__ import annotations

import json
import re
from typing import Any
import html as html_module
from urllib.parse import parse_qs, quote, urlparse

# 검색 JSON 안에서 본문 조회에 쓸 ID 후보 키 (가이드·응답 스키마에 따라 다를 수 있음)
LAW_ID_KEYS = frozenset(
    {
        "법령ID",
        "법령일련번호",
        "행정규칙ID",
        "자치법규ID",
        "admRulId",
        "admRulSeq",
        "자치법규일련번호",
        "법령아이디",
        "법령번호",
        "lsiSeq",
        "lsi_seq",
        "lawId",
        "law_id",
        "LAW_ID",
        "MST",
        "법령MST",
    }
)

_LAW_ID_KEY_SUBSTR = ("법령", "행정", "자치", "lsi", "law", "adm", "rul", "ordin", "mst", "serial", "seq")

_SKIP_LAW_ID_KEYS = frozenset(
    {
        "totalCount",
        "numOfRows",
        "page",
        "display",
        "resultCode",
        "resultcode",
        "status",
        "code",
        "count",
        "index",
        "order",
    }
)


def law_go_kr_json_looks_like_error(data: Any) -> bool:
    """OPEN API 오류 JSON( result/msg 등 ) 여부 — 본문 없음으로 폴백 판단용."""
    if not isinstance(data, dict):
        return False
    law_one = data.get("Law")
    if isinstance(law_one, str) and law_one.strip():
        if "없습니다" in law_one or "확인하여" in law_one:
            return True
    msg = str(data.get("msg") or "")
    if not msg.strip():
        return False
    hints = ("실패", "오류", "OPEN API", "인증", "등록되지", "확인하여", "잘못된", "제한")
    return any(h in msg for h in hints)


def response_to_llm_text(data: Any, max_chars: int = 14000) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data[:max_chars]
    try:
        s = json.dumps(data, ensure_ascii=False, indent=2)
    except TypeError:
        s = str(data)
    return s[:max_chars]


# lawService.do 법령 JSON: 앞부분은 메타·연혁 위주라 json.dumps 상단만 잘리면 조문이 통째로 누락됨
# eflaw(현행본문) 등 필드명은 가이드 표와 동일(조문내용·법령명_한글 등)
_BODY_LINE_KEYS_ORDER: tuple[str, ...] = (
    "조문번호",
    "조문가지번호",
    "조문제목",
    "조문내용",
    "조문키",
    "조문참고자료",
    "항번호",
    "항내용",
    "호번호",
    "호내용",
    "목번호",
    "목내용",
    "부칙내용",
    "별표제목",
    "별표내용",
    "개정문내용",
    "제개정이유내용",
)


def _scalar_to_body_str(v: Any) -> str | None:
    if isinstance(v, str):
        t = v.strip()
        return t if t else None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else str(v)
    return None


def _approx_parts_len(parts: list[str]) -> int:
    return sum(len(p) + 1 for p in parts)


def _walk_law_body_lines(node: Any, parts: list[str], *, max_chars: int) -> None:
    if _approx_parts_len(parts) >= max_chars:
        return
    if isinstance(node, dict):
        block: list[str] = []
        for k in _BODY_LINE_KEYS_ORDER:
            s = _scalar_to_body_str(node.get(k))
            if s:
                block.append(f"{k}: {s}")
        if block:
            parts.extend(block)
        for k, v in node.items():
            if k in _BODY_LINE_KEYS_ORDER and _scalar_to_body_str(v) is not None:
                continue
            _walk_law_body_lines(v, parts, max_chars=max_chars)
    elif isinstance(node, list):
        for it in node:
            _walk_law_body_lines(it, parts, max_chars=max_chars)


def law_service_basic_meta_ids(data: Any) -> tuple[str | None, str | None]:
    """
    lawService.do JSON ``기본정보``에서 식별자 2종을 구분해 반환.

    Returns:
        (본문조회_법령ID, 법령일련번호_MST)

    - **법령ID**: eflaw/law 등 본문 API의 ``ID`` 쿼리 파라미터로 쓰는 값(검색 조문의 법령ID와 동일 명칭).
    - **법령일련번호**: 국가법령정보센터 포털·상세에서 쓰는 MST / ``lsiSeq``에 대응하는 일련번호로,
      검색 API 조문 블록의 「법령일련번호」와 같은 계열이며 **법령ID와 숫자가 다를 수 있음**.

    둘 다 없으면 ``(None, None)``.
    """
    if not isinstance(data, dict):
        return None, None
    law_root = data.get("법령")
    if not isinstance(law_root, dict):
        law_root = data
    info = law_root.get("기본정보")
    if not isinstance(info, dict):
        return None, None

    def _field(key: str) -> str:
        v = info.get(key)
        if v is None:
            return ""
        if isinstance(v, dict) and "content" in v:
            s = str(v.get("content") or "").strip()
            return s
        s = _scalar_to_body_str(v)
        return s or ""

    lid = _field("법령ID") or _field("법령아이디")
    mst = _field("법령일련번호") or _field("법령MST") or _field("MST")
    return (lid or None, mst or None)


def law_service_json_body_plain(data: Any, *, max_chars: int = 200_000) -> str:
    """법령 본문 JSON에서 조문·항·호 등 설명 필드만 순서대로 모은 평문."""
    parts: list[str] = []
    _walk_law_body_lines(data, parts, max_chars=max_chars)
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text.strip()


def law_service_body_stats_for_debug(
    data: Any,
    *,
    preview_max: int = 2000,
    extract_max_chars: int = 600_000,
) -> dict[str, Any]:
    """
    테스트·로그용: JSON에서 뽑은 평문 길이와 앞부분 미리보기.
    평문이 비면 JSON 덤프 앞부분을 넣어 파싱 실패 여부를 확인할 수 있게 한다.
    """
    plain = law_service_json_body_plain(data, max_chars=extract_max_chars).strip()
    n = len(plain)
    if n > 0:
        prev = plain[:preview_max].rstrip()
        if n > preview_max:
            prev += "…"
        return {"body_plain_len": n, "body_preview": prev}
    dump = response_to_llm_text(data, max_chars=max(8000, preview_max * 2))
    dlen = len(dump or "")
    prev = (dump or "")[:preview_max].rstrip()
    if dlen > preview_max:
        prev += "…"
    return {
        "body_plain_len": 0,
        "body_preview": prev or "(빈 응답)",
        "preview_note": "조문키 추출 0자 — JSON 앞부분",
    }


def law_service_data_for_llm(data: Any, *, max_chars: int = 24000) -> str:
    """
    LLM 입력용: 조문 등 구조화 필드를 우선하고, 너무 짧으면 JSON 덤프를 덧붙인다.
    """
    gather_cap = min(500_000, max(max_chars * 6, 120_000))
    plain = law_service_json_body_plain(data, max_chars=gather_cap)
    plain = plain[:max_chars] if len(plain) > max_chars else plain
    p = plain.strip()
    if len(p) >= 400:
        return p
    dump = response_to_llm_text(data, max_chars=max_chars)
    if p:
        return (p + "\n\n---\n\n" + dump)[:max_chars]
    return dump


def portal_search_url(user_query: str) -> str:
    q = (user_query or "").strip()[:300]
    return "https://www.law.go.kr/lsSc.do?menuId=1&subMenuId=15&query=" + quote(q)


def law_search_query_variants(user_text: str, *, max_variants: int = 6) -> list[str]:
    """
    긴 질문·오타·약칭 대비: 법제처 검색에 넣을 짧은 검색어 후보.
    """
    t = (user_text or "").strip()
    if not t:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def add(s: str) -> None:
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) < 2 or len(s) > 120 or s in seen:
            return
        seen.add(s)
        out.append(s)

    add(t[:120])
    tail_cut = re.sub(
        r"(에\s*대해|에\s*관해|에\s*대한|알려\s*줘|알려주세요|알려줘|설명해\s*줘|설명|뭐야|무엇|어떻게|"
        r"조회|검색|찾아|요약|관련|내용|따라|경우|때)\s*[^가-힣]*$",
        "",
        t,
        flags=re.I,
    ).strip()
    if tail_cut and len(tail_cut) >= 2:
        add(tail_cut[:120])

    for pat in (
        r"[가-힣0-9·\s]{2,45}?(?:법률|법령|시행령|시행규칙|특별법|기본법|조례|규칙)(?!\s*원)",
        r"[가-힣0-9·]{2,40}법(?!\s*원)",
    ):
        for m in re.finditer(pat, t):
            frag = m.group(0).strip()
            if len(frag) >= 3:
                add(frag)

    for m in re.finditer(r"\(([가-힣0-9·\s]{2,30})\)", t):
        add(m.group(1).strip())

    return out[:max_variants]


def is_plausible_law_id_scalar(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip()
    if not s or len(s) > 32:
        return False
    if not re.match(r"^[A-Za-z0-9._-]+$", s):
        return False
    if re.fullmatch(r"0+", s):
        return False
    return bool(re.search(r"\d", s))


def key_looks_like_law_id_field(key: str) -> bool:
    if key in LAW_ID_KEYS:
        return True
    if key in _SKIP_LAW_ID_KEYS:
        return False
    kl = key.lower().replace("_", "")
    if kl.startswith("page") or "pageid" in kl or "offset" in kl or "limit" in kl:
        return False
    if kl in ("id", "seq", "no", "num", "idx", "userid", "sessionid"):
        return False
    if any(s in key for s in _LAW_ID_KEY_SUBSTR) and (
        "id" in kl or "seq" in kl or "번호" in key or "일련" in key
    ):
        return True
    if kl in ("lsiseq", "lawid", "lawserial", "admruleid", "admrulseq"):
        return True
    return False


def extract_law_ids_fuzzy(node: Any, out: list[str], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if key_looks_like_law_id_field(k) and is_plausible_law_id_scalar(v):
                s = str(v).strip()
                if s not in out:
                    out.append(s)
                    if len(out) >= limit:
                        return
            extract_law_ids_fuzzy(v, out, limit)
    elif isinstance(node, list):
        for it in node:
            extract_law_ids_fuzzy(it, out, limit)


def extract_law_ids(node: Any, out: list[str], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if k in LAW_ID_KEYS and v is not None:
                s = str(v).strip()
                if s and s not in out:
                    out.append(s)
                    if len(out) >= limit:
                        return
            extract_law_ids(v, out, limit)
    elif isinstance(node, list):
        for it in node:
            extract_law_ids(it, out, limit)


def extract_law_ids_from_many(roots: list[Any], limit: int) -> list[str]:
    out: list[str] = []
    for root in roots:
        if root is None:
            continue
        extract_law_ids(root, out, limit)
        if len(out) >= limit:
            return out[:limit]
        extract_law_ids_fuzzy(root, out, limit)
        if len(out) >= limit:
            return out[:limit]
    return out[:limit]


def parse_detail_link_query_params(detail_link: str) -> dict[str, str]:
    """
    lawSearch 응답의 법령상세링크(상대 경로 또는 절대 URL)에서 쿼리 파라미터만 추출.
    빈 값 키는 제외.
    """
    t = (detail_link or "").strip()
    if not t:
        return {}
    if t.startswith("http://") or t.startswith("https://"):
        qstr = urlparse(t).query
    elif "?" in t:
        qstr = t.split("?", 1)[1]
    else:
        return {}
    raw = parse_qs(qstr, keep_blank_values=False)
    return {k: v[0].strip() for k, v in raw.items() if v and str(v[0]).strip() != ""}


def find_law_hit_service_context(roots: list[Any], law_id: str) -> dict[str, str | None]:
    """
    검색 JSON 트리에서 법령ID·법령일련번호 등이 law_id 와 일치하는 객체를 찾아
    본문 조회에 쓸 법령상세링크·MST·시행일자를 돌려준다.
    """
    want = str(law_id or "").strip()
    if not want:
        return {"detail_link": None, "mst": None, "ef_yd": None}

    def ids_in_dict(d: dict[str, Any]) -> set[str]:
        found: set[str] = set()
        for k, v in d.items():
            if k in LAW_ID_KEYS and is_plausible_law_id_scalar(v):
                found.add(str(v).strip())
        return found

    def walk(node: Any) -> dict[str, str | None] | None:
        if isinstance(node, dict):
            ids = ids_in_dict(node)
            if want in ids:
                dl = node.get("법령상세링크")
                link = str(dl).strip() if dl else ""
                mst = node.get("법령일련번호")
                mst_s = str(mst).strip() if mst is not None and str(mst).strip() else None
                ef = node.get("시행일자")
                ef_s = str(ef).strip() if ef is not None and str(ef).strip() else None
                return {
                    "detail_link": link if link else None,
                    "mst": mst_s,
                    "ef_yd": ef_s,
                }
            for v in node.values():
                hit = walk(v)
                if hit is not None:
                    return hit
        elif isinstance(node, list):
            for it in node:
                hit = walk(it)
                if hit is not None:
                    return hit
        return None

    for root in roots:
        if root is None:
            continue
        hit = walk(root)
        if hit is not None:
            return hit
    return {"detail_link": None, "mst": None, "ef_yd": None}


_TAG_RE = re.compile(r"<[^>]+>")


def xmlish_response_plain_text(body: str, *, max_chars: int = 400_000) -> str:
    """lawService XML(또는 HTML) 응답에서 태그를 제거한 근사 평문 — 길이·발췌 판단용."""
    if not (body or "").strip():
        return ""
    t = html_module.unescape(_TAG_RE.sub(" ", body))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars]


def extract_law_link_entries(
    search_roots: list[Any] | None,
    law_ids: list[str],
    user_query: str,
    *,
    limit: int = 10,
) -> list[dict[str, str]]:
    """국가법령정보센터 웹 링크(OC 불필요). 법령·행정규칙 일부 응답 대응."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def push(label: str, url: str) -> None:
        if not url or url in seen or len(out) >= limit:
            return
        seen.add(url)
        out.append({"label": label[:200], "url": url})

    pq = (user_query or "").strip()
    if pq:
        push("이 질문으로 국가법령정보센터 검색(웹)", portal_search_url(pq))

    def walk(n: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(n, dict):
            adm = n.get("admRulSeq") or n.get("행정규칙일련번호")
            if adm is not None and is_plausible_law_id_scalar(str(adm)):
                sid = str(adm).strip()
                nm = (
                    n.get("행정규칙명")
                    or n.get("admRulNm")
                    or n.get("규칙명")
                    or n.get("법령명한글")
                    or ""
                )
                lab = (str(nm).strip() if nm else "") or f"행정규칙 (일련 {sid})"
                push(lab, f"https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulSeq={quote(sid, safe='')}")
            name = (
                n.get("법령명한글")
                or n.get("법령명")
                or n.get("lawNm")
                or n.get("법령명_한글")
                or n.get("법령명약칭")
            )
            sid = ""
            for pk in (
                "법령일련번호",
                "MST",
                "법령MST",
                "lsiSeq",
                "lsi_seq",
                "법령ID",
                "lawId",
                "LAW_ID",
            ):
                v = n.get(pk)
                if v is None or not is_plausible_law_id_scalar(v):
                    continue
                if pk in ("admRulSeq", "행정규칙일련번호", "ADM_RUL_SEQ"):
                    continue
                sid = str(v).strip()
                break
            if not sid:
                for k, v in n.items():
                    if key_looks_like_law_id_field(k) and is_plausible_law_id_scalar(v):
                        if k in ("admRulSeq", "행정규칙일련번호", "ADM_RUL_SEQ"):
                            continue
                        sid = str(v).strip()
                        break
            if sid:
                lab = (str(name).strip() if name else "") or f"법령 상세 (일련·ID {sid})"
                push(lab, f"https://www.law.go.kr/lsInfoP.do?lsiSeq={quote(sid, safe='')}")
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for it in n:
                walk(it)

    if search_roots:
        for root in search_roots:
            if isinstance(root, (dict, list)):
                walk(root)

    for lid in law_ids:
        if len(out) >= limit:
            break
        s = str(lid).strip()
        if not s:
            continue
        u = f"https://www.law.go.kr/lsInfoP.do?lsiSeq={quote(s, safe='')}"
        push(f"API로 조회한 법령 ID {s}", u)

    return out


def search_json_total_count_hint(data: Any) -> int | None:
    """lawSearch.do JSON 에서 검색 건수 힌트(스키마별 키 상이)."""
    if isinstance(data, dict):
        for k in (
            "검색결과개수",
            "totalCnt",
            "TotalCnt",
            "totalCount",
            "TotCnt",
            "totCnt",
            "numOfRows",
        ):
            v = data.get(k)
            if isinstance(v, int) and v >= 0:
                return v
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
        for v in data.values():
            t = search_json_total_count_hint(v)
            if t is not None:
                return t
    elif isinstance(data, list):
        for it in data:
            t = search_json_total_count_hint(it)
            if t is not None:
                return t
    return None


def search_json_hit_titles(data: Any, *, limit: int = 8) -> list[str]:
    """검색 결과 JSON에서 상위 명칭(법령·행정규칙·자치법규) 목록."""
    seen: set[str] = set()
    out: list[str] = []

    def add(name: str) -> None:
        n = re.sub(r"\s+", " ", name).strip()
        if len(n) < 2 or n in seen or len(out) >= limit:
            return
        seen.add(n)
        out.append(n)

    def walk(n: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(n, dict):
            has_id = any(key_looks_like_law_id_field(k) for k in n.keys())
            if has_id:
                nm = (
                    n.get("법령명한글")
                    or n.get("법령명")
                    or n.get("lawNm")
                    or n.get("법령명약칭")
                    or n.get("행정규칙명")
                    or n.get("admRulNm")
                    or n.get("규칙명")
                    or n.get("자치법규명")
                    or n.get("ordinNm")
                )
                if nm and str(nm).strip():
                    add(str(nm).strip())
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for it in n:
                walk(it)

    walk(data)
    return out


def law_service_json_title_hint(data: Any) -> str | None:
    """lawService.do 본문 JSON에서 제목 힌트."""
    if not isinstance(data, dict):
        return None
    for k in (
        "법령명_한글",
        "법령명한글",
        "법령명",
        "lawNm",
        "행정규칙명",
        "admRulNm",
        "자치법규명",
        "ordinNm",
    ):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:240]
    for v in data.values():
        if isinstance(v, dict):
            t = law_service_json_title_hint(v)
            if t:
                return t
    return None
