"""법령 라우팅 시 사용자 프롬프트에 법령 블록이 RAG 앞에 오는지 확인."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.answer_generator import generate_answer  # noqa: E402
from app.services.law_go_kr.types import LegalFetchResult  # noqa: E402
from app.services.retrieval import RetrievedChunk  # noqa: E402


def test_legal_routed_puts_legal_block_before_internal_rag() -> None:
    legal = LegalFetchResult(
        text="LAW_BLOCK_UNIQUE_MARKER",
        raw_json="{}",
        ok=True,
        debug={},
    )
    chunks = [
        RetrievedChunk(
            chunk_id="c1",
            source_title="내부",
            content="RAG" * 400,
            document_id="d1",
            score=0.5,
        )
    ]
    captured: dict[str, str] = {}

    def _capture(db, *, user_id, model, system, user, temperature=0.3, max_tokens=8192, conversation_history=None):
        captured["user"] = user
        return "ok"

    mock_db = MagicMock()
    with patch("app.services.answer_generator.chat_completion", side_effect=_capture):
        generate_answer(
            mock_db,
            user_id="u1",
            model="gpt-4o-mini",
            user_message="국가계약법이 뭐야",
            chunks=chunks,
            legal=legal,
            legal_routed=True,
            law_query_analysis=None,
        )

    u = captured.get("user", "")
    pos_law = u.find("LAW_BLOCK_UNIQUE_MARKER")
    pos_rag = u.find("내부 문서 발췌")
    assert pos_law != -1 and pos_rag != -1
    assert pos_law < pos_rag
