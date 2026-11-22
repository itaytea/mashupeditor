"""Reject cross-site state-changing requests (CSRF) by checking Origin / Referer.

Browsers send a cross-site "no-cors" POST without any preflight, so endpoints that
take no body (logout, cancel, start...) would otherwise run. The Origin header
(or Referer, when Origin is absent) names the page that issued the request.

Rules for methods other than GET/HEAD/OPTIONS:
  * neither header present -> allowed (curl, scripts, tests);
  * the origin matches the request's own Host, or is in the allowed set -> allowed;
  * anything else -> 403.

The allowed set defaults to the Vite dev server origins and can be replaced with
MASHUP_ALLOWED_ORIGINS=http://host:port,http://other:port.
"""

import os
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DEFAULT_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://[::1]:5173",
)


def allowed_origins_from_env() -> set[str]:
    raw = os.environ.get("MASHUP_ALLOWED_ORIGINS")
    if raw is None:
        return set(DEFAULT_ORIGINS)
    return {o.strip().rstrip("/").lower() for o in raw.split(",") if o.strip()}


def _origin_of(value: str) -> str | None:
    """Reduce an Origin or Referer value to 'scheme://host[:port]'."""
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return None  # includes the opaque "null" origin
    return f"{parts.scheme}://{parts.netloc}".lower()


class OriginCheckMiddleware:
    def __init__(self, app, allowed_origins: set[str] | None = None):
        self.app = app
        self.allowed = allowed_origins if allowed_origins is not None else allowed_origins_from_env()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        value = headers.get("origin") or headers.get("referer")
        if value is not None:
            origin = _origin_of(value)
            host = headers.get("host", "").lower()
            same_host = origin is not None and host and origin.split("://", 1)[1] == host
            if not same_host and origin not in self.allowed:
                await PlainTextResponse("Cross-site request blocked", status_code=403)(
                    scope, receive, send)
                return
        await self.app(scope, receive, send)
