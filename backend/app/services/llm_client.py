"""OpenAI / Anthropic / Google(Gemini) 통합 채팅 완성."""

from __future__ import annotations

import re
import time

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import UserApiKey
from app.services.user_api_keys import read_user_api_key_stored


def _stored_row_exists(db: Session, user_id: str, provider: str) -> bool:
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == provider))
        .scalar_one_or_none()
    )
    return row is not None


def _raise_missing_llm_key(db: Session, user_id: str, provider: str, label: str) -> None:
    """행은 있는데 복호화 실패 시 FERNET_KEY 불일치 안내."""
    if _stored_row_exists(db, user_id, provider):
        raise RuntimeError(
            f"{label} API 키는 DB에 있으나 복호화하지 못했습니다. "
            "배포 서버의 FERNET_KEY가 키를 저장할 때와 동일한지 확인하거나, 「유저 정보」에서 해당 키를 다시 저장하세요."
        )
    raise RuntimeError(f"{label} API 키가 없습니다. 사이드바·유저 정보에서 저장하세요.")


def _get_openai_key(db: Session, user_id: str) -> str | None:
    s = get_settings()
    if s.openai_api_key:
        return s.openai_api_key
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "openai"))
        .scalar_one_or_none()
    )
    if row:
        return read_user_api_key_stored(row.encrypted_key)
    return None


def _get_anthropic_key(db: Session, user_id: str) -> str | None:
    s = get_settings()
    if (s.anthropic_api_key or "").strip():
        return s.anthropic_api_key.strip()
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "anthropic"))
        .scalar_one_or_none()
    )
    if row:
        return read_user_api_key_stored(row.encrypted_key)
    return None


def _get_google_key(db: Session, user_id: str) -> str | None:
    s = get_settings()
    if (s.google_api_key or "").strip():
        return s.google_api_key.strip()
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "google"))
        .scalar_one_or_none()
    )
    if row:
        return read_user_api_key_stored(row.encrypted_key)
    return None


def _provider_for_model(model: str) -> str:
    m = model.lower()
    if "claude" in m or m.startswith("anthropic."):
        return "anthropic"
    if "gemini" in m or m.startswith("google/"):
        return "google"
    return "openai"


def _history_for_openai(
    conversation_history: list[tuple[str, str]] | None,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not conversation_history:
        return out
    for role, content in conversation_history:
        r = role if role in ("user", "assistant") else "user"
        c = (content or "").strip()
        if not c:
            continue
        out.append({"role": r, "content": c})
    return out


def _history_block_for_gemini(conversation_history: list[tuple[str, str]] | None) -> str:
    if not conversation_history:
        return ""
    lines: list[str] = ["[이전 대화(최근 순서)]"]
    for role, content in conversation_history:
        c = (content or "").strip()
        if not c:
            continue
        label = "사용자" if role == "user" else "행정 AI"
        lines.append(f"{label}: {c}")
    lines.append("")
    return "\n".join(lines)


def chat_completion(
    db: Session,
    *,
    user_id: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    conversation_history: list[tuple[str, str]] | None = None,
    meta_out: dict | None = None,
) -> str:
    t0 = time.perf_counter()
    provider = _provider_for_model(model)
    if provider == "anthropic":
        from anthropic import Anthropic

        key = _get_anthropic_key(db, user_id)
        if not key:
            _raise_missing_llm_key(db, user_id, "anthropic", "Anthropic")
        client = Anthropic(api_key=key)
        mid = model if "/" not in model else model.split("/")[-1]
        if not mid.startswith("claude"):
            mid = "claude-3-5-sonnet-20241022"
        hist_msgs: list[dict[str, str]] = []
        for role, content in conversation_history or []:
            r = role if role in ("user", "assistant") else "user"
            c = (content or "").strip()
            if not c:
                continue
            hist_msgs.append({"role": r, "content": c})
        hist_msgs.append({"role": "user", "content": user})
        msg = client.messages.create(
            model=mid,
            max_tokens=min(max_tokens, 8192),
            temperature=temperature,
            system=system,
            messages=hist_msgs,
        )
        parts = msg.content[0]
        out = getattr(parts, "text", str(parts)) if parts else ""
        if meta_out is not None:
            meta_out.update(
                {
                    "provider": provider,
                    "model_requested": model,
                    "model_effective": mid,
                    "llm_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "fallback_used": False,
                    "stream_mode": "sync",
                    "stream_capable": True,
                }
            )
        return out

    if provider == "google":
        import google.generativeai as genai

        key = _get_google_key(db, user_id)
        if not key:
            _raise_missing_llm_key(db, user_id, "google", "Google(Gemini)")
        genai.configure(api_key=key)
        mid = model
        if "gemini" not in mid:
            mid = "gemini-3.1-flash-lite-preview"
        mid = re.sub(r"^google/", "", mid)
        mdl = genai.GenerativeModel(mid)
        gemini_user = _history_block_for_gemini(conversation_history) + user
        try:
            cfg = genai.types.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens)
            r = mdl.generate_content(f"{system}\n\n---\n\n{gemini_user}", generation_config=cfg)
        except Exception:
            r = mdl.generate_content(f"{system}\n\n---\n\n{gemini_user}")
        out = (r.text or "").strip()
        if meta_out is not None:
            meta_out.update(
                {
                    "provider": provider,
                    "model_requested": model,
                    "model_effective": mid,
                    "llm_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "fallback_used": False,
                    "stream_mode": "sync",
                    "stream_capable": True,
                }
            )
        return out

    key = _get_openai_key(db, user_id)
    if not key:
        _raise_missing_llm_key(db, user_id, "openai", "OpenAI")
    client = OpenAI(api_key=key)
    oa_messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    oa_messages.extend(_history_for_openai(conversation_history))
    oa_messages.append({"role": "user", "content": user})
    r = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=oa_messages,
    )
    out = (r.choices[0].message.content or "").strip()
    if meta_out is not None:
        meta_out.update(
            {
                "provider": provider,
                "model_requested": model,
                "model_effective": model,
                "llm_ms": round((time.perf_counter() - t0) * 1000, 2),
                "fallback_used": False,
                "stream_mode": "sync",
                "stream_capable": True,
            }
        )
    return out


