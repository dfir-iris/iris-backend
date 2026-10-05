#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""Send a rendered webhook request.

Every destination — the configured URL and each redirect hop — goes
through `egress_destination_error`. Private destinations are allowed by
default; with `IRIS_WEBHOOKS_ALLOW_PRIVATE_EGRESS=False` they are refused
at every hop, which is why redirects are followed by hand. The result is
a plain dict the caller stores on the delivery row.
"""

import os
import time
import warnings
from urllib.parse import urljoin
from urllib.parse import urlsplit

import requests
import urllib3

from app import app
from app.iris_engine.utils.egress import egress_destination_error
from app.iris_engine.webhooks.render import MASK


_MAX_REDIRECTS = 5
_MAX_RESPONSE_CHARS = 16 * 1024
_REDIRECT_CODES = (301, 302, 303, 307, 308)
# Worth another attempt: the receiver is overloaded, rate-limiting or
# restarting. Any other 4xx is a configuration problem a retry won't fix.
_RETRYABLE_STATUSES = (408, 425, 429, 500, 502, 503, 504)


def webhooks_allow_private_egress() -> bool:
    return bool(app.config.get('WEBHOOKS_ALLOW_PRIVATE_EGRESS', True))


def webhooks_env_proxy_configured() -> bool:
    """Whether the `HTTP(S)_PROXY` / `ALL_PROXY` variables set a proxy."""
    return bool(requests.utils.getproxies())


def webhooks_destination_error(url):
    return egress_destination_error(url, allow_private=webhooks_allow_private_egress())


def _is_certificate_error(error):
    text = str(error).lower()
    return 'certificate' in text or 'hostname mismatch' in text or "doesn't match" in text


def _truncate(text, limit):
    if text is None or len(text) <= limit:
        return text
    return f'{text[:limit]}\n… [truncated, {len(text)} characters]'


def _result(**fields):
    result = {
        'success': False,
        'retryable': False,
        'status_code': None,
        'response_headers': None,
        'response_body': None,
        'error': None,
        'duration_ms': None,
        'final_url': None,
    }
    result.update(fields)
    return result


def webhooks_send(request, verify_tls=True, timeout=10, follow_redirects=False, proxies=None,
                  use_proxy=True) -> dict:
    """Send `request` (from `webhooks_render_request`).

    `proxies` are the server settings proxies; the standard `HTTP(S)_PROXY`
    environment variables apply otherwise. `use_proxy` off connects
    directly, ignoring both.
    """
    method = request['method']
    url = request['url']
    headers = dict(request['headers'])
    secret_headers = {
        name.lower() for name, value in (request.get('log_headers') or {}).items() if value == MASK
    } | {'authorization', 'proxy-authorization', 'cookie'}
    body = request['body']
    started = time.monotonic()

    def elapsed():
        return int((time.monotonic() - started) * 1000)

    session = requests.Session()
    verify = verify_tls
    if not use_proxy:
        session.trust_env = False
        if verify_tls:
            # trust_env off also drops the CA bundle variables: keep them
            verify = os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE') or True
    elif proxies:
        session.proxies.update(proxies)

    try:
        for _hop in range(_MAX_REDIRECTS + 1):
            refused = webhooks_destination_error(url)
            if refused:
                return _result(error=f'Destination refused: {refused}', duration_ms=elapsed(), final_url=url)

            with warnings.catch_warnings():
                if not verify_tls:
                    # Off on purpose — an explicit per-webhook setting shown
                    # in the UI — so not worth a warning per request.
                    warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
                response = session.request(method, url, headers=headers, data=body, timeout=timeout,
                                           verify=verify, allow_redirects=False)

            if follow_redirects and response.status_code in _REDIRECT_CODES and response.headers.get('Location'):
                target = urljoin(url, response.headers['Location'])
                if urlsplit(target).netloc != urlsplit(url).netloc:
                    # Credentials were meant for the configured host only
                    headers = {k: v for k, v in headers.items() if k.lower() not in secret_headers}
                url = target
                if response.status_code == 303 or (response.status_code in (301, 302) and method == 'POST'):
                    method = 'GET'
                    body = None
                    headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
                response.close()
                continue

            status = response.status_code
            return _result(
                success=200 <= status < 300,
                retryable=status in _RETRYABLE_STATUSES,
                status_code=status,
                response_headers=dict(response.headers),
                response_body=_truncate(response.text, _MAX_RESPONSE_CHARS),
                error=None if 200 <= status < 300 else f'HTTP {status} {response.reason or ""}'.strip(),
                duration_ms=elapsed(),
                final_url=url,
            )

        return _result(error=f'More than {_MAX_REDIRECTS} redirects', duration_ms=elapsed(), final_url=url)

    except requests.exceptions.SSLError as e:
        if _is_certificate_error(e):
            hint = ('Disable certificate verification only if the receiver uses a self-signed or private CA '
                    'certificate.')
            return _result(error=f'TLS error: {e}. {hint}', duration_ms=elapsed(), final_url=url)
        # Handshake aborted before any certificate check: verification
        # plays no part, and it may be transient
        hint = ('The TLS handshake failed before any certificate check, so certificate verification is not the '
                'cause. Check that IRIS can reach this host (firewall, IP allow-list, proxy settings, '
                'TLS-intercepting proxy) and whether the receiver requires a client certificate.')
        return _result(retryable=True, error=f'TLS handshake error: {e}. {hint}', duration_ms=elapsed(),
                       final_url=url)
    except requests.exceptions.ProxyError as e:
        hint = ('The proxy refused or could not reach the destination: allow this host on the proxy, or turn off '
                '"Use the proxy" on the webhook to connect directly.')
        return _result(retryable=True, error=f'Proxy error: {e}. {hint}', duration_ms=elapsed(), final_url=url)
    except requests.exceptions.Timeout:
        return _result(retryable=True, error=f'Timed out after {timeout}s', duration_ms=elapsed(), final_url=url)
    except requests.exceptions.ConnectionError as e:
        return _result(retryable=True, error=f'Connection error: {e}', duration_ms=elapsed(), final_url=url)
    except requests.exceptions.RequestException as e:
        return _result(error=f'Request error: {e}', duration_ms=elapsed(), final_url=url)
    finally:
        session.close()
