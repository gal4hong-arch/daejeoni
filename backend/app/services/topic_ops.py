from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, KbChunk, MessageTopicMap, TopicSession


def merge_topics(
    db: Session,
    *,
    stream_id: str,
    into_topic_id: str,
    from_topic_ids: list[str],
) -> None:
    """여러 안건을 하나로 합침."""
    from_topic_ids = [x for x in from_topic_ids if x != into_topic_id]
    if not from_topic_ids:
        return

    into = db.get(TopicSession, into_topic_id)
    if not into or into.conversation_stream_id != stream_id:
        raise ValueError("into_topic invalid")

    for fid in from_topic_ids:
        ft = db.get(TopicSession, fid)
        if not ft or ft.conversation_stream_id != stream_id:
            continue

        maps = db.scalars(select(MessageTopicMap).where(MessageTopicMap.topic_session_id == fid)).all()
        for m in maps:
            db.delete(m)
            exists = db.scalars(
                select(MessageTopicMap).where(
                    MessageTopicMap.message_id == m.message_id,
                    MessageTopicMap.topic_session_id == into_topic_id,
                )
            ).first()
            if not exists:
                db.add(MessageTopicMap(message_id=m.message_id, topic_session_id=into_topic_id))

        db.execute(update(KbChunk).where(KbChunk.topic_session_id == fid).values(topic_session_id=into_topic_id))
        db.delete(ft)


def split_topic_last_messages(
    db: Session,
    *,
    stream_id: str,
    from_topic_id: str,
    move_last_n: int = 1,
) -> str:
    """해당 안건에 매핑된 메시지 중 최근 N개를 새 안건으로 분리."""
    if move_last_n < 1:
        move_last_n = 1

    old = db.get(TopicSession, from_topic_id)
    if not old or old.conversation_stream_id != stream_id:
        raise ValueError("topic invalid")

    msg_ids = (
        db.execute(
            select(ChatMessage.id)
            .join(MessageTopicMap, MessageTopicMap.message_id == ChatMessage.id)
            .where(
                MessageTopicMap.topic_session_id == from_topic_id,
                ChatMessage.conversation_stream_id == stream_id,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(move_last_n)
        )
        .scalars()
        .all()
    )

    new_ts = TopicSession(
        conversation_stream_id=stream_id,
        title=(old.title or "안건") + " (분리)",
        topic_label=(old.topic_label or "") + " 분리",
        work_type=old.work_type,
    )
    db.add(new_ts)
    db.flush()

    for mid in msg_ids:
        db.execute(
            delete(MessageTopicMap).where(
                MessageTopicMap.message_id == mid,
                MessageTopicMap.topic_session_id == from_topic_id,
            )
        )
        db.add(MessageTopicMap(message_id=mid, topic_session_id=new_ts.id))

    return new_ts.id
