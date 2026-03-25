"""웹 URL을 가져와 텍스트 추출 후 kb_documents / kb_chunks 로 분할·임베딩."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import KbChunk, KbDocument
from app.services.audit_log import audit
from app.services.embeddings import embed_text, embedding_to_json
from app.services.rag_document_summary import build_rag_outline_text, persist_outline_chunk


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


def normalize_canonical_url(url: str) -> str:
    """동일 페이지 중복 방지용(쿼리 정렬·프래그먼트 제거)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    p = urlparse(raw if "://" in raw else f"https://{raw}")
    if p.scheme not in ("http", "https"):
        return ""
    host = (p.hostname or "").lower()
    if not host:
        return ""
    path = p.path or "/"
    q = parse_qsl(p.query, keep_blank_values=True)
    q.sort()
    query = urlencode(q)
    return urlunparse((p.scheme, host, path, "", query, ""))


def url_fetch_allowed(url: str) -> tuple[bool, str]:
    """간단 SSRF 방지: 스킴·루프백·사설 호스트명 차단."""
    raw = (url or "").strip()
    if not raw:
        return False, "URL이 비었습니다."
    p = urlparse(raw if "://" in raw else f"https://{raw}")
    if p.scheme not in ("http", "https"):
        return False, "http 또는 https URL만 허용됩니다."
    host = (p.hostname or "").lower().strip(".")
    if not host:
        return False, "호스트가 없습니다."
    if host == "localhost" or host.endswith(".localhost"):
        return False, "localhost URL은 허용되지 않습니다."
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False, "사설·루프백 IP는 허용되지 않습니다."
    except ValueError:
        pass
    blocked = ("metadata.google.internal", "169.254.169.254")
    if host in blocked or host.endswith(".internal"):
        return False, "해당 호스트는 허용되지 않습니다."
    return True, ""


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return text.strip()


def _page_title_from_html(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    t = soup.find("title")
    if not t:
        return ""
    raw = t.get_text(separator=" ", strip=True) if t else ""
    raw = re.sub(r"\s+", " ", (raw or "").strip())
    return raw[:512] if raw else ""


def fetch_url_content(
    url: str, *, timeout: float = 25.0, max_bytes: int = 2_000_000
) -> tuple[str, str, str, str]:
    """
    반환: (canonical_url, final_url_after_redirects, plain_text, page_title_html).
    """
    ok, err = url_fetch_allowed(url)
    if not ok:
        raise ValueError(err)
    canonical = normalize_canonical_url(url)
    if not canonical:
        raise ValueError("유효한 URL이 아닙니다.")

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Innocurve-RAG/1.0; +administrative-assistant)",
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        r = client.get(canonical)
        r.raise_for_status()
        final = str(r.url)
        raw = r.content[:max_bytes]
        ctype = (r.headers.get("content-type") or "").lower().split(";")[0].strip()
        enc = r.encoding or "utf-8"

    head = raw[:4000].lower()
    is_html = "html" in ctype or b"<html" in head or b"<!doctype html" in head
    decoded = raw.decode(enc, errors="replace")
    page_title = ""
    if is_html:
        page_title = _page_title_from_html(decoded)
        text = html_to_text(decoded)
    else:
        text = decoded.strip()
    if len(text) < 80:
        raise ValueError("추출된 본문이 너무 짧습니다. 페이지가 로그인·차단·비HTML일 수 있습니다.")
    return canonical, final, text, page_title


def ingest_arbitrary_text_as_rag_document(
    db: Session,
    *,
    user_id: str,
    plain_text: str,
    title: str,
    source_kind: str,
    source_url: str,
    shared_globally: bool,
    audit_action: str,
    audit_extra: dict | None = None,
) -> dict[str, str | int]:
    """URL·파일 등에서 추출한 본문을 동일 규칙으로 청크·등록."""
    text = (plain_text or "").strip()
    pieces = _split_text(text, max_len=5500, overlap=400)
    if not pieces:
        raise ValueError("분할할 본문이 없습니다.")

    existing = (
        db.execute(
            select(KbDocument).where(
                KbDocument.user_id == user_id,
                KbDocument.source_kind == source_kind,
                KbDocument.source_url == source_url,
            )
        )
        .scalar_one_or_none()
    )
    chosen = (title or "").strip()[:512] or source_url[:512]
    if existing:
        db.execute(delete(KbChunk).where(KbChunk.document_id == existing.id))
        doc = existing
        doc.title = chosen or doc.title
        doc.shared_globally = shared_globally
    else:
        doc = KbDocument(
            user_id=user_id,
            title=chosen,
            source_kind=source_kind,
            source_url=source_url,
            shared_globally=shared_globally,
        )
        db.add(doc)
    db.flush()

    n_ok = 0
    outline_n = 0
    doc_title = doc.title or "문서"
    outline_body = build_rag_outline_text(db, user_id=user_id, doc_title=doc_title, excerpt=text[:16000])
    if outline_body:
        _p, ok = persist_outline_chunk(
            db,
            user_id=user_id,
            document_id=doc.id,
            doc_title=doc_title,
            outline_body=outline_body,
        )
        if _p:
            outline_n = 1
            if ok:
                n_ok += 1
    for i, piece in enumerate(pieces):
        c = KbChunk(
            user_id=user_id,
            document_id=doc.id,
            source_title=f"{doc_title[:240]} · Part {i + 1}/{len(pieces)}",
            content=piece,
            topic_session_id=None,
        )
        vec = embed_text(db, user_id, piece)
        c.embedding_json = embedding_to_json(vec)
        if vec:
            n_ok += 1
        db.add(c)

    total_chunks = len(pieces) + outline_n
    detail = {
        "document_id": doc.id,
        "chunks": total_chunks,
        "embedded_chunks": n_ok,
        "outline_chunks": outline_n,
    }
    if audit_extra:
        detail.update(audit_extra)
    audit(db, user_id=user_id, action=audit_action, detail=detail)
    return {
        "document_id": doc.id,
        "chunks": total_chunks,
        "embedded_chunks": n_ok,
    }


def ingest_url_to_rag(
    db: Session,
    *,
    user_id: str,
    url: str,
    source_title: str = "",
    shared_globally: bool = False,
) -> dict[str, str | int]:
    canonical, _final, text, page_title = fetch_url_content(url)
    explicit = (source_title or "").strip()[:512]
    auto_title = (page_title or "").strip()[:512]
    chosen_title = explicit or auto_title or canonical[:512]
    out = ingest_arbitrary_text_as_rag_document(
        db,
        user_id=user_id,
        plain_text=text,
        title=chosen_title,
        source_kind="url",
        source_url=canonical,
        shared_globally=shared_globally,
        audit_action="rag.url_ingest",
        audit_extra={"url": canonical},
    )
    out["canonical_url"] = canonical
    return out
