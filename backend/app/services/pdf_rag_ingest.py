"""PDF 바이트 → 텍스트 레이어 추출 + (선택) 페이지 래스터 OCR → kb_chunks 임베딩.

- ``hybrid`` (기본): 텍스트 레이어 + 조건부 OCR을 페이지마다 병합한 뒤 청크·임베딩.
- ``text_only``: OCR 없이 ``get_text`` 만 사용. 페이지를 묶어(batch) 추출·분할해
  한 번에 전체 본문 문자열을 만들지 않도록 처리(대용량 대응).

**용량·페이지 제한 이유**

- 업로드 바이트 상한: 환경 변수 ``RAG_PDF_MAX_BYTES``(기본 약 48MB, 8~200MB 클램프).
  리버스 프록시 ``client_max_body_size`` 등과 맞출 것.
- 본문이 길면 **문서 개요 청크**(`RAG_OUTLINE_CHUNK`, 기본 켜짐)를 본문 청크와 함께 임베딩해 검색 누락을 줄임.
- ``MAX_PAGES`` 등: OCR·페이지 래스터 비용이 커서 과도한 문서로 서버가 막히지 않게 함.

**멀티프로세서**

- 청크 분할·임베딩은 이미 페이지/배치 단위로 나뉨. CPU 바운드 구간(예: OCR)만
  프로세스 풀로 병렬화할 수는 있으나, DB 세션·트랜잭션은 **프로세스마다 분리**해야 하고
  결과를 한 문서 ID 아래로 취합하는 추가 설계가 필요하다. 동일 세션을 여러 프로세스에서
  공유하면 안 된다.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import fitz  # pymupdf
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import KbChunk, KbDocument
from app.config import get_settings
from app.services.audit_log import audit
from app.services.embeddings import embed_text, embedding_to_json
from app.services.rag_document_summary import build_rag_outline_text, persist_outline_chunk
from app.services.web_rag_ingest import _split_text

# 테스트·폴백용(설정 `RAG_PDF_MAX_BYTES` 우선)
MAX_PDF_BYTES = 48 * 1024 * 1024
MAX_PAGES = 150
# 텍스트만 추출 시 OCR 비용이 없어 페이지 한도를 넉넉히 둠.
MAX_PAGES_TEXT_ONLY = 700
# 텍스트 전용: 이 쪽수마다 추출 → 분할 → 임베딩(거대 단일 문자열 방지).
TEXT_ONLY_PAGE_BATCH = 28
# 이 페이지 수 이하이면 모든 페이지에 OCR 시도(스캔 PDF 대응). 초과 시 텍스트가 얇은 페이지만 OCR.
MAX_PAGES_FULL_OCR = 40
THIN_TEXT_CHARS = 90

_rapid_ocr_engine: Any = None


def _try_pdf_outline(
    db: Session,
    *,
    user_id: str,
    doc: KbDocument,
    doc_title: str,
    excerpt: str,
) -> tuple[int, int]:
    """(추가된 개요 청크 수 0~1, 임베딩 성공 시 1)."""
    body = build_rag_outline_text(db, user_id=user_id, doc_title=doc_title, excerpt=excerpt)
    if not body:
        return 0, 0
    persisted, ok = persist_outline_chunk(
        db,
        user_id=user_id,
        document_id=doc.id,
        doc_title=doc_title,
        outline_body=body,
    )
    if not persisted:
        return 0, 0
    return 1, 1 if ok else 0


def _get_rapid_ocr():
    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _rapid_ocr_engine = RapidOCR()
    return _rapid_ocr_engine


def _merge_raw_and_ocr(raw: str, ocr: str) -> str:
    raw = (raw or "").strip()
    ocr = (ocr or "").strip()
    if not ocr:
        return raw
    if not raw:
        return ocr
    if len(raw) < THIN_TEXT_CHARS:
        if raw:
            return (raw + "\n\n[OCR 보강]\n" + ocr).strip()
        return ocr
    compact = lambda s: re.sub(r"\s+", "", s)[:800]
    if compact(ocr) in compact(raw):
        return raw
    return (raw + "\n\n[이미지·스캔 OCR 보충]\n" + ocr).strip()


def ocr_image_bytes(image_bytes: bytes) -> str:
    """일반 이미지 바이트(PNG·JPEG 등)에서 RapidOCR 텍스트 추출."""
    if not image_bytes:
        return ""
    try:
        import io

        import numpy as np
        from PIL import Image

        pil = Image.open(io.BytesIO(image_bytes))
        if pil.mode not in ("RGB", "L"):
            pil = pil.convert("RGB")
        img = np.array(pil)
        res, _elapsed = _get_rapid_ocr()(img)
        if not res:
            return ""
        lines: list[str] = []
        for item in res:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                t = item[1]
                if isinstance(t, str) and t.strip():
                    lines.append(t.strip())
        return "\n".join(lines)
    except Exception:
        return ""


def _ocr_page_raster(page: fitz.Page, *, scale: float = 1.75) -> str:
    try:
        import numpy as np
    except Exception:
        return ""
    try:
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        h, w, n = pix.height, pix.width, pix.n
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(h, w, n)
        if n == 1:
            img = np.concatenate([img, img, img], axis=-1)
        elif n == 4:
            img = img[:, :, :3]
        res, _elapsed = _get_rapid_ocr()(img)
        if not res:
            return ""
        lines: list[str] = []
        for item in res:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                t = item[1]
                if isinstance(t, str) and t.strip():
                    lines.append(t.strip())
        return "\n".join(lines)
    except Exception:
        return ""


def extract_pdf_text_hybrid(pdf_bytes: bytes) -> tuple[str, dict[str, Any]]:
    """텍스트 레이어 + 조건부 OCR. 전체 본문을 한 문자열로 반환."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    meta: dict[str, Any] = {
        "pages": doc.page_count,
        "ocr_page_count": 0,
        "extract_mode": "hybrid",
    }
    try:
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"PDF 페이지가 너무 많습니다(최대 {MAX_PAGES}페이지).")
        parts: list[str] = []
        full_ocr = doc.page_count <= MAX_PAGES_FULL_OCR
        for i in range(doc.page_count):
            page = doc[i]
            raw = page.get_text("text").strip()
            need_ocr = full_ocr or len(raw) < THIN_TEXT_CHARS
            ocr = _ocr_page_raster(page) if need_ocr else ""
            if ocr.strip():
                meta["ocr_page_count"] += 1
            merged = _merge_raw_and_ocr(raw, ocr)
            if merged:
                parts.append(f"--- 페이지 {i + 1} ---\n{merged}")
        return "\n\n".join(parts).strip(), meta
    finally:
        doc.close()


