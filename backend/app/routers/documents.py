import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.auth_deps import get_current_user_id, get_current_user_profile
from app.db.session import get_db
from app.db.models import KbChunk, KbDocument
from app.schemas.api import (
    ChunkIngest,
    ChunkIngestAuth,
    KbDocumentOut,
    KbDocumentPatchAuth,
    LawIngestAuth,
    UrlIngestAuth,
    UserLawStatOut,
)
from app.services.audit_log import audit
from app.services.embeddings import embed_text, embedding_to_json
from app.services.law_rag_ingest import ingest_law_to_rag
from app.services.file_rag_ingest import ingest_uploaded_file_to_rag
from app.services.pdf_rag_ingest import ingest_pdf_to_rag
from app.services.rag_admin import is_rag_admin_email
from app.services.web_rag_ingest import ingest_url_to_rag
from app.services.law_user_stats import list_law_popularity

router = APIRouter(prefix="/documents")


@router.get("/auth/law-popularity", response_model=list[UserLawStatOut])
def list_law_popularity_auth(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    limit: int = 80,
) -> list[UserLawStatOut]:
    rows = list_law_popularity(db, user_id, limit=limit)
    return [UserLawStatOut.model_validate(r) for r in rows]


@router.post("/auth/law-ingest", status_code=201)
def ingest_law_rag_auth(
    body: LawIngestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    profile: dict = Depends(get_current_user_profile),
) -> dict[str, str | int]:
    try:
        out = ingest_law_to_rag(
            db,
            user_id=user_id,
            law_id=body.law_id,
            law_title_hint=body.law_title or "",
            shared_globally=is_rag_admin_email(profile.get("email")),
        )
        db.commit()
        return out
    except ValueError as e:
        db.rollback()
        msg = str(e)
        if msg == "already_embedded":
            raise HTTPException(status_code=409, detail=msg) from e
        raise HTTPException(status_code=400, detail=msg) from e


