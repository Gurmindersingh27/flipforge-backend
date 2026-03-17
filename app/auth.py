"""
Clerk JWT verification for /api/deals/* routes.

Flow:
  1. App startup calls preload_jwks() to cache Clerk's public keys.
  2. get_current_user_id() is a FastAPI dependency injected only on
     /api/deals/* endpoints — no existing analysis routes are touched.
  3. The Clerk session JWT is RS256-signed. We verify it with python-jose
     against the JWKS fetched from CLERK_JWKS_URL.
  4. The 'sub' claim is the Clerk user ID (e.g. "user_2XxXx") — this is
     stored as user_id in the saved_deals table.

Required env var:
  CLERK_JWKS_URL — e.g. https://<your-clerk-domain>/.well-known/jwks.json
"""

from __future__ import annotations

import os
import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError, ExpiredSignatureError

# ---------------------------------------------------------------------------
# JWKS cache (populated once at startup via preload_jwks())
# ---------------------------------------------------------------------------

_jwks: dict | None = None

_bearer = HTTPBearer()


def preload_jwks() -> None:
    """Fetch and cache Clerk's JWKS at app startup. Raises on failure."""
    global _jwks
    jwks_url = os.environ.get("CLERK_JWKS_URL", "").strip()
    if not jwks_url:
        # Warn but don't crash — deals endpoints will 503 if called, but
        # analysis endpoints continue to work with no auth requirement.
        print(
            "WARNING: CLERK_JWKS_URL is not set. "
            "/api/deals/* endpoints will return 503 until this is configured."
        )
        return
    try:
        resp = httpx.get(jwks_url, timeout=10)
        resp.raise_for_status()
        _jwks = resp.json()
        print(f"Clerk JWKS loaded ({len(_jwks.get('keys', []))} key(s))")
    except Exception as exc:
        print(f"WARNING: Failed to load Clerk JWKS from {jwks_url}: {exc}")


# ---------------------------------------------------------------------------
# FastAPI dependency — injected only on /api/deals/* routes
# ---------------------------------------------------------------------------

def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """
    Verify the Clerk session JWT and return the user_id (sub claim).

    Raises HTTP 401 on invalid/expired token.
    Raises HTTP 503 if JWKS has not been loaded (misconfigured deployment).
    """
    if _jwks is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not configured (CLERK_JWKS_URL missing).",
        )

    token = credentials.credentials
    try:
        # Decode header first to extract kid, then decode + verify with JWKS.
        payload = jwt.decode(
            token,
            _jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk JWTs use azp not aud
        )
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing sub claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id
