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
MAX_SESSIONS = 1000


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

    _ROLE_PRIORITY = {"admin": 3, "write": 2, "read": 1}

    def get_org_role(self, org_id: str) -> str | None:
        """Return the highest org-level role for the given org, or None."""
        best_role: str | None = None
        for priv in self.privileges:
            if (isinstance(priv, dict)
                    and priv.get("scope") == "org"
                    and priv.get("org_id") == org_id):
                role = priv.get("role")
                if role and (
                    best_role is None
                    or self._ROLE_PRIORITY.get(role, 0) > self._ROLE_PRIORITY.get(best_role, 0)
                ):
                    best_role = role
        return best_role

    def can_write(self, org_id: str) -> bool:
        """True if the user has admin or write role for this org."""
        return self.get_org_role(org_id) in ("admin", "write")


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, SessionData] = {}

    def create(self, data: SessionData) -> str:
        if len(self._sessions) >= MAX_SESSIONS:
            oldest_key = min(self._sessions, key=lambda k: self._sessions[k].created_at)
            del self._sessions[oldest_key]
            logger.info("session_evicted reason=max_sessions_reached")
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
        """Hash (credential + cloud) → 64-char hex (full SHA-256).

        Same opaque format for both auth methods.
        No clear text is ever stored in the DB.
        """
        if email:
            raw = f"{email}|{cloud}"
        elif token:
            raw = f"{token}|{cloud}"
        else:
            raise ValueError("email or token required")
        return hashlib.sha256(raw.encode()).hexdigest()


session_store = SessionStore()
