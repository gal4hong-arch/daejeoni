from sqlalchemy import select
from sqlalchemy.orm import Session
import time

from app.config import get_settings
from app.db.models import ChatMessage, MessageTopicMap
from app.schemas.api import ChatResponse
from app.services.chat_lcel import build_answer_runnable
from app.services.intent_classifier import classify_intent, intent_to_task
from app.services.legal_adapter import fetch_legal
from app.services.law_go_kr.types import LawQueryAnalysis
from app.services.legal_routed_pipeline import (
    build_appendix_for_used_refs,
    fetch_legal_bodies_for_titles,
    propose_relevant_law_titles,
)
from app.services.law_user_stats import links_for_stats, record_law_hits
from app.services.model_resolver import maybe_promote_model_for_complex_query, resolve_model
from app.services.retrieval import hybrid_search
from app.services.topic_manager import record_classification, route_message

_PRIOR_MSG_MAX = 50
_PRIOR_MSG_CHAR_CAP = 8000


def _elapsed_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


def _prior_conversation_for_llm(
    db: Session, *, stream_id: str, before_message_id: str
) -> list[tuple[str, str]]:
    rows = list(
        db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_stream_id == stream_id,
                ChatMessage.id != before_message_id,
                ChatMessage.role.in_(("user", "assistant")),
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(_PRIOR_MSG_MAX)
        )
        .scalars()
        .all()
    )
    rows.reverse()
    out: list[tuple[str, str]] = []
    for m in rows:
        c = (m.content or "").strip()
        if len(c) > _PRIOR_MSG_CHAR_CAP:
            c = c[: _PRIOR_MSG_CHAR_CAP - 1].rstrip() + "…"
        out.append((m.role, c))
    return out


def process_chat_user_only(
    db: Session,
    *,
    stream_id: str,
    user_id: str,
    content: str,
    use_legal: bool,
    task: str,
) -> ChatResponse:
    """사용자 메시지만 저장·토픽 라우팅. 행정 AI 답변·RAG·법령 호출 없음(분할 검토 모드)."""
    t_total = time.perf_counter()
    timings: dict[str, float] = {}
    intent = classify_intent(content)
    if intent == "legal_focus":
        use_legal = True
    effective_task = intent_to_task(intent, task)

    user_msg = ChatMessage(
        conversation_stream_id=stream_id,
        role="user",
        content=content,
    )
    t_db_user = time.perf_counter()
    db.add(user_msg)
    db.flush()
    timings["db_user_msg_ms"] = _elapsed_ms(t_db_user)

    t_topic = time.perf_counter()
    route = route_message(db, stream_id=stream_id, user_id=user_id, message=content)
    record_classification(db, message_id=user_msg.id, result=route)
    timings["topic_classify_ms"] = _elapsed_ms(t_topic)

    t_db_topic = time.perf_counter()
    db.add(
        MessageTopicMap(
            message_id=user_msg.id,
            topic_session_id=route.topic_session_id,
        )
    )
    db.flush()
    timings["db_topic_map_ms"] = _elapsed_ms(t_db_topic)

    model = resolve_model(db, user_id=user_id, topic_session_id=route.topic_session_id, task=effective_task)
    model = maybe_promote_model_for_complex_query(model, content, get_settings())

    chat_trace = {
        "llm": {"model": model, "task": effective_task},
        "rag": {
            "document_ids_selected": 0,
            "candidate_pool_chunks": 0,
            "chunks_in_prompt": 0,
            "filter_by_documents_only": False,
            "top_source_titles": [],
            "note": "skip_assistant: 행정 AI 답변 생략",
        },
        "legal": {
            "use_legal_requested": use_legal,
            "law_api_configured": False,
            "legal_routed_pipeline": False,
        },
        "intent": intent,
        "compose": "none",
        "skip_assistant": True,
    }
    timings["total_ms"] = _elapsed_ms(t_total)
    chat_trace["latency_ms"] = timings

    return ChatResponse(
        answer="",
        topic_session_id=route.topic_session_id,
        decision_type=route.decision_type,
        detected_topic=route.detected_topic,
        work_type=route.work_type,
        confidence=route.confidence,
        sources=[],
        legal_note=None,
        legal_debug=None,
        law_appendix=None,
        model_used=model,
        intent=intent,
        entities_json=route.entities_json,
        chat_trace=chat_trace,
    )


