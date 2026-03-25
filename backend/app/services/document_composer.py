from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, TopicSession


def _topic_transcript(db: Session, stream_id: str, topic_id: str, limit: int = 30) -> str:
    msgs = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_stream_id == stream_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit * 3)
        )
        .scalars()
        .all()
    )
    lines: list[str] = []
    for m in reversed(msgs):
        lines.append(f"{m.role}: {m.content}")
    topic = db.get(TopicSession, topic_id)
    header = f"안건: {topic.title if topic else topic_id}\n주제라벨: {topic.topic_label if topic else ''}\n\n"
    return header + "\n".join(lines[-limit:])


def compose_document(
    db: Session,
    *,
    stream_id: str,
    topic_session_id: str,
    kind: str,
    scenario_hint: str = "",
) -> str:
    """템플릿 기반 초안. report는 설명자료·의회 답변·시뮬 요소를 한 흐름으로 통합."""
    body = _topic_transcript(db, stream_id, topic_session_id)
    hint = (scenario_hint or "").strip()
    hint_block = (
        f"[작성 시 참고 시나리오·강조 각도]\n{hint}\n\n" if hint else ""
    )
    if kind == "report":
        return (
            "【통합 보고·설명·의회 대응 초안】\n"
            "(내부 협의용. 사실·법령·수치는 담당 부서 확인 후 반영.)\n\n"
            f"{hint_block}"
            "한 문서로 통합 서술한다(같은 문단을 설명자료·의회용으로 이중 붙이지 않는다).\n"
            "포함할 고유 축(중복 없이 한 번씩 다룸):\n"
            "· 보고: 개요, 쟁점·주요 사실, 상급 검토가 물을 만한 포인트\n"
            "· 설명자료: 목적, 법·정책·사실 근거, 추진 방향, 기대 효과·유의사항\n"
            "· 의회·대외: 예상 질의 요지, 답변 초안(간결), 향후 계획\n"
            "· 시뮬레이션 성격: 의회·상급이 물을 만한 질문 1~2개와 답변 방향(짧은 대화체 한 블록 가능)\n\n"
            "--- 토픽·대화 근거 ---\n"
            f"{body}\n"
            "--- 끝 ---\n\n"
            "(상급자 검토란은 비워 두거나 메모만 남긴다.)\n"
        )
    if kind == "memo":
        return (
            "【공문·대외 메모 초안】\n\n"
            "수신:\n발신:\n제목:\n\n"
            "본문:\n"
            f"{body}\n\n"
            "(공문 예절 및 번호·시행일은 기안 시스템에 맞게 조정하세요.)\n"
        )
    if kind == "simulation":
        return compose_document(
            db,
            stream_id=stream_id,
            topic_session_id=topic_session_id,
            kind="report",
            scenario_hint=scenario_hint,
        )
    if kind == "explanation":
        return (
            "【설명자료 초안】\n\n"
            "1. 목적\n"
            "2. 배경 및 근거\n"
            f"{body}\n\n"
            "3. 추진 방향\n"
            "4. 기대 효과 및 유의사항\n"
        )
    if kind == "council":
        return (
            "【의회 답변자료 초안】\n\n"
            "가. 질의 요지\n"
            "나. 답변\n"
            f"{body}\n\n"
            "다. 향후 계획\n"
            "※ 사실관계·법령은 주무과·법무 확인 필수.\n"
        )
    return body
