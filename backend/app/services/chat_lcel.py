"""LangChain LCEL로 답변 생성 단계만 Runnable 로 감싼다(구성 명확화·추후 단계 확장용)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda
from sqlalchemy.orm import Session

from app.services.answer_generator import generate_answer


def build_answer_runnable() -> RunnableLambda:
    def _run(state: dict[str, Any]) -> str:
        db: Session = state["db"]
        return generate_answer(
            db,
            user_id=state["user_id"],
            model=state["model"],
            user_message=state["user_message"],
            chunks=state["chunks"],
            legal=state.get("legal_result"),
            legal_routed=bool(state.get("legal_routed")),
            law_query_analysis=state.get("law_query_analysis"),
            conversation_history=state.get("conversation_history"),
        )

    return RunnableLambda(_run)
