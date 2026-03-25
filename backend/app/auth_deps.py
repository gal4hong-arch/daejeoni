"""Supabase Auth JWT 검증 및 데모용 헤더."""

from typing import Annotated, Any

import jwt
from fastapi import Header, HTTPException

from app.config import get_settings


def _decode_supabase_access_token(token: str, secret: str) -> dict[str, Any]:
    """HS256 access_token 디코딩. secret이 있으면 서명 검증, 없으면 서명 생략(로컬 편의 — 운영에서는 SECRET 필수)."""
    if (secret or "").strip():
        try:
            return jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except jwt.InvalidAudienceError:
            return jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
    return jwt.decode(
        token,
        options={"verify_signature": False, "verify_aud": False},
        algorithms=["HS256"],
    )


def get_current_user_id(
    authorization: Annotated[str | None, Header()] = None,
    x_demo_user: Annotated[str | None, Header()] = None,
) -> str:
    """
    우선순위: Authorization Bearer (Supabase access_token) → (옵션) X-Demo-User.
    """
    s = get_settings()
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="빈 토큰")
        secret = (s.supabase_jwt_secret or "").strip()
        try:
            payload = _decode_supabase_access_token(token, secret)
        except jwt.PyJWTError as e:
            raise HTTPException(status_code=401, detail=f"토큰 검증 실패: {e}") from e
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="토큰에 sub 없음")
        return str(sub)

    if s.allow_demo_user_header and x_demo_user and x_demo_user.strip():
        return x_demo_user.strip()[:128]

    raise HTTPException(
        status_code=401,
        detail="로그인이 필요합니다. Supabase 로그인 또는 X-Demo-User 헤더(개발 전용)를 사용하세요.",
    )


def get_current_user_profile(
    authorization: Annotated[str | None, Header()] = None,
    x_demo_user: Annotated[str | None, Header()] = None,
) -> dict:
    """sub, email(있으면) 반환."""
    s = get_settings()
    user_id = get_current_user_id(authorization=authorization, x_demo_user=x_demo_user)
    email: str | None = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        secret = (s.supabase_jwt_secret or "").strip()
        if token:
            try:
                payload = _decode_supabase_access_token(token, secret)
                email = payload.get("email")
            except jwt.PyJWTError:
                email = None
    if not email and x_demo_user and "@" in x_demo_user:
        email = x_demo_user.strip()
    return {"user_id": user_id, "email": email}
