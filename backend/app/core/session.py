"""
Server-side session store for authenticated users.

Sessions are kept in memory with a 24-hour TTL.  The scheduler in main.py
calls ``session_store.cleanup_expired()`` periodically to evict stale entries.
"""

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SESSION_TTL = 86400  # 24 hours


@dataclass
class SessionData:
    user_identifier: str  # opaque sha256 hash — same format for both auth methods
    email: str | None  # None for API-token auth (ephemeral, for frontend display only)
    token_name: str | None  # None for credential auth (ephemeral, for frontend display only)
    org_ids: set[str]  # org_ids extracted from privileges
    privileges: list[dict]  # raw privileges array from /api/v1/self
    mist_host: str
    mist_cloud: str
    mist_method: str  # "token" or "credentials"
    mist_token: str | None
    mist_cookies: dict | None
    mist_csrftoken: str | None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def __post_init__(self):
        if not self.expires_at:
            self.expires_at = self.created_at + SESSION_TTL


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, SessionData] = {}

    def create(self, data: SessionData) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = data
        return session_id

    def get(self, session_id: str) -> SessionData | None:
        s = self._sessions.get(session_id)
        if s and time.time() < s.expires_at:
            return s
        if s:
            del self._sessions[session_id]
        return None

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def count(self) -> int:
        return len(self._sessions)

    def cleanup_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._sessions.items() if now >= v.expires_at]
        for k in expired:
            del self._sessions[k]
        if expired:
            logger.info("session_cleanup removed=%d remaining=%d", len(expired), len(self._sessions))

    @staticmethod
    def make_user_identifier(
        *, cloud: str, email: str | None = None, token: str | None = None
    ) -> str:
        """Hash (credential + cloud) → 16-char hex.

        Same opaque format for both auth methods.
        No clear text is ever stored in the DB.
        """
        if email:
            raw = f"{email}|{cloud}"
        elif token:
            raw = f"{token}|{cloud}"
        else:
            raise ValueError("email or token required")
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


session_store = SessionStore()
