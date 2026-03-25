"""국가법령정보 본문 조회 후 kb_documents / kb_chunks 로 임베딩 저장."""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import KbChunk, KbDocument, UserLawStat
from app.services.audit_log import audit
from app.services.embeddings import embed_text, embedding_to_json
from app.services.law_go_kr.statute_body import fetch_statute_service_body
from app.services.law_go_kr.constants import DEFAULT_HEADERS
from app.services.law_go_kr.parse import (
    law_service_basic_meta_ids,
    law_service_json_body_plain,
    response_to_llm_text,
)


def _split_text(text: str, *, max_len: int = 5500, overlap: int = 400) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    out: list[str] = []
    i = 0
    n = len(t)
    while i < n:
        piece = t[i : i + max_len]
        out.append(piece)
        if i + max_len >= n:
            break
        i += max_len - overlap
    return out


def ingest_law_to_rag(
    db: Session,
    *,
    user_id: str,
    law_id: str,
    law_title_hint: str = "",
    shared_globally: bool = False,
) -> dict[str, str | int]:
    settings = get_settings()
    oc = (getattr(settings, "law_go_kr_oc", None) or "").strip()
    service_url = (getattr(settings, "law_go_kr_service_url", None) or "").strip().rstrip("/")
    stype = (getattr(settings, "law_go_kr_service_type", None) or "JSON").strip().upper() or "JSON"
    timeout = float(getattr(settings, "law_go_kr_timeout", None) or 25.0)

    if not oc:
        raise ValueError("LAW_GO_KR_OC 미설정")
    if not service_url:
        raise ValueError("LAW_GO_KR_SERVICE_URL 미설정")

    lid = law_id.strip()
    if not lid:
        raise ValueError("law_id 없음")

    stat = (
        db.execute(select(UserLawStat).where(UserLawStat.user_id == user_id, UserLawStat.law_id == lid))
        .scalar_one_or_none()
    )
    if stat and stat.rag_document_id:
        existing = db.get(KbDocument, stat.rag_document_id)
        if existing and existing.user_id == user_id:
            raise ValueError("already_embedded")
        if not existing:
            stat.rag_document_id = None

    title_base = (law_title_hint or (stat.law_title if stat else "") or f"법령 {lid}").strip()
    title_base = re.sub(r"\s+", " ", title_base)[:400]

    body_primary = (getattr(settings, "law_go_kr_body_target", None) or "eflaw").strip() or "eflaw"
    body_fallback = (getattr(settings, "law_go_kr_body_target_fallback", None) or "law").strip() or "law"
    with httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        st, _body, svc_data, _eff, _svc_url = fetch_statute_service_body(
            client,
            service_url=service_url,
            oc=oc,
            law_id=lid,
            response_type=stype,
            primary=body_primary,
            fallback=body_fallback,
        )
    if st != 200 or svc_data is None:
        raise ValueError(f"lawService HTTP {st}")

    raw_text = law_service_json_body_plain(svc_data, max_chars=120_000)
    if len(raw_text.strip()) < 500:
        raw_text = response_to_llm_text(svc_data, max_chars=120_000)
    pieces = _split_text(raw_text, max_len=5500, overlap=400)
    if not pieces:
        raise ValueError("본문 텍스트가 비었습니다")

    api_법령id, api_mst = law_service_basic_meta_ids(svc_data)
    svc_id = (api_법령id or lid).strip()
    id_part = f"본문API·법령ID={svc_id}"
    mst_part = f"일련번호(MST)={api_mst}" if api_mst else ""
    if mst_part:
        doc_title = f"[법령RAG] {title_base} · {id_part} · {mst_part}"[:512]
    else:
        doc_title = f"[법령RAG] {title_base} · {id_part}"[:512]
    # RAG·로그에서 혼동 방지: machine-readable (검색 조문의 법령일련번호 ≠ 본문 법령ID 일 수 있음)
    src_url = f"lawgo_rag:법령ID={svc_id}"
    if api_mst:
        src_url += f";MST={api_mst}"
    d = KbDocument(
        user_id=user_id,
        title=doc_title,
        source_kind="law",
        source_url=src_url[:2048],
        shared_globally=shared_globally,
    )
    db.add(d)
    db.flush()

    n_ok = 0
    for i, piece in enumerate(pieces):
        c = KbChunk(
            user_id=user_id,
            document_id=d.id,
            source_title=f"{doc_title[:240]} · Part {i + 1}/{len(pieces)}",
            content=piece,
            topic_session_id=None,
        )
        vec = embed_text(db, user_id, piece)
        c.embedding_json = embedding_to_json(vec)
        db.add(c)
        if vec:
            n_ok += 1

    if stat:
        stat.rag_document_id = d.id
        stat.law_title = stat.law_title or title_base[:512]
    else:
        db.add(
            UserLawStat(
                user_id=user_id,
                law_id=lid,
                law_title=title_base[:512],
                hit_count=0,
                last_access_at=datetime.utcnow(),
                rag_document_id=d.id,
            )
        )

    audit(
        db,
        user_id=user_id,
        action="rag.law_ingest",
        detail={
            "요청_ID_파라미터": lid,
            "본문API_법령ID": svc_id,
            "법령일련번호_MST": api_mst or None,
            "document_id": d.id,
            "chunks": len(pieces),
            "embedded_chunks": n_ok,
        },
    )
    return {
        "document_id": d.id,
        "chunks": len(pieces),
        "embedded_chunks": n_ok,
        "law_service_법령ID": svc_id,
        "law_일련번호_MST": api_mst or None,
    }
