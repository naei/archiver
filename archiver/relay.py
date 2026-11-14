"""Relay: archives, from another computer, the links a server could not download.

Some platforms refuse the addresses of datacenters, where servers usually run.
A relay is the same application started with `run.py relay` on a computer whose
address they accept, with the server's S3 storage settings. It asks the server
for the links whose archiving failed, archives each one into the shared storage,
then tells the server, which takes the archive over without downloading.

The server and the relay share RELAY_TOKEN; the relay also needs RELAY_SERVER,
the server's address. Without RELAY_TOKEN, a server answers no relay."""
import hmac
import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request

from .downloader import Downloader
from .storage import Storage
from .store import Store, source_url

log = logging.getLogger(__name__)
POLL_SECONDS = 60
# Between two archives, so that a long list does not reach a platform in a burst.
PAUSE_SECONDS = 30
# A link the relay could not archive either is tried again after this long, MAX_ATTEMPTS times in all.
RETRY_SECONDS = 6 * 3600
MAX_ATTEMPTS = 3


class Failure(RuntimeError):
    """The server could not be reached, or refused the request (`status` is then its HTTP status)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def token():
    """The secret shared by a server and its relay, or None when relaying is off."""
    value = os.getenv("RELAY_TOKEN", "").strip()
    if value and len(value) < 32:
        raise ValueError("RELAY_TOKEN: expected at least 32 characters, for example from \"openssl rand -hex 32\".")
    return value or None


def authorized(header, expected):
    """Whether an Authorization header carries the relay token."""
    scheme, _, given = (header or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), expected.encode())


def archive(url, started=None, languages=None):
    """Archive one link straight into the S3 storage, with a throwaway catalog; returns its entry in that catalog.

    `started(worker)` is given the worker before it downloads, for whoever may have to stop it;
    `languages` are the ones to ask the site for, as an Accept-Language header."""
    with tempfile.TemporaryDirectory() as temp:
        store, storage = Store(temp), Storage(temp)
        if storage.mode != "s3":
            raise ValueError("This needs the S3 storage of the server that will show the archive (STORAGE_BACKEND=s3).")
        item, _ = store.add(url, [], languages=languages)
        worker = Downloader(store, storage)
        if started:
            started(worker)
        worker.handle(item["id"])
        return store.get(item["id"])


class Relay:
    def __init__(self, server, secret):
        self.server = server.rstrip("/")
        self.secret = secret
        self.stopping = threading.Event()
        self.worker = None
        self.failures = {}  # id -> (failed attempts, when the last one ended)

    def call(self, method, path, data=None):
        request = urllib.request.Request(
            self.server + path, method=method, data=None if data is None else json.dumps(data).encode(),
            headers={"Authorization": f"Bearer {self.secret}", "Content-Type": "application/json", "Accept-Language": "en"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise Failure(f"the server answered HTTP {error.code}", error.code) from None
        except (OSError, ValueError) as error:
            raise Failure(f"the server cannot be reached ({error})") from None

    def stop(self):
        self.stopping.set()
        if self.worker:
            self.worker.stop()

    def due(self, media_id):
        attempts, last = self.failures.get(media_id, (0, 0))
        return not attempts or (attempts < MAX_ATTEMPTS and time.monotonic() - last >= RETRY_SECONDS)

    def run(self):
        log.info("Relay of %s: waiting for links it could not download", self.server)
        while not self.stopping.is_set():
            self.poll()
            self.stopping.wait(POLL_SECONDS)

    def poll(self):
        """Archive the links the server is waiting for; returns how many were handed back to it."""
        try:
            links = self.call("GET", "/api/relay/links")["links"]
        except Failure as error:
            log.warning("Links not fetched: %s", error)
            return 0
        done = 0
        for link in links:
            if self.stopping.is_set():
                break
            if self.due(link["id"]):
                done += self.handle(link)
                self.stopping.wait(PAUSE_SECONDS)
        return done

    def handle(self, link):
        media_id = link["id"]
        try:
            if source_url(link["url"])[0] != media_id:
                raise ValueError("this link would be archived under another id")
            # From here the server leaves the link alone, so that both never write the same archive.
            self.call("POST", f"/api/relay/links/{media_id}", {"state": "taken"})
            log.info("Archive %s: downloading for the server", media_id)
            item = archive(link["url"], lambda worker: setattr(self, "worker", worker), link.get("languages"))
            if self.stopping.is_set():
                return False
            if item is None or item["status"] != "ready":
                raise RuntimeError((item or {}).get("error") or "no archive was made")
            self.call("POST", f"/api/relay/links/{media_id}", {"state": "archived"})
        except Failure as error:
            if error.status != 409:  # 409: retried or deleted on the server in the meantime
                log.warning("Archive %s: %s", media_id, error)
            return False
        except Exception as error:
            self.failures[media_id] = (self.failures.get(media_id, (0, 0))[0] + 1, time.monotonic())
            log.warning("Archive %s: not archived from here either: %s", media_id, str(error)[-500:])
            return False
        finally:
            self.worker = None
        self.failures.pop(media_id, None)
        log.info("Archive %s: handed to the server", media_id)
        return True
