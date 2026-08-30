from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .throttle import LoginThrottle
from atlas_core.identity import IdentityStore, Principal


SESSION_COOKIE = "atlas_session"
CSRF_HEADER = "x-csrf-token"
SESSION_MAX_AGE = 60 * 60 * 24 * 14
OWNER_SUBJECT = "owner"
DEFAULT_AUTH_ENV_PATH = Path("instance/companion-auth.env")


@dataclass(frozen=True)
class SessionData:
    subject: str
    csrf_token: str


@dataclass(frozen=True)
class CookiePolicy:
    """Session cookie flags. Production stays Secure unless explicitly overridden."""

    secure: bool
    samesite: str
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "secure": self.secure,
            "samesite": self.samesite,
            "httponly": True,
            "mode": self.mode,
        }


def resolve_cookie_policy() -> CookiePolicy:
    """Production default is Secure cookies.

    Localhost HTTP development must set ``ATLAS_ENV=development`` or
    ``ATLAS_SECURE_COOKIES=0`` explicitly. Those flags never auto-enable from
    a public bind — ``__main__`` refuses non-loopback hosts.
    """

    explicit = os.environ.get("ATLAS_SECURE_COOKIES")
    if explicit is not None and explicit.strip() != "":
        secure = explicit.strip().lower() not in {"0", "false", "no"}
        mode = "explicit_secure" if secure else "explicit_insecure_dev"
        return CookiePolicy(secure=secure, samesite="lax", mode=mode)

    env = os.environ.get("ATLAS_ENV", "").strip().lower()
    if env in {"development", "dev", "local"}:
        return CookiePolicy(secure=False, samesite="lax", mode="localhost_http_dev")

    return CookiePolicy(secure=True, samesite="lax", mode="production")


class AuthService:
    """Single-user Companion session + CSRF for same-origin browser/PWA."""

    def __init__(
        self,
        *,
        password: str,
        secret: str,
        cookie_policy: CookiePolicy | None = None,
        login_throttle: LoginThrottle | None = None,
        identities: IdentityStore | None = None,
    ) -> None:
        if not password:
            raise ValueError("Companion password must not be empty.")
        if not secret:
            raise ValueError("Session secret must not be empty.")
        self._password = password
        self.cookie_policy = cookie_policy or resolve_cookie_policy()
        self._serializer = URLSafeTimedSerializer(secret, salt="atlas-companion-session")
        self.login_throttle = login_throttle or LoginThrottle()
        self.identities = identities

    def resolve_subject(self, subject: str) -> Principal:
        if subject != OWNER_SUBJECT or self.identities is None:
            raise ValueError(f"unknown session subject: {subject}")
        return self.identities.current_owner()

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_policy.secure

    def verify_password(self, password: str) -> bool:
        left = hashlib.sha256(password.encode("utf-8")).digest()
        right = hashlib.sha256(self._password.encode("utf-8")).digest()
        return hmac.compare_digest(left, right)

    def issue_session(self) -> SessionData:
        return SessionData(subject=OWNER_SUBJECT, csrf_token=secrets.token_urlsafe(32))

    def dump_session(self, session: SessionData) -> str:
        return self._serializer.dumps(
            {"subject": session.subject, "csrf": session.csrf_token}
        )

    def load_session(self, token: str | None) -> SessionData | None:
        if not token:
            return None
        try:
            payload = self._serializer.loads(token, max_age=SESSION_MAX_AGE)
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        subject = str(payload.get("subject") or "")
        csrf = str(payload.get("csrf") or "")
        if subject != OWNER_SUBJECT or not csrf:
            return None
        return SessionData(subject=subject, csrf_token=csrf)

    def set_session_cookie(self, response: Response, session: SessionData) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            self.dump_session(session),
            httponly=True,
            secure=self.cookie_policy.secure,
            samesite=self.cookie_policy.samesite,
            max_age=SESSION_MAX_AGE,
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=self.cookie_policy.secure,
            httponly=True,
            samesite=self.cookie_policy.samesite,
        )

    def session_from_request(self, request: Request) -> SessionData | None:
        return self.load_session(request.cookies.get(SESSION_COOKIE))

    def csrf_ok(self, request: Request, session: SessionData) -> bool:
        header = request.headers.get(CSRF_HEADER) or ""
        return hmac.compare_digest(header, session.csrf_token)


def load_auth_env_file(path: Path = DEFAULT_AUTH_ENV_PATH) -> None:
    """Load KEY=VALUE pairs into os.environ if not already set."""

    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def auth_from_env(*, env_file: Path | None = DEFAULT_AUTH_ENV_PATH) -> AuthService:
    if env_file is not None:
        load_auth_env_file(env_file)
    password = (
        os.environ.get("ATLAS_COMPANION_PASSWORD")
        or os.environ.get("ATLAS_API_PASSWORD")
        or ""
    ).strip()
    secret = (
        os.environ.get("ATLAS_SESSION_SECRET")
        or os.environ.get("ATLAS_API_SESSION_SECRET")
        or ""
    ).strip()
    if not secret:
        raise RuntimeError(
            "ATLAS_SESSION_SECRET (or ATLAS_API_SESSION_SECRET) must be set so sessions survive restart."
        )
    if not password:
        raise RuntimeError(
            "ATLAS_COMPANION_PASSWORD (or ATLAS_API_PASSWORD) must be set for Companion API."
        )
    return AuthService(
        password=password,
        secret=secret,
        cookie_policy=resolve_cookie_policy(),
    )


def unauthorized(detail: str = "authentication required") -> JSONResponse:
    return JSONResponse({"error": detail}, status_code=401)


def forbidden(detail: str = "forbidden") -> JSONResponse:
    return JSONResponse({"error": detail}, status_code=403)


def require_session(request: Request) -> SessionData | JSONResponse:
    auth: AuthService = request.app.state.auth
    session = auth.session_from_request(request)
    if session is None:
        return unauthorized()
    return session


def require_mutation_auth(request: Request) -> SessionData | JSONResponse:
    auth: AuthService = request.app.state.auth
    session = auth.session_from_request(request)
    if session is None:
        return unauthorized()
    if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        if not auth.csrf_ok(request, session):
            return forbidden("csrf token missing or invalid")
    return session


def client_key(request: Request) -> str:
    direct = request.client.host if request.client and request.client.host else ""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded.strip() and _loopback_address(direct):
        return forwarded.split(",")[0].strip()
    return direct or "unknown"


def _loopback_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def json_body(data: Any) -> JSONResponse:
    return JSONResponse(data)
