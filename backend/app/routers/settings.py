from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth_deps import get_current_user_id, get_current_user_profile
from app.config import get_settings
from app.db.session import get_db
from app.db.models import AuditLog, UserApiKey, UserModelPreference
from app.schemas.api import (
    AuditLogOut,
    LLMKeysIn,
    LLMKeysPutOut,
    MeOut,
    UserDataResetIn,
    UserDataResetOut,
    UserSettingsIn,
    UserSettingsOut,
)
from app.services.user_data_reset import reset_scopes
from app.services.user_api_keys import has_usable_stored_key, store_user_api_key

router = APIRouter()


def _providers_with_keys(db: Session, user_id: str) -> dict[str, bool]:
    s = get_settings()
    return {
        "openai": bool((s.openai_api_key or "").strip()) or has_usable_stored_key(db, user_id, "openai"),
        "anthropic": bool((s.anthropic_api_key or "").strip()) or has_usable_stored_key(db, user_id, "anthropic"),
        "google": bool((s.google_api_key or "").strip()) or has_usable_stored_key(db, user_id, "google"),
    }


def _user_stored_keys_only(db: Session, user_id: str) -> dict[str, bool]:
    return {
        "openai": has_usable_stored_key(db, user_id, "openai"),
        "anthropic": has_usable_stored_key(db, user_id, "anthropic"),
        "google": has_usable_stored_key(db, user_id, "google"),
    }


def _load_user_settings(user_id: str, db: Session) -> UserSettingsOut:
    p = db.get(UserModelPreference, user_id)
    return UserSettingsOut(
        user_id=user_id,
        default_model=p.default_model if p else "",
        task_models={},
        dual_api_reporter_sub_first=bool(getattr(p, "dual_api_reporter_sub_first", False)) if p else False,
        providers_with_keys=_providers_with_keys(db, user_id),
        user_stored_keys=_user_stored_keys_only(db, user_id),
    )


def _upsert_api_key(db: Session, user_id: str, provider: str, plain: str) -> None:
    row = (
        db.query(UserApiKey)
        .filter(UserApiKey.user_id == user_id, UserApiKey.provider == provider)
        .one_or_none()
    )
    enc = store_user_api_key(plain)
    if row:
        row.encrypted_key = enc
    else:
        db.add(UserApiKey(user_id=user_id, provider=provider, encrypted_key=enc))


# ----- /me 는 {user_id} 보다 먼저 등록 -----
@router.get("/me", response_model=MeOut)
def read_me(profile: dict = Depends(get_current_user_profile)) -> MeOut:
    return MeOut(user_id=profile["user_id"], email=profile.get("email"))


@router.get("/me/settings", response_model=UserSettingsOut)
def read_me_settings(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> UserSettingsOut:
    return _load_user_settings(user_id, db)


@router.put("/me/settings", response_model=UserSettingsOut)
def update_me_settings(
    body: UserSettingsIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> UserSettingsOut:
    p = db.get(UserModelPreference, user_id)
    if not p:
        p = UserModelPreference(user_id=user_id)
        db.add(p)
    p.default_model = body.default_model
    p.task_models_json = "{}"
    p.dual_api_reporter_sub_first = body.dual_api_reporter_sub_first
    if body.openai_api_key:
        _upsert_api_key(db, user_id, "openai", body.openai_api_key)
    db.commit()
    return _load_user_settings(user_id, db)


@router.put("/me/llm-keys", response_model=LLMKeysPutOut)
def update_me_llm_keys(
    body: LLMKeysIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> LLMKeysPutOut:
    if body.openai_api_key:
        _upsert_api_key(db, user_id, "openai", body.openai_api_key)
    if body.anthropic_api_key:
        _upsert_api_key(db, user_id, "anthropic", body.anthropic_api_key)
    if body.google_api_key:
        _upsert_api_key(db, user_id, "google", body.google_api_key)
    db.commit()
    return LLMKeysPutOut(status="ok", providers_with_keys=_providers_with_keys(db, user_id))


@router.delete("/me/llm-keys", response_model=LLMKeysPutOut)
def delete_me_llm_keys(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> LLMKeysPutOut:
    """사용자가 DB에 저장한 API 키 전부 삭제(서버 OPENAI_API_KEY는 유지)."""
    db.execute(delete(UserApiKey).where(UserApiKey.user_id == user_id))
    db.commit()
    return LLMKeysPutOut(status="ok", providers_with_keys=_providers_with_keys(db, user_id))


@router.post("/me/data-reset", response_model=UserDataResetOut)
def reset_my_data(
    body: UserDataResetIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> UserDataResetOut:
    """영역별 데이터 삭제(chat, review_drafts, embeddings, prompts, topics, logs, api_keys)."""
    try:
        detail = reset_scopes(db, user_id, body.scopes)
        db.commit()
        return UserDataResetOut(ok=True, detail=detail)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/me/audit-logs", response_model=list[AuditLogOut])
def list_my_audit_logs(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(100, ge=1, le=500),
) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.user_id == user_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        ).all()
    )


@router.get("/{user_id}/settings", response_model=UserSettingsOut)
def read_user_settings(user_id: str, db: Session = Depends(get_db)) -> UserSettingsOut:
    return _load_user_settings(user_id, db)


@router.put("/{user_id}/settings", response_model=UserSettingsOut)
def update_user_settings(user_id: str, body: UserSettingsIn, db: Session = Depends(get_db)) -> UserSettingsOut:
    p = db.get(UserModelPreference, user_id)
    if not p:
        p = UserModelPreference(user_id=user_id)
        db.add(p)
    p.default_model = body.default_model
    p.task_models_json = "{}"
    p.dual_api_reporter_sub_first = body.dual_api_reporter_sub_first
    if body.openai_api_key:
        _upsert_api_key(db, user_id, "openai", body.openai_api_key)
    db.commit()
    return _load_user_settings(user_id, db)
