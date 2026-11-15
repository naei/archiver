"""The Instagram integration, without network access."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from archiver.integrations import ONLINE, UNKNOWN
from archiver.integrations.instagram import best_image, fetch_post, online, post_details
from archiver.store import source_url


def instagram_image(name, size=None):
    stp = f"dst-jpg_e35_s{size}x{size}_tt6" if size else "dst-jpg_e35_tt6"
    return {"url": f"https://scontent.cdninstagram.com/v/{name}.jpg?stp={stp}&oh=1"}


class InstagramTests(unittest.TestCase):
    def test_post_and_reel_urls(self):
        for url in ("https://www.instagram.com/p/Example123-/", "instagram.com/reel/Example123-?igsh=abc",
                    "https://www.instagram.com/reels/Example123-/", "https://www.instagram.com/tv/Example123-",
                    "https://www.instagram.com/example/p/Example123-/"):
            with self.subTest(url=url):
                self.assertEqual(source_url(url), ("ig-Example123-", "https://www.instagram.com/p/Example123-/", "Instagram"))
        for url in ("https://www.instagram.com/example/", "https://www.instagram.com/stories/example/123/",
                    "https://www.instagram.com/p/abc/", "https://instagr.am/p/Example123-/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)

    def test_full_size_photo_is_preferred(self):
        media = {"image_versions2": {"candidates": [instagram_image("a", 480), instagram_image("a"), instagram_image("a", 150)]}}
        self.assertNotIn("s480x480", best_image(media))
        self.assertIn("s150x150", best_image({"image_versions2": {"candidates": [instagram_image("a", 150)]}}))

    def test_carousel_items_match_yt_dlp_entries(self):
        product = {
            "code": "ABCDEF", "taken_at": 1546621545, "caption": {"text": " Légende "},
            "user": {"username": "compte", "full_name": "Le Compte"},
            "carousel_media": [
                {"media_type": 1, "image_versions2": {"candidates": [instagram_image("p")]}, "accessibility_caption": "Une photo",
                 "original_width": 1080, "original_height": 1350},
                {"media_type": 2, "original_width": 720, "original_height": 1280},
            ],
        }
        info = {"_type": "playlist", "entries": [{"id": "1"}, {"id": "2", "formats": [{"url": "https://cdn/v.mp4"}]}]}
        post = post_details(product, info)
        self.assertEqual((post["author"], post["handle"], post["text"]), ("Le Compte", "compte", "Légende"))
        self.assertEqual(post["published_at"], "2019-01-04T17:05:45+00:00")
        self.assertEqual(post["url"], "https://www.instagram.com/p/ABCDEF/")
        self.assertEqual([asset["type"] for asset in post["assets"]], ["image", "video"])
        self.assertEqual(post["assets"][0]["alt_text"], "Une photo")
        self.assertEqual(post["assets"][1]["entry"]["id"], "2")
        with self.assertRaises(RuntimeError):
            post_details({**product, "carousel_media": [{"media_type": 2}]}, info={"_type": "playlist", "entries": [{"id": "1"}]})

    def test_private_or_mismatched_posts_are_refused(self):
        for product in ({"code": "ABCDEF", "user": {"is_private": True}}, {"code": "AUTRE", "user": {}}, None):
            session = unittest.mock.Mock(capture=unittest.mock.Mock(return_value=(product, None)))
            with self.subTest(product=product), self.assertRaises(RuntimeError):
                fetch_post(session, "ABCDEF")

    def test_availability(self):
        item, worker = {"id": "ig-Example123-"}, SimpleNamespace(stopping=threading.Event())
        for answer, expected in (((None, None), UNKNOWN), (({"code": "Example123-"}, None), ONLINE)):
            with self.subTest(expected=expected), patch("archiver.ytdlp.Session.capture", return_value=answer):
                self.assertEqual(online(item, worker), expected)
