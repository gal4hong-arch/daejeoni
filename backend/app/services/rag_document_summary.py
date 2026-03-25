"""RAG용 문서 개요 청크: LLM 요약(선택) + 실패 시 발췌 기반 개요."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.llm_client import chat_completion
from app.services.model_resolver import resolve_model

_OUTLINE_FOOTER = (
    "\n\n[안내] 이 청크는 문서 검색용 개요입니다. "
    "세부 근거는 같은 문서의 본문(Part) 청크를 참고하세요."
)

_MAX_EXCERPT = 16000
_EXTRACTIVE_CAP = 3800


def _extractive_outline(excerpt: str) -> str:
    t = excerpt.strip()
    if not t:
        return ""
    body = t[:_EXTRACTIVE_CAP].strip()
    if len(t) > _EXTRACTIVE_CAP:
        body += "\n\n… (원문 앞부분 발췌만 저장됨)"
    return "[문서 개요 · 발췌]\n" + body + _OUTLINE_FOOTER


def build_rag_outline_text(db: Session, *, user_id: str, doc_title: str, excerpt: str) -> str:
    """본문 임베딩에 추가할 개요 문자열. 설정 꺼짐이면 빈 문자열."""
    s = get_settings()
    if not getattr(s, "rag_outline_chunk_enabled", True):
        return ""
    ex = (excerpt or "").strip()
    if len(ex) < 80:
        return ""
    ex = ex[:_MAX_EXCERPT]
    title = (doc_title or "문서").strip()[:300]

    model = resolve_model(db, user_id=user_id, topic_session_id=None, task="chat")
    sys = (
        "너는 행정·업무 문서를 RAG(검색 증강)에 쓰기 위한 짧은 개요를 쓴다. "
        "한국어, 5~12문장 또는 bullet. 법령명·수치·고유명사는 가능하면 유지. "
        "없는 정보를 지어내지 말 것."
    )
    user = (
        f"문서 제목: {title}\n\n"
        f"아래는 본문 발췌이다. 이 발췌만 근거로 문서가 다루는 주제·목적·핵심 사실을 요약하라.\n\n{ex}"
    )
    try:
        out = (chat_completion(
            db,
            user_id=user_id,
            model=model,
            system=sys,
            user=user,
            temperature=0.2,
            max_tokens=2048,
        ) or "").strip()
        if len(out) >= 80:
            return "[문서 개요 · 요약]\n" + out + _OUTLINE_FOOTER
    except Exception:
        pass
    return _extractive_outline(ex)


def persist_outline_chunk(
    db: Session,
    *,
    user_id: str,
    document_id: str,
    doc_title: str,
    outline_body: str,
) -> tuple[bool, bool]:
    """(저장 여부, 임베딩 성공 여부)."""
    from app.db.models import KbChunk
    from app.services.embeddings import embed_text, embedding_to_json

    body = (outline_body or "").strip()
    if not body:
        return False, False
    title = (doc_title or "문서").strip()[:200]
    c = KbChunk(
        user_id=user_id,
        document_id=document_id,
        source_title=f"{title} · [문서개요] · RAG",
        content=body,
        topic_session_id=None,
    )
    vec = embed_text(db, user_id, body)
    c.embedding_json = embedding_to_json(vec)
    db.add(c)
    return True, bool(vec)
