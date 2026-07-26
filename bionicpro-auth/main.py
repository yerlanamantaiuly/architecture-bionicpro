"""bionicpro-auth — BFF for Keycloak OAuth2/OIDC with server-side sessions."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Internal URL for server-to-server calls (docker network)
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
# Public URL for browser redirects (must be reachable from the user's browser)
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", KEYCLOAK_URL)
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "reports-realm")
CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "bionicpro-auth")
CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "bionicpro-auth-secret")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8001/auth/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
COOKIE_NAME = os.getenv("COOKIE_NAME", "bionicpro_session")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")
# Session must outlive access_token (≤2 min). Default 30 minutes.
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
# Fernet key for encrypting refresh tokens at rest in memory
_FERNET_KEY = os.getenv("TOKEN_ENCRYPTION_KEY") or Fernet.generate_key().decode()
_fernet = Fernet(_FERNET_KEY.encode() if isinstance(_FERNET_KEY, str) else _FERNET_KEY)

REALM_BASE = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"
REALM_PUBLIC = f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"

app = FastAPI(title="bionicpro-auth", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# In-memory stores (encrypted refresh tokens)
# ---------------------------------------------------------------------------


@dataclass
class SessionData:
    access_token: str
    refresh_token_encrypted: bytes
    expires_at: float  # access token expiry (unix)
    userinfo: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# session_id -> SessionData
_sessions: dict[str, SessionData] = {}
# temporary PKCE state: state -> {code_verifier, created_at}
_pending_pkce: dict[str, dict[str, Any]] = {}


def _encrypt(token: str) -> bytes:
    return _fernet.encrypt(token.encode())


def _decrypt(blob: bytes) -> str:
    return _fernet.decrypt(blob).decode()


def _new_session_id() -> str:
    return secrets.token_urlsafe(32)


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _get_session(session_id: str | None) -> SessionData | None:
    if not session_id:
        return None
    data = _sessions.get(session_id)
    if not data:
        return None
    if time.time() - data.created_at > SESSION_TTL_SECONDS:
        _sessions.pop(session_id, None)
        return None
    return data


async def _refresh_access_token(session: SessionData) -> None:
    refresh = _decrypt(session.refresh_token_encrypted)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{REALM_BASE}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to refresh token")
    payload = resp.json()
    session.access_token = payload["access_token"]
    if "refresh_token" in payload:
        session.refresh_token_encrypted = _encrypt(payload["refresh_token"])
    session.expires_at = time.time() + int(payload.get("expires_in", 120))


async def _ensure_fresh_access(session: SessionData) -> None:
    # refresh ~10s before expiry
    if time.time() >= session.expires_at - 10:
        await _refresh_access_token(session)


def _rotate_session(old_id: str, session: SessionData) -> str:
    """Re-bind tokens to a new session id (anti session-fixation)."""
    new_id = _new_session_id()
    _sessions[new_id] = session
    _sessions.pop(old_id, None)
    return new_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/login")
async def login() -> RedirectResponse:
    """Start Authorization Code + PKCE flow against Keycloak."""
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    _pending_pkce[state] = {"code_verifier": verifier, "created_at": time.time()}

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{REALM_PUBLIC}/auth?{urlencode(params)}")


@app.get("/auth/callback")
async def callback(code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/?error={error}")
    if not code or not state or state not in _pending_pkce:
        raise HTTPException(status_code=400, detail="Invalid callback")

    pending = _pending_pkce.pop(state)
    verifier = pending["code_verifier"]

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"{REALM_BASE}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code_verifier": verifier,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=401, detail=f"Token exchange failed: {token_resp.text}")

        tokens = token_resp.json()
        access = tokens["access_token"]
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise HTTPException(status_code=500, detail="No refresh_token from Keycloak")

        userinfo_resp = await client.get(
            f"{REALM_BASE}/userinfo",
            headers={"Authorization": f"Bearer {access}"},
        )
        userinfo = userinfo_resp.json() if userinfo_resp.status_code == 200 else {}

    session_id = _new_session_id()
    _sessions[session_id] = SessionData(
        access_token=access,
        refresh_token_encrypted=_encrypt(refresh),
        expires_at=time.time() + int(tokens.get("expires_in", 120)),
        userinfo=userinfo,
    )

    response = RedirectResponse(url=FRONTEND_URL, status_code=302)
    _set_session_cookie(response, session_id)
    return response


@app.get("/auth/me")
async def me(request: Request) -> JSONResponse:
    """Return current user; rotates session id on success (anti fixation)."""
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await _ensure_fresh_access(session)
    new_id = _rotate_session(old_id, session)

    body = {
        "authenticated": True,
        "session_id": new_id,
        "user": {
            "sub": session.userinfo.get("sub"),
            "username": session.userinfo.get("preferred_username"),
            "email": session.userinfo.get("email"),
            "name": session.userinfo.get("name"),
        },
    }
    response = JSONResponse(content=body)
    _set_session_cookie(response, new_id)
    return response


@app.get("/auth/session")
async def validate_session(request: Request) -> JSONResponse:
    """
    Protected resource check used by other services / FE.
    Ensures fresh access_token and rotates session id.
    """
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await _ensure_fresh_access(session)
    new_id = _rotate_session(old_id, session)

    response = JSONResponse(
        content={
            "valid": True,
            "session_id": new_id,
            "user": {
                "sub": session.userinfo.get("sub"),
                "username": session.userinfo.get("preferred_username"),
                "email": session.userinfo.get("email"),
                "roles": session.userinfo.get("realm_access", {}).get("roles", [])
                if isinstance(session.userinfo.get("realm_access"), dict)
                else [],
            },
            # access_token is NOT returned to the browser — only for internal callers
            # that share the same trust boundary would use a separate internal API.
        }
    )
    _set_session_cookie(response, new_id)
    return response


@app.get("/auth/token")
async def internal_access_token(request: Request) -> JSONResponse:
    """
    Internal helper for trusted backend services (reports-api).
    Still rotates session. Never call this from the browser in production
    without network isolation; for the course it is cookie-gated.
    """
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await _ensure_fresh_access(session)
    new_id = _rotate_session(old_id, session)

    response = JSONResponse(
        content={
            "access_token": session.access_token,
            "session_id": new_id,
            "user": session.userinfo,
        }
    )
    _set_session_cookie(response, new_id)
    return response


@app.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    session_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(session_id)
    if session and session_id:
        try:
            refresh = _decrypt(session.refresh_token_encrypted)
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{REALM_BASE}/logout",
                    data={
                        "client_id": CLIENT_ID,
                        "client_secret": CLIENT_SECRET,
                        "refresh_token": refresh,
                    },
                )
        except Exception:
            pass
        _sessions.pop(session_id, None)

    response = JSONResponse({"ok": True})
    _clear_session_cookie(response)
    return response
