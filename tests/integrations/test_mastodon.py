"""The Mastodon integration, without network access."""
import unittest
from unittest.mock import patch

from archiver import availability, net
from archiver.integrations import mastodon
from archiver.store import source_url
from tests.fixtures import json_answer


class MastodonTests(unittest.TestCase):
    def status(self, status_id, content="<p>Bonjour</p>", author="7", reply_to=None, **extra):
        return {"id": status_id, "created_at": f"2026-01-02T03:04:0{status_id[-1]}.000Z", "content": content, "spoiler_text": "",
                "url": f"https://social.example/@alice/{status_id}", "replies_count": 0,
                "in_reply_to_id": reply_to, "in_reply_to_account_id": reply_to and "7",
                "account": {"id": author, "acct": "alice", "username": "alice", "display_name": "Alice"}, "media_attachments": [], **extra}

    def test_post_links_are_told_by_their_shape(self):
        media_id, url, platform = source_url("https://Social.Example/@alice/1123?utm_source=x")
        self.assertEqual((url, platform), ("https://social.example/@alice/1123", "Mastodon"))
        self.assertRegex(media_id, r"^masto-[0-9a-f]{16}$")
        # The same post under its permanent address; the same number on another server is another post.
        self.assertEqual(source_url("https://social.example/users/alice/statuses/1123")[0], media_id)
        self.assertNotEqual(source_url("https://autre.example/@alice/1123")[0], media_id)
        self.assertEqual(source_url("https://social.example/@bob@autre.example/1123")[1], "https://social.example/@bob@autre.example/1123")
        self.assertEqual(mastodon.address({"source_url": "https://social.example/@bob@autre.example/1123"}), ("social.example", "1123"))
        for url in ("https://social.example/@alice", "https://social.example/@alice/with_replies", "https://social.example/@alice/1123/extra",
                    "https://social.example/tags/1123"):
            with self.subTest(url=url):
                self.assertEqual(source_url(url)[2], "Web")
        with self.assertRaises(ValueError):
            source_url("https://www.threads.com/@alice/post/C1")
        # Platforms with their own integration keep their links.
        self.assertEqual(source_url("https://www.tiktok.com/@alice/video/7234567890123456789")[2], "TikTok")

    def test_text_from_html(self):
        content = ('<p><span class="h-card"><a href="https://autre.example/@bob" class="u-url mention">@<span>bob</span></a></span> Voir '
                   '<a href="https://exemple.test/un-tres-long-lien"><span class="invisible">https://</span><span class="ellipsis">exemple.test/un-tr</span>'
                   '<span class="invisible">es-long-lien</span></a><br>suite <a href="https://social.example/tags/chat" class="mention hashtag">#<span>chat</span></a></p>'
                   '<p>Fin &amp; fin</p>')
        self.assertEqual(mastodon.plain(content), "@bob Voir https://exemple.test/un-tres-long-lien\nsuite #chat\n\nFin & fin")
        self.assertEqual(mastodon.plain(""), "")
        self.assertEqual(mastodon.plain("Texte nu"), "Texte nu")
        quote = '<p class="quote-inline">RE: <a href="https://autre.example/@bob/5">lien</a></p><p>Mon avis</p>'
        self.assertEqual(mastodon.plain(quote, quoting=True), "Mon avis")
        self.assertIn("RE:", mastodon.plain(quote))

    def test_post_details(self):
        media = [
            {"type": "image", "url": "https://files.example/a.jpg", "remote_url": None, "description": "Un chat",
             "meta": {"original": {"width": 800, "height": 600}}},
            {"type": "gifv", "url": "https://files.example/b.mp4", "remote_url": "https://autre.example/b.mp4",
             "meta": {"original": {"width": 320, "height": 240, "duration": 2.5}}},
            {"type": "audio", "url": "https://files.example/c.mp3"}, {"type": "unknown", "url": None},
        ]
        quoted = self.status("5", "<p>Cité</p>", author="9", url="https://autre.example/@bob/55",
                             quote={"state": "accepted", "quoted_status": self.status("6")})
        quoted["account"] = {"id": "9", "acct": "bob@autre.example", "username": "bob", "display_name": ""}
        status = self.status("1123", "<p>Texte</p>", spoiler_text="Attention", media_attachments=media,
                             poll={"options": [{"title": "Oui"}, {"title": "Non"}]}, quote={"state": "accepted", "quoted_status": quoted})
        post = mastodon.post_details(status, "social.example")
        self.assertEqual((post["id"], post["url"], post["author"], post["handle"], post["published_at"]),
                         ("1123", "https://social.example/@alice/1123", "Alice", "alice@social.example", "2026-01-02T03:04:03+00:00"))
        self.assertEqual(post["text"], "Attention\n\nTexte\n\n– Oui\n– Non\n\nhttps://files.example/c.mp3")
        self.assertEqual([(a["type"], a["alt_text"], a["duration"], a["source_urls"]) for a in post["assets"]], [
            ("image", "Un chat", None, ["https://files.example/a.jpg"]),
            ("video", "", 2.5, ["https://files.example/b.mp4", "https://autre.example/b.mp4"])])

    def test_keeps_quote(self):
        # The quoted post quotes another one itself: only the first is kept.
        quoted = self.status("5", "<p>Cité</p>", author="9", url="https://autre.example/@bob/55",
                             quote={"state": "accepted", "quoted_status": self.status("6")})
        quoted["account"] = {"id": "9", "acct": "bob@autre.example", "username": "bob", "display_name": ""}
        post = mastodon.post_details(self.status("1123", "<p>Texte</p>", quote={"state": "accepted", "quoted_status": quoted}), "social.example")
        self.assertEqual(post["quote"], {"author": "bob", "handle": "bob@autre.example", "text": "Cité",
                                         "url": "https://autre.example/@bob/55", "published_at": "2026-01-02T03:04:05+00:00", "assets": []})
        self.assertIsNone(mastodon.post_details(self.status("1", quote={"state": "deleted", "quoted_status": None}), "social.example")["quote"])

    def test_keeps_thread_of_the_author(self):
        first, second, third = self.status("1"), self.status("2", reply_to="1"), self.status("3", reply_to="2")
        late = self.status("4", reply_to="2")
        stranger = self.status("5", author="9", reply_to="2")
        context = {"ancestors": [first], "descendants": [late, stranger, third]}
        self.assertEqual([one["id"] for one in mastodon.thread(second, context)], ["1", "2", "3"])
        reply = {**self.status("2", reply_to="1"), "in_reply_to_account_id": "9"}
        self.assertEqual([one["id"] for one in mastodon.thread(reply, {"ancestors": [self.status("1", author="9")], "descendants": []})], ["2"])

    def test_keeps_reply_to(self):
        parent = self.status("5", "<p>Question</p>", author="9", url="https://autre.example/@bob/55")
        parent["account"] = {"id": "9", "acct": "bob@autre.example", "username": "bob", "display_name": "Bob"}
        reply = {**self.status("6", reply_to="5"), "in_reply_to_account_id": "9"}
        with patch("archiver.net.fetch", return_value=json_answer(parent)) as fetch:
            answered = mastodon.answered(reply, "social.example")
        self.assertEqual(fetch.call_args[0][0], "https://social.example/api/v1/statuses/5")
        self.assertEqual((answered["author"], answered["handle"], answered["text"], answered["url"]),
                         ("Bob", "bob@autre.example", "Question", "https://autre.example/@bob/55"))
        # Gone or hidden: the post is archived without it. A self-reply or a post that answers nothing asks for nothing.
        with patch("archiver.net.fetch", side_effect=net.HTTPStatus("absent", 404)):
            self.assertIsNone(mastodon.answered(reply, "social.example"))
        with patch("archiver.net.fetch", side_effect=AssertionError("not asked")):
            self.assertIsNone(mastodon.answered(self.status("6", reply_to="5"), "social.example"))
            self.assertIsNone(mastodon.answered(self.status("6"), "social.example"))

    def test_server_answers(self):
        status = self.status("1123")
        with patch("archiver.net.fetch", return_value=json_answer(status)) as fetch:
            self.assertEqual(mastodon.fetch_status("social.example", "1123")["id"], "1123")
        self.assertEqual(fetch.call_args[0][0], "https://social.example/api/v1/statuses/1123")
        with patch("archiver.net.fetch", return_value=json_answer({**self.status("9"), "reblog": status})):
            self.assertEqual(mastodon.fetch_status("social.example", "9")["id"], "1123")
        # Not found on a Mastodon server: deleted. Not found on another site: not a Mastodon post.
        with patch("archiver.net.fetch", side_effect=[net.HTTPStatus("absent", 404), json_answer({"uri": "social.example"})]), \
                self.assertRaises(mastodon.Gone):
            mastodon.fetch_status("social.example", "1123")
        for failure in ([net.HTTPStatus("absent", 404), net.HTTPStatus("absent", 404)], [ValueError("text/html")], [json_answer({"page": 1})]):
            with self.subTest(failure=failure), patch("archiver.net.fetch", side_effect=failure), self.assertRaises(mastodon.Final) as raised:
                mastodon.fetch_status("blog.example", "1123")
            self.assertNotIsInstance(raised.exception, mastodon.Gone)
            self.assertIn("blog.example", str(raised.exception))
        with patch("archiver.net.fetch", side_effect=net.Blocked("refus", 401)), self.assertRaises(RuntimeError) as raised:
            mastodon.fetch_status("social.example", "1123")
        self.assertIn("without logging in", str(raised.exception))
        self.assertNotIsInstance(raised.exception, mastodon.Final)

    def test_availability(self):
        item = {"platform": "Mastodon", "id": "masto-0123456789abcdef", "source_url": "https://social.example/@alice/1123"}
        with patch("archiver.integrations.mastodon.page", return_value=availability.GONE) as page:
            self.assertEqual(availability.check(item, None), availability.GONE)
        self.assertEqual(page.call_args[0][0], "https://social.example/api/v1/statuses/1123")
