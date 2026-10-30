"""Session cookie names and a dual-read accessor.

The cookie was originally named ikiru_dashboard_session, back when ikiru was
one of the scrapers. That source is gone, but the name is the live auth
contract: renaming it outright would invalidate every active session.

So the new name is manhwa_dashboard_session and the old one is still read
for as long as it may exist in someone's browser. Reading is dual, writing is
single — a login always issues the new name, so the old cookie stops being
refreshed and eventually ages out of existence on its own.

Drop the legacy read once no client can be holding the old cookie any more.
"""

SESSION_COOKIE = "manhwa_dashboard_session"
CSRF_COOKIE = "manhwa_csrf_token"
SESSION_ROLE_COOKIE = "manhwa_role"

_LEGACY_SESSION_COOKIE = "ikiru_dashboard_session"
_LEGACY_CSRF_COOKIE = "ikiru_csrf_token"
_LEGACY_ROLE_COOKIE = "ikiru_role"

# Every legacy name paired with its current replacement.
RENAMED_COOKIES: tuple[tuple[str, str], ...] = (
    (_LEGACY_SESSION_COOKIE, SESSION_COOKIE),
    (_LEGACY_CSRF_COOKIE, CSRF_COOKIE),
    (_LEGACY_ROLE_COOKIE, SESSION_ROLE_COOKIE),
)


def read_cookie(cookies: dict, name: str) -> str:
    """Read `name`, falling back to the legacy name it replaced.

    Dual-read on purpose: see the module docstring.
    """
    value = cookies.get(name) or ""
    if value:
        return value
    for old, new in RENAMED_COOKIES:
        if new == name:
            return cookies.get(old) or ""
    return ""


def read_session_cookie(cookies: dict) -> str:
    return read_cookie(cookies, SESSION_COOKIE)


def read_csrf_cookie(cookies: dict) -> str:
    return read_cookie(cookies, CSRF_COOKIE)


def read_role_cookie(cookies: dict) -> str:
    return read_cookie(cookies, SESSION_ROLE_COOKIE)
