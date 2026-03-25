import json

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import TopicSession, UserModelPreference


def resolve_model(
    db: Session,
    *,
    user_id: str,
    topic_session_id: str | None,
    task: str,
) -> str:
    """우선순위: topic override → task model → user default → system fallback."""
    settings = get_settings()
    if topic_session_id:
        topic = db.get(TopicSession, topic_session_id)
        if topic and topic.model_override:
            return topic.model_override

    pref = db.get(UserModelPreference, user_id)
    if pref and pref.task_models_json:
        try:
            tasks = json.loads(pref.task_models_json)
            if isinstance(tasks, dict) and task in tasks and tasks[task]:
                return str(tasks[task])
        except json.JSONDecodeError:
            pass

    if pref and pref.default_model:
        return pref.default_model

    return settings.system_fallback_model
