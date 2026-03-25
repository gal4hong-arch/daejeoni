from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.roundtable import format_answer_from_turns  # noqa: E402


def test_format_answer_markers() -> None:
    t = [
        {"role_id": "supervisor", "label": "상급자", "content": "A내용"},
        {"role_id": "citizen", "label": "시민", "content": "B내용"},
    ]
    s = format_answer_from_turns(t)
    assert "【역할별 토의" in s
    assert "── 상급자 ──" in s
    assert "A내용" in s
    assert "── 시민 ──" in s
