from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import TopicSession, UserModelPreference
from app.services.user_api_keys import has_usable_stored_key


def providers_with_keys_ordered(db: Session, user_id: str, settings) -> list[str]:
    """OpenAI → Anthropic → Google 순으로, 키가 있는 제공자만."""
    order = ["openai", "anthropic", "google"]
    out: list[str] = []
    for p in order:
        if p == "openai":
            if (settings.openai_api_key or "").strip() or has_usable_stored_key(db, user_id, "openai"):
                out.append(p)
        elif p == "anthropic":
            if (settings.anthropic_api_key or "").strip() or has_usable_stored_key(db, user_id, "anthropic"):
                out.append(p)
        elif p == "google":
            if (settings.google_api_key or "").strip() or has_usable_stored_key(db, user_id, "google"):
                out.append(p)
    return out


def default_model_for_provider(provider: str, settings) -> str:
    """이중 API 시 두 번째(서브) 제공자용 — 비용이 낮은 티어 모델."""
    if provider == "openai":
        return settings.system_fallback_model or "gpt-4o-mini"
    if provider == "anthropic":
        return "claude-3-5-haiku-20241022"
    if provider == "google":
        return "gemini-2.5-flash-lite"
    return settings.system_fallback_model or "gpt-4o-mini"


def maybe_promote_model_for_complex_query(model: str, user_message: str, settings) -> str:
    """
    복잡한 질의에서만 조건부 승격(기본 off).
    - MODEL_COMPLEX_PROMOTE=true 일 때만 동작
    - 체감 지연 우선 기본 정책과 충돌하지 않도록 승격 기준을 보수적으로 둔다.
    """
    if not bool(getattr(settings, "model_complex_promote", False)):
        return model
    msg = (user_message or "").strip()
    if len(msg) < 1100:
        return model
    low = (model or "").lower()
    if low == "gpt-4o-mini":
        return "gpt-4.1"
    if low == "claude-3-5-haiku-20241022":
        return "claude-3-5-sonnet-20241022"
    if low in {"gemini-2.5-flash-lite", "gemini-2.5-flash"}:
        return "gemini-2.5-pro"
    return model


def resolve_dialogue_reporter_reviewer_models(
    db: Session,
    *,
    user_id: str,
    topic_session_id: str | None,
) -> tuple[str, str]:
    """대화 모드(검토 분할): (보고자 모델, 검토자 모델).

    API 키가 2개 이상일 때 기본은 보고자=메인(기본 모델)·검토자=서브(저가).
    ``UserModelPreference.dual_api_reporter_sub_first`` 가 True 이면 둘을 맞바꾼다.
    키가 1개뿐이면 동일 모델을 반환한다.
    """
    settings = get_settings()
    if topic_session_id:
        topic = db.get(TopicSession, topic_session_id)
        if topic and topic.model_override:
            m = topic.model_override
            return m, m

    pref = db.get(UserModelPreference, user_id)
    swap = bool(getattr(pref, "dual_api_reporter_sub_first", False)) if pref else False

    prov = providers_with_keys_ordered(db, user_id, settings)
    if len(prov) < 2:
        single = resolve_model(db, user_id=user_id, topic_session_id=topic_session_id, task="chat")
        return single, single

    main_m = resolve_model(db, user_id=user_id, topic_session_id=topic_session_id, task="chat")
    sub_m = resolve_model(db, user_id=user_id, topic_session_id=topic_session_id, task="review")
    if not swap:
        return main_m, sub_m
    return sub_m, main_m


def resolve_model(
    db: Session,
    *,
    user_id: str,
    topic_session_id: str | None,
    task: str,
) -> str:
    """우선순위: topic override → (review+이중 API 시 서브 제공자 저가 모델) → 사용자 기본 → 시스템 fallback."""
    settings = get_settings()
    if topic_session_id:
        topic = db.get(TopicSession, topic_session_id)
        if topic and topic.model_override:
            return topic.model_override

    pref = db.get(UserModelPreference, user_id)

    if task == "review":
        prov = providers_with_keys_ordered(db, user_id, settings)
        if len(prov) >= 2:
            return default_model_for_provider(prov[1], settings)

    if pref and pref.default_model:
        return pref.default_model

    prov = providers_with_keys_ordered(db, user_id, settings)
    if prov:
        return default_model_for_provider(prov[0], settings)

    return settings.system_fallback_model
