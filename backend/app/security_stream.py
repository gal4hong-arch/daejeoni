from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import ConversationStream, TopicSession


def get_owned_stream(db: Session, stream_id: str, user_id: str) -> ConversationStream:
    s = db.get(ConversationStream, stream_id)
    if not s or s.user_id != user_id:
        raise HTTPException(status_code=404, detail="stream not found")
    return s


def get_owned_topic(db: Session, topic_id: str, user_id: str) -> TopicSession:
    t = db.get(TopicSession, topic_id)
    if not t:
        raise HTTPException(status_code=404, detail="topic not found")
    st = db.get(ConversationStream, t.conversation_stream_id)
    if not st or st.user_id != user_id:
        raise HTTPException(status_code=404, detail="topic not found")
    return t
