"""The TikTok integration, without network access."""
import json
import unittest
from unittest.mock import patch

from archiver import net
from archiver.integrations import GONE, tiktok
from archiver.store import source_url


class TikTokTests(unittest.TestCase):
    def test_tiktok_urls(self):
        video = ("tt-7000000000000000001", "https://www.tiktok.com/@example/video/7000000000000000001", "TikTok")
        self.assertEqual(source_url("https://www.tiktok.com/@example/video/7000000000000000001?lang=fr"), video)
        self.assertEqual(source_url("https://m.tiktok.com/@example/video/7000000000000000001/")[0], video[0])
        self.assertEqual(source_url("https://www.tiktok.com/embed/v2/7000000000000000001")[0], video[0])
        self.assertEqual(source_url("https://www.tiktok.com/@a.b/photo/7000000000000000001")[1], "https://www.tiktok.com/@a.b/photo/7000000000000000001")
        self.assertEqual(source_url("https://vm.tiktok.com/ZTexample/"), ("tt-ZTexample", "https://vm.tiktok.com/ZTexample/", "TikTok"))
        self.assertEqual(source_url("https://www.tiktok.com/t/ZTexample")[0], "tt-ZTexample")
        for url in ("https://www.tiktok.com/@example", "https://www.tiktok.com/@example/live", "https://www.tiktok.com/tag/chat"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)

    def test_tiktok_video_and_photo_posts(self):
        raw = {"id": "70", "desc": " Légende ", "createTime": "1640896362", "author": {"uniqueId": "example", "nickname": "Example"},
               "video": {"width": 576, "height": 1024, "duration": 21, "playAddr": "https://v16.tiktok.com/play.mp4",
                         "bitrateInfo": [{"Bitrate": 800, "PlayAddr": {"UrlList": ["https://v16.tiktok.com/low.mp4"]}},
                                         {"Bitrate": 1900, "PlayAddr": {"UrlList": ["https://v16.tiktok.com/high.mp4"]}}]}}
        post = tiktok.post_details(raw)
        self.assertEqual((post["author"], post["text"], post["url"]), ("Example", "Légende", "https://www.tiktok.com/@example/video/70"))
        self.assertEqual(post["published_at"], "2021-12-30T20:32:42+00:00")
        self.assertEqual(post["assets"][0]["source_urls"], ["https://v16.tiktok.com/high.mp4", "https://v16.tiktok.com/low.mp4",
                                                            "https://v16.tiktok.com/play.mp4"])
        with self.assertRaises(RuntimeError):
            tiktok.post_details({**raw, "video": {}})
        images = {"images": [{"imageWidth": 1080, "imageHeight": 1440, "imageURL": {"urlList": [
            "https://p16.tiktokcdn.com/a.heic", "https://p16.tiktokcdn.com/a.jpeg"]}}]}
        post = tiktok.post_details({**raw, "imagePost": images})
        self.assertEqual(post["url"], "https://www.tiktok.com/@example/photo/70")
        self.assertEqual(post["assets"][0]["source_urls"][0], "https://p16.tiktokcdn.com/a.jpeg")

    def test_tiktok_page_data_and_refusals(self):
        def page(status=0, item=None):
            detail = {"statusCode": status, "itemInfo": {"itemStruct": item}} if item is not None else {"statusCode": status}
            data = json.dumps({"__DEFAULT_SCOPE__": {"webapp.video-detail": detail}})
            return f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{data}</script>'
        self.assertEqual(tiktok.page_item(page(item={"id": "70"}), "70"), {"id": "70"})
        self.assertIsNone(tiktok.page_item(page(item={"id": "71"}), "70"))
        self.assertIsNone(tiktok.page_item("<html>sans données</html>", "70"))
        self.assertIsNone(tiktok.page_item(page().replace('{"statusCode": 0}', "null"), "70"))
        for status, message in ((10222, "private"), (10204, "deleted")):
            with self.subTest(status=status), self.assertRaises(RuntimeError) as raised:
                tiktok.page_item(page(status=status), "70")
            self.assertIn(message, str(raised.exception))
        private = net.Response("https://www.tiktok.com/", "text/html", page(item={"id": "70", "author": {"privateAccount": True}}).encode(), {})
        with patch("archiver.net.fetch", return_value=private), self.assertRaises(RuntimeError):
            tiktok.fetch_post("70")

    def test_tiktok_cookies_only_go_to_tiktok_hosts(self):
        cookies = {"tt_chain_token": "abc", "ttwid": "1"}
        self.assertEqual(tiktok.media_headers("https://v16-webapp-prime.tiktok.com/video/x", cookies)["Cookie"], "tt_chain_token=abc; ttwid=1")
        self.assertNotIn("Cookie", tiktok.media_headers("https://p16-sign.tiktokcdn-us.com/a.jpeg", cookies))
        self.assertNotIn("Cookie", tiktok.media_headers("https://eviltiktok.com/x", cookies))

    def test_availability(self):
        deleted = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{"__DEFAULT_SCOPE__": {"webapp.video-detail": {"statusCode": 10204}}}</script>'
        with patch("archiver.net.fetch", return_value=net.Response("", "text/html", deleted.encode(), {})):
            self.assertEqual(tiktok.online({"id": "tt-70"}, None), GONE)
            # An archive made from a short link is checked by the post its link became.
            self.assertEqual(tiktok.online({"id": "tt-ZTexample", "source_url": "https://www.tiktok.com/@example/video/70"}, None), GONE)
        self.assertIsNone(tiktok.online({"id": "tt-ZTexample", "source_url": "https://vm.tiktok.com/ZTexample/"}, None))
