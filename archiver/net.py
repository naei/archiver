"""HTTP fetching of arbitrary public URLs, refusing internal network addresses."""
import contextvars
import http.client
import ipaddress
import re
import socket
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from . import proxy as proxies

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
MAX_REDIRECTS = 5
# The languages asked of a site when nothing says which ones its reader prefers.
LANGUAGES = "en"
_languages = contextvars.ContextVar("languages", default=LANGUAGES)
PORTS = (None, 80, 443)


class HTTPStatus(RuntimeError):
    """The site answered with an error status."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class Blocked(HTTPStatus):
    """The site refused an automated visit (authentication, bot protection or rate limit)."""


class UnknownHost(RuntimeError):
    """The site's name does not resolve: it no longer exists or was mistyped."""


@dataclass
class Response:
    url: str
    content_type: str
    body: bytes
    cookies: dict


def languages(header):
    """A browser's Accept-Language header as it can be sent on to a site, or None when it is missing or malformed."""
    header = header.strip() if isinstance(header, str) else ""
    return header if re.fullmatch(r"[A-Za-z0-9*,;=. -]{1,200}", header) else None


@contextmanager
def reading(preferred):
    """Ask sites, for every request made inside this block, for the languages of whoever asked for the archive."""
    token = _languages.set(languages(preferred) or LANGUAGES)
    try:
        yield
    finally:
        _languages.reset(token)


NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def allowed(address):
    """Only public unicast addresses: no loopback, private, link-local or cloud metadata ranges."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    # NAT64 prefixes count as global but reach IPv4 addresses, private ones included, through a gateway.
    if any(address in network for network in NAT64):
        return False
    return address.is_global and not address.is_multicast


def check_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Address refused: only public http and https links are accepted.")
    if parsed.port not in PORTS:
        raise ValueError("Address refused: non-standard ports are not accepted.")
    return parsed


def resolve(host, port, ipv4=False):
    """A public address of `host`; an IPv4 one when there is one and `ipv4` is set (proxies seldom reach IPv6)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UnknownHost(f"The site {host} cannot be found. Check the address.") from None
    addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    # Every resolved address must be public, or a hostile DNS answer could pick an internal one.
    if not addresses or not all(allowed(address) for address in addresses):
        raise ValueError("Address refused: this link leads to a private or internal network.")
    if ipv4:
        addresses.sort(key=lambda address: address.version != 4)
    return str(addresses[0])


def open_socket(address, port, timeout, proxy):
    # Through a proxy, the tunnel goes to the address checked above, not to a name the proxy would resolve itself.
    if proxy:
        return proxy.connect(address, port, timeout)
    return socket.create_connection((address, port), timeout)


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout, proxy=None):
        super().__init__(host, port, timeout=timeout)
        self.address = address
        self.proxy = proxy

    def connect(self):
        self.sock = open_socket(self.address, self.port, self.timeout, self.proxy)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, address, timeout, proxy=None):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.proxy = proxy

    def connect(self):
        sock = open_socket(self.address, self.port, self.timeout, self.proxy)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def open_url(url, accept, timeout=30, headers=None):
    """Open `url`, following up to MAX_REDIRECTS redirects, each re-checked. Returns (final url, response)."""
    proxy = proxies.current()
    for _ in range(MAX_REDIRECTS + 1):
        parsed = check_url(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = resolve(parsed.hostname, port, ipv4=proxy is not None)
        connection_class = PinnedHTTPSConnection if parsed.scheme == "https" else PinnedHTTPConnection
        connection = connection_class(parsed.hostname, port, address, timeout, proxy)
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        try:
            connection.request("GET", path, headers={
                "User-Agent": USER_AGENT, "Accept": accept,
                "Accept-Language": _languages.get(), "Host": parsed.netloc, **(headers or {}),
            })
            response = connection.getresponse()
        except proxies.Failure:
            connection.close()
            raise
        except ssl.SSLError:
            connection.close()
            raise RuntimeError(f"The secure connection to {parsed.hostname} failed.") from None
        except (OSError, http.client.HTTPException):
            connection.close()
            raise RuntimeError(f"{parsed.hostname} cannot be reached. Check the connection and try again.") from None
        if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
            url = urljoin(url, response.getheader("Location"))
            connection.close()
            continue
        if response.status != 200:
            connection.close()
            error = Blocked if response.status in (401, 403, 429, 503) else HTTPStatus
            raise error(f"The site refused access to the page (HTTP {response.status}). It must be public and accessible without logging in.",
                        response.status)
        response.connection_ = connection
        return url, response
    raise RuntimeError("Too many redirects: the page could not be reached.")


def media_type(response):
    return (response.getheader("Content-Type") or "").split(";")[0].strip().lower()


def fetch(url, accept, types, limit, on_progress=None, stopping=None, headers=None, destination=None):
    """Download a public resource whose media type starts with one of `types`, up to `limit` bytes.

    With `destination`, the content is written to that file instead of being returned."""
    url, response = open_url(url, accept, headers=headers)
    content_type = media_type(response)
    if not content_type.startswith(types):
        response.connection_.close()
        raise ValueError(f"Unsupported content type ({content_type or 'unknown'}).")
    return receive(url, response, content_type, limit, on_progress, stopping, destination)


def receive(url, response, content_type, limit, on_progress=None, stopping=None, destination=None):
    """Read the body of a response from `open_url`, up to `limit` bytes, and close its connection."""
    output = None
    try:
        total = int(response.getheader("Content-Length") or 0)
        if total > limit:
            raise ValueError("The file is too large to be archived.")
        chunks, size = [], 0
        output = destination.open("wb") if destination else None
        while True:
            if stopping is not None and stopping.is_set():
                raise RuntimeError("Server shutting down.")
            try:
                chunk = response.read(256 * 1024)
            except (OSError, http.client.HTTPException):
                raise RuntimeError("The download was interrupted. Try again.") from None
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError("The file is too large to be archived.")
            output.write(chunk) if output else chunks.append(chunk)
            if on_progress and total:
                on_progress(min(size / total, 1))
        if total and size != total:
            raise RuntimeError("The download is incomplete. Try again.")
        cookies = dict(cookie.split(";")[0].strip().split("=", 1) for cookie in response.headers.get_all("Set-Cookie") or []
                       if "=" in cookie.split(";")[0])
        return Response(url, content_type, b"".join(chunks), cookies)
    finally:
        if output:
            output.close()
        response.connection_.close()
