"""The X integration, without network access."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from archiver import net
from archiver.integrations import GONE, ONLINE, UNKNOWN, Gone
from archiver.integrations.x import XClient, cdn_url, online, original_photo, page_detail, page_post, page_posts, syndication_post, thread
from tests.fixtures import POST_ID, POST_URL, x_post


def page_tweet(post_id, text="Texte", user="10", handle="auteur", reply_to=None, reply_to_user=None, **extra):
    """A post in the format of the logged-out x.com page data."""
    tweet = {
        "__typename": "Tweet", "rest_id": post_id,
        "core": {"user_results": {"result": {"rest_id": user, "core": {"name": handle.title(), "screen_name": handle}}}},
        "details": {"created_at_ms": 1740167696000, "full_text": text},
        "url_entities": [], "media_entities2": [],
    }
    if reply_to:
        tweet["reply_to_results"] = {"rest_id": reply_to}
        tweet["reply_to_user_results"] = {"rest_id": reply_to_user or user}
    tweet.update(extra)
    return tweet


def page_html(focal, *conversation):
    data = {"data": {
        "tweet_result_by_rest_id": {"result": focal},
        "threaded_conversation_with_injections_v2": {"instructions": [{"entries": [
            {"content": {"items": [{"item": {"content": {"tweet_results": {"result": tweet}}}}]}} for tweet in conversation
        ]}]},
    }}
    key = json.dumps('abc{"x":1}')
    return (f'<script>($R=>$R[33].next($R[35]={{kind:"GraphQLRequestStream.Started",requestId:1,key:{key},name:"TweetDetail"}}))</script>'
            f'<script>($R=>$R[33].next($R[39]={{kind:"GraphQLRequestStream.Completed",requestId:1,key:{key},'
            f'result:$R[40]={{kind:"Result.Ok",value:{json.dumps(json.dumps(data))}}}}}))</script>')


def numbered(page):
    """The same page as X writes it since November 2026: the kind of each record is a number."""
    return page.replace('kind:"GraphQLRequestStream.Started"', "kind:0").replace('kind:"GraphQLRequestStream.Completed"', "kind:1")


def chain(*posts):
    return {post["id"]: post for post in map(page_post, posts)}


class XTests(unittest.TestCase):
    def test_embed_post_metadata_and_best_media(self):
        post = syndication_post(x_post(), POST_ID)
        self.assertEqual(post["text"], "Merci & bravo https://example.org/page")
        self.assertEqual(post["author"], "Example")
        self.assertEqual(post["published_at"], "2017-08-06T01:05:32+00:00")
        self.assertEqual(post["url"], POST_URL)
        self.assertEqual(post["assets"][0]["source_url"], "https://pbs.twimg.com/media/abc?format=jpg&name=orig")
        self.assertEqual(post["assets"][0]["alt_text"], "Une équipe")
        self.assertEqual(post["assets"][1]["source_url"], "https://video.twimg.com/high.mp4")
        self.assertEqual(post["assets"][1]["duration"], 4.5)

    def test_embed_text_only_and_long_posts(self):
        self.assertEqual(syndication_post(x_post(mediaDetails=[]), POST_ID)["assets"], [])
        note = {"text": "Texte complet https://t.co/long", "entity_set": {
            "urls": [{"url": "https://t.co/long", "expanded_url": "https://example.org/long"}]}}
        post = x_post(note_tweet={"id": "abc", "note_tweet_results": {"result": note}})
        self.assertEqual(syndication_post(post, POST_ID)["text"], "Texte complet https://example.org/long")
        text = syndication_post(x_post(note_tweet={"id": "abc"}), POST_ID)["text"]
        self.assertTrue(text.startswith("Merci & bravo https://example.org/page […]"))
        self.assertIn("excerpt", text)

    def test_rejects_unavailable_posts_and_foreign_media_hosts(self):
        for post in ({}, x_post(id_str="1"), x_post(mediaDetails=[{"type": "poll"}])):
            with self.subTest(post=post), self.assertRaises(ValueError):
                syndication_post(post, POST_ID)
        for url in ("http://pbs.twimg.com/a.jpg", "https://evil.test/a.mp4", "https://video.twimg.com:8443/a.mp4"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                cdn_url(url)
        with self.assertRaises(ValueError):
            original_photo("https://video.twimg.com/a.jpg")

    def test_page_post_text_media_and_quote(self):
        quoted = page_tweet("5", "Post cité", user="20", handle="autre")
        raw = page_tweet(
            "1", "Lire https://t.co/link &amp; voir https://t.co/media",
            url_entities=[{"url": "https://t.co/link", "expanded_url": "https://example.org"}],
            media_entities2=[{"type": "photo", "media_url_https": "https://pbs.twimg.com/media/p.png"}],
            quoted_tweet_results={"result": quoted},
        )
        post = page_post(raw)
        self.assertEqual(post["text"], "Lire https://example.org & voir")
        self.assertEqual(post["published_at"], "2025-02-21T19:54:56+00:00")
        self.assertEqual(post["url"], "https://x.com/auteur/status/1")
        self.assertEqual(post["assets"][0]["source_url"], "https://pbs.twimg.com/media/p?format=png&name=orig")
        self.assertEqual(post["quote"], {"author": "Autre", "handle": "autre", "text": "Post cité", "url": "https://x.com/autre/status/5", "assets": []})
        note = {"text": "Texte long complet", "entity_set": {"urls": []}}
        self.assertEqual(page_post(page_tweet("2", "Texte long…", note_tweet={"note_tweet_results": {"result": note}}))["text"], "Texte long complet")

    def test_keeps_quote_with_its_media(self):
        video = x_post()["mediaDetails"][1]
        quoted = page_tweet("5", "Post cité https://t.co/v", user="20", handle="autre", media_entities2=[video])
        quote = page_post(page_tweet("1", "Regardez", quoted_tweet_results={"result": quoted}))["quote"]
        self.assertEqual(quote["text"], "Post cité")
        self.assertEqual([asset["source_url"] for asset in quote["assets"]], ["https://video.twimg.com/high.mp4"])
        embed = x_post(mediaDetails=[], quoted_tweet={"id_str": "5", "text": "Post cité", "user": {"name": "Autre", "screen_name": "autre"},
                                                      "mediaDetails": [video]})
        post = syndication_post(embed, POST_ID)
        self.assertEqual((post["assets"], [asset["duration"] for asset in post["quote"]["assets"]]), ([], [4.5]))
        # Media X offers in an unsupported form leave the quote with its text, and the post archivable.
        quoted["media_entities2"] = [{"type": "poll"}]
        quote = page_post(page_tweet("1", "Regardez", quoted_tweet_results={"result": quoted}))["quote"]
        self.assertEqual((quote["text"], quote["assets"]), ("Post cité", []))

    def test_page_detail_and_unavailable_or_protected_posts(self):
        focal = page_tweet("1", "Premier")
        posts = page_posts(page_detail(page_html(focal, focal, page_tweet("2", "Suite", reply_to="1"))), "1")
        self.assertEqual(sorted(posts), ["1", "2"])
        # The focal copy may lack the reply link that the conversation copy has.
        focal = page_tweet("2", "Suite")
        posts = page_posts(page_detail(page_html(focal, page_tweet("2", "Suite", reply_to="1"))), "2")
        self.assertEqual(posts["2"]["parent_id"], "1")
        self.assertEqual(sorted(page_posts(page_detail(numbered(page_html(focal, page_tweet("2", "Suite", reply_to="1")))), "2")), ["2"])
        with self.assertRaises(ValueError):
            page_detail("<html>sans données</html>")
        with self.assertRaises(ValueError):
            page_posts(page_detail(page_html({"__typename": "TweetTombstone"})), "1")
        protected = page_tweet("1")
        protected["core"]["user_results"]["result"]["privacy"] = {"protected": True}
        with self.assertRaises(ValueError):
            page_posts(page_detail(page_html(protected, protected)), "1")

    def test_keeps_thread_of_author_self_replies_only(self):
        known = chain(
            page_tweet("1", "Début"), page_tweet("2", "Deux", reply_to="1"), page_tweet("3", "Trois", reply_to="2"),
            page_tweet("4", "Réponse parallèle de l’auteur", reply_to="1"),
            page_tweet("5", "Réponse d’un autre", user="20", handle="autre", reply_to="3", reply_to_user="10"),
        )
        fetched = []
        posts = thread(known["2"], known, lambda post_id: fetched.append(post_id) or {})
        self.assertEqual([post["id"] for post in posts], ["1", "2", "3"])
        self.assertEqual(fetched, ["3"])  # one check for continuations after the last post

    def test_thread_fills_gaps_from_other_pages(self):
        known = chain(page_tweet("1", "Début"), page_tweet("2", "Deux", reply_to="1"), page_tweet("4", "Quatre", reply_to="3"))
        pages = {"3": chain(page_tweet("3", "Trois", reply_to="2"), page_tweet("4", "Quatre", reply_to="3")),
                 "4": chain(page_tweet("4", "Quatre", reply_to="3"), page_tweet("5", "Cinq", reply_to="4"))}
        posts = thread(known["1"], known, lambda post_id: pages.get(post_id, {}))
        self.assertEqual([post["id"] for post in posts], ["1", "2", "3", "4", "5"])

    def test_thread_stops_at_replies_to_other_accounts_and_on_failed_pages(self):
        known = chain(page_tweet("9", "Post d’un autre", user="20", handle="autre"),
                      page_tweet("10", "Réponse", reply_to="9", reply_to_user="20"))
        self.assertEqual([post["id"] for post in thread(known["10"], known, lambda post_id: {})], ["10"])
        known = chain(page_tweet("2", "Deux", reply_to="1"))
        self.assertEqual([post["id"] for post in thread(known["2"], known, lambda post_id: {})], ["2"])


    def test_keeps_reply_to(self):
        answered = page_tweet("1", "Question", user="20", handle="autre")
        reply = page_tweet("2", "Réponse", reply_to="1", reply_to_user="20")
        embed = {"id_str": "2", "text": "Réponse", "user": {"id_str": "10", "name": "Auteur", "screen_name": "auteur"}}
        client = XClient(SimpleNamespace(stopping=threading.Event()))
        with patch.object(client, "fetch_post", return_value=embed), \
                patch.object(client, "read", return_value=page_html(reply, answered, reply).encode()):
            posts, raw = client.fetch_thread("2")
        self.assertEqual([post["id"] for post in posts], ["2"])
        self.assertEqual((posts[0]["reply_to"]["handle"], posts[0]["reply_to"]["text"]), ("autre", "Question"))
        self.assertEqual(raw["reply_to"]["rest_id"], "1")
        # The author's own thread answers no one: its first post has no such post.
        first = page_tweet("1", "Premier")
        with patch.object(client, "fetch_post", return_value={**embed, "id_str": "1", "text": "Premier"}), \
                patch.object(client, "read", return_value=page_html(first, first, page_tweet("2", "Suite", reply_to="1")).encode()):
            posts, _ = client.fetch_thread("1")
        self.assertEqual([post["id"] for post in posts], ["1", "2"])
        self.assertNotIn("reply_to", posts[0])
        self.assertEqual(client.missing, set())

    def test_an_unreadable_page_leaves_the_post_alone_and_says_what_may_be_missing(self):
        embed = {"id_str": "2", "text": "Réponse", "user": {"id_str": "10", "name": "Auteur", "screen_name": "auteur"}}
        for answer, missing in ((embed, {"thread"}),
                                ({**embed, "in_reply_to_status_id_str": "1", "in_reply_to_user_id_str": "20"}, {"thread", "reply_to"})):
            client = XClient(SimpleNamespace(stopping=threading.Event()))
            with patch.object(client, "fetch_post", return_value=answer), \
                    patch.object(client, "read", return_value=b"<html>sans donn\xc3\xa9es</html>"):
                posts, raw = client.fetch_thread("2")
            self.assertEqual(([post["id"] for post in posts], raw["source"], client.missing), (["2"], "syndication", missing))
        # A post X no longer knows is deleted: nothing is tried again.
        client = XClient(SimpleNamespace(stopping=threading.Event()))
        with patch.object(client, "fetch_post", side_effect=net.HTTPStatus("absent", 404)), self.assertRaises(Gone):
            client.fetch_thread("2")
        # A long text that X does not complete is said to be cut short, with or without an error.
        client = XClient(SimpleNamespace(stopping=threading.Event()))
        with patch.object(client, "fetch_post", return_value={**embed, "note_tweet": {"id": "abc"}}), \
                patch.object(client, "read", return_value=b"<html></html>"), patch("archiver.integrations.x.note_tweet", return_value=None):
            client.fetch_thread("2")
        self.assertEqual(client.missing, {"thread", "text"})

    def test_availability(self):
        for answer, expected in ((net.HTTPStatus("absent", 404), GONE), (net.Blocked("limite", 429), UNKNOWN),
                                 ({"__typename": "TweetTombstone"}, GONE), ({"__typename": "Tweet", "id_str": "1"}, ONLINE),
                                 ({}, UNKNOWN)):
            failure = isinstance(answer, Exception)
            with self.subTest(expected=expected), patch("archiver.integrations.x.XClient.fetch_post",
                                                        side_effect=answer if failure else None, return_value=None if failure else answer):
                self.assertEqual(online({"id": "x-1"}, None), expected)
