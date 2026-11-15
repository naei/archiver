"""Slow background care of the archive, separate from downloads.

- the offline viewer at the root of the storage, rewritten after the library changed;
- fixity checks: every file is re-hashed periodically to detect corruption;
- checks that originals are still online, to know what disappeared."""
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from . import availability, crypto, manifest, proxy, viewer
from .store import TRASH_DAYS, now

VIEWER_SECONDS = 60  # at most one rewrite of the viewer per minute
PURGE_SECONDS = 3600  # the trash is emptied of expired archives once an hour
# One original checked an hour on each platform, 24 a day: a slow visitor that no platform takes for a robot.
# When a platform has more originals than a week allows, the turn of each simply comes less often.
CHECK_SECONDS = 3600

log = logging.getLogger(__name__)


class Runner:
    """The worker of availability checks: what integrations need of one, running a program and a stop signal."""

    def __init__(self, stopping):
        self.stopping = stopping

    def run(self, command):
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=True).stdout


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class Maintenance:
    def __init__(self, store, storage):
        self.store = store
        self.storage = storage
        self.stopping = threading.Event()
        self.fixity_days = float(os.getenv("ARCHIVER_FIXITY_DAYS", "30"))
        self.check_days = float(os.getenv("ARCHIVER_CHECK_DAYS", "7"))
        self.viewer_written = float("-inf")  # never: the first step may write it, whatever the uptime
        self.purged = float("-inf")
        self.checked = {}  # platform -> when one of its originals was last checked
        self.thread = threading.Thread(target=self.work, daemon=True, name="archive-maintenance")

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=8)

    def work(self):
        self.stopping.wait(30)  # let the server start first
        while not self.stopping.is_set():
            try:
                pause = self.step()
            except Exception:
                log.exception("Maintenance step failed")
                pause = 300
            self.stopping.wait(pause)

    def step(self):
        """Do one unit of work; returns the pause in seconds before the next one."""
        if time.monotonic() - self.purged >= PURGE_SECONDS:
            self.purge()
        if self.store.changed.is_set() and time.monotonic() - self.viewer_written >= VIEWER_SECONDS:
            self.write_viewer()
        if self.fixity_days > 0 and (item := self.store.due("integrity_checked_at", days_ago(self.fixity_days), "files != '{}'")):
            self.attempt(self.verify, item, "integrity_checked_at")
            return 1
        if self.check_days > 0:
            # Platform names are those of the integrations, never user-supplied.
            resting = [name for name, last in self.checked.items() if time.monotonic() - last < CHECK_SECONDS]
            rested = f"platform NOT IN ({', '.join(repr(name) for name in resting)})" if resting else "1"
            item = (self.store.due("availability_checked_at", days_ago(1), f"availability = 'gone' AND {rested}")
                    or self.store.due("availability_checked_at", days_ago(self.check_days), rested))
            if item:
                if item["platform"] != "Web":  # web pages are as many different sites
                    self.checked[item["platform"]] = time.monotonic()
                self.attempt(self.check, item, "availability_checked_at")
                return 30  # stay a slow, polite visitor
        return 600

    def attempt(self, action, item, column):
        """Run one check; on failure, date it anyway so one unreadable archive never blocks the others."""
        try:
            action(item)
        except Exception:
            log.exception("Archive %s: %s failed", item["id"], action.__name__)
            self.store.update(item["id"], **{column: now()})

    def purge(self):
        """Delete for good the archives in the trash for more than TRASH_DAYS days."""
        self.purged = time.monotonic()
        for item in self.store.trashed(days_ago(TRASH_DAYS)):
            try:
                self.storage.remove(item)
                self.store.delete(item["id"])
            except Exception:
                log.exception("Archive %s: not deleted from the trash", item["id"])

    def write_viewer(self):
        """index.html at the root of the storage: the library, readable without the application."""
        self.viewer_written = time.monotonic()
        # Consume only the changes preceding this snapshot; concurrent edits must schedule another write.
        self.store.changed.clear()
        try:
            items = [item for item in self.store.all() if not item["deleted_at"]]
            page = viewer.build(viewer.records(items, self.storage), datetime.now().isoformat(timespec="minutes"))
            self.storage.put_root("index.html", page)
        except Exception:
            self.store.changed.set()
            raise

    def convert(self, encrypt):
        """Encrypt the ready archives that are not yet, or decrypt those that are (run.py encrypt, decrypt).

        Returns how many were converted, and the (archive, error) of those left as they were."""
        done, failed = 0, []
        for item in self.store.all():
            if item["status"] != "ready":
                continue
            try:
                changed = bool(item["encrypted"]) != encrypt
                if changed:
                    self.storage.convert(item, encrypt)
                    # From here the archive is read in its new form; the previous one is then removed.
                    item = self.store.update(item["id"], encrypted=int(encrypt))
                # Also on retry: a previous conversion may have switched the catalog but failed to publish
                # its current record. Do not prune the previous form until publication succeeds.
                manifest.publish(self.store, self.storage, item["id"])
                if changed:
                    done += 1
                self.storage.prune(item)  # also what an interrupted run left behind
            except Exception as error:
                failed.append((item["id"], str(error)))
        if encrypt:
            self.write_viewer()
        return done, failed

    def read_hash(self, item, name):
        """The file's hash, or None if it is missing or no longer decrypts; other storage errors propagate."""
        try:
            with self.storage.read(item, name) as stream:
                return manifest.hash_stream(stream)
        except (FileNotFoundError, crypto.Damaged):
            return None
        except Exception as error:
            # botocore's connection errors carry a `response` that is None.
            code = (getattr(error, "response", None) or {}).get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                return None
            raise

    def verify(self, item):
        actual = {name: self.read_hash(item, name) for name in item["files"]}
        damaged = [name for name, expected in item["files"].items() if not manifest.same_content(actual[name], expected)]
        if damaged:
            log.warning("Archive %s: altered or missing files: %s", item["id"], ", ".join(damaged))
        # Intact files refresh their record, which completes older ones with a CRC.
        files = {name: actual[name] if name not in damaged else expected for name, expected in item["files"].items()}
        # A result that did not change is not written again: it would tell the library's readers of a change.
        integrity = "damaged" if damaged else "ok"
        self.store.update(item["id"], files=files, integrity_checked_at=now(),
                          **({} if integrity == item["integrity"] else {"integrity": integrity}))

    def check(self, item):
        # In fallback mode, an original that could not be checked directly is checked again through the proxy.
        for through in proxy.routes():
            with proxy.chosen(through):
                result = availability.check(item, Runner(self.stopping))
            if result != availability.UNKNOWN:
                break
        values = {"availability_checked_at": now()}
        if result and result != item["availability"]:
            values["availability"] = result
        if result == availability.GONE and not item["gone_since"]:
            values["gone_since"] = values["availability_checked_at"]
        elif result == availability.ONLINE and item["gone_since"]:
            values["gone_since"] = None
        self.store.update(item["id"], **values)
