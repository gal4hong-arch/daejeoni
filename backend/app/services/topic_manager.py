import json
import re
import uuid
from dataclasses import dataclass

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ChatMessage, TopicClassification, TopicSession, UserApiKey
from app.services.user_api_keys import read_user_api_key_stored


@dataclass
class TopicRouteResult:
    topic_session_id: str
    decision_type: str  # matched | new_topic | ambiguous
    detected_topic: str
    work_type: str
    confidence: float
    entities_json: str | None = None


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w가-힣]+", text.lower()))


def _similarity(a: str, b: str) -> float:
    sa, sb = _tokens(a), _tokens(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _recent_context(db: Session, stream_id: str, limit: int = 8) -> str:
    rows = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_stream_id == stream_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    lines = [f"{m.role}: {m.content[:500]}" for m in reversed(rows)]
    return "\n".join(lines)


def _openai_client_for_user(db: Session, user_id: str) -> OpenAI | None:
    settings = get_settings()
    if settings.openai_api_key:
        return OpenAI(api_key=settings.openai_api_key)
    row = db.execute(
        select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "openai")
    ).scalar_one_or_none()
    if row:
        plain = read_user_api_key_stored(row.encrypted_key)
        if plain:
            return OpenAI(api_key=plain)
    return None


def _llm_classify(client: OpenAI, message: str, context: str, topics: list[TopicSession]) -> dict | None:
    topic_lines = "\n".join(f"- id={t.id} title={t.title!r} label={t.topic_label!r}" for t in topics)
    system = (
        "행정 업무 대화의 주제를 분류한다. detected_topic은 대화 세션 목록에 표시할 짧은 주제 요약(명사구 위주, "
        "질문 원문을 그대로 베끼지 말 것, 40자 이내 권장)으로 쓴다. JSON만 출력: "
        '{"detected_topic": str, "work_type": str, "decision_type": "matched"|"new_topic"|"ambiguous", '
        '"matched_topic_id": str|null, "confidence": 0~1, "entities": object|null}'
    )
    topics_block = topic_lines if topic_lines else "(없음)"
    user = f"최근대화:\n{context}\n\n현재메시지:\n{message}\n\n기존안건:\n{topics_block}"
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        raw = r.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception:
        return None


def route_message(
    db: Session,
    *,
    stream_id: str,
    user_id: str,
    message: str,
) -> TopicRouteResult:
    topics = (
        db.execute(select(TopicSession).where(TopicSession.conversation_stream_id == stream_id))
        .scalars()
        .all()
    )
    ctx = _recent_context(db, stream_id)

    client = _openai_client_for_user(db, user_id)
    if client:
        parsed = _llm_classify(client, message, ctx, topics)
        if parsed:
            dt = parsed.get("decision_type") or "new_topic"
            conf = float(parsed.get("confidence") or 0.5)
            wt = str(parsed.get("work_type") or "general")
            det = str(parsed.get("detected_topic") or message[:80])
            mid = parsed.get("matched_topic_id")
            ent = parsed.get("entities")
            ent_json = json.dumps(ent, ensure_ascii=False) if ent is not None else None
            if dt == "matched" and mid and any(t.id == mid for t in topics):
                return TopicRouteResult(str(mid), "matched", det, wt, conf, ent_json)
            if dt == "ambiguous" and mid and any(t.id == mid for t in topics):
                return TopicRouteResult(str(mid), "ambiguous", det, wt, conf * 0.8, ent_json)
            if dt == "new_topic":
                tid = str(uuid.uuid4())
                ts = TopicSession(
                    id=tid,
                    conversation_stream_id=stream_id,
                    title=(det[:60] + "…") if len(det) > 60 else det,
                    topic_label=det[:200],
                    work_type=wt,
                )
                db.add(ts)
                db.flush()
                return TopicRouteResult(tid, "new_topic", det, wt, conf, ent_json)

    # 휴리스틱 폴백
    if not topics:
        tid = str(uuid.uuid4())
        ts = TopicSession(
            id=tid,
            conversation_stream_id=stream_id,
            title=message[:60] + ("…" if len(message) > 60 else ""),
            topic_label=message[:80],
            work_type="general",
        )
        db.add(ts)
        db.flush()
        return TopicRouteResult(tid, "new_topic", ts.topic_label, ts.work_type, 0.4, None)

    def _score(t: TopicSession) -> float:
        return max(_similarity(message, t.title), _similarity(message, t.topic_label))

    best_t = max(topics, key=_score)
    best_s = _score(best_t)

    if best_s >= 0.35:
        return TopicRouteResult(best_t.id, "matched", best_t.topic_label, best_t.work_type, best_s, None)
    if best_s < 0.12:
        tid = str(uuid.uuid4())
        ts = TopicSession(
            id=tid,
            conversation_stream_id=stream_id,
            title=message[:60] + ("…" if len(message) > 60 else ""),
            topic_label=message[:80],
            work_type="general",
        )
        db.add(ts)
        db.flush()
        return TopicRouteResult(tid, "new_topic", ts.topic_label, ts.work_type, 0.35, None)
    return TopicRouteResult(best_t.id, "ambiguous", best_t.topic_label, best_t.work_type, best_s, None)


def record_classification(
    db: Session,
    *,
    message_id: str,
    result: TopicRouteResult,
) -> None:
    db.add(
        TopicClassification(
            message_id=message_id,
            detected_topic=result.detected_topic,
            decision_type=result.decision_type,
            work_type=result.work_type,
            confidence=result.confidence,
            entities_json=result.entities_json,
        )
    )