def chat_completion_with_fallback(
    db: Session,
    *,
    user_id: str,
    primary_model: str,
    fallback_model: str | None,
    system: str,
    user: str,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    conversation_history: list[tuple[str, str]] | None = None,
    meta_out: dict | None = None,
) -> tuple[str, str]:
    """검토자(서브) API 등 primary 실패·빈 응답 시 메인(fallback) 모델로 재시도. (본문, 실제 사용 모델 id) 반환."""
    pm = (primary_model or "").strip()
    fb = (fallback_model or "").strip()
    if not fb or fb == pm:
        cm = {} if meta_out is not None else None
        out = chat_completion(
            db,
            user_id=user_id,
            model=pm,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            conversation_history=conversation_history,
            meta_out=cm,
        )
        if meta_out is not None:
            meta_out.update(cm or {})
            meta_out["fallback_used"] = False
        return out, pm

    try:
        pm_meta = {} if meta_out is not None else None
        out = chat_completion(
            db,
            user_id=user_id,
            model=pm,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            conversation_history=conversation_history,
            meta_out=pm_meta,
        )
    except Exception:
        fb_meta = {} if meta_out is not None else None
        out = chat_completion(
            db,
            user_id=user_id,
            model=fb,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            conversation_history=conversation_history,
            meta_out=fb_meta,
        )
        if meta_out is not None:
            meta_out.update(fb_meta or {})
            meta_out["fallback_used"] = True
            meta_out["fallback_reason"] = "primary_exception"
            meta_out["primary_model"] = pm
            meta_out["fallback_model"] = fb
        return out, fb

    if (out or "").strip():
        if meta_out is not None:
            meta_out.update(pm_meta or {})
            meta_out["fallback_used"] = False
            meta_out["primary_model"] = pm
            meta_out["fallback_model"] = fb
        return out, pm
    fb_meta = {} if meta_out is not None else None
    out = chat_completion(
        db,
        user_id=user_id,
        model=fb,
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        conversation_history=conversation_history,
        meta_out=fb_meta,
    )
    if meta_out is not None:
        meta_out.update(fb_meta or {})
        meta_out["fallback_used"] = True
        meta_out["fallback_reason"] = "primary_empty"
        meta_out["primary_model"] = pm
        meta_out["fallback_model"] = fb
    return out, fb
