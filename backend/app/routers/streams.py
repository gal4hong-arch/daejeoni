from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_deps import get_current_user_id
from app.db.session import get_db
from app.db.models import ChatMessage, ConversationStream, TopicSession
from app.schemas.api import (
    ChatMessageOut,
    ChatRequest,
    ChatRequestAuth,
    ChatResponse,
    ReviewBootstrapRequestAuth,
    ReviewBootstrapResponse,
    ReviewReporterReplyRequestAuth,
    ReviewReporterReplyResponse,
    ReviewTurnRequestAuth,
    ReviewTurnResponse,
    RoundtableRequestAuth,
    RoundtableResponse,
    RoundtableTurnOut,
    StreamCreate,
    StreamCreateAuth,
    StreamOut,
    UserPromptOut,
)
from app.security_stream import get_owned_stream
from app.services.model_resolver import resolve_dialogue_reporter_reviewer_models, resolve_model
from app.services.orchestrator import process_chat, process_chat_user_only
from app.services.audit_log import audit
from app.services.review_chat import (
    run_review_followup_on_reporter_reply,
    run_reporter_reply_to_reviewer,
    run_review_bootstrap_pair,
    run_review_turn,
)
from app.services.roundtable import ROLE_CATALOG, run_roundtable
from app.services.stream_title import stream_title_from_topic

router = APIRouter()


def _latest_topic_id_for_stream(db: Session, stream_id: str) -> str | None:
    row = (
        db.query(TopicSession)
        .filter(TopicSession.conversation_stream_id == stream_id)
        .order_by(TopicSession.created_at.desc())
        .first()
    )
    return row.id if row else None


def _prompt_preview(content: str, max_len: int = 160) -> str:
    line = (content or "").split("\n", 1)[0].strip()
    line = " ".join(line.split())
    if len(line) > max_len:
        return line[: max_len - 1].rstrip() + "…"
    return line


