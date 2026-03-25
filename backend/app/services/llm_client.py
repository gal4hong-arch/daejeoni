"""OpenAI / Anthropic / Google(Gemini) 통합 채팅 완성."""

from __future__ import annotations

import re

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import UserApiKey
from app.services.crypto_keys import decrypt_secret


def _get_openai_key(db: Session, user_id: str) -> str | None:
    s = get_settings()
    if s.openai_api_key:
        return s.openai_api_key
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "openai"))
        .scalar_one_or_none()
    )
    if row:
        return decrypt_secret(row.encrypted_key)
    return None


def _get_anthropic_key(db: Session, user_id: str) -> str | None:
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "anthropic"))
        .scalar_one_or_none()
    )
    if row:
        return decrypt_secret(row.encrypted_key)
    return None


def _get_google_key(db: Session, user_id: str) -> str | None:
    row = (
        db.execute(select(UserApiKey).where(UserApiKey.user_id == user_id, UserApiKey.provider == "google"))
        .scalar_one_or_none()
    )
    if row:
        return decrypt_secret(row.encrypted_key)
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
) -> str:
    provider = _provider_for_model(model)
    if provider == "anthropic":
        from anthropic import Anthropic

        key = _get_anthropic_key(db, user_id)
        if not key:
            raise RuntimeError("Anthropic API 키가 없습니다. 사이드바에서 저장하세요.")
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
        return getattr(parts, "text", str(parts)) if parts else ""

    if provider == "google":
        import google.generativeai as genai

        key = _get_google_key(db, user_id)
        if not key:
            raise RuntimeError("Google(Gemini) API 키가 없습니다.")
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
        return (r.text or "").strip()

    key = _get_openai_key(db, user_id)
    if not key:
        raise RuntimeError("OpenAI API 키가 없습니다.")
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
    return (r.choices[0].message.content or "").strip()
