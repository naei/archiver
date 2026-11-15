"""The Facebook integration, without network access."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from archiver.downloader import Downloader
from archiver.integrations import ONLINE, UNKNOWN, facebook
from archiver.storage import Storage
from archiver.store import Store, source_url


class FacebookTests(unittest.TestCase):
    def test_facebook_video_urls_only(self):
        for url, expected in (
            ("https://www.facebook.com/example/videos/10000000000000001/", ("fb-10000000000000001", "https://www.facebook.com/watch/?v=10000000000000001")),
            ("https://www.facebook.com/example/videos/vb.10000000001/10000000000000002/?type=2", ("fb-10000000000000002", "https://www.facebook.com/watch/?v=10000000000000002")),
            ("https://m.facebook.com/watch/?v=100000000000003", ("fb-100000000000003", "https://www.facebook.com/watch/?v=100000000000003")),
            ("https://www.facebook.com/video.php?v=100000000000004", ("fb-100000000000004", "https://www.facebook.com/watch/?v=100000000000004")),
            ("https://www.facebook.com/reel/1000000000000005", ("fb-1000000000000005", "https://www.facebook.com/reel/1000000000000005")),
            ("https://fb.watch/Exam-ple01/", ("fb-Exam-ple01", "https://fb.watch/Exam-ple01/")),
            ("https://www.facebook.com/share/v/Example002/", ("fb-Example002", "https://www.facebook.com/share/v/Example002/")),
        ):
            with self.subTest(url=url):
                self.assertEqual(source_url(url), (*expected, "Facebook"))
        for url in ("https://www.facebook.com/example/posts/10000000000000006", "https://www.facebook.com/groups/1/permalink/2/",
                    "https://www.facebook.com/photo/?fbid=123456", "https://www.facebook.com/example"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)

    def test_facebook_titles_lose_engagement_counts(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            store = Store(temp)
            item, _ = store.add("https://www.facebook.com/reel/1000000000000005", [])
            info = Path(temp) / "info.json"
            info.write_text(json.dumps({"title": "9.9K views · 341 reactions | Un reel | Avec une barre", "uploader": "Page"}))
            Downloader(store, Storage(temp)).read_info(item["id"], info, facebook.title)
            self.assertEqual(store.get(item["id"])["title"], "Un reel | Avec une barre")

    def test_facebook_availability(self):
        item, worker = {"source_url": "https://www.facebook.com/watch/?v=10000000000000001"}, SimpleNamespace(stopping=threading.Event())
        for readable, expected in ((True, ONLINE), (False, UNKNOWN)):
            with self.subTest(expected=expected), patch("archiver.ytdlp.Session.readable", return_value=readable) as read:
                self.assertEqual(facebook.online(item, worker), expected)
            self.assertEqual(read.call_args[0][0], item["source_url"])
