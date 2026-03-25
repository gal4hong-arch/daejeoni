"""
국가법령정보 lawService.do 의 JO(조·조의) 요청값 — 6자리: 조문번호 4자리 + 조가지번호 2자리.

예: 000200=제2조, 001002=제10조의2 (가이드 샘플과 동일)

전체 본문(모든 조):
  가이드상 JO 를 생략하면 모든 조가 포함된 응답이 된다.
  이 코드에서는 질의에 「n조」패턴이 없으면 JO 파라미터를 보내지 않으므로 전체 본문 조회와 동일하다.
"""

from __future__ import annotations

import re


def parse_law_service_jo_from_query(user_query: str) -> str | None:
    """
    사용자 질의에서 첫 번째 「제n조」또는 「n조」「제n조의m」 패턴을 찾아 JO 문자열을 만든다.
    공백은 무시하고 매칭한다. 해당 패턴이 없으면 None.
    """
    t = re.sub(r"\s+", "", (user_query or "").strip())
    if not t:
        return None
    m = re.search(r"(?:제)?(\d{1,4})조(?:의(\d{1,2}))?", t)
    if not m:
        return None
    article = int(m.group(1))
    branch = int(m.group(2)) if m.group(2) is not None else 0
    if article < 1 or article > 9999:
        return None
    if branch < 0 or branch > 99:
        return None
    return f"{article:04d}{branch:02d}"
