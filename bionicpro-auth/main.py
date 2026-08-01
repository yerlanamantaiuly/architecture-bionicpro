"""bionicpro-auth — BFF for Keycloak OAuth2/OIDC with server-side sessions."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", KEYCLOAK_URL)
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "reports-realm")
CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "bionicpro-auth")
CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "bionicpro-auth-secret")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8001/auth/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
COOKIE_NAME = os.getenv("COOKIE_NAME", "bionicpro_session")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
YANDEX_IDP_ALIAS = os.getenv("YANDEX_IDP_ALIAS", "yandex")
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "5e79e67eaecc4daaa87a3b5a5e09c05e")
YANDEX_CLIENT_SECRET = os.getenv(
    "YANDEX_CLIENT_SECRET", "b64e2bf97bdb4bbfa08f474595de7d40"
)
PROFILE_DB_PATH = os.getenv("PROFILE_DB_PATH", "/data/profiles.db")

logger = logging.getLogger("bionicpro-auth")

_FERNET_KEY = os.getenv("TOKEN_ENCRYPTION_KEY") or Fernet.generate_key().decode()
_fernet = Fernet(_FERNET_KEY.encode() if isinstance(_FERNET_KEY, str) else _FERNET_KEY)

REALM_BASE = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"
REALM_PUBLIC = f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"
REALM_ROOT = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"

app = FastAPI(title="bionicpro-auth", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        FRONTEND_URL,
        "http://localhost:3000",
        "http://10.2.67.21:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Profile DB (consent + Yandex profile)
# ---------------------------------------------------------------------------


def _init_db() -> None:
    Path(PROFILE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_sub TEXT PRIMARY KEY,
                consent_granted INTEGER NOT NULL DEFAULT 0,
                yandex_profile_json TEXT,
                updated_at REAL NOT NULL
            )
            """
        )


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(PROFILE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup() -> None:
    _init_db()


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------


@dataclass
class SessionData:
    access_token: str
    refresh_token_encrypted: bytes
    expires_at: float
    userinfo: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


_sessions: dict[str, SessionData] = {}
_pending_pkce: dict[str, dict[str, Any]] = {}
# Keycloak 21 OIDC broker always sends nonce and requires it back in id_token.
# disableNonce is ignored on KC 21 (added only in later versions).
# Token exchange is server-to-server (no state), so keep the latest authorize nonce.
_last_yandex_nonce: str | None = None


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
    if time.time() >= session.expires_at - 10:
        await _refresh_access_token(session)


def _rotate_session(old_id: str, session: SessionData) -> str:
    new_id = _new_session_id()
    _sessions[new_id] = session
    _sessions.pop(old_id, None)
    return new_id


def _profile_row(user_sub: str) -> sqlite3.Row | None:
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM user_profiles WHERE user_sub = ?", (user_sub,)
        ).fetchone()


