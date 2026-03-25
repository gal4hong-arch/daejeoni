import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_deps import get_current_user_id, get_current_user_profile
from app.config import get_settings
from app.db.session import get_db
from app.db.models import AuditLog, UserApiKey, UserModelPreference
from app.schemas.api import (
    AuditLogOut,
    LLMKeysIn,
    MeOut,
    UserDataResetIn,
    UserDataResetOut,
    UserSettingsIn,
    UserSettingsOut,
)
from app.services.user_data_reset import reset_scopes
from app.services.crypto_keys import encrypt_secret

router = APIRouter()


def _has_stored_key(db: Session, user_id: str, provider: str) -> bool:
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == provider))
        .scalar_one_or_none()
    )
    return row is not None


def _providers_with_keys(db: Session, user_id: str) -> dict[str, bool]:
    s = get_settings()
    return {
        "openai": bool((s.openai_api_key or "").strip()) or _has_stored_key(db, user_id, "openai"),
        "anthropic": _has_stored_key(db, user_id, "anthropic"),
        "google": _has_stored_key(db, user_id, "google"),
    }


def _load_user_settings(user_id: str, db: Session) -> UserSettingsOut:
    p = db.get(UserModelPreference, user_id)
    tasks: dict[str, str] = {}
    if p and p.task_models_json:
        try:
            raw = json.loads(p.task_models_json)
            if isinstance(raw, dict):
                tasks = {str(k): str(v) for k, v in raw.items()}
        except json.JSONDecodeError:
            pass
    return UserSettingsOut(
        user_id=user_id,
        default_model=p.default_model if p else "",
        task_models=tasks,
        providers_with_keys=_providers_with_keys(db, user_id),
    )


def _upsert_api_key(db: Session, user_id: str, provider: str, plain: str) -> None:
    row = (
        db.query(UserApiKey)
        .filter(UserApiKey.user_id == user_id, UserApiKey.provider == provider)
        .one_or_none()
    )
    enc = encrypt_secret(plain)
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
    p.task_models_json = json.dumps(body.task_models, ensure_ascii=False)
    if body.openai_api_key:
        _upsert_api_key(db, user_id, "openai", body.openai_api_key)
    db.commit()
    return _load_user_settings(user_id, db)


@router.put("/me/llm-keys")
def update_me_llm_keys(
    body: LLMKeysIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    if body.openai_api_key:
        _upsert_api_key(db, user_id, "openai", body.openai_api_key)
    if body.anthropic_api_key:
        _upsert_api_key(db, user_id, "anthropic", body.anthropic_api_key)
    if body.google_api_key:
        _upsert_api_key(db, user_id, "google", body.google_api_key)
    db.commit()
    return {"status": "ok"}


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
    p.task_models_json = json.dumps(body.task_models, ensure_ascii=False)
    if body.openai_api_key:
        _upsert_api_key(db, user_id, "openai", body.openai_api_key)
    db.commit()
    return _load_user_settings(user_id, db)
