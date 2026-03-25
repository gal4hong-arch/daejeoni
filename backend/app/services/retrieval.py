import math
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import KbChunk, KbDocument
from app.services.embeddings import embed_text, json_to_embedding


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\w가-힣]+", text.lower())


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _norm_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [1.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


@dataclass
class RetrievedChunk:
    chunk_id: str
    source_title: str
    content: str
    document_id: str | None
    score: float


def hybrid_search(
    db: Session,
    *,
    user_id: str,
    query: str,
    topic_session_id: str | None,
    document_ids: list[str] | None = None,
    top_k: int = 8,
    vector_weight: float = 0.45,
    meta_out: dict | None = None,
) -> list[RetrievedChunk]:
    """
    BM25 + (가능 시) 임베딩 코사인 결합. document_ids 가 있으면 해당 문서의 청크만 검색.
    본인 청크 + ``shared_globally`` 문서의 청크(관리자 공유 RAG)를 후보에 포함.
    """
    q = (
        select(KbChunk)
        .outerjoin(KbDocument, KbChunk.document_id == KbDocument.id)
        .where(
            or_(
                KbChunk.user_id == user_id,
                and_(KbDocument.shared_globally.is_(True)),
            )
        )
    )
    if document_ids:
        q = q.where(KbChunk.document_id.in_(document_ids))
    if topic_session_id:
        q = q.where(
            or_(KbChunk.topic_session_id == topic_session_id, KbChunk.topic_session_id.is_(None))
        )
    rows = list(db.execute(q).scalars().all())
    if meta_out is not None:
        meta_out["pool_size"] = len(rows)
        meta_out["document_ids_filter"] = bool(document_ids and len(document_ids) > 0)
        meta_out["document_ids_count"] = len(document_ids or [])
    if not rows:
        return []

    q_tokens = _tokenize(query)
    corpus = [_tokenize(r.content) for r in rows]

    q_vec = embed_text(db, user_id, query) if query.strip() else None
    has_emb = bool(q_vec) and any(r.embedding_json for r in rows)

    if q_tokens:
        bm25 = BM25Okapi(corpus)
        bm25_raw = list(bm25.get_scores(q_tokens))
    else:
        bm25_raw = [0.0] * len(rows)

    vec_raw: list[float] = []
    if has_emb:
        for r in rows:
            ev = json_to_embedding(r.embedding_json)
            vec_raw.append(_cosine(q_vec or [], ev) if ev else 0.0)
    else:
        vec_raw = [0.0] * len(rows)

    nb = _norm_scores(bm25_raw)
    nv = _norm_scores(vec_raw)

    fused: list[tuple[KbChunk, float]] = []
    for i, r in enumerate(rows):
        if has_emb:
            s = (1.0 - vector_weight) * nb[i] + vector_weight * nv[i]
        else:
            s = bm25_raw[i]
        fused.append((r, s))

    fused.sort(key=lambda x: x[1], reverse=True)
    return [
        RetrievedChunk(
            chunk_id=r.id,
            source_title=r.source_title,
            content=r.content,
            document_id=r.document_id,
            score=float(s),
        )
        for r, s in fused[:top_k]
    ]
