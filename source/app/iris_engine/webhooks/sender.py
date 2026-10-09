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
through `webhooks_resolve_destination`. Private destinations are allowed
by default; with `IRIS_WEBHOOKS_ALLOW_PRIVATE_EGRESS=False` (or
`allow_private=False` from the caller) they are refused at every hop,
which is why redirects are followed by hand. When they are refused the
host is resolved once, every address checked, and the connection pinned
to the checked address (Host header, SNI and certificate verification
still use the host name), so a DNS answer that changes between the check
and the connection cannot reach an internal service. Through a proxy the
proxy resolves, so the pin does not apply.

The response body is streamed and read up to `_MAX_RESPONSE_BYTES`, and
the whole exchange is bounded by a wall-clock deadline. The result is a
plain dict the caller stores on the delivery row.
"""

import ipaddress
import os
import socket
import time
import warnings
from urllib.parse import urljoin
from urllib.parse import urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter

from app import app
from app.iris_engine.webhooks.render import MASK


_MAX_REDIRECTS = 5
_MAX_RESPONSE_CHARS = 16 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_TOTAL_DEADLINE_SECONDS = 30
_CHUNK_BYTES = 16 * 1024
_ALLOWED_SCHEMES = ('http', 'https')
_NAT64 = ipaddress.ip_network('64:ff9b::/96')
_REDIRECT_CODES = (301, 302, 303, 307, 308)
# Worth another attempt: the receiver is overloaded, rate-limiting or
# restarting. Any other 4xx is a configuration problem a retry won't fix.
_RETRYABLE_STATUSES = (408, 425, 429, 500, 502, 503, 504)


class _DeadlineExceeded(Exception):
    pass


def webhooks_allow_private_egress() -> bool:
    return bool(app.config.get('WEBHOOKS_ALLOW_PRIVATE_EGRESS', True))


def webhooks_env_proxy_configured() -> bool:
    """Whether the `HTTP(S)_PROXY` / `ALL_PROXY` variables set a proxy."""
    return bool(requests.utils.getproxies())


def _embedded_ipv4(address):
    """IPv4 address carried inside an IPv6 one (mapped, 6to4, Teredo,
    NAT64), which the network would deliver to."""
    if address.version != 6:
        return None
    if address.ipv4_mapped:
        return address.ipv4_mapped
    if address.sixtofour:
        return address.sixtofour
    if address.teredo:
        return address.teredo[1]
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def _address_blocked(address) -> bool:
    """Anything that is not a globally routable unicast address."""
    embedded = _embedded_ipv4(address)
    if embedded is not None and _address_blocked(embedded):
        return True
    return (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
    )


def _resolve(hostname, port):
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None
    # Keep the resolver order, without duplicates
    return list(dict.fromkeys(info[4][0] for info in infos))


def webhooks_resolve_destination(url, allow_private=None):
    """`(error, address)`: why `url` must not be requested (None if it
    may be), and — when private destinations are refused — the checked
    address the connection must be pinned to.

    Every address the host resolves to is checked, not just the first,
    so a host with a public and a private record does not pass because
    of resolver ordering.
    """
    if not url or not isinstance(url, str):
        return 'empty URL', None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as e:
        return f'invalid URL ({e})', None
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"scheme '{parts.scheme}' is not allowed (use http or https)", None
    hostname = parts.hostname
    if not hostname:
        return 'URL has no host', None

    if allow_private is None:
        allow_private = webhooks_allow_private_egress()
    if allow_private:
        return None, None

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if _address_blocked(literal):
            return f'destination {hostname} is a private or reserved address', None
        return None, str(literal)

    addresses = _resolve(hostname, port or (443 if parts.scheme.lower() == 'https' else 80))
    if not addresses:
        return f'host {hostname} could not be resolved', None
    checked = []
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw.split('%', 1)[0])
        except ValueError:
            return f'host {hostname} resolved to an unusable address', None
        if _address_blocked(address):
            return f'host {hostname} resolves to a private or reserved address', None
        checked.append(str(address))
    return None, checked[0]


def webhooks_destination_error(url, allow_private=None):
    return webhooks_resolve_destination(url, allow_private=allow_private)[0]


class _PinnedAdapter(HTTPAdapter):
    """Connects to `address` whatever the URL host resolves to now. TLS
    still sends the host name as SNI and verifies the certificate
    against it."""

    def __init__(self, address):
        self._address = address
        super().__init__(max_retries=0)

    def _pinned_pool(self, url):
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        pool_kwargs = {}
        if scheme == 'https':
            pool_kwargs = {'server_hostname': parts.hostname, 'assert_hostname': parts.hostname}
        return self.poolmanager.connection_from_host(
            self._address, port=parts.port or (443 if scheme == 'https' else 80), scheme=scheme,
            pool_kwargs=pool_kwargs)

    def get_connection(self, url, proxies=None):
        return self._pinned_pool(url)

    def get_connection_with_tls_context(self, request, verify, proxies=None, **_kwargs):
        return self._pinned_pool(request.url)


def _proxy_for(session, url):
    settings = session.merge_environment_settings(url, {}, None, None, None)
    return requests.utils.select_proxy(url, settings.get('proxies') or {})


def _host_header(url):
    parts = urlsplit(url)
    host = parts.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    return f'{host}:{parts.port}' if parts.port else host


def _read_body(response, deadline, max_chars):
    """Body text, at most `_MAX_RESPONSE_BYTES` read from the wire and
    `max_chars` kept."""
    chunks = []
    size = 0
    clipped = False
    for chunk in response.iter_content(_CHUNK_BYTES):
        if time.monotonic() > deadline:
            raise _DeadlineExceeded()
        if not chunk:
            continue
        if isinstance(chunk, str):
            chunk = chunk.encode('utf-8')
        if size + len(chunk) > _MAX_RESPONSE_BYTES:
            chunks.append(chunk[:_MAX_RESPONSE_BYTES - size])
            clipped = True
            break
        chunks.append(chunk)
        size += len(chunk)
    encoding = getattr(response, 'encoding', None)
    if not isinstance(encoding, str):
        encoding = 'utf-8'
    try:
        text = b''.join(chunks).decode(encoding, errors='replace')
    except LookupError:
        text = b''.join(chunks).decode('utf-8', errors='replace')
    if clipped:
        return f'{text[:max_chars]}\n… [truncated, more than {_MAX_RESPONSE_BYTES} bytes]'
    return _truncate(text, max_chars)


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
                  use_proxy=True, allow_private=None, deadline=None, max_response_chars=None) -> dict:
    """Send `request` (from `webhooks_render_request`).

    `proxies` are the server settings proxies; the standard `HTTP(S)_PROXY`
    environment variables apply otherwise. `use_proxy` off connects
    directly, ignoring both. `allow_private` overrides the webhooks
    private-egress setting (AI workflows pass their own); None keeps it.
    `deadline` bounds the whole exchange in seconds, redirects and body
    included (default: the larger of 30s and `timeout`).
    `max_response_chars` is how much of the response body is kept
    (default 16 KiB, enough for a delivery log; at most what is read).
    """
    method = request['method']
    url = request['url']
    headers = dict(request['headers'])
    secret_headers = {
        name.lower() for name, value in (request.get('log_headers') or {}).items() if value == MASK
    } | {'authorization', 'proxy-authorization', 'cookie'}
    body = request['body']
    started = time.monotonic()
    budget = deadline if deadline is not None else max(_TOTAL_DEADLINE_SECONDS, timeout or 0)
    ends_at = started + budget

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

    response = None
    try:
        for _hop in range(_MAX_REDIRECTS + 1):
            refused, address = webhooks_resolve_destination(url, allow_private=allow_private)
            if refused:
                return _result(error=f'Destination refused: {refused}', duration_ms=elapsed(), final_url=url)
            remaining = ends_at - time.monotonic()
            if remaining <= 0:
                raise _DeadlineExceeded()

            send_headers = headers
            if address and not _proxy_for(session, url):
                adapter = _PinnedAdapter(address)
                session.mount('http://', adapter)
                session.mount('https://', adapter)
                send_headers = {k: v for k, v in headers.items() if k.lower() != 'host'}
                send_headers['Host'] = _host_header(url)

            with warnings.catch_warnings():
                if not verify_tls:
                    # Off on purpose — an explicit per-webhook setting shown
                    # in the UI — so not worth a warning per request.
                    warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
                response = session.request(method, url, headers=send_headers, data=body,
                                           timeout=min(timeout, remaining) if timeout else remaining,
                                           verify=verify, allow_redirects=False, stream=True)

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
                response = None
                continue

            status = response.status_code
            text = _read_body(response, ends_at, max_response_chars or _MAX_RESPONSE_CHARS)
            return _result(
                success=200 <= status < 300,
                retryable=status in _RETRYABLE_STATUSES,
                status_code=status,
                response_headers=dict(response.headers),
                response_body=text,
                error=None if 200 <= status < 300 else f'HTTP {status} {response.reason or ""}'.strip(),
                duration_ms=elapsed(),
                final_url=url,
            )

        return _result(error=f'More than {_MAX_REDIRECTS} redirects', duration_ms=elapsed(), final_url=url)

    except _DeadlineExceeded:
        return _result(retryable=True, error=f'Timed out after {budget}s (total deadline)', duration_ms=elapsed(),
                       final_url=url)
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
        if response is not None:
            response.close()
        session.close()
