#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Cross-origin policy for an instance answering on several hostnames.

One IRIS deployment is not necessarily reachable under a single name.
An instance is provisioned with a hostname assigned at deploy time, and
the operator may then point one or more further domains at the very same
stack. Every one of those hostnames is a legitimate origin for the same
application, so a single-valued allow-list cannot describe the
deployment: whichever hostname is *not* the configured one has its
cross-origin calls rejected while the configured one keeps working.

`IRIS_ALLOW_ORIGIN` therefore accepts a *list* — comma- or
whitespace-separated. Parsing is done here, once, and the result lives
on `Config.IRIS_ALLOWED_ORIGINS`.

Two values keep their historical meaning:

  * `*` — any origin. Because the CORS spec forbids pairing `*` with
    `Access-Control-Allow-Credentials`, a wildcard deployment gets
    anonymous cross-origin access only; a browser has always refused
    the credentialed variant, so nothing that worked before stops
    working. Deployments that need credentialed cross-origin access
    must list their origins explicitly — which is the multi-hostname
    fix anyway.
  * unset — same as `*`, matching the historical fallback.

Matching is exact on scheme + host + port, per the CORS spec's
definition of an origin. No wildcard subdomains: a `*.example.com`
entry would hand every hostname under a domain the right to drive the
API with the user's credentials, and the list is cheap to enumerate.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import Request
from flask import Response

ANY_ORIGIN = '*'

# Loopback origins used by the frontend dev server and the containerised
# dev stack. Historically hard-coded into the `flask_cors` resource
# block and applied unconditionally; kept here so the developer loop
# behaves exactly as it did before this module existed.
DEV_ORIGINS: tuple[str, ...] = (
    'https://127.0.0.1:5137',
    'https://localhost:5173',
    'https://localhost',
    'https://127.0.0.1',
    'http://app:8000',
    'http://frontend:5173',
)

# Methods the v2 API exposes. PATCH is in the list because several v2
# resources are patch-only.
_ALLOWED_METHODS = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'

# Fallback for preflights that don't announce which headers they want.
_DEFAULT_ALLOWED_HEADERS = 'Content-Type, Authorization'

# Response headers cross-origin callers are allowed to read. Without
# this the SPA sees `null` for both: the CORS spec hides every response
# header outside a small safelist. `X-Request-Id` pairs a client-side
# capture with the server's logs, `Content-Disposition` carries the
# filename for datastore and report downloads.
_EXPOSED_HEADERS = 'X-Request-Id, Content-Disposition'

# How long a browser may cache a preflight result.
_PREFLIGHT_MAX_AGE = '600'


def normalise_origin(value: str) -> str | None:
    """Reduce `value` to a bare `scheme://host[:port]` origin.

    Accepts anything URL-ish an operator is likely to write — a full
    URL with a path, a trailing slash, mixed case — and returns the
    origin a browser would send in the `Origin` header, or None when
    the value carries no usable scheme + host pair.
    """
    candidate = value.strip()
    if not candidate:
        return None
    if candidate == ANY_ORIGIN:
        return ANY_ORIGIN

    parts = urlsplit(candidate)
    if not parts.scheme or not parts.netloc:
        # Bare hostnames ("iris.example.com") are a common way to get
        # this wrong. There is no safe guess for the scheme, and
        # guessing would silently authorise the plaintext variant too.
        return None

    # `netloc` keeps userinfo, which an Origin header never carries.
    host = parts.netloc.rsplit('@', 1)[-1].lower()
    return f'{parts.scheme.lower()}://{host}'


