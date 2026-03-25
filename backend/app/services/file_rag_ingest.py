"""업로드 파일(확장자별) → 본문 추출 후 RAG 문서로 등록."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.pdf_rag_ingest import MAX_PDF_BYTES, ingest_pdf_to_rag, ocr_image_bytes
from app.services.web_rag_ingest import ingest_arbitrary_text_as_rag_document

# 프론트 안내와 맞출 것
SUPPORTED_EXTENSIONS = frozenset({
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".hwp",
    ".hwpx",
})

_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
_TEXT_EXT = frozenset({".txt", ".md", ".markdown"})


def _max_upload_bytes() -> int:
    return int(getattr(get_settings(), "rag_pdf_max_bytes", MAX_PDF_BYTES) or MAX_PDF_BYTES)


def _decode_text_file(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _basename_title(name: str) -> str:
    base = (Path(name).name or "문서").strip()
    return base[:512] if base else "문서"


def _text_via_unstructured(filename: str, data: bytes) -> str:
    from unstructured.partition.auto import partition

    bio = io.BytesIO(data)
    bio.seek(0)
    elements = partition(file=bio, metadata_filename=filename)
    parts = [str(el).strip() for el in elements if str(el).strip()]
    return "\n\n".join(parts).strip()


def ingest_uploaded_file_to_rag(
    db: Session,
    *,
    user_id: str,
    filename: str,
    data: bytes,
    extract_mode: str = "text_only",
    shared_globally: bool = False,
) -> dict[str, str | int]:
    """
    확장자에 따라 PDF / 텍스트 / 이미지(OCR) / unstructured 오피스·HWP 등 처리.
    """
    max_b = _max_upload_bytes()
    if len(data) > max_b:
        raise ValueError(
            f"파일이 너무 큽니다(약 {max_b // (1024 * 1024)}MB 이하, 환경 RAG_PDF_MAX_BYTES로 조정 가능)."
        )

    name = (filename or "").strip() or "upload"
    ext = Path(name).suffix.lower()
    if not ext:
        raise ValueError("파일에 확장자가 없습니다. 지원 형식의 파일을 선택하세요.")
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"지원하지 않는 형식입니다({ext}). 지원: {supported}")

    if ext == ".pdf":
        return ingest_pdf_to_rag(
            db,
            user_id=user_id,
            pdf_bytes=data,
            filename=name,
            source_title="",
            extract_mode=extract_mode if extract_mode in ("hybrid", "text_only") else "text_only",
            shared_globally=shared_globally,
        )

    fingerprint = f"file:{hashlib.sha256(data).hexdigest()}"
    title = _basename_title(name)

    if ext in _TEXT_EXT:
        text = _decode_text_file(data).strip()
        if len(text) < 3:
            raise ValueError("텍스트 파일에서 읽을 내용이 거의 없습니다.")
        out = ingest_arbitrary_text_as_rag_document(
            db,
            user_id=user_id,
            plain_text=text,
            title=title,
            source_kind="file",
            source_url=fingerprint,
            shared_globally=shared_globally,
            audit_action="rag.file_ingest",
            audit_extra={"filename": name[:240], "kind": "text"},
        )
        out["filename"] = name[:240]
        return out

    if ext in _IMAGE_EXT:
        text = ocr_image_bytes(data).strip()
        if len(text) < 2:
            raise ValueError(
                "이미지에서 글자를 거의 찾지 못했습니다. 선명한 스캔·캡처 이미지인지 확인하거나 PDF·텍스트 파일로 등록해 보세요."
            )
        out = ingest_arbitrary_text_as_rag_document(
            db,
            user_id=user_id,
            plain_text=text,
            title=title,
            source_kind="file",
            source_url=fingerprint,
            shared_globally=shared_globally,
            audit_action="rag.file_ingest",
            audit_extra={"filename": name[:240], "kind": "image_ocr"},
        )
        out["filename"] = name[:240]
        return out

    try:
        text = _text_via_unstructured(name, data)
    except Exception as e:
        raise ValueError(
            f"이 파일 형식은 서버에서 본문을 추출하지 못했습니다({ext}). "
            f"LibreOffice·한글 뷰어 등으로 PDF나 TXT로보낸 뒤 등록해 보세요. ({e})"
        ) from e
    text = (text or "").strip()
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) < 20:
        raise ValueError(
            "추출된 본문이 너무 짧습니다. 암호화된 문서이거나 빈 파일일 수 있습니다. PDF·TXT로 변환 후 다시 시도해 보세요."
        )
    out = ingest_arbitrary_text_as_rag_document(
        db,
        user_id=user_id,
        plain_text=text,
        title=title,
        source_kind="file",
        source_url=fingerprint,
        shared_globally=shared_globally,
        audit_action="rag.file_ingest",
        audit_extra={"filename": name[:240], "kind": "unstructured"},
    )
    out["filename"] = name[:240]
    return out
