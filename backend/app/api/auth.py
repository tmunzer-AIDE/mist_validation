import logging
import time

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.session import SessionData, SessionStore, session_store
from app.services.mist_service import ALLOWED_HOSTS, get_cloud_list, get_host_for_cloud

logger = logging.getLogger(__name__)

# Simple in-memory rate limiter for login endpoint
_LOGIN_RATE_LIMIT: dict[str, list[float]] = {}
_LOGIN_RATE_WINDOW = 60  # seconds
_LOGIN_RATE_MAX = 10  # max attempts per window


def _check_login_rate_limit(client_ip: str) -> None:
    """Raise 429 if the client IP exceeds the login rate limit."""
    now = time.monotonic()
    timestamps = _LOGIN_RATE_LIMIT.get(client_ip, [])
    # Prune old entries
    timestamps = [t for t in timestamps if now - t < _LOGIN_RATE_WINDOW]
    if len(timestamps) >= _LOGIN_RATE_MAX:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    timestamps.append(now)
    if timestamps:
        _LOGIN_RATE_LIMIT[client_ip] = timestamps
    else:
        _LOGIN_RATE_LIMIT.pop(client_ip, None)

router = APIRouter()

SESSION_COOKIE = "session_id"
SESSION_MAX_AGE = 86400  # 24 hours


class LoginRequest(BaseModel):
    host: str | None = None  # direct host (e.g. "api.mist.com")
    cloud: str | None = None  # cloud key  (e.g. "Global 01")
    token: str | None = None
    email: str | None = None
    password: str | None = None
    two_factor: str | None = None


@router.get("/clouds")
async def list_clouds():
    return get_cloud_list()


def _set_session_cookie(response: JSONResponse, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_AGE,
    )


def _clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )


@router.post("/auth/login")
async def login(request: LoginRequest, req: Request):
    """
    Authenticate against Mist Cloud, create a server-side session,
    and return an httpOnly session cookie.

    Returns user info (email/token_name, orgs) but never raw credentials.
    """
    from mistapi import APISession

    _check_login_rate_limit(req.client.host if req.client else "unknown")

    # Resolve host and cloud
    cloud = request.cloud or "Global 01"
    host = request.host
    if not host:
        host = get_host_for_cloud(cloud)
    if not host:
        raise HTTPException(status_code=400, detail="Cloud or host required")
    if host not in ALLOWED_HOSTS:
        raise HTTPException(status_code=400, detail="Invalid API host")

    try:
        session = APISession(
            host=host,
            console_log_level=0,
            logging_log_level=20,
            show_cli_notif=False,
        )

        if request.token:
            # --- Token auth ---
            result = session.login_with_return(apitoken=request.token)
            if not result.get("authenticated"):
                error = result.get("error", "Invalid API token")
                detail = error.get("detail", str(error)) if isinstance(error, dict) else str(error)
                raise HTTPException(status_code=401, detail=detail)

            resp = session.mist_get("/api/v1/self")
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Failed to retrieve user info")

            user_data = _extract_user_data(resp.data)
            user_id = SessionStore.make_user_identifier(cloud=cloud, token=request.token)

            sess_data = SessionData(
                user_identifier=user_id,
                email=None,
                token_name=user_data.get("token_name"),
                org_ids=user_data["org_ids"],
                privileges=user_data["privileges"],
                mist_host=host,
                mist_cloud=cloud,
                mist_method="token",
                mist_token=request.token,
                mist_cookies=None,
                mist_csrftoken=None,
            )
            sess_id = session_store.create(sess_data)

            body = {
                "method": "token",
                "cloud": cloud,
                "host": host,
                "user_email": "",
                "token_name": user_data.get("token_name", ""),
                "orgs": user_data["orgs"],
            }
            response = JSONResponse(content=body)
            _set_session_cookie(response, sess_id)
            return response

        elif request.email and request.password:
            # --- Credential auth ---
            result = session.login_with_return(
                email=request.email,
                password=request.password,
                two_factor=request.two_factor,
            )

            if not result.get("authenticated"):
                error = result.get("error", "Authentication failed")
                if isinstance(error, dict):
                    if error.get("two_factor_required") and not error.get("two_factor_passed"):
                        return JSONResponse(content={
                            "two_factor_required": True,
                            "two_factor_passed": False,
                        })
                    detail = error.get("detail", str(error))
                else:
                    detail = str(error)
                raise HTTPException(status_code=401, detail=detail)

            cookies_dict = dict(session._session.cookies)
            csrftoken = session._csrftoken or None

            resp = session.mist_get("/api/v1/self")
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Failed to retrieve user info")

            user_data = _extract_user_data(resp.data)
            user_id = SessionStore.make_user_identifier(cloud=cloud, email=request.email)

            sess_data = SessionData(
                user_identifier=user_id,
                email=request.email,
                token_name=None,
                org_ids=user_data["org_ids"],
                privileges=user_data["privileges"],
                mist_host=host,
                mist_cloud=cloud,
                mist_method="credentials",
                mist_token=None,
                mist_cookies=cookies_dict,
                mist_csrftoken=csrftoken,
            )
            sess_id = session_store.create(sess_data)

            body = {
                "method": "credentials",
                "cloud": cloud,
                "host": host,
                "user_email": request.email,
                "token_name": "",
                "orgs": user_data["orgs"],
            }
            response = JSONResponse(content=body)
            _set_session_cookie(response, sess_id)
            return response

        else:
            raise HTTPException(status_code=400, detail="Token or email+password required")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("auth_login_failed error=%s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Authentication failed")


@router.get("/auth/session")
async def get_session(session_id: str = Cookie(default="")):
    """Return current session info, or 401 if not authenticated."""
    sess = session_store.get(session_id)
    if not sess:
        raise HTTPException(status_code=401, detail="Not authenticated")

    orgs = _orgs_from_privileges(sess.privileges)
    return {
        "method": sess.mist_method,
        "cloud": sess.mist_cloud,
        "host": sess.mist_host,
        "user_email": sess.email or "",
        "token_name": sess.token_name or "",
        "orgs": orgs,
    }


@router.post("/auth/logout")
async def logout(session_id: str = Cookie(default="")):
    """Destroy session and clear cookie."""
    if session_id:
        session_store.delete(session_id)
    response = JSONResponse(content={"ok": True})
    _clear_session_cookie(response)
    return response


def _orgs_from_privileges(privileges: list[dict]) -> list[dict]:
    """Build org list from raw privileges array (keeps highest role per org)."""
    role_priority = SessionData._ROLE_PRIORITY
    orgs_seen: dict[str, dict] = {}
    for priv in privileges:
        if isinstance(priv, dict) and priv.get("scope") == "org":
            org_id = priv.get("org_id", "")
            org_name = priv.get("name", "") or priv.get("org_name", "") or org_id[:8]
            role = priv.get("role", "read")
            if org_id:
                existing = orgs_seen.get(org_id)
                if not existing or role_priority.get(role, 0) > role_priority.get(existing.get("role", ""), 0):
                    orgs_seen[org_id] = {"id": org_id, "name": org_name, "role": role}
    return list(orgs_seen.values())


def _extract_user_data(user_data: dict) -> dict:
    """Extract user info and orgs from /api/v1/self response."""
    privileges = user_data.get("privileges", [])
    orgs = _orgs_from_privileges(privileges)
    return {
        "email": user_data.get("email", ""),
        "token_name": user_data.get("name", ""),
        "privileges": privileges,
        "org_ids": {o["id"] for o in orgs},
        "orgs": orgs,
    }