def parse_allowed_origins(raw: str | None) -> list[str]:
    """Parse the configured allow-list into normalised origins.

    Separators are commas and whitespace so both `a,b` and `a b` work,
    and a value spanning several lines in a config file parses too.
    Order is preserved and duplicates collapse — the first entry is the
    canonical public URL (see `primary_public_url`).
    """
    if raw is None:
        return [ANY_ORIGIN]

    tokens = [token for chunk in str(raw).split(',') for token in chunk.split()]
    if not tokens:
        return [ANY_ORIGIN]

    origins: list[str] = []
    for token in tokens:
        origin = normalise_origin(token)
        if origin is not None and origin not in origins:
            origins.append(origin)

    return origins or [ANY_ORIGIN]


def primary_public_url(allowed_origins: list[str]) -> str:
    """The instance's canonical public URL, for links built server-side.

    That is the first configured origin that is an actual origin — `*`
    says nothing about where the instance lives, so it yields an empty
    string rather than a link starting with a literal asterisk.
    """
    for origin in allowed_origins:
        if origin != ANY_ORIGIN:
            return origin
    return ''


def is_origin_allowed(origin: str | None, allowed_origins: list[str]) -> bool:
    if not origin:
        return False
    if ANY_ORIGIN in allowed_origins:
        return True
    return normalise_origin(origin) in allowed_origins


def _is_preflight(request: Request) -> bool:
    return (
        request.method == 'OPTIONS'
        and request.headers.get('Access-Control-Request-Method') is not None
    )


def apply_cors_headers(response: Response, request: Request,
                       allowed_origins: list[str]) -> Response:
    """Attach the cross-origin headers this request has earned.

    A request without an `Origin`, or with one that is not allowed, is
    left untouched: emitting no header is what makes the browser block
    the read, and emitting a wrong one would only turn a clean CORS
    error into a confusing one. `set` rather than `add` throughout —
    two `Access-Control-Allow-Origin` headers on one response make
    browsers reject it outright, which is exactly the failure a
    second CORS layer used to produce here.
    """
    origin = request.headers.get('Origin')

    # The allow-list is keyed on Origin, so caches must key on it too —
    # even for the responses we decline to decorate, otherwise a cached
    # header-less response gets replayed to an allowed origin.
    response.headers.add('Vary', 'Origin')

    if not is_origin_allowed(origin, allowed_origins):
        return response

    if ANY_ORIGIN in allowed_origins:
        # Wildcard: anonymous access only. `Allow-Credentials` is
        # deliberately absent — pairing it with `*` is a spec violation
        # browsers reject, so claiming it would just break the request.
        response.headers.set('Access-Control-Allow-Origin', ANY_ORIGIN)
    else:
        response.headers.set('Access-Control-Allow-Origin', origin)
        response.headers.set('Access-Control-Allow-Credentials', 'true')

    response.headers.set('Access-Control-Expose-Headers', _EXPOSED_HEADERS)

    if _is_preflight(request):
        response.headers.set('Access-Control-Allow-Methods', _ALLOWED_METHODS)
        # Echo what the browser asked for. A fixed list silently breaks
        # any caller sending a header we forgot to enumerate.
        requested = request.headers.get('Access-Control-Request-Headers')
        response.headers.set('Access-Control-Allow-Headers',
                             requested or _DEFAULT_ALLOWED_HEADERS)
        response.headers.set('Access-Control-Max-Age', _PREFLIGHT_MAX_AGE)

    return response


def preflight_response(request: Request, allowed_origins: list[str]) -> Response | None:
    """Answer a CORS preflight without routing it to a view.

    Flask's automatic OPTIONS handling only covers rules it built the
    method for, and it still runs every `before_request` hook — so an
    auth hook can reject the preflight, and a browser reports that as a
    generic CORS failure with no hint that the credentials were fine.
    Short-circuiting here keeps preflights answerable on every route.

    Returns None for anything that is not a preflight from an allowed
    origin, so normal OPTIONS requests keep their existing behaviour.
    """
    if not _is_preflight(request):
        return None
    if not is_origin_allowed(request.headers.get('Origin'), allowed_origins):
        return None

    response = Response(status=204)
    return apply_cors_headers(response, request, allowed_origins)
