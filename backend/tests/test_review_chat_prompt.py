"""review_chat: 역할별 시스템 프롬프트(페르소나) 구성."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.review_chat import build_review_system_prompt  # noqa: E402


def test_build_review_system_prompt_uses_catalog_per_role() -> None:
    sup = build_review_system_prompt("supervisor", None)
    cou = build_review_system_prompt("councilor", None)
    cit = build_review_system_prompt("citizen", None)
    assert "상급자" in sup and "시청 내부" in sup
    assert "시의원" in cou and "의회" in cou
    assert "시민" in cit and "일반 시민" in cit
    assert sup != cou != cit


def test_build_review_system_prompt_override_wins() -> None:
    custom = "【테스트】오직 이 문구만 시스템으로 쓴다."
    out = build_review_system_prompt("supervisor", custom)
    assert out.startswith(custom)
    assert "보고자 메시지 의도" in out
    assert "상급자" not in out
