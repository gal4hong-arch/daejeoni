"""법령 조회 결과 타입 (answer_generator·legal_adapter에서 공유)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LegalFetchResult:
    text: str
    raw_json: str
    ok: bool
    warning: str | None = None
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LawQueryAnalysis:
    """1단계 LLM이 정리한 질의 의도·초점(최종 답변 user 프롬프트에 전달)."""

    intent_summary: str
    law_focus: str
    notes_for_search: str
