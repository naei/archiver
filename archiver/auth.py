"""The single account that protects the interface, its login sessions, and the unlocking of private archives.

The account is created once, from the interface, on first launch. Its password
is stored as a scrypt hash and sessions as hashes of random tokens, both in the
catalog database so they survive restarts.

Private archives are shown only to requests that carry an unlock token, given
for the account's password typed again. Tokens live in memory: the page keeps
its own in memory too, so reloading it, restarting the server or ten minutes
without requests hides private archives again. The page itself hides them
after ten minutes without activity, a playing video counting as activity."""
import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing

from .store import now


SESSION_SECONDS = 90 * 24 * 3600
# A session opened by a client that keeps its token itself, such as the browser extension: it has no login page to return to.
TOKEN_SECONDS = 365 * 24 * 3600
# An unlock token expires after this long without a request using it (mirrors PRIVATE_IDLE in app.js).
UNLOCK_SECONDS = 10 * 60
MIN_PASSWORD = 12
# Failed attempts allowed, from all clients together, before attempts pause.
MAX_FAILURES, FAILURE_WINDOW = 10, 600
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 5}
SCRYPT_MAXMEM = 64 * 1024 * 1024


class TooManyAttempts(Exception):
    pass


class AccountExists(Exception):
    pass


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=SCRYPT_MAXMEM, **SCRYPT)
    encode = lambda value: base64.b64encode(value).decode()
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${encode(salt)}${encode(digest)}"


def verify_password(password, encoded):
    _, n, r, p, salt, expected = encoded.split("$")
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                            maxmem=SCRYPT_MAXMEM)
    return hmac.compare_digest(digest, base64.b64decode(expected))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Throttle:
    """Counts failed attempts from every client together: behind a proxy, client
    addresses cannot be trusted, and a single account needs no finer limit."""

    def __init__(self, limit=MAX_FAILURES, window=FAILURE_WINDOW):
        self.limit, self.window = limit, window
        self.failures = deque()
        self.lock = threading.Lock()

    def blocked(self):
        with self.lock:
            while self.failures and self.failures[0] <= time.monotonic() - self.window:
                self.failures.popleft()
            return len(self.failures) >= self.limit

    def fail(self):
        with self.lock:
            self.failures.append(time.monotonic())


class Auth:
    def __init__(self, store):
        self.store = store
        self.throttle = Throttle()
        self.unlocks = {}  # hash of an unlock token -> when it expires (monotonic clock)
        self.unlocks_lock = threading.Lock()
        with closing(store.connect()) as db, db:
            # A single row: the CHECK keeps a second account from ever being created.
            db.execute("""CREATE TABLE IF NOT EXISTS account (
                id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL,
                password TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)")

    def account(self):
        with closing(self.store.connect()) as db:
            return db.execute("SELECT username, password FROM account WHERE id = 1").fetchone()

    def configured(self):
        return self.account() is not None

    def create(self, username, password):
        """Create the account, only while none exists."""
        if self.configured():
            raise AccountExists()
        username = username.strip() if isinstance(username, str) else ""
        if not 1 <= len(username) <= 64:
            raise ValueError("The username must have 1 to 64 characters.")
        if not isinstance(password, str) or len(password) < MIN_PASSWORD:
            raise ValueError(f"The password must have at least {MIN_PASSWORD} characters.")
        try:
            with closing(self.store.connect()) as db, db:
                db.execute("INSERT INTO account VALUES (1, ?, ?, ?)", (username, hash_password(password), now()))
        except sqlite3.IntegrityError:
            raise AccountExists()

    def check(self, username, password):
        """Whether these are the account's credentials. Failures count towards the throttle."""
        if self.throttle.blocked():
            raise TooManyAttempts()
        account = self.account()
        if account and isinstance(username, str) and isinstance(password, str):
            same_user = hmac.compare_digest(username.strip().encode(), account["username"].encode())
            # The password is always verified, so a wrong username takes as long as a wrong password.
            if verify_password(password, account["password"]) and same_user:
                return True
        self.throttle.fail()
        return False

    def change_password(self, current, new):
        """Replace the password, given the current one (counted by the throttle); False when it is wrong.

        Every session is closed and every unlock token revoked: whoever knew the old password is out."""
        account = self.account()
        if not isinstance(new, str) or len(new) < MIN_PASSWORD:
            raise ValueError(f"The password must have at least {MIN_PASSWORD} characters.")
        if not account or not self.check(account["username"], current):
            return False
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE account SET password = ? WHERE id = 1", (hash_password(new),))
            db.execute("DELETE FROM sessions")
        self.lock()
        return True

    def unlock(self, password):
        """An unlock token for private archives, or None when the password is wrong (counted by the throttle)."""
        account = self.account()
        if not account or not self.check(account["username"], password):
            return None
        token = secrets.token_urlsafe(32)
        with self.unlocks_lock:
            moment = time.monotonic()
            self.unlocks = {key: expiry for key, expiry in self.unlocks.items() if expiry > moment}
            self.unlocks[token_hash(token)] = moment + UNLOCK_SECONDS
        return token

    def unlocked(self, token):
        """Whether an unlock token is valid; using it postpones its expiry."""
        if not token:
            return False
        key = token_hash(token)
        with self.unlocks_lock:
            moment = time.monotonic()
            if self.unlocks.get(key, 0) <= moment:
                self.unlocks.pop(key, None)
                return False
            self.unlocks[key] = moment + UNLOCK_SECONDS
            return True

    def lock(self, token=None):
        """Revoke an unlock token, or every one."""
        with self.unlocks_lock:
            if token is None:
                self.unlocks.clear()
            else:
                self.unlocks.pop(token_hash(token), None)

    def open_session(self, lifetime=SESSION_SECONDS):
        token = secrets.token_urlsafe(32)
        with closing(self.store.connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
            db.execute("INSERT INTO sessions VALUES (?, ?)", (token_hash(token), int(time.time()) + lifetime))
        return token

    def session(self, token):
        """Whether a session token is valid."""
        if not token:
            return False
        with closing(self.store.connect()) as db:
            row = db.execute("SELECT expires_at FROM sessions WHERE token = ?", (token_hash(token),)).fetchone()
        return bool(row) and row["expires_at"] > time.time()

    def close_session(self, token, lock=True):
        if lock:
            self.lock()
        if token:
            with closing(self.store.connect()) as db, db:
                db.execute("DELETE FROM sessions WHERE token = ?", (token_hash(token),))

    def reset(self):
        """Delete the account and its sessions; the next visit offers to create one."""
        self.lock()
        with closing(self.store.connect()) as db, db:
            db.execute("DELETE FROM account")
            db.execute("DELETE FROM sessions")
