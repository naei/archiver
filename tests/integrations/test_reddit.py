"""The Reddit integration, without network access."""
import threading
import unittest
from unittest.mock import patch

from archiver import net
from archiver.integrations import GONE, ONLINE, UNKNOWN, reddit
from archiver.store import source_url


class RedditTests(unittest.TestCase):
    PERMALINK = "https://www.reddit.com/r/example/comments/abc12/un_titre/"

    def entry(self, kind="t3", code="abc12", body="<p>Texte</p>", target=None, author="/u/alice", title="Un titre &amp; plus", link=None):
        content = (f'<!-- SC_OFF --><div class="md">{body}</div><!-- SC_ON -->' if body else '<table><tr><td><img src="https://preview.redd.it/x.jpg?width=140"/></td></tr></table>')
        content += f' submitted by <a href="https://www.reddit.com/user/alice"> /u/alice </a> <span><a href="{target or self.PERMALINK}">[link]</a></span> <span><a href="{self.PERMALINK}">[comments]</a></span>'
        escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return (f'<entry><author><name>{author}</name></author><category term="example" label="r/example"/><content type="html">{escaped}</content>'
                f'<id>{kind}_{code}</id><link href="{link or self.PERMALINK}"/><updated>2026-01-02T03:04:05+00:00</updated>'
                f'<published>2026-01-02T03:04:05+00:00</published><title>{title}</title></entry>')

    def feed(self, *entries):
        return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>").encode()

    def details(self, **entry):
        return reddit.post_details(reddit.read_entries(self.feed(self.entry(**entry)))[0], photos=lambda: [{"gallery": True}])

    def test_post_and_comment_links(self):
        for url in (self.PERMALINK + "?utm_source=share", "https://old.reddit.com/comments/abc12", "redd.it/abc12", "https://www.reddit.com/gallery/abc12",
                    "https://www.reddit.com/user/alice/comments/abc12/un_titre/"):
            with self.subTest(url=url):
                self.assertEqual(source_url(url), ("rdt-abc12", "https://www.reddit.com/comments/abc12/", "Reddit"))
        self.assertEqual(source_url(self.PERMALINK + "def34/?context=3"),
                         ("rdt-abc12-def34", "https://www.reddit.com/comments/abc12/_/def34/", "Reddit"))
        self.assertEqual(source_url("https://www.reddit.com/r/example/s/AbCdEf1234"),
                         ("rdts-AbCdEf1234", "https://www.reddit.com/r/example/s/AbCdEf1234", "Reddit"))
        for url in ("https://www.reddit.com/", "https://www.reddit.com/r/example/", "https://www.reddit.com/user/alice", "https://redd.it/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)
        self.assertEqual(reddit.address({"source_url": "https://www.reddit.com/comments/abc12/_/def34/"}), ("abc12", "def34"))
        self.assertEqual(reddit.address({"source_url": self.PERMALINK}), ("abc12", None))

    def test_share_link_is_followed(self):
        item = {"source_url": "https://www.reddit.com/r/example/s/AbCdEf1234"}
        response = unittest.mock.Mock()
        with patch("archiver.net.open_url", return_value=(self.PERMALINK + "?share_id=x", response)):
            self.assertEqual(reddit.address(item), ("abc12", None))
        # A shared comment: its address has no title, and its id is longer than a post's.
        with patch("archiver.net.open_url", return_value=("https://www.reddit.com/r/example/comments/abc12/comment/1a2b3c4d5e6f7/?share_id=x", response)):
            self.assertEqual(reddit.address(item), ("abc12", "1a2b3c4d5e6f7"))
        self.assertEqual(source_url("https://www.reddit.com/r/example/comments/abc12/comment/1a2b3c4d5e6f7/")[0], "rdt-abc12-1a2b3c4d5e6f7")
        with patch("archiver.net.open_url", return_value=("https://www.reddit.com/r/example/", response)), self.assertRaises(RuntimeError):
            reddit.address(item)

    def test_text_post(self):
        body = ('<p>Bonjour <a href="https://exemple.test/page">https://exemple.test/page</a> et <a href="https://ailleurs.example/">ici</a></p>'
                '<ul><li>un</li><li>deux</li></ul><blockquote><p>cité</p></blockquote>')
        post = self.details(body=body)
        self.assertEqual((post["id"], post["url"], post["title"], post["author"], post["published_at"], post["assets"], post["quote"]),
                         ("abc12", self.PERMALINK, "Un titre & plus", "u/alice · r/example", "2026-01-02T03:04:05+00:00", [], None))
        self.assertEqual(post["text"], "Bonjour https://exemple.test/page et ici (https://ailleurs.example/)\n\n– un\n– deux\n\ncité")

    def test_media_and_link_posts(self):
        self.assertEqual(self.details(body="", target="https://i.redd.it/photo1.jpeg")["assets"],
                         [{"alt_text": "", "duration": None, "width": None, "height": None, "type": "image", "source_urls": ["https://i.redd.it/photo1.jpeg"]}])
        video = self.details(body="", target="https://v.redd.it/video1abc")["assets"][0]
        self.assertEqual((video["type"], video["entry"]["url"]), ("video", "https://v.redd.it/video1abc/DASHPlaylist.mpd"))
        self.assertEqual(self.details(body="", target="https://www.reddit.com/gallery/abc12")["assets"], [{"gallery": True}])
        shared = self.details(body="", target="https://exemple.test/article")
        self.assertEqual((shared["text"], shared["assets"]), ("https://exemple.test/article", []))
        crossposted = self.details(body="<p>Vu ailleurs</p>", target="https://www.reddit.com/r/autre/comments/zzz99/origine/")
        self.assertEqual(crossposted["text"], "Vu ailleurs\n\nhttps://www.reddit.com/r/autre/comments/zzz99/origine/")
        with self.assertRaises(reddit.Gone):
            self.details(body="<p>[removed]</p>", author="/u/[deleted]")

    def test_gallery_photos(self):
        page = ('<gallery-carousel><img src="https://preview.redd.it/un-titre-v0-aaa111.jpeg?width=320&amp;crop=smart&amp;s=1">'
                '<img srcset="https://preview.redd.it/un-titre-v0-aaa111.jpeg?width=1080&amp;crop=smart&amp;s=2 1080w, '
                'https://preview.redd.it/un-titre-v0-bbb222.png?width=640&amp;s=3 640w"></gallery-carousel>'
                '<img src="https://external-preview.redd.it/other.png?width=320">')
        with patch("archiver.net.fetch", return_value=net.Response("", "text/html", page.encode(), {})) as fetch:
            photos = reddit.gallery(reddit.embed(self.PERMALINK))
        self.assertEqual(fetch.call_args[0][0], "https://embed.reddit.com/r/example/comments/abc12/un_titre/?embed=true")
        self.assertEqual([photo["source_urls"] for photo in photos], [
            ["https://i.redd.it/aaa111.jpeg", "https://preview.redd.it/un-titre-v0-aaa111.jpeg?width=1080&crop=smart&s=2"],
            ["https://i.redd.it/bbb222.png", "https://preview.redd.it/un-titre-v0-bbb222.png?width=640&s=3"]])
        with self.assertRaises(RuntimeError):
            reddit.gallery("<html></html>")

    def test_feed_answers(self):
        stopping = threading.Event()
        feed = net.Response("", "application/atom+xml", self.feed(self.entry()), {})
        with patch("archiver.net.fetch", return_value=feed) as fetch:
            reddit.fetch_feed("abc12", "def34", stopping)
        self.assertEqual(fetch.call_args[0][0], "https://www.reddit.com/comments/abc12/_/def34/.rss?limit=1")
        with patch("archiver.net.fetch", side_effect=net.HTTPStatus("absent", 404)), self.assertRaises(reddit.Gone):
            reddit.fetch_feed("abc12", None, stopping)
        with patch("archiver.net.fetch", side_effect=net.Blocked("privé", 403)), self.assertRaises(RuntimeError) as raised:
            reddit.fetch_feed("abc12", None, stopping)
        self.assertIn("without logging in", str(raised.exception))
        # One visit a minute: the next one is waited for, then the archive gives up until its automatic retry.
        with patch.object(reddit, "WAIT_SECONDS", 0), patch("archiver.net.fetch", side_effect=[net.Blocked("limite", 429), feed]) as fetch:
            self.assertEqual(reddit.fetch_feed("abc12", None, stopping), feed.body)
        with patch.object(reddit, "WAIT_SECONDS", 0), patch("archiver.net.fetch", side_effect=net.Blocked("limite", 429)) as fetch, \
                self.assertRaises(RuntimeError) as raised:
            reddit.fetch_feed("abc12", None, stopping)
        self.assertEqual(fetch.call_count, 3)
        self.assertIn("temporarily limiting", str(raised.exception))
        with self.assertRaises(RuntimeError):
            reddit.read_entries(b"<html>captcha")

    def test_keeps_reply_to_for_a_comment_under_its_post(self):
        comment = self.entry(kind="t1", code="def34", body="<p>Un avis</p>", author="/u/bob", link=self.PERMALINK + "def34/")
        feed = self.feed(self.entry(body="<p>Le post</p>", target="https://i.redd.it/photo1.jpeg"), comment)
        worker = unittest.mock.Mock(stopping=threading.Event())
        item = {"id": "rdt-abc12-def34", "source_url": "https://www.reddit.com/comments/abc12/_/def34/"}
        with patch("archiver.net.fetch", return_value=net.Response("", "application/atom+xml", feed, {})) as fetch:
            reddit.archive(item, worker)
        (_, posts, raw, _), _ = worker.archive_posts.call_args
        self.assertEqual((posts[0]["id"], posts[0]["author"], posts[0]["text"], posts[0]["url"], posts[0]["assets"]),
                         ("def34", "u/bob", "Un avis", self.PERMALINK + "def34/", []))
        under = posts[0]["reply_to"]
        self.assertEqual((under["author"], under["text"], under["url"]), ("u/alice · r/example", "Un titre & plus\n\nLe post", self.PERMALINK))
        self.assertEqual(under["assets"][0]["source_urls"], ["https://i.redd.it/photo1.jpeg"])
        self.assertEqual(raw["source"], "reddit")
        # Only the feed was read: the embed page is for galleries.
        self.assertEqual(fetch.call_count, 1)
        # A comment that is gone from its post's feed.
        with patch("archiver.net.fetch", return_value=net.Response("", "application/atom+xml", self.feed(self.entry()), {})), \
                self.assertRaises(reddit.Gone):
            reddit.archive(item, worker)

    def test_availability(self):
        item = {"id": "rdt-abc12", "source_url": self.PERMALINK}
        answers = lambda *entries: [net.Response("", "application/atom+xml", self.feed(*entries), {})]
        comment = self.entry(kind="t1", code="def34", body="<p>[removed]</p>", link=self.PERMALINK + "def34/")
        for answer, expected in ((net.HTTPStatus("absent", 404), GONE), (net.Blocked("limite", 429), UNKNOWN),
                                 (answers(self.entry()), ONLINE), ([net.Response("", "text/xml", b"<html>captcha", {})], UNKNOWN),
                                 # A deleted post is still in its feed, emptied; one of another post is not this one.
                                 (answers(self.entry(body="<p>[removed]</p>", author="/u/[deleted]")), GONE),
                                 (answers(self.entry(code="zzz99")), GONE)):
            with self.subTest(expected=expected), patch("archiver.net.fetch", side_effect=answer) as fetch:
                self.assertEqual(reddit.online(item, None), expected)
            self.assertEqual(fetch.call_args[0][0], "https://www.reddit.com/comments/abc12/.rss?limit=1")
        with patch("archiver.net.fetch", side_effect=answers(self.entry(), comment)):
            self.assertEqual(reddit.online({"id": "rdt-abc12-def34", "source_url": self.PERMALINK + "def34/"}, None), GONE)