# ----- 인증 필수 (멀티유저) -----
@router.post("/auth", response_model=StreamOut)
def create_stream_auth(
    body: StreamCreateAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ConversationStream:
    s = ConversationStream(user_id=user_id, title=body.title or "새 대화")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/auth", response_model=list[StreamOut])
def list_streams_auth(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[ConversationStream]:
    return (
        db.query(ConversationStream)
        .filter(ConversationStream.user_id == user_id)
        .order_by(ConversationStream.created_at.desc())
        .all()
    )


@router.get("/auth/my-prompts", response_model=list[UserPromptOut])
def list_my_prompts_auth(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    limit: int = 120,
) -> list[UserPromptOut]:
    lim = max(1, min(limit, 300))
    rows = db.execute(
        select(ChatMessage, ConversationStream.title)
        .join(ConversationStream, ChatMessage.conversation_stream_id == ConversationStream.id)
        .where(ConversationStream.user_id == user_id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .limit(lim)
    ).all()
    out: list[UserPromptOut] = []
    for msg, stream_title in rows:
        out.append(
            UserPromptOut(
                stream_id=msg.conversation_stream_id,
                stream_title=(stream_title or "")[:512],
                message_id=msg.id,
                preview=_prompt_preview(msg.content),
                created_at=msg.created_at,
            )
        )
    return out


@router.get("/auth/{stream_id}", response_model=StreamOut)
def get_stream_auth(
    stream_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ConversationStream:
    return get_owned_stream(db, stream_id, user_id)


@router.delete("/auth/{stream_id}", status_code=204)
def delete_stream_auth(
    stream_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> None:
    s = get_owned_stream(db, stream_id, user_id)
    db.delete(s)
    db.commit()


@router.get("/auth/{stream_id}/messages", response_model=list[ChatMessageOut])
def list_messages_auth(
    stream_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[ChatMessage]:
    get_owned_stream(db, stream_id, user_id)
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_stream_id == stream_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


@router.post("/auth/{stream_id}/messages", response_model=ChatResponse)
def post_message_auth(
    stream_id: str,
    body: ChatRequestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ChatResponse:
    stream = get_owned_stream(db, stream_id, user_id)
    prior_n = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_stream_id == stream_id)
        .count()
    )
    try:
        if body.skip_assistant:
            out = process_chat_user_only(
                db,
                stream_id=stream_id,
                user_id=user_id,
                content=body.content,
                use_legal=body.use_legal,
                task=body.task,
            )
        else:
            out = process_chat(
                db,
                stream_id=stream_id,
                user_id=user_id,
                content=body.content,
                use_legal=body.use_legal,
                task=body.task,
                document_ids=body.document_ids,
            )
        lat = ((out.chat_trace or {}).get("latency_ms") or {}) if out.chat_trace else {}
        if lat:
            audit(
                db,
                user_id=user_id,
                action="chat.turn.latency",
                detail={
                    "stream_id": stream_id,
                    "intent": out.intent,
                    "model_used": out.model_used,
                    "total_ms": lat.get("total_ms"),
                    "llm_answer_ms": lat.get("llm_answer_ms"),
                    "rag_ms": lat.get("rag_ms"),
                    "legal_fetch_ms": lat.get("legal_fetch_ms"),
                    "topic_classify_ms": lat.get("topic_classify_ms"),
                },
            )
        if prior_n == 0:
            stream.title = stream_title_from_topic(out.detected_topic, body.content)
        db.commit()
        return out
    except Exception:
        db.rollback()
        raise


@router.post("/auth/{stream_id}/roundtable", response_model=RoundtableResponse)
def post_roundtable_auth(
    stream_id: str,
    body: RoundtableRequestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> RoundtableResponse:
    get_owned_stream(db, stream_id, user_id)
    topic_id = _latest_topic_id_for_stream(db, stream_id)
    model = resolve_model(db, user_id=user_id, topic_session_id=topic_id, task="chat")
    turns_raw, answer_text = run_roundtable(
        db,
        user_id=user_id,
        model=model,
        premise=body.premise,
        roles=body.roles,
    )
    if not (answer_text or "").strip():
        raise HTTPException(
            status_code=502,
            detail="역할 토의 응답이 비었습니다. LLM API 키·모델을 확인하세요.",
        )
    roles_ko = " · ".join(ROLE_CATALOG[r][0] for r in body.roles)
    db.add(
        ChatMessage(
            conversation_stream_id=stream_id,
            role="user",
            content=f"【역할 토의 요청】 ({roles_ko})\n\n{body.premise.strip()[:8000]}",
        )
    )
    db.add(
        ChatMessage(
            conversation_stream_id=stream_id,
            role="assistant",
            content=answer_text[:200000],
        )
    )
    db.commit()
    return RoundtableResponse(
        answer=answer_text,
        turns=[RoundtableTurnOut(**t) for t in turns_raw],
        model_used=model,
    )


@router.post("/auth/{stream_id}/review-bootstrap", response_model=ReviewBootstrapResponse)
def post_review_bootstrap_auth(
    stream_id: str,
    body: ReviewBootstrapRequestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ReviewBootstrapResponse:
    """검토 패널 켤 때: 대화 요약 보고 → 검토자가 보고 기반 검토 (메시지 DB 미저장)."""
    get_owned_stream(db, stream_id, user_id)
    topic_id = _latest_topic_id_for_stream(db, stream_id)
    reporter_model, reviewer_model = resolve_dialogue_reporter_reviewer_models(
        db, user_id=user_id, topic_session_id=topic_id
    )
    override = body.system_prompt_override
    if override is not None:
        override = override.strip() or None
    try:
        report, review, rep_used, rev_used = run_review_bootstrap_pair(
            db,
            user_id=user_id,
            reporter_model=reporter_model,
            reviewer_model=reviewer_model,
            stream_id=stream_id,
            role_id=body.role_id,
            system_prompt_override=override,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not review.strip():
        raise HTTPException(status_code=502, detail="검토 응답이 비었습니다.")
    return ReviewBootstrapResponse(
        report=report,
        review=review,
        model_used=f"보고자 {rep_used} | 검토자 {rev_used}",
        reporter_model_used=rep_used,
        reviewer_model_used=rev_used,
    )


@router.post("/auth/{stream_id}/review-reporter-reply", response_model=ReviewReporterReplyResponse)
def post_review_reporter_reply_auth(
    stream_id: str,
    body: ReviewReporterReplyRequestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ReviewReporterReplyResponse:
    """보고자가 검토자 최신 의견에 한 번 답하는 초안 (DB 미저장)."""
    get_owned_stream(db, stream_id, user_id)
    topic_id = _latest_topic_id_for_stream(db, stream_id)
    reporter_model, _ = resolve_dialogue_reporter_reviewer_models(
        db, user_id=user_id, topic_session_id=topic_id
    )
    brief = body.reporter_brief
    if brief is not None:
        brief = brief.strip() or None
    try:
        reply, reply_model_used = run_reporter_reply_to_reviewer(
            db,
            user_id=user_id,
            model=reporter_model,
            stream_id=stream_id,
            reporter_brief=brief,
            reviewer_opinion=(body.reviewer_opinion or "").strip(),
            composer_prompt=body.composer_prompt,
            prior_reporter_replies=body.prior_reporter_replies,
            prior_reviewer_opinions=body.prior_reviewer_opinions,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not (reply or "").strip():
        raise HTTPException(status_code=502, detail="보고자 답변이 비었습니다.")
    return ReviewReporterReplyResponse(reply=reply.strip(), model_used=reply_model_used)


@router.post("/auth/{stream_id}/review-turn", response_model=ReviewTurnResponse)
def post_review_turn_auth(
    stream_id: str,
    body: ReviewTurnRequestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ReviewTurnResponse:
    """reporter_brief 있으면 최근 1턴 검토, 없으면 트랜스크립트 전체 검토 (DB 미저장)."""
    get_owned_stream(db, stream_id, user_id)
    topic_id = _latest_topic_id_for_stream(db, stream_id)
    reporter_model, model = resolve_dialogue_reporter_reviewer_models(
        db, user_id=user_id, topic_session_id=topic_id
    )
    override = body.system_prompt_override
    if override is not None:
        override = override.strip() or None
    rb = body.reporter_brief
    if rb is not None:
        rb = rb.strip() or None
    prv = body.prior_reviewer_opinion
    if prv is not None:
        prv = prv.strip() or None
    rpf = body.reporter_reply_followup
    if rpf is not None:
        rpf = rpf.strip() or None
    try:
        if rpf:
            answer, review_model_used = run_review_followup_on_reporter_reply(
                db,
                user_id=user_id,
                model=model,
                reporter_model=reporter_model,
                stream_id=stream_id,
                role_id=body.role_id,
                reporter_brief=rb,
                prior_reviewer_opinion=prv or "",
                reporter_reply=rpf,
                system_prompt_override=override,
                prior_reporter_replies=body.prior_reporter_replies,
                prior_reviewer_opinions=body.prior_reviewer_opinions,
                composer_prompt=body.composer_prompt,
            )
        else:
            answer, review_model_used = run_review_turn(
                db,
                user_id=user_id,
                model=model,
                reporter_model=reporter_model,
                stream_id=stream_id,
                role_id=body.role_id,
                system_prompt_override=override,
                reporter_brief=rb,
                prior_reviewer_opinions=body.prior_reviewer_opinions,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not (answer or "").strip():
        raise HTTPException(
            status_code=502,
            detail="검토 응답이 비었습니다. LLM API 키·모델을 확인하세요.",
        )
    return ReviewTurnResponse(answer=answer.strip(), model_used=review_model_used)


# ----- 레거시(데모/스크립트): user_id 바디 -----
@router.post("", response_model=StreamOut)
def create_stream(body: StreamCreate, db: Session = Depends(get_db)) -> ConversationStream:
    s = ConversationStream(user_id=body.user_id, title=body.title or "대화")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/{stream_id}", response_model=StreamOut)
def get_stream(stream_id: str, db: Session = Depends(get_db)) -> ConversationStream:
    s = db.get(ConversationStream, stream_id)
    if not s:
        raise HTTPException(404, "stream not found")
    return s


@router.post("/{stream_id}/messages", response_model=ChatResponse)
def post_message(stream_id: str, body: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    s = db.get(ConversationStream, stream_id)
    if not s:
        raise HTTPException(404, "stream not found")
    if s.user_id != body.user_id:
        raise HTTPException(403, "user_id does not own this stream")
    prior_n = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_stream_id == stream_id)
        .count()
    )
    try:
        out = process_chat(
            db,
            stream_id=stream_id,
            user_id=body.user_id,
            content=body.content,
            use_legal=body.use_legal,
            task=body.task,
            document_ids=body.document_ids,
        )
        lat = ((out.chat_trace or {}).get("latency_ms") or {}) if out.chat_trace else {}
        if lat:
            audit(
                db,
                user_id=body.user_id,
                action="chat.turn.latency",
                detail={
                    "stream_id": stream_id,
                    "intent": out.intent,
                    "model_used": out.model_used,
                    "total_ms": lat.get("total_ms"),
                    "llm_answer_ms": lat.get("llm_answer_ms"),
                    "rag_ms": lat.get("rag_ms"),
                    "legal_fetch_ms": lat.get("legal_fetch_ms"),
                    "topic_classify_ms": lat.get("topic_classify_ms"),
                },
            )
        if prior_n == 0:
            s.title = stream_title_from_topic(out.detected_topic, body.content)
        db.commit()
        return out
    except Exception:
        db.rollback()
        raise
