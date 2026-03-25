import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class ConversationStream(Base):
    __tablename__ = "conversation_streams"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(128), nullable=False, index=True)
    title = Column(String(512), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    topics = relationship("TopicSession", back_populates="stream", cascade="all, delete-orphan")
    messages = relationship("ChatMessage", back_populates="stream", cascade="all, delete-orphan")


class TopicSession(Base):
    __tablename__ = "topic_sessions"

    id = Column(String(36), primary_key=True, default=_uuid)
    conversation_stream_id = Column(String(36), ForeignKey("conversation_streams.id"), nullable=False)
    title = Column(String(512), nullable=False, default="")
    topic_label = Column(String(256), nullable=False, default="")
    work_type = Column(String(64), nullable=False, default="general")
    model_override = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    stream = relationship("ConversationStream", back_populates="topics")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=_uuid)
    conversation_stream_id = Column(String(36), ForeignKey("conversation_streams.id"), nullable=False)
    role = Column(String(16), nullable=False)  # user | assistant | system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    stream = relationship("ConversationStream", back_populates="messages")


class MessageTopicMap(Base):
    __tablename__ = "message_topic_maps"

    message_id = Column(String(36), ForeignKey("chat_messages.id"), primary_key=True)
    topic_session_id = Column(String(36), ForeignKey("topic_sessions.id"), primary_key=True)


class TopicClassification(Base):
    __tablename__ = "topic_classifications"

    id = Column(String(36), primary_key=True, default=_uuid)
    message_id = Column(String(36), ForeignKey("chat_messages.id"), nullable=False)
    detected_topic = Column(String(512), nullable=False, default="")
    decision_type = Column(String(32), nullable=False)  # matched | new_topic | ambiguous
    work_type = Column(String(64), nullable=False, default="general")
    confidence = Column(Float, nullable=False, default=0.0)
    entities_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class KbDocument(Base):
    """RAG 소스(문서) 단위 — 다중 문서 선택에 사용."""

    __tablename__ = "kb_documents"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(128), nullable=False, index=True)
    title = Column(String(512), nullable=False, default="")
    source_kind = Column(String(32), nullable=False, default="manual")  # manual | url
    source_url = Column(Text, nullable=True)
    shared_globally = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(128), nullable=False, index=True)
    action = Column(String(128), nullable=False)
    detail_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class UserApiKey(Base):
    __tablename__ = "user_api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_provider"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(128), nullable=False, index=True)
    provider = Column(String(64), nullable=False)
    encrypted_key = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserModelPreference(Base):
    __tablename__ = "user_model_preferences"

    user_id = Column(String(128), primary_key=True)
    default_model = Column(String(128), nullable=False, default="")
    task_models_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(128), nullable=False, index=True)
    document_id = Column(String(36), ForeignKey("kb_documents.id"), nullable=True, index=True)
    source_title = Column(String(512), nullable=False, default="")
    content = Column(Text, nullable=False)
    embedding_json = Column(Text, nullable=True)
    topic_session_id = Column(String(36), ForeignKey("topic_sessions.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class LegalSnapshot(Base):
    __tablename__ = "legal_snapshots"

    id = Column(String(36), primary_key=True, default=_uuid)
    topic_session_id = Column(String(36), ForeignKey("topic_sessions.id"), nullable=True)
    query = Column(Text, nullable=False)
    response_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class UserLawStat(Base):
    """사용자별 조회·답변에 쓰인 법령 빈도(자주 찾는 법령 RAG 후보)."""

    __tablename__ = "user_law_stats"
    __table_args__ = (UniqueConstraint("user_id", "law_id", name="uq_user_law_stat_law_id"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(128), nullable=False, index=True)
    law_id = Column(String(64), nullable=False, index=True)
    law_title = Column(String(512), nullable=False, default="")
    hit_count = Column(Integer, nullable=False, default=0)
    last_access_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    rag_document_id = Column(String(36), ForeignKey("kb_documents.id"), nullable=True, index=True)
