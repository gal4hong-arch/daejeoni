"""채팅 분할 검토: 보고 요약 → 검토자 의견 → 이후 턴별 검토 · 보고자 답변."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage
from app.services.llm_client import chat_completion
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
    "직전 검토 요구와 보고자 답변·이전 보고자 답변을 비교한다. "
    "**구체적 사실·조사결과·일정·산출물·인용 근거**가 새로 생겼는지 먼저 평가한다. "
    "추상적 계획만 반복이면 「진전 없음」이라 짚고, **남은 결정·리스크**만 2~4개로 짧게 제시한다. "
    "이미 답변·채팅에서 충족된 항목에 대해 첫째·둘째·셋째로 **같은 질문을 되풀이하지 않는다**. "
    "4문단 이내, 격식·서론 최소화."
)

_REPORTER_SYSTEM = (
    "너는 같은 채팅방의 보고자(작성 담당)다. 아래 대화 전체를 읽고 "
    "상급에게 올릴 수 있게 **간결한 요약 보고**만 작성한다. "
    "불필요한 서론 없이 핵심 사실·쟁점·필요 조치를 bullet 또는 짧은 문단으로. "
    "한국어, 5~12문장 또는 동등 분량. 과장·허위 금지."
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


def build_review_system_prompt(role_id: str, override: str | None) -> str:
    if override and override.strip():
        return override.strip()
    title, hint = ROLE_CATALOG[role_id]
    return (
        f"너는 {title} 역할을 수행하는 검토자다.\n{hint}\n"
        "한국어로만 답한다. 주어진 **보고 내용** 또는 **최근 질의·답변**을 근거로 "
        "검토 의견·질문·보완점을 5문단 이내로 제시한다. 과장·허위 사실은 금지다."
    )


def run_review_bootstrap_pair(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    role_id: str,
    system_prompt_override: str | None = None,
) -> tuple[str, str]:
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    report = run_reporter_summary(db, user_id=user_id, model=model, stream_id=stream_id)
    review = run_reviewer_on_report(
        db,
        user_id=user_id,
        model=model,
        report_text=report,
        role_id=role_id,
        system_prompt_override=system_prompt_override,
    )
    return report.strip(), review.strip()


def run_reporter_summary(db: Session, *, user_id: str, model: str, stream_id: str) -> str:
    transcript = _transcript_for_review(db, stream_id=stream_id, limit=40)
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=_REPORTER_SYSTEM,
        user=f"다음은 채팅방 대화이다. 요약 보고를 작성하라.\n\n{transcript}",
        temperature=0.25,
    )


def run_reviewer_on_report(
    db: Session,
    *,
    user_id: str,
    model: str,
    report_text: str,
    role_id: str,
    system_prompt_override: str | None = None,
) -> str:
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    system = build_review_system_prompt(role_id, system_prompt_override)
    body = (report_text or "").strip() or "(보고 없음)"
    if len(body) > 28000:
        body = body[:27997] + "…"
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=system,
        user=f"[보고 내용]\n{body}\n\n위 보고를 네 역할 관점에서 검토하라.",
        temperature=0.3,
    )


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
    stream_id: str,
    role_id: str,
    reporter_brief: str,
    system_prompt_override: str | None = None,
) -> str | None:
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
    user_block = (
        f"[지금까지의 요약 보고]\n{brief}\n\n"
        f"[방금 질의]\n{uq}\n\n"
        f"[행정 AI 답변]\n{ans}\n\n"
        "위 맥락에서 최신 질의·답변을 중심으로 네 역할 관점에서 검토하라."
    )
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=system,
        user=user_block,
        temperature=0.3,
    )


def run_review_reporter_context_latest_user(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    role_id: str,
    reporter_brief: str,
    system_prompt_override: str | None = None,
) -> str:
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
    user_block = (
        f"[지금까지의 요약 보고]\n{brief}\n\n"
        f"[보고자(사용자) 최신 메시지]\n{uq}\n\n"
        "이 턴에는 행정 AI 자동 답변이 없다. 위 사용자 메시지를 읽고, 네 역할 관점에서 "
        "검토 의견·질문·실질적 답변을 제시한다. 한국어로, 과장·허위는 금지."
    )
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=system,
        user=user_block,
        temperature=0.3,
    )


def run_review_followup_on_reporter_reply(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    role_id: str,
    reporter_brief: str | None,
    prior_reviewer_opinion: str,
    reporter_reply: str,
    system_prompt_override: str | None = None,
    prior_reporter_replies: list[str] | None = None,
) -> str:
    """보고자 답변 생성 직후: 직전 검토 의견 + 보고자 답변을 읽고 검토자가 재검토한다."""
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    op = (prior_reviewer_opinion or "").strip()
    rep = (reporter_reply or "").strip()
    if not op:
        raise ValueError("prior_reviewer_opinion이 비었습니다.")
    if not rep:
        raise ValueError("reporter_reply가 비었습니다.")
    brief = (reporter_brief or "").strip() or "(요약 없음)"
    if len(brief) > 12000:
        brief = brief[:11997] + "…"
    if len(op) > 12000:
        op = op[:11997] + "…"
    if len(rep) > 12000:
        rep = rep[:11997] + "…"
    base_system = build_review_system_prompt(role_id, system_prompt_override)
    system = base_system + _REVIEW_FOLLOWUP_APPENDIX
    chat_ctx = _transcript_for_review(db, stream_id=stream_id, limit=16)
    if len(chat_ctx) > 14000:
        chat_ctx = chat_ctx[:13997] + "…"
    prior_blk = _format_prior_reporter_block(prior_reporter_replies)
    head = f"[최근 채팅(사용자·행정 AI)]\n{chat_ctx}\n\n"
    if prior_blk:
        head += prior_blk + "\n\n"
    user_block = (
        head
        + f"[요약 보고]\n{brief}\n\n"
        + f"[직전 검토자 의견]\n{op}\n\n"
        + f"[보고자 이번 답변]\n{rep}\n\n"
        + "위 맥락을 반영해 재검토한다. 과장·허위 사실은 금지다."
    )
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=system,
        user=user_block,
        temperature=0.28,
    )


def run_reporter_reply_to_reviewer(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    reporter_brief: str | None,
    reviewer_opinion: str,
    prior_reporter_replies: list[str] | None = None,
    prior_reviewer_opinions: list[str] | None = None,
) -> str:
    b = (reporter_brief or "").strip() or "(요약 없음)"
    rv = (reviewer_opinion or "").strip()
    if len(b) > 8000:
        b = b[:7997] + "…"
    if len(rv) > 12000:
        rv = rv[:11997] + "…"
    transcript = _transcript_for_review(db, stream_id=stream_id, limit=22)
    if len(transcript) > 16000:
        transcript = transcript[:15997] + "…"
    prior_rep_blk = _format_prior_reporter_block(prior_reporter_replies)
    prior_rev_blk = _format_prior_reviewer_block(prior_reviewer_opinions)
    user_parts: list[str] = [
        f"[최근 채팅(사용자·행정 AI) — 답변 근거로만 인용]\n{transcript}",
    ]
    if prior_rep_blk:
        user_parts.append(prior_rep_blk)
    user_parts.append(f"[요약 보고]\n{b}")
    if prior_rev_blk:
        user_parts.append(prior_rev_blk)
    user_parts.append(f"[검토자 의견 — 이번에 답할 최신 의견]\n{rv}")
    user_body = "\n\n".join(user_parts) + "\n\n위 **최신** 검토자 의견에 보고자로서 답하라."
    temp = 0.36 if (prior_reporter_replies or prior_reviewer_opinions) else 0.28
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=_REPORTER_REPLY_SYSTEM,
        user=user_body,
        temperature=temp,
    )


def run_review_turn(
    db: Session,
    *,
    user_id: str,
    model: str,
    stream_id: str,
    role_id: str,
    system_prompt_override: str | None = None,
    reporter_brief: str | None = None,
    message_limit: int = 28,
) -> str:
    """reporter_brief 가 있으면 최근 1턴 검토, 없으면 전체 트랜스크립트 검토(레거시)."""
    if role_id not in ROLE_CATALOG:
        raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
    if reporter_brief and reporter_brief.strip():
        rb = reporter_brief.strip()
        one = run_review_after_assistant_turn(
            db,
            user_id=user_id,
            model=model,
            stream_id=stream_id,
            role_id=role_id,
            reporter_brief=rb,
            system_prompt_override=system_prompt_override,
        )
        if one is not None:
            return one
        return run_review_reporter_context_latest_user(
            db,
            user_id=user_id,
            model=model,
            stream_id=stream_id,
            role_id=role_id,
            reporter_brief=rb,
            system_prompt_override=system_prompt_override,
        )
    transcript = _transcript_for_review(db, stream_id=stream_id, limit=message_limit)
    system = build_review_system_prompt(role_id, system_prompt_override)
    user_block = (
        "다음은 이 채팅방의 최근 대화이다. 역할에 맞게 검토하라.\n\n"
        f"{transcript}"
    )
    return chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=system,
        user=user_block,
    )
