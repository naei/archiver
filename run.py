#!/usr/bin/env python3
import logging
import os
import signal
import sys
import tempfile
import threading

from archiver import crypto, manifest, proxy, relay, version
from archiver.auth import Auth
from archiver.maintenance import Maintenance
from archiver.server import AppServer
from archiver.storage import Storage
from archiver.store import Store


def archive(url):
    """Archive one link straight into the S3 storage, with a throwaway catalog.

    For links a server's address cannot download: run from a computer the platform
    accepts, then retry the link on the server that shares the storage."""
    try:
        item = relay.archive(url)
    except ValueError as error:
        print(error, flush=True)
        return 2
    if item["status"] != "ready":
        print(f"Archiving failed: {item['error'] or ''}", flush=True)
        return 1
    print(f"Archived in the S3 storage: {item['title']}. Retry this link on the server to show it there.", flush=True)
    return 0


def run_relay():
    """Archive, for as long as it runs, the links that the server at RELAY_SERVER could not download."""
    server, secret = os.getenv("RELAY_SERVER", "").strip(), relay.token()
    if not server.startswith(("https://", "http://")) or not secret:
        print("Set RELAY_SERVER (the server's address, https://…) and RELAY_TOKEN (the same value as on the server).", flush=True)
        return 2
    try:
        with tempfile.TemporaryDirectory() as temp:
            mode = Storage(temp).mode
    except ValueError as error:
        print(error, flush=True)
        return 2
    if mode != "s3":
        print("A relay needs the S3 storage of the server it works for (STORAGE_BACKEND=s3).", flush=True)
        return 2
    worker = relay.Relay(server, secret)
    print(f"Archiver relay, version {version.VERSION}", flush=True)

    def stop(signum, frame):
        threading.Thread(target=worker.stop, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.run()
    return 0


def convert(encrypt):
    """Encrypt the archives that are not yet, or decrypt those that are; the server may keep running."""
    store = Store(os.getenv("DATA_DIR", "./data"))
    try:
        storage = Storage(store.root)
    except ValueError as error:
        print(error, flush=True)
        return 2
    if storage.encryption is None:
        print("Set ENCRYPTION_KEY first (python run.py keygen creates one).", flush=True)
        return 2
    done, failed = Maintenance(store, storage).convert(encrypt)
    for media_id, error in failed:
        print(f"{media_id}: conversion incomplete ({error}). Run this command again to finish.", flush=True)
    if encrypt:
        print(f"{done} archive(s) encrypted, {len(failed)} failed.", flush=True)
    else:
        print(f"{done} archive(s) decrypted, {len(failed)} failed. Remove ENCRYPTION_KEY and restart the server "
              "to keep new archives unencrypted.", flush=True)
    return 1 if failed else 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sys.argv[1:] == ["reset-account"]:
        Auth(Store(os.getenv("DATA_DIR", "./data"))).reset()
        print("Account deleted. Open Archiver to create a new one.", flush=True)
        return
    if sys.argv[1:] == ["keygen"]:
        identity = crypto.Key.generate()
        print(f"ENCRYPTION_KEY={identity}\n\nKeep a copy of this key outside the server: without it, encrypted archives "
              f"cannot be read by anyone.\nPublic key, for reference: {crypto.Key(identity).recipient}", flush=True)
        return
    if sys.argv[1:] in (["encrypt"], ["decrypt"]):
        sys.exit(convert(sys.argv[1] == "encrypt"))
    if sys.argv[1:2] in (["archive"], ["relay"]) or not sys.argv[1:]:
        try:
            proxy.pool()
            proxy.mode()
            relay.token()
        except ValueError as error:
            print(error, flush=True)
            sys.exit(2)
    if len(sys.argv) == 3 and sys.argv[1] == "archive":
        sys.exit(archive(sys.argv[2]))
    if sys.argv[1:] == ["relay"]:
        sys.exit(run_relay())
    if sys.argv[1:] == ["rebuild"]:
        store = Store(os.getenv("DATA_DIR", "./data"))
        try:
            storage = Storage(store.root)
        except ValueError as error:
            print(error, flush=True)
            sys.exit(2)
        added, known, unreadable = manifest.restore(store, storage)
        print(f"{added} archive(s) restored to the catalog, {known} already there, {unreadable} unreadable.", flush=True)
        return
    if sys.argv[1:]:
        print("Unknown command, or missing argument. See “Commands” in the README.", flush=True)
        sys.exit(2)
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    try:
        server = AppServer((host, port), os.getenv("DATA_DIR", "./data"))
    except ValueError as error:
        print(error, flush=True)
        sys.exit(2)

    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Archiver → http://{'localhost' if host == '0.0.0.0' else host}:{port} (version {version.VERSION})", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
