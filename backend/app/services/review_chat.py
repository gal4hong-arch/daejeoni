"""채팅 분할 검토: 보고 요약 → 검토자 의견 → 이후 턴별 검토 · 보고자 답변."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage
from app.services.llm_client import chat_completion, chat_completion_with_fallback
from app.services.roundtable import ROLE_CATALOG

# 보고자(검토 대응): 채팅 근거 + 반복 금지 + 허위 법령·기관명 억제
_REPORTER_REPLY_SYSTEM = (
    "너는 같은 채팅방의 보고자(작성 담당)다. 검토자의 의견에 대해 **실무적으로** 답한다.\n"
    "【근거】아래 [최근 채팅]·[요약 보고]에 나온 사실·법령명·기관명·수치만 인용한다. "
    "채팅에 없는 구체 법령·조문·기관·내부 일정을 **새로 지어내지 않는다**. "
    "외부 조사 결과가 없으면 「자료·채팅 범위에서는 미확정이며, 실무에서는 ○○를 조회해야 한다」처럼 명시한다.\n"
    "【맥락】검토 패널 대화는 DB에 없을 수 있다. [이전 검토자 의견]이 있으면 **최신 검토 흐름**을 읽고, "
    "이미 지적·질문된 주제는 짧게 수긍만 하고 **이번 [검토자 의견]에서 새로 나온 요구**에 집중한다.\n"
    "【진전】검토자가 요구한 항목마다: (1) 채팅·보고에서 **확인된 내용**을 한두 문장으로 요약하고, "
    "(2) 아직 비어 있으면 그 한계를 인정하고 **다음 실무 조치**를 한 가지씩만 제시한다. "
    "「다음 주 검토」「정기 보고」 같은 추상적 문장만 늘어놓지 않는다.\n"
    "【중복】[보고자 이전 답변]이 있으면, 이미 한 약속·문장·**검토자에게 던진 되묻기 문장**을 다시 쓰지 않는다. "
    "비슷한 질문을 반복하지 말고, 검토자가 이미 답한 주제면 되묻기는 생략하거나 한 문장으로만 넘긴다. "
    "새 정보가 없으면 「추가 확정 사실 없음」 한 문장 후, **이번 검토에서만** 남은 확인 1가지를 짚는다.\n"
    "【마무리】검토자에게 되묻는 문장은 많아도 **1문장**이며, 직전 보고자 답변과 동일·유사한 표현이면 바꾸거나 생략한다.\n"
    "출력 형식: 수용 / 보완·조치 / 한계·추가 확인 을 구분하되, 전체 **6~14문장**, 한국어, 과장·허위 금지."
)

_REVIEW_FOLLOWUP_APPENDIX = (
    "\n\n【이번 호출: 보고자 답변 직후 재검토】\n"
    "보고자 이번 답변의 **의도·취지**(해명·추가 자료·일정 제시·수용·이의 등)를 파악한 뒤 그에 맞게 반응한다. "
    "직전 검토 요구와 보고자 답변·이전 보고자 답변을 비교한다. "
    "**구체적 사실·조사결과·일정·산출물·인용 근거**가 새로 생겼는지 먼저 평가한다. "
    "추상적 계획만 반복이면 「진전 없음」이라 짚고, **남은 결정·리스크**만 2~4개로 짧게 제시한다. "
    "이미 답변·채팅에서 충족된 항목에 대해 첫째·둘째·셋째로 **같은 질문을 되풀이하지 않는다**. "
    "답변은 **대화체**로 이어간다. 4문단 이내, 격식·서론 최소화."
)

# 채팅창에 입력이 있었을 때: 검토자는 그 입력 취지에 대한 반응만 (추가 질문·새 제안 금지)
_REVIEW_FOLLOWUP_COMPOSER_ONLY_APPENDIX = (
    "\n\n【이번 호출: 보고자 채팅 입력 기준 재검토】\n"
    "아래 [보고자 입력 원문]이 이번 턴의 **유일한 초점**이다. 검토자 응답은 **그 입력에 담긴 말에 대한 반응만** 쓴다.\n"
    "**금지**: 보고자 입력과 무관한 새 주제, **추가 질문**(되묻기·확인 요청 나열), **새 제안·새 과제·추가 일정 제시**, "
    "「추가로 확인할 점」 목록, 검토 체크리스트식 나열.\n"
    "**허용**: 입력 취지에 대한 수긍·보완·한계 인정·짧은 질문 없는 반박, 보고자 이번 답변과의 정합성 짚기.\n"
    "**대화체**, 3~8문장, 격식·서론 최소화."
)

_REPORTER_SYSTEM = (
    "너는 같은 채팅방의 보고자(작성 담당)다. 아래 대화 전체를 읽고 "
    "상급에게 올릴 수 있게 **간결한 요약 보고**만 작성한다. "
    "불필요한 서론 없이 핵심 사실·쟁점·필요 조치를 bullet 또는 짧은 문단으로. "
    "한국어, 5~12문장 또는 동등 분량. 과장·허위 금지."
)

# 검토자: 대화체 + 보고자 의도(특히 검토 요청 시 검토 의견으로 답함)
_REVIEWER_INTENT_APPENDIX = (
    "\n\n【말투】\n"
    "답변은 항상 **대화체**로 한다. 말하듯 자연스러운 구어체·존댓말을 쓰고, "
    "공문·보고서식의 제목·항목만 나열하는 문장은 피한다.\n\n"
    "【보고자 메시지 의도】\n"
    "보고자(사용자) 메시지의 **의도**를 먼저 파악한다: "
    "예) 정보·근거 요청, 확인·승인 필요, 반박·재검토 요청, 추가 설명·정정, 일정·협조·긴급 대응, 단순 진행 공유 등.\n"
    "**검토해 달라·의견 달라·한번 봐 달라·피드백 줘** 등 **검토·의견을 요청**하는 경우에는, "
    "형식 갖춘 보고서 본문이 아니라 **그 역할의 검토 의견**(지적·질문·보완점·리스크)을 **대화로 바로 전달**하면 된다. "
    "불필요한 머리말 없이 핵심 의견부터 말한다.\n"
    "그 밖의 의도에도 초점을 맞춘다. 의도와 무관한 틀에 박힌 검토 문장만 반복하지 않는다.\n"
    "보고자가 질문했다면 그에 직접 답하거나, 역할상 답할 수 없으면 한계를 밝히고 실무적으로 다음 조치를 안내한다.\n"
    "대화 턴의 연속성을 유지하고, 보고자가 기대하는 소통 방식(간결·상세 등)에 가능한 한 맞춘다."
)


def _transcript_for_review(db: Session, *, stream_id: str, limit: int = 28) -> str:
    rows = (
        db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_stream_id == stream_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(max(4, min(limit, 60)))
        )
        .scalars()
        .all()
    )
    lines: list[str] = []
    for m in reversed(rows):
        if m.role not in ("user", "assistant", "system"):
            continue
        role_ko = {"user": "사용자", "assistant": "행정 AI", "system": "시스템"}.get(m.role, m.role)
        body = (m.content or "").strip()
        if len(body) > 12000:
            body = body[:11997] + "…"
        lines.append(f"[{role_ko}]\n{body}")
    return "\n\n---\n\n".join(lines).strip() or "(대화 없음)"


def _format_prior_reporter_block(prior: list[str] | None) -> str:
    if not prior:
        return ""
    lines: list[str] = []
    for i, block in enumerate(prior, start=1):
        t = (block or "").strip()
        if not t:
            continue
        if len(t) > 11000:
            t = t[:10997] + "…"
        lines.append(f"[보고자 이전 답변 #{i}]\n{t}")
    return "\n\n".join(lines).strip()


def _format_prior_reviewer_block(prior: list[str] | None) -> str:
    if not prior:
        return ""
    lines: list[str] = []
    for i, block in enumerate(prior, start=1):
        t = (block or "").strip()
        if not t:
            continue
        if len(t) > 11000:
            t = t[:10997] + "…"
        lines.append(f"[이전 검토자 의견 #{i}]\n{t}")
    return "\n\n".join(lines).strip()


def _reviewer_completion(
    db: Session,
    *,
    user_id: str,
    reviewer_model: str,
    reporter_model: str,
    system: str,
    user: str,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    conversation_history: list[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """검토자(서브) 호출 실패·쿼터·빈 응답 시 보고자와 동일한 메인 모델로 재시도. (본문, 실제 모델 id)."""
    return chat_completion_with_fallback(
        db,
        user_id=user_id,
        primary_model=reviewer_model,
        fallback_model=reporter_model,
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        conversation_history=conversation_history,
    )


def build_review_system_prompt(role_id: str, override: str | None) -> str:
    if override and override.strip():
        return override.strip() + _REVIEWER_INTENT_APPENDIX
    title, hint = ROLE_CATALOG[role_id]
    return (
        f"너는 {title} 역할을 수행하는 검토자다.\n{hint}\n"
        "한국어로만 답한다. 주어진 **보고 내용** 또는 **최근 질의·답변**을 근거로 "
        "검토 의견·질문·보완점을 말한다. **대화체**로 전달하며, 과장·허위 사실은 금지다."
        + _REVIEWER_INTENT_APPENDIX
    )


def run_review_bootstrap_pair(
    db: Session,
    *,
    user_id: str,
    reporter_model: str,
    reviewer_model: str,
    stream_id: str,
    role_id: str,
    system_prompt_override: str | None = None,
) -> tuple[str, str, str, str]:
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    report, rep_used = run_reporter_summary(db, user_id=user_id, model=reporter_model, stream_id=stream_id)
    review, rev_used = run_reviewer_on_report(
        db,
        user_id=user_id,
        model=reviewer_model,
        reporter_model=reporter_model,
        report_text=report,
        role_id=role_id,
        system_prompt_override=system_prompt_override,
    )
    return report.strip(), review.strip(), rep_used, rev_used


def run_reporter_summary(db: Session, *, user_id: str, model: str, stream_id: str) -> tuple[str, str]:
    transcript = _transcript_for_review(db, stream_id=stream_id, limit=40)
    out = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=_REPORTER_SYSTEM,
        user=f"다음은 채팅방 대화이다. 요약 보고를 작성하라.\n\n{transcript}",
        temperature=0.25,
    )
    return out, model


def run_reviewer_on_report(
    db: Session,
    *,
    user_id: str,
    model: str,
    reporter_model: str,
    report_text: str,
    role_id: str,
    system_prompt_override: str | None = None,
) -> tuple[str, str]:
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    system = build_review_system_prompt(role_id, system_prompt_override)
    body = (report_text or "").strip() or "(보고 없음)"
    if len(body) > 28000:
        body = body[:27997] + "…"
    text, used = _reviewer_completion(
        db,
        user_id=user_id,
        reviewer_model=model,
        reporter_model=reporter_model,
        system=system,
        user=(
            f"[보고 내용]\n{body}\n\n"
            "위 요약은 채팅을 보고자가 정리한 것이다. 그 안에서 드러나는 **보고자의 쟁점·요청·의도**를 파악한 뒤, "
            "네 역할 관점에서 그 의도에 맞는 **검토 의견·질문**을 **대화체**로 이어간다. "
            "검토를 요청하는 맥락이면 검토 의견을 대화로 바로 답하면 된다."
        ),
        temperature=0.3,
    )
    return text, used


def _last_user_assistant_turn(db: Session, *, stream_id: str) -> tuple[str, str] | None:
    rows = list(
        db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_stream_id == stream_id,
                ChatMessage.role.in_(("user", "assistant")),
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(12)
        )
        .scalars()
        .all()
    )
    if not rows or rows[0].role != "assistant":
        return None
    asst = (rows[0].content or "").strip()
    for m in rows[1:]:
        if m.role == "user":
            return ((m.content or "").strip(), asst)
    return None


def _latest_user_message_body(db: Session, *, stream_id: str) -> str | None:
    m = (
        db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_stream_id == stream_id,
                ChatMessage.role == "user",
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if not m:
        return None
    t = (m.content or "").strip()
    return t if t else None


def run_review_after_assistant_turn(
    db: Session,
    *,
    user_id: str,
    model: str,
    reporter_model: str,
    stream_id: str,
    role_id: str,
    reporter_brief: str,
    system_prompt_override: str | None = None,
    prior_reviewer_opinions: list[str] | None = None,
) -> tuple[str, str] | None:
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    pair = _last_user_assistant_turn(db, stream_id=stream_id)
    if not pair:
        return None
    uq, ans = pair
    brief = (reporter_brief or "").strip() or "(요약 없음)"
    if len(brief) > 12000:
        brief = brief[:11997] + "…"
    system = build_review_system_prompt(role_id, system_prompt_override)
    prior_blk = _format_prior_reviewer_block(prior_reviewer_opinions)
    user_block = (
        f"[지금까지의 요약 보고]\n{brief}\n\n"
        f"[방금 질의]\n{uq}\n\n"
        f"[행정 AI 답변]\n{ans}\n\n"
        "**먼저 [방금 질의]에서 보고자의 의도**(무엇을 묻거나 요청·확인하려는지)를 짚은 뒤, "
        "그 의도에 맞게 네 역할 관점에서 **검토 의견·질문을 대화체**로 답한다. "
        "검토해 달라는 요청이면 검토 의견이 곧 답변이다. 행정 AI 답변과 보고자 의도의 정합성도 본다."
    )
    if prior_blk:
        user_block = prior_blk + "\n\n" + user_block
    text, used = _reviewer_completion(
        db,
        user_id=user_id,
        reviewer_model=model,
        reporter_model=reporter_model,
        system=system,
        user=user_block,
        temperature=0.3,
    )
    return text, used


def run_review_reporter_context_latest_user(
    db: Session,
    *,
    user_id: str,
    model: str,
    reporter_model: str,
    stream_id: str,
    role_id: str,
    reporter_brief: str,
    system_prompt_override: str | None = None,
    prior_reviewer_opinions: list[str] | None = None,
) -> tuple[str, str]:
    """행정 AI 답변이 없는 턴: 최신 사용자 메시지만 검토 대상으로 삼는다."""
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    uq = _latest_user_message_body(db, stream_id=stream_id)
    if not uq:
        raise ValueError("사용자 메시지를 찾을 수 없습니다.")
    brief = (reporter_brief or "").strip() or "(요약 없음)"
    if len(brief) > 12000:
        brief = brief[:11997] + "…"
    if len(uq) > 12000:
        uq = uq[:11997] + "…"
    system = build_review_system_prompt(role_id, system_prompt_override)
    prior_blk = _format_prior_reviewer_block(prior_reviewer_opinions)
    user_block = (
        f"[지금까지의 요약 보고]\n{brief}\n\n"
        f"[보고자(사용자) 최신 메시지]\n{uq}\n\n"
        "이 턴에는 행정 AI 자동 답변이 없다. **위 최신 메시지의 의도**(질문·요청·반박·진행 공유·**검토 요청** 등)를 먼저 파악하고, "
        "그 의도에 맞게 **대화체**로 답한다. 검토를 요청한 뜻이면 **검토 의견**을 대화로 말하면 된다. "
        "[이전 검토자 의견]이 있으면 패널 대화의 연속으로 읽되, "
        "네 역할·페르소나를 유지한 채 **새로** 검토 의견·질문·실질적 답변을 제시한다. "
        "이전과 동일한 문단을 반복하지 않는다. 한국어로, 과장·허위는 금지."
    )
    if prior_blk:
        user_block = prior_blk + "\n\n" + user_block
    text, used = _reviewer_completion(
        db,
        user_id=user_id,
        reviewer_model=model,
        reporter_model=reporter_model,
        system=system,
        user=user_block,
        temperature=0.3,
    )
    return text, used


def run_review_followup_on_reporter_reply(
    db: Session,
    *,
    user_id: str,
    model: str,
    reporter_model: str,
    stream_id: str,
    role_id: str,
    reporter_brief: str | None,
    prior_reviewer_opinion: str,
    reporter_reply: str,
    system_prompt_override: str | None = None,
    prior_reporter_replies: list[str] | None = None,
    prior_reviewer_opinions: list[str] | None = None,
    composer_prompt: str | None = None,
) -> tuple[str, str]:
    """보고자 답변 생성 직후: 직전 검토 의견 + 보고자 답변을 읽고 검토자가 재검토한다."""
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    op = (prior_reviewer_opinion or "").strip()
    rep = (reporter_reply or "").strip()
    if not op:
        op = "(직전 검토자 의견 없음 — 보고자 답변·요약·대화만 맥락으로 삼는다.)"
    if not rep:
        raise ValueError("reporter_reply가 비었습니다.")
    brief = (reporter_brief or "").strip() or "(요약 없음)"
    if len(brief) > 12000:
        brief = brief[:11997] + "…"
    if len(op) > 12000:
        op = op[:11997] + "…"
    if len(rep) > 12000:
        rep = rep[:11997] + "…"
    cp = (composer_prompt or "").strip()
    base_system = build_review_system_prompt(role_id, system_prompt_override)
    if cp:
        system = base_system + _REVIEW_FOLLOWUP_COMPOSER_ONLY_APPENDIX
    else:
        system = base_system + _REVIEW_FOLLOWUP_APPENDIX
    chat_ctx = _transcript_for_review(db, stream_id=stream_id, limit=16)
    if len(chat_ctx) > 14000:
        chat_ctx = chat_ctx[:13997] + "…"
    prior_blk = _format_prior_reporter_block(prior_reporter_replies)
    earlier_rev_blk = _format_prior_reviewer_block(prior_reviewer_opinions)
    head = f"[최근 채팅(사용자·행정 AI)]\n{chat_ctx}\n\n"
    if prior_blk:
        head += prior_blk + "\n\n"
    if cp:
        if len(cp) > 12000:
            cp = cp[:11997] + "…"
        head = f"[보고자 입력 원문 — 이번 검토의 초점]\n{cp}\n\n" + head
    tail = (
        f"[요약 보고]\n{brief}\n\n"
        + f"[직전 검토자 의견]\n{op}\n\n"
        + f"[보고자 이번 답변]\n{rep}\n\n"
    )
    if cp:
        tail += (
            "위 **[보고자 입력 원문]**에 담긴 말에 대해 보고자가 이번 답변으로 전한 내용을 기준으로, "
            "검토자로서 **그에 대한 반응만** 말한다. 추가 질문·새 제안은 하지 않는다."
        )
    else:
        tail += "보고자 답변의 의도에 맞춰 재검토 의견을 **대화체**로 이어간다. 위 맥락을 반영하고, 과장·허위 사실은 금지다."
    if earlier_rev_blk:
        tail = earlier_rev_blk + "\n\n" + tail
    user_block = head + tail
    temp = 0.22 if cp else 0.28
    text, used = _reviewer_completion(
        db,
        user_id=user_id,
        reviewer_model=model,
        reporter_model=reporter_model,
        system=system,
        user=user_block,
        temperature=temp,
    )
    return text, used


def run_reporter_reply_to_reviewer(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    reporter_brief: str | None,
    reviewer_opinion: str,
    composer_prompt: str | None = None,
    prior_reporter_replies: list[str] | None = None,
    prior_reviewer_opinions: list[str] | None = None,
) -> tuple[str, str]:
    b = (reporter_brief or "").strip() or "(요약 없음)"
    rv = (reviewer_opinion or "").strip()
    cp = (composer_prompt or "").strip()
    if len(b) > 8000:
        b = b[:7997] + "…"
    if len(rv) > 12000:
        rv = rv[:11997] + "…"
    if len(cp) > 12000:
        cp = cp[:11997] + "…"
    transcript = _transcript_for_review(db, stream_id=stream_id, limit=22)
    if len(transcript) > 16000:
        transcript = transcript[:15997] + "…"
    prior_rep_blk = _format_prior_reporter_block(prior_reporter_replies)
    prior_rev_blk = _format_prior_reviewer_block(prior_reviewer_opinions)
    user_parts: list[str] = [
        f"[최근 채팅(사용자·행정 AI) — 답변 근거로만 인용]\n{transcript}",
    ]
    if cp:
        user_parts.append(f"[보고자 채팅 입력(작성 의도·표현·초안)]\n{cp}")
    if prior_rep_blk:
        user_parts.append(prior_rep_blk)
    user_parts.append(f"[요약 보고]\n{b}")
    if prior_rev_blk:
        user_parts.append(prior_rev_blk)
    if rv:
        user_parts.append(f"[검토자 의견 — 이번에 답할 최신 의견]\n{rv}")
    elif cp:
        user_parts.append("[검토자 의견 — 이번에 답할 최신 의견]\n(없음 — 채팅 입력과 요약·대화만 반영)")

    if rv and cp:
        tail = (
            "\n\n위 맥락에서 **보고자 채팅 입력**에 담긴 표현·의도를 우선 반영하고, "
            "**최신 검토자 의견**에 보고자로서 답하라."
        )
    elif rv:
        tail = "\n\n위 **최신** 검토자 의견에 보고자로서 답하라."
    elif cp:
        tail = (
            "\n\n위 **보고자 채팅 입력**을 바탕으로 보고자 입장에서 답변을 작성하라. "
            "(검토자 의견이 없으면 요약·대화 맥락에 맞춘다.)"
        )
    else:
        tail = "\n\n위 맥락에 맞게 보고자로서 답하라."

    user_body = "\n\n".join(user_parts) + tail
    temp = 0.36 if (prior_reporter_replies or prior_reviewer_opinions or cp) else 0.28
    out = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=_REPORTER_REPLY_SYSTEM,
        user=user_body,
        temperature=temp,
    )
    return out, model


def run_review_turn(
    db: Session,
    *,
    user_id: str,
    model: str,
    reporter_model: str,
    stream_id: str,
    role_id: str,
    system_prompt_override: str | None = None,
    reporter_brief: str | None = None,
    message_limit: int = 28,
    prior_reviewer_opinions: list[str] | None = None,
) -> tuple[str, str]:
    """reporter_brief 가 있으면 최근 1턴 검토, 없으면 전체 트랜스크립트 검토(레거시)."""
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    if reporter_brief and reporter_brief.strip():
        rb = reporter_brief.strip()
        one = run_review_after_assistant_turn(
            db,
            user_id=user_id,
            model=model,
            reporter_model=reporter_model,
            stream_id=stream_id,
            role_id=role_id,
            reporter_brief=rb,
            system_prompt_override=system_prompt_override,
            prior_reviewer_opinions=prior_reviewer_opinions,
        )
        if one is not None:
            return one
        return run_review_reporter_context_latest_user(
            db,
            user_id=user_id,
            model=model,
            reporter_model=reporter_model,
            stream_id=stream_id,
            role_id=role_id,
            reporter_brief=rb,
            system_prompt_override=system_prompt_override,
            prior_reviewer_opinions=prior_reviewer_opinions,
        )
    transcript = _transcript_for_review(db, stream_id=stream_id, limit=message_limit)
    system = build_review_system_prompt(role_id, system_prompt_override)
    user_block = (
        "다음은 이 채팅방의 최근 대화이다. **보고자(사용자) 메시지마다 의도**를 구분해 읽고, "
        "최신 턴의 의도에 맞춰 역할에 맞게 **대화체**로 검토·답한다. 검토 요청이면 검토 의견을 말로 전달한다.\n\n"
        f"{transcript}"
    )
    text, used = _reviewer_completion(
        db,
        user_id=user_id,
        reviewer_model=model,
        reporter_model=reporter_model,
        system=system,
        user=user_block,
        temperature=0.3,
    )
    return text, used
