"""Shared bearer-token guard.

``media.py`` and ``pahe/scanner/main.py`` each carried their own copy of the
same ``hmac.compare_digest`` check against two different env vars. One
implementation, one rule: accept ``Authorization: Bearer <token>`` or
``?token=<token>``, compare in constant time, and deny when no token is
configured (fail closed rather than open).
"""

from __future__ import annotations

import hmac
import os


def configured(env_var: str) -> bool:
    """True when the token for ``env_var`` is set to a non-empty value."""
    return bool((os.environ.get(env_var) or "").strip())


def check(headers, query_params, env_var: str) -> bool:
    """Authorize a request against the token in ``env_var``.

    ``headers`` and ``query_params`` are anything with ``.get()`` — a Starlette
    ``Request``, or a stub in tests. Missing values are treated as empty.
    """
    token = (os.environ.get(env_var) or "").strip()
    if not token:
        return False
    try:
        auth = headers.get("authorization", "") or ""
        supplied = query_params.get("token", "") or ""
    except AttributeError:
        return False
    return hmac.compare_digest(auth, f"Bearer {token}") or hmac.compare_digest(supplied, token)
