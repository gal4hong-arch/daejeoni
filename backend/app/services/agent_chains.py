"""문서: writer → reviewer → legal_checker / 시뮬: simulation → legal_checker."""

from sqlalchemy.orm import Session

from app.services.document_composer import compose_document
from app.services.llm_client import chat_completion


def adapt_plain_draft_to_template(
    db: Session,
    *,
    user_id: str,
    model: str,
    draft_text: str,
    template_plaintext: str,
) -> str:
    """템플릿만 있고 전체 체인은 끈 경우: 1회 LLM으로 양식에 맞춤."""
    tpl = (template_plaintext or "").strip()
    dr = (draft_text or "").strip()
    if len(tpl) < 40 or not dr:
        return dr
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system="행정 서식 편집자. 양식의 목차·번호·항목을 유지하고 본문만 근거 초안에 맞게 채운다. 한국어.",
        user=(
            f"[양식 추출본]\n{tpl[:22000]}\n\n"
            f"[근거 초안]\n{dr[:16000]}\n\n"
            "양식 구조를 우선한다. 빈 칸·작성란을 초안 내용으로 채운 최종본만 출력한다."
        ),
        temperature=0.22,
    )


def run_document_agent_chain(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    topic_id: str,
    kind: str,
    legal_excerpt: str | None,
    scenario_hint: str = "",
    template_plaintext: str | None = None,
) -> dict[str, str]:
    base = compose_document(
        db,
        stream_id=stream_id,
        topic_session_id=topic_id,
        kind=kind,
        scenario_hint=scenario_hint or "",
    )
    legal_part = legal_excerpt or "(법령 스냅샷 없음)"

    tpl = (template_plaintext or "").strip()
    template_block = ""
    if len(tpl) > 40:
        template_block = (
            "\n\n[기관·서식 양식에서 추출한 본문(제목·번호·항목·표 줄 순서 유지)]\n"
            f"{tpl[:20000]}\n\n"
            "위 양식의 목차·번호·항목 제목·표 머리글을 가능한 한 유지하고, "
            "빈 칸·○○·(작성) 자리를 대화 근거에 맞게 채운다. 양식에 없는 장만 임의로 늘리지 않는다."
        )

    hint_sys = ""
    if (scenario_hint or "").strip():
        hint_sys = " 시나리오 힌트에서 강조한 각도(예산·민원·쟁점)를 개요·의회 대응에 반영한다."

    writer = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=(
            "행정 문서 작성자. 통합 보고·설명·의회 대응 초안을 실무용으로 완성한다. 한국어, 공문체에 가깝게."
            + hint_sys
        ),
        user=f"[템플릿·근거]\n{base}{template_block}\n\n위 구조와 근거를 바탕으로 하나의 완성 문서를 작성하라.",
        temperature=0.28,
    )

    if len(tpl) > 80:
        writer = chat_completion(
            db,
            user_id=user_id,
            model=model,
            system="행정 서식 편집자. 주어진 양식 뼈대를 깨뜨리지 않고 본문만 대화 근거에 맞게 정리한다. 한국어.",
            user=(
                f"[양식 원문(추출)]\n{tpl[:22000]}\n\n"
                f"[이전 작성 초안]\n{writer[:16000]}\n\n"
                "양식의 제목·번호·항목·표 구조를 우선하고, 본문만 필요 시 고쳐 양식에 맞는 최종본을 출력하라. "
                "불필요한 중복 장(설명·의회 블록을 두 번 쓰기 등)은 제거한다."
            ),
            temperature=0.22,
        )

    reviewer = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system="상급자 검토자. 누락·논리 비약·정책 일관성을 지적하고 개선안을 제시한다. 한국어.",
        user=f"[초안]\n{writer}\n\n검토 의견을 번호 목록으로 작성하라.",
    )

    legal_checker = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system="법령 정합성 검토자. 제공된 법령 발췌와 초안·검토의 충돌 여부를 본다. 한국어.",
        user=f"[법령·발췌]\n{legal_part[:6000]}\n\n[초안]\n{writer[:4000]}\n\n[상급자 검토]\n{reviewer[:3000]}\n\n법령 관점에서 위험·수정 권고를 요약하라.",
    )

    sep = "\n\n\n\n"
    return {
        "writer": writer,
        "reviewer": reviewer,
        "legal_checker": legal_checker,
        "final": (
            f"【최종 반영본 제안】\n\n{writer}{sep}"
            f"---\n【검토】\n{reviewer}{sep}"
            f"---\n【법령 점검】\n{legal_checker}"
        ),
    }


def run_simulation_agent_chain(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    topic_id: str,
    scenario_hint: str,
    legal_excerpt: str | None,
) -> dict[str, str]:
    transcript = compose_document(
        db,
        stream_id=stream_id,
        topic_session_id=topic_id,
        kind="report",
        scenario_hint=scenario_hint or "",
    )
    legal_part = legal_excerpt or "(법령 없음)"
    hint_tail = (scenario_hint or "").strip() or "(힌트 없음 — 일반 의회 질의)"

    sim = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system="지방의회 의원 역할을 가정한다. 날카롭지만 건설적인 질문과 후속 질문을 한다. 한국어.",
        user=f"[안건 맥락]\n{transcript}\n\n[시나리오 힌트]\n{hint_tail}\n\n의회 질의를 대화체로 작성하라.",
    )

    legal_checker = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system="법무 검토. 시뮬레이션 질의·가상 답변에 대한 법령 리스크를 짧게 정리한다. 한국어.",
        user=f"[법령 발췌]\n{legal_part[:5000]}\n\n[시뮬레이션]\n{sim[:6000]}",
    )

    sep = "\n\n\n\n"
    return {
        "simulation": sim,
        "legal_checker": legal_checker,
        "final": f"【시뮬레이션】\n{sim}{sep}---\n【법령 점검】\n{legal_checker}",
    }
