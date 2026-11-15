"""The Bilibili integration, without network access."""
import json
import unittest
from unittest.mock import patch

from archiver import availability, i18n, net
from archiver.integrations import bilibili
from archiver.store import source_url


class BilibiliTests(unittest.TestCase):
    VIEW = {"bvid": "BV1xxxxxxxxx", "title": "Concerts", "desc": " Tous les concerts ", "pubdate": 1619921827,
            "pic": "http://i0.hdslb.com/bfs/archive/cover.png", "owner": {"name": "Auteur"}, "duration": 60000,
            "pages": [{"page": 1, "cid": 10, "part": "Premier", "duration": 100},
                      {"page": 44, "cid": 440, "part": "AOL Sessions", "duration": 1342}]}
    PLAY = {"dash": {"video": [{"id": 80, "codecid": 7, "codecs": "avc1.640032", "width": 1920, "height": 1080,
                                "frameRate": "29.970", "bandwidth": 2000000, "baseUrl": "https://upos.bilivideo.com/v.m4s"}],
                     "audio": [{"id": 30280, "codecs": "mp4a.40.2", "bandwidth": 320000, "baseUrl": "https://upos.bilivideo.com/a.m4s"}]}}

    def test_video_urls_keep_their_part(self):
        for url, expected in (
            ("https://www.bilibili.com/zh-Hans/video/BV1xxxxxxxxx/?p=44",
             ("bili-BV1xxxxxxxxx-p44", "https://www.bilibili.com/video/BV1xxxxxxxxx/?p=44")),
            ("https://m.bilibili.com/video/BV1xxxxxxxxx?p=44&spm_id_from=333.788",
             ("bili-BV1xxxxxxxxx-p44", "https://www.bilibili.com/video/BV1xxxxxxxxx/?p=44")),
            ("bilibili.com/video/bv1xxxxxxxxx?p=1", ("bili-BV1xxxxxxxxx", "https://www.bilibili.com/video/BV1xxxxxxxxx/")),
            ("https://www.bilibili.com/video/BV1xxxxxxxxx/?p=abc", ("bili-BV1xxxxxxxxx", "https://www.bilibili.com/video/BV1xxxxxxxxx/")),
            ("https://www.bilibili.com/video/av100000001/?p=3", ("bili-av100000001-p3", "https://www.bilibili.com/video/av100000001/?p=3")),
        ):
            with self.subTest(url=url):
                self.assertEqual(source_url(url), (*expected, "Bilibili"))
        self.assertEqual(bilibili.parse("bili-BV1xxxxxxxxx-p44"), ("BV1xxxxxxxxx", 44))
        self.assertEqual(bilibili.parse("bili-av100000001"), ("av100000001", 1))
        for url in ("https://www.bilibili.com/", "https://space.bilibili.com/0", "https://live.bilibili.com/0",
                    "https://www.bilibili.com/bangumi/play/ep1", "https://www.bilibili.com/video/BV0OOOOOOOOO/", "https://b23.tv/abcdef"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)

    def test_part_details_and_streams(self):
        video = bilibili.video_details(self.VIEW, self.VIEW["pages"][1], self.PLAY, "https://www.bilibili.com/video/BV1xxxxxxxxx/?p=44")
        self.assertEqual((video["title"], video["author"], video["description"], video["duration"]),
                         ("AOL Sessions – Concerts", "Auteur", "Tous les concerts", 1342))
        self.assertEqual(video["published_at"], "2021-05-02T02:17:07+00:00")
        self.assertEqual(video["cover"], "https://i0.hdslb.com/bfs/archive/cover.png")
        entry = video["entry"]
        self.assertEqual(entry["id"], "BV1xxxxxxxxx_p44")
        self.assertEqual(entry["http_headers"]["Referer"], "https://www.bilibili.com/")
        self.assertNotIn("Mozilla", entry["http_headers"]["User-Agent"])
        streams = {stream["format_id"]: stream for stream in entry["formats"]}
        self.assertEqual((streams["video-80-7"]["height"], streams["video-80-7"]["acodec"]), (1080, "none"))
        self.assertEqual(streams["audio-30280"]["vcodec"], "none")
        single = bilibili.video_details({**self.VIEW, "pages": self.VIEW["pages"][:1]}, self.VIEW["pages"][0], self.PLAY, "")
        self.assertEqual(single["title"], "Concerts")
        old = bilibili.formats({"quality": 64, "durl": [{"url": "https://upos.bilivideo.com/v.flv?e=1"}]})
        self.assertEqual([(stream["format_id"], stream["ext"]) for stream in old], [("file-64", "flv")])
        with self.assertRaises(RuntimeError):
            bilibili.video_details(self.VIEW, self.VIEW["pages"][0], {"dash": {}}, "")

    def test_api_answers(self):
        def answer(data):
            return net.Response("", "application/json", json.dumps(data).encode(), {})
        with patch("archiver.net.fetch", side_effect=[answer({"code": 0, "data": self.VIEW}), answer({"code": 0, "data": self.PLAY})]) as fetch:
            view, page, play = bilibili.fetch_video("BV1xxxxxxxxx", 44)
        self.assertEqual((page["cid"], play), (440, self.PLAY))
        self.assertIn("bvid=BV1xxxxxxxxx&cid=440", fetch.call_args_list[1][0][0])
        with patch("archiver.net.fetch", return_value=answer({"code": 0, "data": self.VIEW})), self.assertRaises(RuntimeError) as raised:
            bilibili.fetch_video("BV1xxxxxxxxx", 45)
        self.assertIn("part 45", str(raised.exception))
        with patch("archiver.net.fetch", return_value=answer({"code": -404})), self.assertRaises(bilibili.Gone):
            bilibili.video("av1")
        with patch("archiver.net.fetch", return_value=answer({"code": -352, "message": "风控"})), self.assertRaises(bilibili.Refused):
            bilibili.video("av1")
        with patch("archiver.net.fetch", side_effect=net.HTTPStatus("refus", 412)), self.assertRaises(bilibili.Refused):
            bilibili.video("av1")
        with patch("archiver.net.fetch", return_value=answer({"code": 62002})), self.assertRaises(RuntimeError) as raised:
            bilibili.video("av1")
        self.assertNotIsInstance(raised.exception, bilibili.Gone)

    def test_availability(self):
        item = {"platform": "Bilibili", "id": "bili-BV1xxxxxxxxx-p44.2", "source_url": ""}
        for side_effect, expected in ((bilibili.Gone("supprimée"), availability.GONE), (RuntimeError("cachée"), availability.UNKNOWN),
                                      (None, availability.ONLINE)):
            with self.subTest(expected=expected), patch("archiver.integrations.bilibili.video", side_effect=side_effect) as video:
                self.assertEqual(availability.check(item, None), expected)
            video.assert_called_with("BV1xxxxxxxxx")

    def test_a_profile_link_is_refused_with_a_translated_message(self):
        with self.assertRaises(ValueError) as raised:
            source_url("https://space.bilibili.com/0")
        self.assertNotEqual(i18n.translate(str(raised.exception), "fr"), str(raised.exception))
