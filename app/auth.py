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
    """
    Attempt to fetch and cache Clerk's JWKS at startup.
    Never raises — a failure here is non-fatal. The first authenticated
    /api/deals/* request will retry via _load_jwks().
    """
    global _jwks
    jwks_url = os.environ.get("CLERK_JWKS_URL", "").strip()
    if not jwks_url:
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
        print(f"WARNING: Failed to preload Clerk JWKS from {jwks_url}: {exc}. "
              "Will retry on first authenticated request.")


def _load_jwks() -> dict | None:
    """
    Lazy-load JWKS on demand. Called by get_current_user_id() when
    _jwks is None (startup fetch was skipped or failed).
    Returns the JWKS dict on success, None if URL is unset or fetch fails.
    """
    global _jwks
    jwks_url = os.environ.get("CLERK_JWKS_URL", "").strip()
    if not jwks_url:
        return None
    try:
        resp = httpx.get(jwks_url, timeout=10)
        resp.raise_for_status()
        _jwks = resp.json()
        print(f"Clerk JWKS lazy-loaded ({len(_jwks.get('keys', []))} key(s))")
        return _jwks
    except Exception as exc:
        print(f"WARNING: Clerk JWKS lazy-load failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# FastAPI dependency — injected only on /api/deals/* routes
# ---------------------------------------------------------------------------

def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """
    Verify the Clerk session JWT and return the user_id (sub claim).

    Raises HTTP 401 on invalid/expired token.
    Raises HTTP 503 if JWKS cannot be loaded (CLERK_JWKS_URL unset or unreachable).
    """
    jwks = _jwks or _load_jwks()
    if jwks is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not configured (CLERK_JWKS_URL missing).",
        )

    token = credentials.credentials
    try:
        # Decode header first to extract kid, then decode + verify with JWKS.
        payload = jwt.decode(
            token,
            jwks,
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