def process_chat(
    db: Session,
    *,
    stream_id: str,
    user_id: str,
    content: str,
    use_legal: bool,
    task: str,
    document_ids: list[str] | None = None,
) -> ChatResponse:
    t_total = time.perf_counter()
    timings: dict[str, float] = {}
    intent = classify_intent(content)
    if intent == "legal_focus":
        use_legal = True
    effective_task = intent_to_task(intent, task)

    user_msg = ChatMessage(
        conversation_stream_id=stream_id,
        role="user",
        content=content,
    )
    t_db_user = time.perf_counter()
    db.add(user_msg)
    db.flush()
    timings["db_user_msg_ms"] = _elapsed_ms(t_db_user)

    t_topic = time.perf_counter()
    route = route_message(db, stream_id=stream_id, user_id=user_id, message=content)
    record_classification(db, message_id=user_msg.id, result=route)
    timings["topic_classify_ms"] = _elapsed_ms(t_topic)

    db.add(
        MessageTopicMap(
            message_id=user_msg.id,
            topic_session_id=route.topic_session_id,
        )
    )

    rag_meta: dict = {}
    # document_ids == [] : RAG 소스 미선택 → 검색 생략. None : 기존처럼 전체 후보 풀.
    rag_skipped_no_selection = document_ids is not None and len(document_ids) == 0
    t_rag = time.perf_counter()
    if rag_skipped_no_selection:
        chunks = []
        rag_meta = {"pool_size": 0, "document_ids_filter": False, "document_ids_count": 0, "skipped_no_selection": True}
    else:
        chunks = hybrid_search(
            db,
            user_id=user_id,
            query=content,
            topic_session_id=route.topic_session_id,
            document_ids=document_ids,
            meta_out=rag_meta,
        )
    timings["rag_ms"] = _elapsed_ms(t_rag)
    sources = [
        {
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "source_title": c.source_title,
            "score": c.score,
            "excerpt": c.content[:400],
        }
        for c in chunks
    ]

    settings = get_settings()
    oc = (getattr(settings, "law_go_kr_oc", None) or "").strip()

    model = resolve_model(db, user_id=user_id, topic_session_id=route.topic_session_id, task=effective_task)
    model = maybe_promote_model_for_complex_query(model, content, settings)

    legal_note = None
    legal_result = None
    legal_debug: dict | None = None
    used_law_refs: list[dict[str, str]] = []
    legal_routed = False

    law_query_analysis: LawQueryAnalysis | None = None
    t_legal = time.perf_counter()
    llm_legal_meta: dict = {}
    if use_legal and oc:
        legal_routed = True
        titles, raw_prop, law_query_analysis = propose_relevant_law_titles(
            db, user_id=user_id, model=model, user_message=content, llm_meta_out=llm_legal_meta
        )
        legal_result, used_law_refs = fetch_legal_bodies_for_titles(
            db,
            topic_session_id=route.topic_session_id,
            user_query=content,
            titles=titles,
            oc=oc,
            timeout=float(settings.law_go_kr_timeout or 25.0),
            service_max_ids=int(getattr(settings, "law_go_kr_service_max_ids", None) or 2),
        )
        legal_debug = dict(legal_result.debug or {})
        proposal: dict = {"titles": titles, "raw_preview": (raw_prop or "")[:2000]}
        if law_query_analysis is not None:
            proposal["intent_summary"] = law_query_analysis.intent_summary
            proposal["law_focus"] = law_query_analysis.law_focus
            proposal["notes_for_search"] = law_query_analysis.notes_for_search
        legal_debug["law_title_proposal"] = proposal
        legal_note = legal_result.warning or (
            "법령 본문을 조회해 답변 근거로 반영했습니다." if legal_result.text else None
        )
    elif use_legal:
        legal_result = fetch_legal(db, topic_session_id=route.topic_session_id, query=content)
        legal_debug = getattr(legal_result, "debug", None) or None
        legal_note = legal_result.warning or ("스텁/조회 결과가 답변에 반영되었습니다." if legal_result.text else None)
    timings["legal_fetch_ms"] = _elapsed_ms(t_legal)

    t_hist = time.perf_counter()
    conversation_history = _prior_conversation_for_llm(
        db, stream_id=stream_id, before_message_id=user_msg.id
    )
    timings["history_ms"] = _elapsed_ms(t_hist)
    llm_answer_meta: dict = {}
    t_llm = time.perf_counter()
    answer = build_answer_runnable().invoke(
        {
            "db": db,
            "user_id": user_id,
            "model": model,
            "user_message": content,
            "chunks": chunks,
            "legal_result": legal_result,
            "legal_routed": legal_routed,
            "law_query_analysis": law_query_analysis if legal_routed else None,
            "conversation_history": conversation_history,
            "llm_meta_out": llm_answer_meta,
        }
    )
    timings["llm_answer_ms"] = _elapsed_ms(t_llm)

    law_appendix: str | None = None
    resolved_links: list[dict[str, str]] = []
    resolution_meta: dict | None = None

    # 법령 토글(off)일 때는 답변 본문에서 법령명을 긁어 부록·법제처 링크를 붙이지 않음
    # (일반 질의에 무관한 법이 붙는 오탐·환각 완화)
    if use_legal and oc:
        ap = build_appendix_for_used_refs(used_law_refs)
        law_appendix = ap.strip() if ap and ap.strip() else None
        resolved_links = [{"label": r["label"], "url": r["url"]} for r in used_law_refs]
        if legal_debug is not None:
            legal_debug["links"] = resolved_links

    final_answer = answer.rstrip()
    if law_appendix and "📘 관련 법령" not in final_answer and "📘 참고 법령" not in final_answer:
        final_answer = final_answer + law_appendix

    if legal_debug is None:
        legal_debug = {}
    if resolution_meta:
        legal_debug["law_resolution"] = resolution_meta
    if resolved_links and not (use_legal and oc):
        base_links = list(legal_debug.get("links") or [])
        seen_u = {x.get("url") for x in base_links if x.get("url")}
        for L in resolved_links:
            u = L.get("url")
            if u and u not in seen_u:
                base_links.append(L)
                seen_u.add(u)
        legal_debug["links"] = base_links

    t_db_asst = time.perf_counter()
    asst = ChatMessage(
        conversation_stream_id=stream_id,
        role="assistant",
        content=final_answer,
    )
    db.add(asst)
    db.flush()
    timings["db_assistant_ms"] = _elapsed_ms(t_db_asst)
    db.add(
        MessageTopicMap(
            message_id=asst.id,
            topic_session_id=route.topic_session_id,
        )
    )
    db.flush()

    legal_debug_out = legal_debug if legal_debug and len(legal_debug) > 0 else None

    stats_refs = links_for_stats(
        use_legal=use_legal,
        oc=oc,
        used_law_refs=used_law_refs,
        resolved_links=resolved_links,
    )
    t_stats = time.perf_counter()
    if stats_refs:
        record_law_hits(db, user_id, stats_refs)
    timings["law_stats_ms"] = _elapsed_ms(t_stats)

    doc_ids_sent = document_ids if document_ids else []
    rag_note = (
        "RAG에서 문서를 하나도 선택하지 않아 내부 문서 검색을 건너뜁니다."
        if rag_skipped_no_selection
        else (
            "문서를 지정하지 않으면(요청에 document_ids 없음) 본인·공유 RAG의 모든 청크를 후보로 두고, "
            "BM25·벡터 점수 상위 N개(기본 약 8개)만 답변 근거로 넘어갑니다. "
            "등록만 해 두었어도 다른 문서 청크가 더 높은 점수면 빠질 수 있습니다. "
            "특정 자료만 쓰려면 RAG에서 해당 문서를 선택하세요."
        )
    )
    chat_trace = {
        "llm": {"model": model, "task": effective_task},
        "rag": {
            "document_ids_selected": len(doc_ids_sent),
            "candidate_pool_chunks": rag_meta.get("pool_size", 0),
            "candidate_pool_cap": rag_meta.get("pool_cap"),
            "chunks_in_prompt": len(chunks),
            "filter_by_documents_only": rag_meta.get("document_ids_filter", False),
            "skipped_no_selection": rag_skipped_no_selection,
            "top_k": rag_meta.get("top_k"),
            "embed_ms": rag_meta.get("embed_ms"),
            "embed_cache_hit": rag_meta.get("embed_cache_hit"),
            "top_source_titles": [c.source_title[:120] for c in chunks[:5]],
            "note": rag_note,
        },
        "legal": {
            "use_legal_requested": use_legal,
            "law_api_configured": bool(oc),
            "legal_routed_pipeline": legal_routed,
        },
        "intent": intent,
        "compose": "langchain_lcel",
    }
    timings["total_ms"] = _elapsed_ms(t_total)
    chat_trace["latency_ms"] = timings
    if llm_answer_meta:
        chat_trace["llm_runtime"] = llm_answer_meta
    if llm_legal_meta:
        chat_trace["legal_llm_runtime"] = llm_legal_meta

    return ChatResponse(
        answer=final_answer,
        topic_session_id=route.topic_session_id,
        decision_type=route.decision_type,
        detected_topic=route.detected_topic,
        work_type=route.work_type,
        confidence=route.confidence,
        sources=sources,
        legal_note=legal_note,
        legal_debug=legal_debug_out,
        law_appendix=law_appendix,
        model_used=model,
        intent=intent,
        entities_json=route.entities_json,
        chat_trace=chat_trace,
    )