async def _fetch_yandex_profile(access_token: str) -> dict[str, Any]:
    """Pull profile from Yandex using stored broker token (or fallback to userinfo)."""
    async with httpx.AsyncClient() as client:
        broker = await client.get(
            f"{REALM_ROOT}/broker/{YANDEX_IDP_ALIAS}/token",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if broker.status_code == 200:
            broker_payload = broker.json()
            yandex_token = broker_payload.get("access_token")
            if yandex_token:
                info = await client.get(
                    "https://login.yandex.ru/info",
                    params={"format": "json"},
                    headers={"Authorization": f"OAuth {yandex_token}"},
                )
                if info.status_code == 200:
                    return info.json()

        # Fallback: Keycloak userinfo (mapped claims)
        ui = await client.get(
            f"{REALM_BASE}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if ui.status_code == 200:
            return ui.json()
    return {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/login")
async def login(idp: str | None = None) -> RedirectResponse:
    """Start Authorization Code + PKCE. Optional ?idp=yandex for broker hint."""
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    _pending_pkce[state] = {"code_verifier": verifier, "created_at": time.time()}

    params: dict[str, str] = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if idp:
        params["kc_idp_hint"] = idp
    return RedirectResponse(f"{REALM_PUBLIC}/auth?{urlencode(params)}")


@app.get("/auth/login/yandex")
async def login_yandex() -> RedirectResponse:
    return await login(idp=YANDEX_IDP_ALIAS)


@app.get("/auth/yandex-authorize")
async def yandex_authorize_proxy(request: Request) -> RedirectResponse:
    """
    Keycloak OIDC broker always prepends scope 'openid'.
    Yandex OAuth does not accept 'openid' → invalid_scope.
    This proxy strips 'openid' and forwards the browser to Yandex.
    """
    global _last_yandex_nonce
    params = {k: v for k, v in request.query_params.multi_items()}
    raw_scope = params.get("scope", "")
    scopes = [s for s in raw_scope.replace(",", " ").split() if s and s != "openid"]
    if not scopes:
        scopes = ["login:info"]
    params["scope"] = " ".join(scopes)
    # Remember nonce for synthesized id_token; Yandex itself does not echo it.
    nonce = params.pop("nonce", None)
    if nonce:
        _last_yandex_nonce = nonce
        logger.info("yandex-authorize: stored nonce for id_token echo")
    return RedirectResponse(
        f"https://oauth.yandex.ru/authorize?{urlencode(params)}",
        status_code=302,
    )


@app.post("/auth/yandex-token")
async def yandex_token_proxy(request: Request) -> JSONResponse:
    """
    Yandex returns access_token but no id_token.
    Keycloak OIDC broker requires id_token → synthesize one from Yandex profile.
    """
    form = await request.form()
    data = {k: str(v) for k, v in form.items()}

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_resp = await client.post("https://oauth.yandex.ru/token", data=data)
        payload = token_resp.json()
        if token_resp.status_code != 200 or "access_token" not in payload:
            return JSONResponse(content=payload, status_code=token_resp.status_code)

        access_token = payload["access_token"]
        info_resp = await client.get(
            "https://login.yandex.ru/info",
            params={"format": "json"},
            headers={"Authorization": f"OAuth {access_token}"},
        )
        profile = info_resp.json() if info_resp.status_code == 200 else {}

    now = int(time.time())
    expires_in = int(payload.get("expires_in", 3600))
    sub = str(profile.get("id") or profile.get("psuid") or secrets.token_hex(8))
    emails = profile.get("emails") or []
    email = profile.get("default_email") or (emails[0] if emails else None)
    secret = data.get("client_secret") or YANDEX_CLIENT_SECRET

    global _last_yandex_nonce
    id_claims = {
        "iss": "https://login.yandex.ru",
        "sub": sub,
        "aud": data.get("client_id") or YANDEX_CLIENT_ID,
        "iat": now,
        "exp": now + expires_in,
        "auth_time": now,
        "preferred_username": profile.get("login"),
        "email": email,
        "name": profile.get("real_name") or profile.get("display_name"),
        "given_name": profile.get("first_name"),
        "family_name": profile.get("last_name"),
        "login": profile.get("login"),
        "default_email": email,
        "first_name": profile.get("first_name"),
        "last_name": profile.get("last_name"),
    }
    # KC 21 requires nonce claim in id_token (disableNonce is a no-op there).
    if _last_yandex_nonce:
        id_claims["nonce"] = _last_yandex_nonce
        logger.info("yandex-token: echoed nonce into synthesized id_token")
        _last_yandex_nonce = None
    else:
        logger.warning("yandex-token: no stored nonce — Keycloak broker may fail")
    payload["id_token"] = jwt.encode(id_claims, secret, algorithm="HS256")
    payload["token_type"] = payload.get("token_type") or "bearer"
    return JSONResponse(content=payload)


@app.get("/auth/yandex-userinfo")
async def yandex_userinfo_proxy(request: Request) -> JSONResponse:
    """Yandex expects Authorization: OAuth <token>, Keycloak sends Bearer."""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").replace("OAuth ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    async with httpx.AsyncClient(timeout=30.0) as client:
        info_resp = await client.get(
            "https://login.yandex.ru/info",
            params={"format": "json"},
            headers={"Authorization": f"OAuth {token}"},
        )
    if info_resp.status_code != 200:
        return JSONResponse(content=info_resp.json(), status_code=info_resp.status_code)

    profile = info_resp.json()
    emails = profile.get("emails") or []
    email = profile.get("default_email") or (emails[0] if emails else None)
    return JSONResponse(
        {
            "sub": str(profile.get("id") or profile.get("psuid")),
            "login": profile.get("login"),
            "preferred_username": profile.get("login"),
            "email": email,
            "default_email": email,
            "name": profile.get("real_name") or profile.get("display_name"),
            "first_name": profile.get("first_name"),
            "last_name": profile.get("last_name"),
            "given_name": profile.get("first_name"),
            "family_name": profile.get("last_name"),
        }
    )


@app.get("/auth/callback")
async def callback(
    code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
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
            raise HTTPException(
                status_code=401, detail=f"Token exchange failed: {token_resp.text}"
            )

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

    # Ask for profile-data consent when not yet granted
    sub = userinfo.get("sub")
    needs_consent = True
    if sub:
        row = _profile_row(sub)
        needs_consent = not (row and row["consent_granted"])

    dest = f"{FRONTEND_URL}/?consent=1" if needs_consent else FRONTEND_URL
    response = RedirectResponse(url=dest, status_code=302)
    _set_session_cookie(response, session_id)
    return response


@app.get("/auth/me")
async def me(request: Request) -> JSONResponse:
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await _ensure_fresh_access(session)
    new_id = _rotate_session(old_id, session)
    sub = session.userinfo.get("sub")
    row = _profile_row(sub) if sub else None
    consent = bool(row and row["consent_granted"])
    profile = json.loads(row["yandex_profile_json"]) if row and row["yandex_profile_json"] else None

    body = {
        "authenticated": True,
        "session_id": new_id,
        "consent_granted": consent,
        "profile": profile,
        "user": {
            "sub": sub,
            "username": session.userinfo.get("preferred_username"),
            "email": session.userinfo.get("email"),
            "name": session.userinfo.get("name"),
        },
    }
    response = JSONResponse(content=body)
    _set_session_cookie(response, new_id)
    return response


@app.get("/auth/consent")
async def consent_status(request: Request) -> JSONResponse:
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    await _ensure_fresh_access(session)
    sub = session.userinfo.get("sub")
    row = _profile_row(sub) if sub else None
    return JSONResponse(
        {
            "consent_granted": bool(row and row["consent_granted"]),
            "has_profile": bool(row and row["yandex_profile_json"]),
        }
    )


@app.post("/auth/consent/accept")
async def consent_accept(request: Request) -> JSONResponse:
    """User allows storing Yandex profile → fetch from Yandex and save to DB."""
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await _ensure_fresh_access(session)
    sub = session.userinfo.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="Missing user sub")

    profile = await _fetch_yandex_profile(session.access_token)
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO user_profiles (user_sub, consent_granted, yandex_profile_json, updated_at)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(user_sub) DO UPDATE SET
              consent_granted = 1,
              yandex_profile_json = excluded.yandex_profile_json,
              updated_at = excluded.updated_at
            """,
            (sub, json.dumps(profile, ensure_ascii=False), time.time()),
        )

    new_id = _rotate_session(old_id, session)
    response = JSONResponse({"ok": True, "profile": profile, "session_id": new_id})
    _set_session_cookie(response, new_id)
    return response


@app.post("/auth/consent/deny")
async def consent_deny(request: Request) -> JSONResponse:
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await _ensure_fresh_access(session)
    sub = session.userinfo.get("sub")
    if sub:
        with _db() as conn:
            conn.execute(
                """
                INSERT INTO user_profiles (user_sub, consent_granted, yandex_profile_json, updated_at)
                VALUES (?, 0, NULL, ?)
                ON CONFLICT(user_sub) DO UPDATE SET
                  consent_granted = 0,
                  yandex_profile_json = NULL,
                  updated_at = excluded.updated_at
                """,
                (sub, time.time()),
            )

    new_id = _rotate_session(old_id, session)
    response = JSONResponse({"ok": True, "consent_granted": False, "session_id": new_id})
    _set_session_cookie(response, new_id)
    return response


@app.get("/auth/profile")
async def get_profile(request: Request) -> JSONResponse:
    old_id = request.cookies.get(COOKIE_NAME)
    session = _get_session(old_id)
    if not session or not old_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    await _ensure_fresh_access(session)
    sub = session.userinfo.get("sub")
    row = _profile_row(sub) if sub else None
    if not row or not row["consent_granted"]:
        raise HTTPException(status_code=403, detail="Consent required")
    profile = json.loads(row["yandex_profile_json"]) if row["yandex_profile_json"] else {}
    new_id = _rotate_session(old_id, session)
    response = JSONResponse({"profile": profile, "session_id": new_id})
    _set_session_cookie(response, new_id)
    return response


@app.get("/auth/session")
async def validate_session(request: Request) -> JSONResponse:
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
        }
    )
    _set_session_cookie(response, new_id)
    return response


@app.get("/auth/token")
async def internal_access_token(request: Request) -> JSONResponse:
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
