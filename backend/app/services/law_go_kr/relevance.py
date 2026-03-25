"""본문 JSON에서 사용자 질의와 겹치는 구절 발췌(키워드·토큰 겹침)."""

from __future__ import annotations

import re
from typing import Any


def _query_tokens(query: str) -> set[str]:
    return {t for t in re.findall(r"[\w가-힣]{2,}", (query or "").lower()) if len(t) >= 2}


def _collect_string_leaves(obj: Any, out: list[str], *, max_strings: int, depth: int) -> None:
    if depth < 0 or len(out) >= max_strings:
        return
    if isinstance(obj, str):
        s = obj.strip()
        if len(s) >= 8:
            out.append(s)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_string_leaves(v, out, max_strings=max_strings, depth=depth - 1)
    elif isinstance(obj, list):
        for it in obj:
            _collect_string_leaves(it, out, max_strings=max_strings, depth=depth - 1)


def score_text_against_query(text: str, query: str) -> float:
    q = _query_tokens(query)
    if not q:
        return 0.0
    tl = (text or "").lower()
    hit = sum(1 for t in q if t in tl)
    return hit / max(len(q), 1)


def extract_relevant_excerpts(
    data: Any,
    user_query: str,
    *,
    max_excerpts: int = 4,
    max_chars_each: int = 900,
    max_strings_scan: int = 400,
) -> list[str]:
    """
    lawService 등 JSON 전체에서 문자열 리프를 모은 뒤, 질의 토큰과 겹침이 큰 순으로 잘라 반환.
    """
    leaves: list[str] = []
    _collect_string_leaves(data, leaves, max_strings=max_strings_scan, depth=14)
    if not leaves:
        return []

    scored = [(score_text_against_query(s, user_query), s) for s in leaves]
    scored.sort(key=lambda x: x[0], reverse=True)

    out: list[str] = []
    seen_compact: set[str] = set()
    for sc, s in scored:
        if sc <= 0 and len(out) > 0:
            break
        if sc <= 0 and not out:
            continue  # 첫 발췌는 아래 '긴 문자열' 폴백에서 처리
        c = re.sub(r"\s+", "", s[:200])
        if c in seen_compact:
            continue
        seen_compact.add(c)
        chunk = s.strip()
        if len(chunk) > max_chars_each:
            chunk = chunk[: max_chars_each - 1].rstrip() + "…"
        out.append(chunk)
        if len(out) >= max_excerpts:
            break

    if not out and leaves:
        for s in sorted(leaves, key=len, reverse=True)[:max_excerpts]:
            chunk = s.strip()
            if len(chunk) > max_chars_each:
                chunk = chunk[: max_chars_each - 1].rstrip() + "…"
            out.append(chunk)

    return out
