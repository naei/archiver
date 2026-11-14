"""Optional outgoing proxy, set with PROXY: a single HTTP proxy or a pool of them.

Every request to a platform or a web page goes through it (PROXY_MODE=always,
the default), or only a job's second attempt after a direct one failed
(PROXY_MODE=fallback); the S3 storage never does. Each job (an archive, an availability check) uses one address of the pool
for all its requests, as some platforms tie cookies and media links to the
visitor's address. Jobs take the addresses in turn, so a retry leaves from
another one.

Accepted forms, several separated by commas:
    host:port
    host:port:username:password
    host:10001-10010:username:password   (one address per port, as providers list them)
    http://username:password@host:port   (a port range is accepted here too)"""
import base64
import contextvars
import http.client
import os
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import quote, unquote

UNSET = object()
MODES = ("always", "fallback")
_current = contextvars.ContextVar("proxy", default=UNSET)
_turn = 0
_turn_lock = threading.Lock()


class Failure(RuntimeError):
    """The proxy could not be reached, refused its credentials or could not reach the site."""


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def url(self):
        """For yt-dlp and urllib, credentials included."""
        credentials = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@" if self.username else ""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{credentials}{host}:{self.port}"

    def connect(self, host, port, timeout):
        """A socket tunnelled to host:port through the proxy's CONNECT method."""
        try:
            sock = socket.create_connection((self.host, self.port), timeout)
        except OSError:
            raise Failure(f"The proxy {self.host} cannot be reached. Check the PROXY setting.") from None
        try:
            target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
            if self.username:
                token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
                lines.append(f"Proxy-Authorization: Basic {token}")
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            response = http.client.HTTPResponse(sock, method="CONNECT")
            try:
                response.begin()
            except (OSError, http.client.HTTPException):
                raise Failure(f"The proxy {self.host} cannot be reached. Check the PROXY setting.") from None
            if response.status == 407:
                raise Failure("The proxy refused its credentials (HTTP 407). Check the PROXY setting.")
            if response.status != 200:
                raise Failure(f"The proxy could not reach the site (HTTP {response.status}).")
            return sock
        except BaseException:
            sock.close()
            raise


def ports(text):
    first, _, last = text.partition("-")
    try:
        first, last = int(first), int(last or first)
    except ValueError:
        raise ValueError(f"PROXY: invalid port {text!r}.") from None
    if not 0 < first <= last < 65536:
        raise ValueError(f"PROXY: invalid port {text!r}.")
    return range(first, last + 1)


def parse_entry(entry):
    if "://" in entry:
        scheme, _, rest = entry.partition("://")
        if scheme.lower() != "http":
            raise ValueError(f"PROXY: only HTTP proxies are supported, not {scheme}://.")
        credentials, _, address = rest.rstrip("/").rpartition("@")
        username, _, password = credentials.partition(":")
        username, password = unquote(username), unquote(password)
        host, _, port_text = address.rpartition(":")
        host = host.removeprefix("[").removesuffix("]")
    else:
        parts = entry.split(":", 3)
        if len(parts) not in (2, 4):
            raise ValueError("PROXY: expected host:port or host:port:username:password.")
        host, port_text = parts[:2]
        username, password = parts[2:] or ("", "")
    if not host:
        raise ValueError("PROXY: a proxy has no host.")
    return [Proxy(host, port, username, password) for port in ports(port_text)]


def parse(value):
    """The proxies a PROXY value lists; an empty list when it is empty."""
    value = (value or "").strip().strip("\"'")
    return [proxy for entry in value.split(",") if entry.strip() for proxy in parse_entry(entry.strip())]


def pool():
    return parse(os.getenv("PROXY", ""))


def mode():
    value = os.getenv("PROXY_MODE", "").strip().lower() or "always"
    if value not in MODES:
        raise ValueError(f"PROXY_MODE: expected {' or '.join(MODES)}, not {value!r}.")
    return value


def routes():
    """Whether each attempt of a job goes through the proxy, in order."""
    if not pool():
        return [False]
    return [False, True] if mode() == "fallback" else [True]


def next_proxy():
    """The next address of the pool, in turn; None without a proxy."""
    global _turn
    proxies = pool()
    if not proxies:
        return None
    with _turn_lock:
        proxy = proxies[_turn % len(proxies)]
        _turn += 1
    return proxy


@contextmanager
def chosen(through=True):
    """Send every request made inside this block through one address of the pool, or directly."""
    token = _current.set(next_proxy() if through else None)
    try:
        yield
    finally:
        _current.reset(token)


def current():
    """The proxy for a request: the one chosen for the job, else the next of the pool, or None."""
    proxy = _current.get()
    return next_proxy() if proxy is UNSET else proxy


def describe():
    """For the logs, without credentials."""
    proxies = pool()
    if not proxies:
        return None
    hosts = sorted({proxy.host for proxy in proxies})
    where = f"{', '.join(hosts)} ({len(proxies)} address{'es' if len(proxies) > 1 else ''})"
    return f"{where}, after a direct attempt fails" if mode() == "fallback" else where


def redact(text):
    """`text` without the proxies' passwords, which yt-dlp may repeat in its errors."""
    for proxy in pool():
        if proxy.password:
            text = text.replace(proxy.password, "****").replace(quote(proxy.password, safe=""), "****")
    return text