def _pdf_text_only_page_range(doc: fitz.Document, start: int, end: int) -> str:
    """[start, end) 구간 페이지의 텍스트 레이어만 이어붙임(OCR 없음)."""
    parts: list[str] = []
    for i in range(start, min(end, doc.page_count)):
        raw = doc[i].get_text("text").strip()
        if raw:
            parts.append(f"--- 페이지 {i + 1} ---\n{raw}")
    return "\n\n".join(parts).strip()


def ingest_pdf_to_rag(
    db: Session,
    *,
    user_id: str,
    pdf_bytes: bytes,
    filename: str,
    source_title: str = "",
    extract_mode: str = "hybrid",
    shared_globally: bool = False,
) -> dict[str, str | int]:
    max_b = int(getattr(get_settings(), "rag_pdf_max_bytes", MAX_PDF_BYTES) or MAX_PDF_BYTES)
    if len(pdf_bytes) > max_b:
        raise ValueError(f"PDF 파일이 너무 큽니다(약 {max_b // (1024 * 1024)}MB 이하, RAG_PDF_MAX_BYTES로 조정 가능).")

    if extract_mode not in ("hybrid", "text_only"):
        raise ValueError("extract_mode는 hybrid 또는 text_only 여야 합니다.")
    mode = extract_mode

    fingerprint = "pdf:" + hashlib.sha256(pdf_bytes).hexdigest()
    base_title = (source_title or "").strip()[:512] or (filename or "문서.pdf")[:512]

    existing = (
        db.execute(
            select(KbDocument).where(
                KbDocument.user_id == user_id,
                KbDocument.source_kind == "pdf",
                KbDocument.source_url == fingerprint,
            )
        )
        .scalar_one_or_none()
    )
    if existing:
        db.execute(delete(KbChunk).where(KbChunk.document_id == existing.id))
        doc = existing
        doc.title = base_title or doc.title
        doc.shared_globally = shared_globally
    else:
        doc = KbDocument(
            user_id=user_id,
            title=base_title,
            source_kind="pdf",
            source_url=fingerprint,
            shared_globally=shared_globally,
        )
        db.add(doc)
    db.flush()

    doc_title = doc.title or "PDF"
    n_ok = 0
    outline_chunks = 0
    extract_meta: dict[str, Any]

    if mode == "text_only":
        fdoc = fitz.open(stream=pdf_bytes, filetype="pdf")
        extract_meta = {
            "pages": fdoc.page_count,
            "ocr_page_count": 0,
            "extract_mode": "text_only",
            "text_only_page_batch": TEXT_ONLY_PAGE_BATCH,
        }
        try:
            if fdoc.page_count > MAX_PAGES_TEXT_ONLY:
                raise ValueError(
                    f"텍스트만 추출 모드에서 PDF 페이지가 너무 많습니다(최대 {MAX_PAGES_TEXT_ONLY}페이지)."
                )
            total_chars = 0
            chunk_seq = 0
            summary_excerpt = ""
            outline_done = False
            for batch_start in range(0, fdoc.page_count, TEXT_ONLY_PAGE_BATCH):
                batch_end = min(batch_start + TEXT_ONLY_PAGE_BATCH, fdoc.page_count)
                batch_text = _pdf_text_only_page_range(fdoc, batch_start, batch_end)
                if batch_text.strip() and not summary_excerpt:
                    summary_excerpt = batch_text[:16000]
                if not outline_done and summary_excerpt:
                    oa, oe = _try_pdf_outline(
                        db,
                        user_id=user_id,
                        doc=doc,
                        doc_title=doc_title,
                        excerpt=summary_excerpt,
                    )
                    outline_chunks += oa
                    n_ok += oe
                    outline_done = True
                if not batch_text:
                    continue
                total_chars += len(batch_text)
                pieces = _split_text(batch_text, max_len=5500, overlap=400)
                for piece in pieces:
                    chunk_seq += 1
                    c = KbChunk(
                        user_id=user_id,
                        document_id=doc.id,
                        source_title=(
                            f"{doc_title[:200]} · 텍스트추출 · "
                            f"p{batch_start + 1}-{batch_end} · #{chunk_seq}"
                        ),
                        content=piece,
                        topic_session_id=None,
                    )
                    vec = embed_text(db, user_id, piece)
                    c.embedding_json = embedding_to_json(vec)
                    if vec:
                        n_ok += 1
                    db.add(c)
        finally:
            fdoc.close()

        if total_chars < 40 or chunk_seq == 0:
            raise ValueError(
                "PDF에서 추출된 텍스트가 거의 없습니다. 암호화·손상·이미지 전용 PDF이거나 빈 문서일 수 있습니다."
            )
        total_chunks = chunk_seq + outline_chunks
    else:
        text, extract_meta = extract_pdf_text_hybrid(pdf_bytes)
        if len(text) < 40:
            raise ValueError(
                "PDF에서 추출된 텍스트가 거의 없습니다. 암호화·손상 파일이거나 빈 문서일 수 있습니다."
            )

        oa, oe = _try_pdf_outline(
            db,
            user_id=user_id,
            doc=doc,
            doc_title=doc_title,
            excerpt=text[:16000],
        )
        outline_chunks += oa
        n_ok += oe

        pieces = _split_text(text, max_len=5500, overlap=400)
        if not pieces:
            raise ValueError("분할할 본문이 없습니다.")
        total_chunks = len(pieces) + outline_chunks
        for i, piece in enumerate(pieces):
            c = KbChunk(
                user_id=user_id,
                document_id=doc.id,
                source_title=f"{doc_title[:220]} · Part {i + 1}/{len(pieces)}",
                content=piece,
                topic_session_id=None,
            )
            vec = embed_text(db, user_id, piece)
            c.embedding_json = embedding_to_json(vec)
            if vec:
                n_ok += 1
            db.add(c)

    audit(
        db,
        user_id=user_id,
        action="rag.pdf_ingest",
        detail={
            "document_id": doc.id,
            "filename": (filename or "")[:240],
            "chunks": total_chunks,
            "embedded_chunks": n_ok,
            "outline_chunks": outline_chunks,
            **extract_meta,
        },
    )
    return {
        "document_id": doc.id,
        "chunks": total_chunks,
        "embedded_chunks": n_ok,
        "pages": extract_meta.get("pages", 0),
        "ocr_pages": extract_meta.get("ocr_page_count", 0),
        "extract_mode": str(extract_meta.get("extract_mode", mode)),
    }
