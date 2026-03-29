from sqlalchemy.orm import Session

from app.services.legal_adapter import LegalFetchResult
from app.services.law_go_kr.types import LawQueryAnalysis
from app.services.llm_client import chat_completion
from app.services.retrieval import RetrievedChunk


def _law_analysis_user_block(analysis: LawQueryAnalysis | None) -> str:
    if not analysis:
        return ""
    if not (analysis.intent_summary or analysis.law_focus or analysis.notes_for_search):
        return ""
    return (
        "[질의 분석(시스템이 법령 연동 전에 정리한 요약 — 참고용)]\n"
        f"의도: {analysis.intent_summary or '(미정)'}\n"
        f"초점: {analysis.law_focus or '(미정)'}\n"
        f"검색 힌트: {analysis.notes_for_search or '(없음)'}\n\n"
    )


def _legal_display_intent(user_message: str) -> bool:
    t = user_message or ""
    needles = (
        "조문",
        "본문",
        "전문",
        "원문",
        "텍스트",
        "보여",
        "인용",
        "발췌",
        "붙여",
        "그대로",
        "전부",
    )
    return any(n in t for n in needles)


def generate_answer(
    db: Session,
    *,
    user_id: str,
    model: str,
    user_message: str,
    chunks: list[RetrievedChunk],
    legal: LegalFetchResult | None,
    legal_routed: bool = False,
    law_query_analysis: LawQueryAnalysis | None = None,
    conversation_history: list[tuple[str, str]] | None = None,
    llm_meta_out: dict | None = None,
) -> str:
    # 법령 라우팅 시: 일부 프로바이더가 긴 입력에서 뒤쪽을 잘라내므로 RAG 발췌를 짧게 하고,
    # 법령 블록을 질문 직후에 둔다(잘리더라도 법령이 남도록).
    chunk_cap = 900 if legal_routed else 2000
    sources_text = "\n\n".join(
        f"[출처: {c.source_title or '문서'} id={c.chunk_id}]\n{c.content[:chunk_cap]}" for c in chunks
    )
    legal_part = ""
    legal_cap = 48000 if legal_routed else 4000
    if legal and legal.text:
        legal_part = f"\n\n[법령 본문·발췌]\n{legal.text[:legal_cap]}"

    if legal_routed:
        base = (
            "너는 지자체 내부 직원을 돕는 행정 보조 AI다. "
            "입력 순서: (1) 질문 (2) 내부 문서(RAG) 발췌 (3) 국가법령정보 API [법령 본문·발췌]. "
            "**내부 문서 발췌가 있으면 그것을 우선 근거로 삼고**, 법령 API 발췌는 그와 모순되지 않는 범위에서 보완·교차 확인에 쓴다. "
            "RAG에 해당 주제의 법령·문서가 있으면 먼저 그 출처를 인용하고, API 조문은 추가 근거나 최신 조문 확인용으로 활용한다. "
            "사용자 질문의 의도(열람·요건·절차·해석 등)를 짧게 확인한 뒤, 위 우선순위에 맞게 답한다. "
            "여러 법령이 API 발췌에 있으면 질문과 직접 관련된 조문을 우선한다. "
            "실제 조문 문장이 API 발췌에 있으면 반드시 그것을 근거로 활용할 수 있다. "
            "근거에 없는 사항은 추측하지 말고 명시가 없다고 말한다. 한국어로 답한다."
        )
        if _legal_display_intent(user_message):
            sys = base + " 사용자가 본문·조문 열람을 요청한 경우, 발췌된 조문을 가능한 한 구조적으로 인용·제시한다."
        else:
            sys = base + " 사용자의 질문에 맞게 발췌된 규정을 검토·종합하여 설명한다."
    else:
        sys = (
            "너는 지자체 내부 직원을 돕는 행정 보조 AI다. "
            "법령 스냅샷이 있으면 우선 반영하고, 내부 문서는 보조 근거로만 쓴다. "
            "출처를 구분해 서술하고, 근거가 없으면 추측하지 말고 말한다. 한국어로 답한다."
        )
    analysis_prefix = _law_analysis_user_block(law_query_analysis) if legal_routed else ""
    if legal_routed and legal_part:
        user_prompt = (
            f"{analysis_prefix}"
            f"질문:\n{user_message}\n\n"
            f"내부 문서 발췌(RAG·우선 근거):\n{sources_text or '(없음)'}\n"
            f"{legal_part}"
        )
    else:
        user_prompt = (
            f"{analysis_prefix}"
            f"질문:\n{user_message}\n\n내부 문서 발췌:\n{sources_text or '(없음)'}{legal_part}"
        )

    try:
        return chat_completion(
            db,
            user_id=user_id,
            model=model,
            system=sys,
            user=user_prompt,
            temperature=0.3,
            conversation_history=conversation_history,
            meta_out=llm_meta_out,
        )
    except RuntimeError:
        if not chunks and not (legal and legal.text):
            return "근거 문서가 없고 사용 가능한 LLM API 키도 없습니다. OpenAI/Anthropic/Google 키를 저장하거나 .env를 설정하세요."
        parts = []
        if legal and legal.text:
            parts.append(f"(법령·조회)\n{legal.text[:2000]}")
        if sources_text:
            parts.append(f"(내부 문서)\n{sources_text[:6000]}")
        return (
            "【검색 기반 초안 — LLM 키 없음】\n"
            + "\n\n---\n\n".join(parts)
            + "\n\n※ 실제 행정 답변은 담당자 검토 후 사용하세요."
        )
    except Exception as e:
        return f"모델 호출 실패({model}): {e}. 검색 요약:\n{sources_text[:3000]}"
