"""Shared FastAPI dependencies."""

from fastapi import Cookie, HTTPException

from app.core.session import SessionData, session_store


def get_session(session_id: str = Cookie(default="")) -> SessionData:
    """Resolve session from httpOnly cookie, or 401."""
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return s
