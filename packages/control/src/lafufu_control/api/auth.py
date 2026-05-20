"""Optional shared-token auth for the control API + WebSocket bridge.

Design goals — deliberately minimal, no users/sessions/OAuth:

* **Optional.** When no token is configured the whole layer is inert, so dev
  and existing deployments behave exactly as before.
* **Loopback is trusted.** The on-device kiosk reaches the API over
  ``localhost``; those requests never need a token, so the Pi's own screen
  has zero friction.
* **One shared token** for everything else (a phone / laptop on the LAN),
  presented via the ``lafufu_token`` cookie (set by ``/api/auth/login``) or
  an ``Authorization: Bearer`` header for scripts.

The configured token lives on ``app.state.api_token`` so it is injectable in
tests and never read from the environment inside this module.
"""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from pydantic import BaseModel

COOKIE_NAME = "lafufu_token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # remember this browser for a year
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _presented_token(headers, cookies) -> str:
    """Pull the caller's token from a Bearer header, falling back to the cookie."""
    authorization = headers.get("authorization", "")
    if authorization[:7].lower() == "bearer ":
        return authorization[7:].strip()
    return cookies.get(COOKIE_NAME, "")


def _token_ok(presented: str, configured: str) -> bool:
    # Constant-time compare so a wrong token can't be guessed character-by-character.
    return bool(presented) and hmac.compare_digest(presented, configured)


def is_authorized(*, configured: str, client_host: str | None, headers, cookies) -> bool:
    """Single source of truth for the auth decision — shared by HTTP and WS.

    Returns True when: auth is disabled, the caller is loopback (the kiosk),
    or the caller presented the correct token.
    """
    if not configured:
        return True  # auth disabled — nothing to check
    if client_host in _LOOPBACK_HOSTS:
        return True  # the on-device kiosk
    return _token_ok(_presented_token(headers, cookies), configured)


async def require_auth(request: Request) -> None:
    """FastAPI dependency — guards every mutating/data router. 401 on failure."""
    if not is_authorized(
        configured=request.app.state.api_token,
        client_host=request.client.host if request.client else None,
        headers=request.headers,
        cookies=request.cookies,
    ):
        raise HTTPException(
            status_code=401,
            detail={"error_code": "unauthorized", "message": "access token required"},
        )


def ws_authorized(ws: WebSocket) -> bool:
    """WebSocket equivalent of ``require_auth`` — the browser sends the cookie
    on the same-origin handshake automatically, so no client code is needed."""
    return is_authorized(
        configured=ws.app.state.api_token,
        client_host=ws.client.host if ws.client else None,
        headers=ws.headers,
        cookies=ws.cookies,
    )


# --- /api/auth router ---------------------------------------------------------

router = APIRouter()


class _LoginBody(BaseModel):
    token: str


@router.get("/check", dependencies=[Depends(require_auth)])
async def check() -> dict:
    """Reachable only when already authorized. The web app calls this on load
    to decide whether to raise the lock screen — a 401 means "show it"."""
    return {"ok": True}


@router.post("/login")
async def login(body: _LoginBody, request: Request, response: Response) -> dict:
    """Exchange the shared token for a session cookie. Intentionally NOT behind
    ``require_auth`` — this is how an unauthorized client becomes authorized."""
    configured: str = request.app.state.api_token
    if not configured:
        return {"ok": True}  # auth disabled — nothing to grant
    if not _token_ok(body.token.strip(), configured):
        raise HTTPException(
            status_code=401,
            detail={"error_code": "bad_token", "message": "that token was not recognised"},
        )
    response.set_cookie(
        COOKIE_NAME,
        body.token.strip(),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,  # not readable by JS — XSS can't lift the token
        samesite="strict",  # not sent cross-site — closes CSRF
        path="/",
    )
    return {"ok": True}
