"""
Middleware for the WebAdmin plugin.
Provides auth checking, permission gating, and rate limiting for aiohttp.
"""

import time
import logging
from collections import defaultdict
from aiohttp import web

Log = logging.getLogger(__name__)

# Session cookie name
SESSION_COOKIE = "webadmin_session"

# Paths that don't require authentication
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/accounts",
}

# Paths that are served as static files (no auth needed)
STATIC_PREFIX = "/static"


def get_client_ip(request):
    """Extract client IP from the request, respecting X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    peername = request.transport.get_extra_info("peername")
    if peername:
        return peername[0]
    return "unknown"


# --- Auth Middleware ---

def create_auth_middleware(auth_module):
    """
    Create an aiohttp middleware that checks session cookies.
    Attaches user dict to request['user'] if authenticated.
    """
    @web.middleware
    async def auth_middleware(request, handler):
        path = request.path

        # Skip auth for static files and public paths
        if path.startswith(STATIC_PREFIX) or path in PUBLIC_PATHS or path == "/" or path == "":
            return await handler(request)

        # Check session cookie
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return web.json_response({"error": "Authentication required"}, status=401)

        user = auth_module.get_session_user(token)
        if not user:
            response = web.json_response({"error": "Session expired or invalid"}, status=401)
            response.del_cookie(SESSION_COOKIE)
            return response

        # Attach user to request for downstream handlers
        request["user"] = user
        return await handler(request)

    return auth_middleware


# --- Permission Checking ---

def require_smod_level(min_level):
    """
    Decorator for route handlers that require a minimum SMOD level.
    Must be used AFTER auth middleware has run (so request['user'] exists).
    """
    def decorator(handler):
        async def wrapper(request):
            user = request.get("user")
            if not user:
                return web.json_response({"error": "Authentication required"}, status=401)

            if user["smod_level"] < min_level:
                return web.json_response(
                    {"error": f"Insufficient permissions (requires level {min_level})"},
                    status=403
                )

            return await handler(request)
        # Preserve the original function name for aiohttp routing
        wrapper.__name__ = handler.__name__
        wrapper.__qualname__ = handler.__qualname__
        return wrapper
    return decorator


# --- Rate Limiting ---

class RateLimiter:
    """Simple in-memory per-IP rate limiter."""

    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits = defaultdict(list)  # ip -> [timestamps]

    def is_allowed(self, ip):
        """Check if an IP is within the rate limit. Returns True if allowed."""
        now = time.monotonic()
        hits = self._hits[ip]

        # Prune old entries
        cutoff = now - self.window
        self._hits[ip] = [t for t in hits if t > cutoff]
        hits = self._hits[ip]

        if len(hits) >= self.max_requests:
            return False

        hits.append(now)
        return True


def create_rate_limit_middleware(rate_limiter):
    """Create an aiohttp middleware for rate limiting."""
    @web.middleware
    async def rate_limit_middleware(request, handler):
        # Don't rate limit static files
        if request.path.startswith(STATIC_PREFIX) or request.path == "/" or request.path == "":
            return await handler(request)

        ip = get_client_ip(request)
        if not rate_limiter.is_allowed(ip):
            return web.json_response(
                {"error": "Rate limit exceeded. Try again later."},
                status=429
            )

        return await handler(request)

    return rate_limit_middleware
