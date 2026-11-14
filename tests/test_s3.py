"""Run against a disposable S3-compatible service, never a personal bucket."""
import http.client
import io
import os
import tempfile
import threading
import unittest
import uuid
import zipfile
from unittest.mock import patch

from archiver import crypto, manifest
from archiver.server import AppServer

KEY = "AGE-SECRET-KEY-1G9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9QSC006VW"


@unittest.skipUnless(os.getenv("TEST_S3_ENDPOINT"), "Set TEST_S3_ENDPOINT to test real S3 operations")
class S3IntegrationTests(unittest.TestCase):
    def test_upload_seek_read_and_delete_with_isolated_prefix(self):
        self.check("")

    def test_encrypted_files_are_uploaded_read_and_converted(self):
        self.check(KEY)

    def check(self, key):
        suffix = crypto.SUFFIX if key else ""
        bucket = "archiver-test-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "STORAGE_BACKEND": "s3",
            "S3_BUCKET": bucket,
            "S3_ENDPOINT_URL": os.environ["TEST_S3_ENDPOINT"],
            "S3_PREFIX": "tests",
            "S3_ADDRESSING_STYLE": "path",
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "archiver-test",
            "AWS_SECRET_ACCESS_KEY": "archiver-test-secret",
            "ENCRYPTION_KEY": key,
        }):
            server = AppServer(("127.0.0.1", 0), temp, start_worker=False)
            client = server.storage.client
            client.create_bucket(Bucket=bucket)
            server.auth.create("admin", "correct horse battery")
            cookie = {"Cookie": f"archiver_session={server.auth.open_session()}"}
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                media_id = "jNQXAC9IVRw"
                server.store.add(f"https://youtu.be/{media_id}", [])
                directory = server.storage.directory(media_id)
                directory.mkdir(parents=True)
                video = directory / "original.mp4"
                video.write_bytes(b"0123456789abcdef")
                encrypted = server.storage.publish(media_id, [video])
                self.assertEqual(encrypted, bool(key))
                server.store.update(media_id, status="ready", storage="s3", original=video.name, playback=video.name,
                                    encrypted=int(encrypted))
                server.storage.cleanup_work(media_id)
                self.assertFalse(directory.exists())
                folder = server.storage.folder(server.store.get(media_id))
                self.assertEqual(folder == media_id, not key)
                uploaded = client.get_object(Bucket=bucket, Key=server.storage.key(folder, video.name + suffix))["Body"].read()
                self.assertEqual(b"0123456789abcdef" in uploaded, not key)
                # A neighboring prefix must never be deleted.
                neighbor = server.storage.key(media_id + "-other", "keep.txt")
                client.put_object(Bucket=bucket, Key=neighbor, Body=b"keep")
                conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                conn.request("GET", f"/api/media/{media_id}/file/playback", headers={"Range": "bytes=4-8", **cookie})
                response = conn.getresponse()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"45678")
                conn.close()
                # An export resumes, reading the bucket by ranges.
                conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                conn.request("GET", "/api/export?at=2026-10-25", headers=cookie)
                response = conn.getresponse()
                whole, etag = response.read(), response.getheader("ETag")
                conn.request("GET", "/api/export?at=2026-10-25", headers={"Range": "bytes=100-", "If-Range": etag, **cookie})
                response = conn.getresponse()
                self.assertEqual((response.status, response.read()), (206, whole[100:]))
                conn.close()
                with zipfile.ZipFile(io.BytesIO(whole)) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertEqual(archive.read(f"archiver-2026-10-25/media/{media_id}/original.mp4"), b"0123456789abcdef")
                # The viewer goes at the root of the prefix, next to the archives' folders.
                server.maintenance.write_viewer()
                client.put_object(Bucket=bucket, Key=server.storage.key("", "index.html" + ("" if key else crypto.SUFFIX)), Body=b"other form")
                server.maintenance.write_viewer()
                viewer = server.storage.key("", "index.html" + suffix)
                self.assertEqual(viewer, "tests/index.html" + suffix)
                page = client.get_object(Bucket=bucket, Key=viewer)["Body"].read()
                if key:
                    page = crypto.reader(lambda first, last: (io.BytesIO(page[first:last + 1]), len(page)), crypto.Key(key)).read()
                self.assertIn(b'data-readonly="1"', page)
                # The catalog is rebuilt from the records stored in the bucket.
                server.store.update(media_id, files={"original.mp4": {"sha256": "0" * 64, "size": 16}})
                manifest.publish(server.store, server.storage, media_id)
                record, encrypted = server.storage.record(media_id, manifest.NAME)
                self.assertIn(media_id.encode(), record)
                self.assertEqual(encrypted, bool(key))
                self.assertIsNone(server.storage.record(media_id + "-other", manifest.NAME))
                server.store.delete(media_id)
                self.assertEqual(manifest.restore(server.store, server.storage), (1, 0, 0))
                self.assertEqual((server.store.get(media_id)["storage"], server.store.get(media_id)["encrypted"]), ("s3", int(bool(key))))
                if key:
                    # The library is decrypted, then encrypted again, in the bucket.
                    for encrypt, names in ((False, ["archive.json", "original.mp4"]), (True, ["archive.json.age", "original.mp4.age"])):
                        self.assertEqual(server.maintenance.convert(encrypt), (1, []))
                        item = server.store.get(media_id)
                        self.assertEqual(sorted(server.storage.names(item)), names)
                        with server.storage.open(item, "original.mp4", 4, 8) as stream:
                            self.assertEqual(stream.read(), b"45678")
                # Deleted to the trash, which keeps the files, then purged for good.
                for method, action, trashed in (("DELETE", "", True), ("POST", "/purge", False)):
                    conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                    conn.request(method, f"/api/media/{media_id}{action}", body="{}" if method == "POST" else None,
                                 headers={"Content-Type": "application/json", **cookie} if method == "POST" else cookie)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                    conn.close()
                    self.assertEqual(server.store.get(media_id) is not None, trashed)
                self.assertIsNone(server.store.get(media_id))
                keys = [obj["Key"] for obj in client.list_objects_v2(Bucket=bucket).get("Contents", [])]
                self.assertEqual(sorted(keys), sorted([neighbor, viewer]))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                for obj in client.list_objects_v2(Bucket=bucket).get("Contents", []):
                    client.delete_object(Bucket=bucket, Key=obj["Key"])
                client.delete_bucket(Bucket=bucket)
