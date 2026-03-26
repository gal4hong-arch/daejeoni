"""Supabase Auth JWT 검증 및 데모용 헤더."""

from typing import Annotated, Any

import jwt
from jwt import PyJWKClient
from fastapi import Header, HTTPException

from app.config import get_settings

_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_url_from_supabase_url(supabase_url: str) -> str | None:
    u = (supabase_url or "").strip().rstrip("/")
    if not u:
        return None
    return f"{u}/auth/v1/.well-known/jwks.json"


# JWKS로 검증하는 비대칭 알고리즘 (Supabase 프로젝트 설정에 따라 RS256 또는 ES256 등)
_JWKS_ALGORITHMS = frozenset({"RS256", "ES256"})


def _decode_jwks_asymmetric(token: str, jwks_url: str, alg: str) -> dict[str, Any]:
    if alg not in _JWKS_ALGORITHMS:
        raise jwt.InvalidAlgorithmError(f"JWKS 경로에서 지원하지 않는 alg: {alg}")
    if jwks_url not in _jwks_clients:
        _jwks_clients[jwks_url] = PyJWKClient(jwks_url)
    signing_key = _jwks_clients[jwks_url].get_signing_key_from_jwt(token)
    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            audience="authenticated",
        )
    except jwt.InvalidAudienceError:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            options={"verify_aud": False},
        )


def _decode_supabase_access_token(token: str, secret: str, supabase_url: str) -> dict[str, Any]:
    """
    Supabase access_token 검증.
    - 레거시: HS256 + JWT Secret
    - 비대칭 키: RS256 / ES256 + JWKS (`SUPABASE_URL` …/auth/v1/.well-known/jwks.json)
    secret이 비어 있으면 서명 생략(로컬 편의 — 운영에서는 SECRET 또는 JWKS 검증 경로 권장).
    """
    jwks_url = _jwks_url_from_supabase_url(supabase_url)
    header = jwt.get_unverified_header(token)
    alg = (header.get("alg") or "").upper()

    if not (secret or "").strip():
        return jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False},
            algorithms=["HS256", "RS256", "ES256"],
        )

    if alg in _JWKS_ALGORITHMS:
        if not jwks_url:
            raise jwt.InvalidAlgorithmError(
                f"{alg} 토큰인데 SUPABASE_URL 이 없어 JWKS 검증을 할 수 없습니다."
            )
        return _decode_jwks_asymmetric(token, jwks_url, alg)

    if alg == "HS256":
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

    raise jwt.InvalidAlgorithmError(f"지원하지 않는 JWT alg: {alg or '(없음)'}")


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
            payload = _decode_supabase_access_token(token, secret, s.supabase_url or "")
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
                payload = _decode_supabase_access_token(token, secret, s.supabase_url or "")
                email = payload.get("email")
            except jwt.PyJWTError:
                email = None
    if not email and x_demo_user and "@" in x_demo_user:
        email = x_demo_user.strip()
    return {"user_id": user_id, "email": email}