@router.get("/auth/list", response_model=list[KbDocumentOut])
def list_documents_auth(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[KbDocumentOut]:
    docs = db.execute(
        select(KbDocument).where(
            or_(KbDocument.user_id == user_id, KbDocument.shared_globally.is_(True))
        )
    ).scalars().all()
    result: list[KbDocumentOut] = []
    for d in docs:
        n = (
            db.execute(select(func.count()).select_from(KbChunk).where(KbChunk.document_id == d.id)).scalar()
            or 0
        )
        shared = bool(getattr(d, "shared_globally", False))
        result.append(
            KbDocumentOut(
                id=d.id,
                title=d.title,
                chunk_count=int(n),
                source_kind=getattr(d, "source_kind", None) or "manual",
                source_url=getattr(d, "source_url", None),
                shared_globally=shared,
                is_owner=d.user_id == user_id,
            )
        )
    return result


@router.patch("/auth/{document_id}")
def patch_document_auth(
    document_id: str,
    body: KbDocumentPatchAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> KbDocumentOut:
    d = db.get(KbDocument, document_id)
    if not d:
        raise HTTPException(status_code=404, detail="document not found")
    if d.user_id != user_id:
        raise HTTPException(status_code=403, detail="본인이 등록한 문서만 수정할 수 있습니다.")
    d.title = body.title.strip()[:512]
    audit(
        db,
        user_id=user_id,
        action="rag.document_patch",
        detail={"document_id": document_id, "title": d.title[:240]},
    )
    db.commit()
    db.refresh(d)
    n = (
        db.execute(select(func.count()).select_from(KbChunk).where(KbChunk.document_id == d.id)).scalar()
        or 0
    )
    return KbDocumentOut(
        id=d.id,
        title=d.title,
        chunk_count=int(n),
        source_kind=getattr(d, "source_kind", None) or "manual",
        source_url=getattr(d, "source_url", None),
        shared_globally=bool(getattr(d, "shared_globally", False)),
        is_owner=True,
    )


@router.delete("/auth/{document_id}", status_code=204)
def delete_document_auth(
    document_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> None:
    d = db.get(KbDocument, document_id)
    if not d:
        raise HTTPException(status_code=404, detail="document not found")
    if d.user_id != user_id:
        raise HTTPException(status_code=403, detail="본인이 등록한 문서만 삭제할 수 있습니다.")
    db.execute(delete(KbChunk).where(KbChunk.document_id == d.id))
    db.delete(d)
    audit(
        db,
        user_id=user_id,
        action="rag.document_delete",
        detail={"document_id": document_id},
    )
    db.commit()


@router.post("/auth/pdf-ingest", status_code=201)
async def ingest_pdf_rag_auth(
    file: UploadFile = File(...),
    source_title: str = Form(""),
    extract_mode: str = Form("text_only"),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    profile: dict = Depends(get_current_user_profile),
) -> dict[str, str | int]:
    name = (file.filename or "").strip()
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일(.pdf)만 업로드할 수 있습니다.")
    try:
        data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 읽기 실패: {e}") from e
    em = (extract_mode or "hybrid").strip().lower()
    if em not in ("hybrid", "text_only"):
        raise HTTPException(
            status_code=400,
            detail="extract_mode는 hybrid(텍스트+OCR) 또는 text_only(텍스트만)만 허용됩니다.",
        )
    try:
        out = ingest_pdf_to_rag(
            db,
            user_id=user_id,
            pdf_bytes=data,
            filename=name,
            source_title=(source_title or "").strip(),
            extract_mode=em,
            shared_globally=is_rag_admin_email(profile.get("email")),
        )
        db.commit()
        return out
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/auth/file-ingest", status_code=201)
async def ingest_file_rag_auth(
    file: UploadFile = File(...),
    extract_mode: str = Form("text_only"),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    profile: dict = Depends(get_current_user_profile),
) -> dict[str, str | int]:
    name = (file.filename or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="파일 이름이 없습니다.")
    try:
        data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 읽기 실패: {e}") from e
    em = (extract_mode or "text_only").strip().lower()
    if em not in ("hybrid", "text_only"):
        raise HTTPException(
            status_code=400,
            detail="extract_mode는 hybrid(텍스트+OCR) 또는 text_only(텍스트만)만 허용됩니다.",
        )
    try:
        out = ingest_uploaded_file_to_rag(
            db,
            user_id=user_id,
            filename=name,
            data=data,
            extract_mode=em,
            shared_globally=is_rag_admin_email(profile.get("email")),
        )
        db.commit()
        return out
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/auth/url-ingest", status_code=201)
def ingest_url_rag_auth(
    body: UrlIngestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    profile: dict = Depends(get_current_user_profile),
) -> dict[str, str | int]:
    try:
        out = ingest_url_to_rag(
            db,
            user_id=user_id,
            url=body.url.strip(),
            source_title=body.source_title.strip(),
            shared_globally=is_rag_admin_email(profile.get("email")),
        )
        db.commit()
        return out
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"URL 요청 실패: {e}") from e


@router.post("/chunks/auth", status_code=201)
def ingest_chunk_auth(
    body: ChunkIngestAuth,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    profile: dict = Depends(get_current_user_profile),
) -> dict[str, str]:
    doc_id = body.document_id
    share = is_rag_admin_email(profile.get("email"))
    if not doc_id:
        d = KbDocument(
            user_id=user_id,
            title=body.source_title or "문서",
            source_kind="manual",
            shared_globally=share,
        )
        db.add(d)
        db.flush()
        doc_id = d.id
    else:
        existing = db.get(KbDocument, doc_id)
        if not existing or existing.user_id != user_id:
            raise HTTPException(status_code=404, detail="document not found")

    c = KbChunk(
        user_id=user_id,
        document_id=doc_id,
        source_title=body.source_title,
        content=body.content,
        topic_session_id=body.topic_session_id,
    )
    vec = embed_text(db, user_id, body.content)
    c.embedding_json = embedding_to_json(vec)
    db.add(c)
    db.commit()
    db.refresh(c)
    audit(db, user_id=user_id, action="rag.ingest", detail={"document_id": doc_id, "chunk_id": c.id})
    db.commit()
    return {"id": c.id, "document_id": doc_id, "status": "stored"}


@router.post("/chunks", status_code=201)
def ingest_chunk(body: ChunkIngest, db: Session = Depends(get_db)) -> dict[str, str]:
    d = KbDocument(user_id=body.user_id, title=body.source_title or "문서", source_kind="manual")
    db.add(d)
    db.flush()
    doc_id = d.id
    c = KbChunk(
        user_id=body.user_id,
        document_id=doc_id,
        source_title=body.source_title,
        content=body.content,
        topic_session_id=body.topic_session_id,
    )
    vec = embed_text(db, body.user_id, body.content)
    c.embedding_json = embedding_to_json(vec)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "document_id": doc_id, "status": "stored"}
