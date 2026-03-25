import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.auth_deps import get_current_user_id
from app.db.session import get_db
from app.db.models import ConversationStream, TopicSession
from app.schemas.api import DocxExportAuthIn, TopicMergeIn, TopicOut, TopicPatch, TopicSplitIn
from app.security_stream import get_owned_stream, get_owned_topic
from app.services.agent_chains import adapt_plain_draft_to_template, run_document_agent_chain
from app.services.audit_log import audit
from app.services.document_composer import compose_document
from app.services.report_template_extract import extract_template_plaintext
from app.services.model_resolver import resolve_model
from app.services.docx_export import text_to_docx_bytes
from app.services.topic_ops import merge_topics, split_topic_last_messages

router = APIRouter()


def _safe_docx_filename_base(s: str | None) -> str:
    raw = (s or "통합보고서_초안").strip()[:120] or "통합보고서_초안"
    raw = re.sub(r'[\r\n<>:"/\\|?*\x00-\x1f]', "_", raw)
    return raw or "report"

_KINDS = ("report", "memo", "simulation", "explanation", "council")


@router.post("/auth/merge")
def merge_topics_auth(
    body: TopicMergeIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    get_owned_stream(db, body.stream_id, user_id)
    get_owned_topic(db, body.into_topic_id, user_id)
    for tid in body.from_topic_ids:
        get_owned_topic(db, tid, user_id)
    try:
        merge_topics(
            db,
            stream_id=body.stream_id,
            into_topic_id=body.into_topic_id,
            from_topic_ids=body.from_topic_ids,
        )
        audit(db, user_id=user_id, action="topic.merge", detail=body.model_dump())
        db.commit()
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/auth/{topic_id}/split")
def split_topic_auth(
    topic_id: str,
    body: TopicSplitIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    t = get_owned_topic(db, topic_id, user_id)
    try:
        new_id = split_topic_last_messages(
            db,
            stream_id=t.conversation_stream_id,
            from_topic_id=topic_id,
            move_last_n=body.move_last_n,
        )
        audit(
            db,
            user_id=user_id,
            action="topic.split",
            detail={"from": topic_id, "to": new_id, "n": body.move_last_n},
        )
        db.commit()
        return {"status": "ok", "new_topic_id": new_id}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/auth/export-docx")
def export_docx_auth(
    body: DocxExportAuthIn,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """검토 초안 등 마크다운/평문을 Word(.docx)로 내려받기."""
    _ = user_id
    try:
        data = text_to_docx_bytes(body.content, body.title)
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    base = _safe_docx_filename_base(body.filename_base)
    ascii_fallback = base.encode("ascii", "ignore").decode().strip() or "report"
    quoted = quote(f"{base}.docx")
    cd = f'attachment; filename="{ascii_fallback}.docx"; filename*=UTF-8\'\'{quoted}'
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": cd},
    )


@router.get("/auth/stream/{stream_id}", response_model=list[TopicOut])
def list_topics_auth(
    stream_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[TopicSession]:
    get_owned_stream(db, stream_id, user_id)
    return (
        db.query(TopicSession)
        .filter(TopicSession.conversation_stream_id == stream_id)
        .order_by(TopicSession.created_at.asc())
        .all()
    )


@router.patch("/auth/{topic_id}", response_model=TopicOut)
def patch_topic_auth(
    topic_id: str,
    body: TopicPatch,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> TopicSession:
    t = get_owned_topic(db, topic_id, user_id)
    if body.title is not None:
        t.title = body.title
    if body.topic_label is not None:
        t.topic_label = body.topic_label
    db.commit()
    db.refresh(t)
    return t


@router.post("/auth/{topic_id}/compose")
async def compose_topic_doc_auth(
    topic_id: str,
    kind: str = Query("report", description="UI는 report만 사용"),
    full_chain: bool = Query(False, description="writer→reviewer→legal_checker LLM 체인"),
    legal_excerpt: str | None = Query(None),
    scenario_hint: str = Form("", description="비우면 생략. 보고서·전체체인에 반영"),
    template_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    t = get_owned_topic(db, topic_id, user_id)
    if kind not in _KINDS:
        raise HTTPException(400, f"kind must be one of {_KINDS}")
    if kind != "report":
        raise HTTPException(400, "현재 UI 기준으로 kind=report 만 지원합니다.")
    hint = (scenario_hint or "").strip()

    template_plain: str | None = None
    if template_file and (template_file.filename or "").strip():
        try:
            raw = await template_file.read()
        except Exception as e:
            raise HTTPException(400, f"양식 파일 읽기 실패: {e}") from e
        try:
            template_plain = extract_template_plaintext(template_file.filename or "template", raw)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    if full_chain:
        model = resolve_model(db, user_id=user_id, topic_session_id=topic_id, task="report")
        try:
            audit(
                db,
                user_id=user_id,
                action="agent.document_chain.start",
                detail={
                    "topic_id": topic_id,
                    "kind": kind,
                    "has_template": bool(template_plain),
                    "has_scenario_hint": bool(hint),
                },
            )
            db.commit()
            chain = run_document_agent_chain(
                db,
                user_id=user_id,
                model=model,
                stream_id=t.conversation_stream_id,
                topic_id=topic_id,
                kind=kind,
                legal_excerpt=legal_excerpt,
                scenario_hint=hint,
                template_plaintext=template_plain,
            )
            audit(db, user_id=user_id, action="agent.document_chain.done", detail={"topic_id": topic_id})
            db.commit()
            out: dict = {"kind": kind, "full_chain": True, **chain}
            if template_plain:
                out["template_applied"] = True
            return out
        except Exception as e:
            audit(db, user_id=user_id, action="agent.document_chain.fail", detail={"error": str(e)})
            db.commit()
            raise HTTPException(500, str(e)) from e

    text = compose_document(
        db,
        stream_id=t.conversation_stream_id,
        topic_session_id=topic_id,
        kind=kind,
        scenario_hint=hint,
    )
    if template_plain:
        model = resolve_model(db, user_id=user_id, topic_session_id=topic_id, task="report")
        try:
            text = adapt_plain_draft_to_template(
                db,
                user_id=user_id,
                model=model,
                draft_text=text,
                template_plaintext=template_plain,
            )
        except Exception as e:
            raise HTTPException(500, str(e)) from e
        return {"kind": kind, "full_chain": False, "text": text, "template_applied": True}
    return {"kind": kind, "full_chain": False, "text": text}


@router.get("/stream/{stream_id}", response_model=list[TopicOut])
def list_topics(stream_id: str, db: Session = Depends(get_db)) -> list[TopicSession]:
    st = db.get(ConversationStream, stream_id)
    if not st:
        raise HTTPException(404, "stream not found")
    return (
        db.query(TopicSession)
        .filter(TopicSession.conversation_stream_id == stream_id)
        .order_by(TopicSession.created_at.asc())
        .all()
    )


@router.patch("/{topic_id}", response_model=TopicOut)
def patch_topic(topic_id: str, body: TopicPatch, db: Session = Depends(get_db)) -> TopicSession:
    t = db.get(TopicSession, topic_id)
    if not t:
        raise HTTPException(404, "topic not found")
    if body.title is not None:
        t.title = body.title
    if body.topic_label is not None:
        t.topic_label = body.topic_label
    db.commit()
    db.refresh(t)
    return t


@router.post("/{topic_id}/compose")
def compose_topic_doc(
    topic_id: str,
    kind: str = "report",
    db: Session = Depends(get_db),
) -> dict[str, str]:
    t = db.get(TopicSession, topic_id)
    if not t:
        raise HTTPException(404, "topic not found")
    if kind not in _KINDS:
        raise HTTPException(400, f"kind must be one of {_KINDS}")
    text = compose_document(db, stream_id=t.conversation_stream_id, topic_session_id=topic_id, kind=kind)
    return {"kind": kind, "text": text}
