"""사용자 데이터 영역별 초기화."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    ChatMessage,
    ConversationStream,
    KbChunk,
    KbDocument,
    LegalSnapshot,
    MessageTopicMap,
    TopicClassification,
    TopicSession,
    UserApiKey,
    UserLawStat,
    UserModelPreference,
)

ALLOWED_SCOPES = frozenset(
    {
        "chat",
        "review_drafts",
        "embeddings",
        "prompts",
        "topics",
        "logs",
        "api_keys",
    }
)


def _user_stream_ids(db: Session, user_id: str) -> list[str]:
    return list(
        db.scalars(select(ConversationStream.id).where(ConversationStream.user_id == user_id)).all()
    )


def _user_topic_ids(db: Session, stream_ids: list[str]) -> list[str]:
    if not stream_ids:
        return []
    return list(
        db.scalars(
            select(TopicSession.id).where(TopicSession.conversation_stream_id.in_(stream_ids))
        ).all()
    )


def _user_message_ids(db: Session, stream_ids: list[str]) -> list[str]:
    if not stream_ids:
        return []
    return list(
        db.scalars(
            select(ChatMessage.id).where(ChatMessage.conversation_stream_id.in_(stream_ids))
        ).all()
    )


def reset_scopes(db: Session, user_id: str, scopes: list[str]) -> dict[str, Any]:
    bad = [s for s in scopes if s not in ALLOWED_SCOPES]
    if bad:
        raise ValueError(f"허용되지 않은 범위: {', '.join(bad)}")
    applied: dict[str, Any] = {}
    stream_ids = _user_stream_ids(db, user_id)

    if "chat" in scopes:
        topic_ids = _user_topic_ids(db, stream_ids)
        msg_ids = _user_message_ids(db, stream_ids)
        if topic_ids:
            db.execute(delete(LegalSnapshot).where(LegalSnapshot.topic_session_id.in_(topic_ids)))
            db.execute(
                update(KbChunk)
                .where(KbChunk.topic_session_id.in_(topic_ids))
                .values(topic_session_id=None)
            )
        if msg_ids:
            db.execute(delete(TopicClassification).where(TopicClassification.message_id.in_(msg_ids)))
            db.execute(delete(MessageTopicMap).where(MessageTopicMap.message_id.in_(msg_ids)))
        if stream_ids:
            db.execute(delete(ChatMessage).where(ChatMessage.conversation_stream_id.in_(stream_ids)))
            db.execute(delete(TopicSession).where(TopicSession.conversation_stream_id.in_(stream_ids)))
            db.execute(delete(ConversationStream).where(ConversationStream.user_id == user_id))
        applied["chat"] = {"deleted_streams": len(stream_ids)}

    if "prompts" in scopes and "chat" not in scopes:
        msg_ids = _user_message_ids(db, stream_ids)
        if msg_ids:
            db.execute(delete(TopicClassification).where(TopicClassification.message_id.in_(msg_ids)))
            db.execute(delete(MessageTopicMap).where(MessageTopicMap.message_id.in_(msg_ids)))
            db.execute(delete(ChatMessage).where(ChatMessage.id.in_(msg_ids)))
        applied["prompts"] = {"cleared_messages": len(msg_ids)}

    if "topics" in scopes and "chat" not in scopes:
        topic_ids = _user_topic_ids(db, stream_ids)
        if topic_ids:
            db.execute(delete(MessageTopicMap).where(MessageTopicMap.topic_session_id.in_(topic_ids)))
            db.execute(delete(LegalSnapshot).where(LegalSnapshot.topic_session_id.in_(topic_ids)))
            db.execute(
                update(KbChunk)
                .where(KbChunk.topic_session_id.in_(topic_ids))
                .values(topic_session_id=None)
            )
            db.execute(delete(TopicSession).where(TopicSession.id.in_(topic_ids)))
        applied["topics"] = {"deleted_topic_sessions": len(topic_ids)}

    if "review_drafts" in scopes:
        r = db.execute(
            delete(AuditLog).where(AuditLog.user_id == user_id, AuditLog.action.like("agent.%"))
        )
        applied["review_drafts"] = {"deleted_audit_rows": r.rowcount or 0}

    if "embeddings" in scopes:
        doc_rows = db.scalars(select(KbDocument.id).where(KbDocument.user_id == user_id)).all()
        doc_ids = list(doc_rows)
        if doc_ids:
            db.execute(delete(KbChunk).where(KbChunk.document_id.in_(doc_ids)))
            db.execute(delete(KbDocument).where(KbDocument.id.in_(doc_ids)))
        db.execute(delete(KbChunk).where(KbChunk.user_id == user_id))
        db.execute(
            update(UserLawStat)
            .where(UserLawStat.user_id == user_id)
            .values(rag_document_id=None)
        )
        applied["embeddings"] = {"deleted_documents": len(doc_ids)}

    if "logs" in scopes:
        r = db.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
        applied["logs"] = {"deleted_audit_rows": r.rowcount or 0}

    if "api_keys" in scopes:
        r = db.execute(delete(UserApiKey).where(UserApiKey.user_id == user_id))
        applied["api_keys"] = {"deleted_keys": r.rowcount or 0}

    return {"scopes": scopes, "detail": applied}
