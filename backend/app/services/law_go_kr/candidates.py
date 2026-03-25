"""사용자 질의에서 법령·행정규칙·조례 등 규범 후보를 추정."""

from __future__ import annotations

import re


def wants_administrative_rule_search(query: str) -> bool:
    return bool(
        re.search(
            r"행정규칙|훈령|예규|고시|공고|지침|세칙|업무처리지침",
            query or "",
        )
    )


def wants_ordinance_search(query: str) -> bool:
    return bool(re.search(r"조례|자치법규|시\s*조례|군\s*조례|구\s*조례|도\s*조례", query or ""))
