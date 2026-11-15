"""The Bluesky integration, without network access."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from archiver import availability, net
from archiver.integrations import bluesky
from archiver.store import source_url
from tests.fixtures import json_answer


class BlueskyTests(unittest.TestCase):
    DID = "did:plc:alice"

    def view(self, key, text="Bonjour", reply_count=0, embed=None, record_embed=None, did=None, labels=(), facets=None):
        did = did or self.DID
        return {"uri": f"at://{did}/app.bsky.feed.post/{key}", "replyCount": reply_count, "indexedAt": f"2026-01-0{key[-1]}T00:00:00Z",
                "author": {"did": did, "handle": "alice.example", "displayName": "Alice", "labels": [{"val": label} for label in labels]},
                "record": {"$type": "app.bsky.feed.post", "text": text, "createdAt": "2026-01-02T03:04:05.000Z",
                           **({"embed": record_embed} if record_embed else {}), **({"facets": facets} if facets else {})},
                **({"embed": embed} if embed else {})}

    def node(self, view, parent=None, replies=None):
        return {"$type": "app.bsky.feed.defs#threadViewPost", "post": view, **({"parent": parent} if parent else {}),
                **({"replies": replies} if replies is not None else {})}

    def test_post_links(self):
        media_id, url, platform = source_url("bsky.app/profile/Alice.Example/post/3kabc/?ref=share")
        self.assertEqual((url, platform), ("https://bsky.app/profile/Alice.Example/post/3kabc", "Bluesky"))
        self.assertRegex(media_id, r"^bsky-[0-9a-f]{16}$")
        self.assertEqual(source_url("https://bsky.app/profile/alice.example/post/3kabc")[0], media_id)
        self.assertNotEqual(source_url("https://bsky.app/profile/bob.example/post/3kabc")[0], media_id)
        # The usual key names the post by itself: the same archive under the account's handle or its identifier.
        self.assertEqual(source_url("https://bsky.app/profile/alice.example/post/3kexampleaaaa")[0], "bsky-3kexampleaaaa")
        self.assertEqual(source_url(f"https://bsky.app/profile/{self.DID}/post/3kexampleaaaa")[0], "bsky-3kexampleaaaa")
        self.assertEqual(bluesky.address({"source_url": f"https://bsky.app/profile/{self.DID}/post/3kabc"}), (self.DID, "3kabc"))
        for url in ("https://bsky.app/", "https://bsky.app/profile/alice.example", "https://bsky.app/profile/alice.example/feed/aaa",
                    "https://bsky.app/profile/alice%2Fexample/post/3kabc", "https://bsky.app/profile/alice%20example/post/3kabc"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)

    def test_text_keeps_full_links(self):
        text = "Voir exemple.test/un-tr… et ici"
        start = len("Voir ".encode())
        short = {"index": {"byteStart": start, "byteEnd": start + len("exemple.test/un-tr…".encode())},
                 "features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://exemple.test/un-tres-long-lien"}]}
        word = {"index": {"byteStart": len(text.encode()) - 3, "byteEnd": len(text.encode())},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": "https://ailleurs.example/"}]}
        mention = {"index": {"byteStart": 0, "byteEnd": 4}, "features": [{"$type": "app.bsky.richtext.facet#mention", "did": "did:plc:x"}]}
        record = {"text": text, "facets": [word, short, mention],
                  "embed": {"$type": "app.bsky.embed.external", "external": {"uri": "https://carte.example/page"}}}
        self.assertEqual(bluesky.post_text(record),
                         "Voir https://exemple.test/un-tres-long-lien et ici (https://ailleurs.example/)\n\nhttps://carte.example/page")
        self.assertEqual(bluesky.post_text({"text": "https://carte.example/page", "embed": record["embed"]}), "https://carte.example/page")

    def test_media_come_from_the_author_server(self):
        blob = lambda cid: {"$type": "blob", "ref": {"$link": cid}, "mimeType": "image/jpeg"}
        images = {"$type": "app.bsky.embed.images", "images": [
            {"alt": "Un chat", "aspectRatio": {"width": 4, "height": 3}, "image": blob("cid1")}, {"image": {"cid": "old"}}]}
        assets = bluesky.media_assets(images, self.DID, lambda did: "https://pds.example")
        self.assertEqual([(a["type"], a["alt_text"], a["width"]) for a in assets], [("image", "Un chat", 4), ("image", "", None)])
        self.assertEqual(assets[0]["source_urls"], [
            "https://pds.example/xrpc/com.atproto.sync.getBlob?did=did%3Aplc%3Aalice&cid=cid1",
            "https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:alice/cid1@jpeg"])
        self.assertIn("cid=old", assets[1]["source_urls"][0])
        gallery = {"$type": "app.bsky.embed.recordWithMedia", "media": {"$type": "app.bsky.embed.gallery", "items": [{"image": blob("g1")}]}}
        self.assertEqual(len(bluesky.media_assets(gallery, self.DID, lambda did: None)[0]["source_urls"]), 1)
        video = {"$type": "app.bsky.embed.video", "video": blob("v1"), "aspectRatio": {"width": 16, "height": 9}}
        self.assertEqual(bluesky.media_assets(video, self.DID, lambda did: "https://pds.example")[0]["type"], "video")
        with self.assertRaises(RuntimeError):
            bluesky.media_assets(video, self.DID, lambda did: None)
        self.assertEqual(bluesky.media_assets(None, self.DID, lambda did: None), [])

    def test_data_server(self):
        document = {"service": [{"id": "#other", "serviceEndpoint": "https://x.example"},
                                {"id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": "https://pds.example/"}]}
        with patch("archiver.net.fetch", return_value=json_answer(document)) as fetch:
            self.assertEqual(bluesky.data_server("did:plc:alice"), "https://pds.example")
            self.assertEqual(fetch.call_args[0][0], "https://plc.directory/did:plc:alice")
            bluesky.data_server("did:web:alice.example")
            self.assertEqual(fetch.call_args[0][0], "https://alice.example/.well-known/did.json")
        with patch("archiver.net.fetch", side_effect=net.HTTPStatus("absent", 404)):
            self.assertIsNone(bluesky.data_server("did:plc:alice"))
        with patch("archiver.net.fetch", return_value=json_answer({"service": [{"id": "#atproto_pds", "serviceEndpoint": "http://pds.example"}]})):
            self.assertIsNone(bluesky.data_server("did:plc:alice"))
        self.assertIsNone(bluesky.data_server("did:other:alice"))

    def test_keeps_quote_with_the_post_details(self):
        quoted = {"$type": "app.bsky.embed.record#viewRecord", "uri": "at://did:plc:bob/app.bsky.feed.post/3kq",
                  "author": {"did": "did:plc:bob", "handle": "bob.example", "displayName": "Bob"},
                  "value": {"$type": "app.bsky.feed.post", "text": "Cité"}}
        view = self.view("3ka", embed={"$type": "app.bsky.embed.record#view", "record": quoted})
        post = bluesky.post_details(view, lambda did: None)
        self.assertEqual((post["id"], post["url"], post["author"], post["handle"], post["published_at"], post["text"], post["assets"]),
                         ("3ka", "https://bsky.app/profile/did:plc:alice/post/3ka", "Alice", "alice.example",
                          "2026-01-02T03:04:05+00:00", "Bonjour", []))
        self.assertEqual(post["quote"], {"author": "Bob", "handle": "bob.example", "text": "Cité",
                                         "url": "https://bsky.app/profile/did:plc:bob/post/3kq", "published_at": None, "assets": []})
        with_media = self.view("3ka", embed={"$type": "app.bsky.embed.recordWithMedia#view", "record": {"record": quoted}})
        self.assertEqual(bluesky.post_details(with_media, lambda did: None)["quote"]["text"], "Cité")
        for other in ({**quoted, "$type": "app.bsky.embed.record#viewNotFound"},
                      {**quoted, "value": {"$type": "app.bsky.feed.generator"}}):
            view = self.view("3ka", embed={"$type": "app.bsky.embed.record#view", "record": other})
            self.assertIsNone(bluesky.post_details(view, lambda did: None)["quote"])

    def test_keeps_thread_of_the_author(self):
        first, second, third = (self.view(f"3k{n}", reply_count=1) for n in (1, 2, 3))
        fourth = self.view("3k4")
        other = self.view("3k9", did="did:plc:bob")
        side = self.view("3k8")
        # Asked for the second post: its parent, then the earliest self-reply; the next one is beyond the answer's depth.
        focal = self.node(second, parent=self.node(first, parent=self.node(other)),
                          replies=[self.node(other), self.node(side), self.node(third)])
        deeper = self.node(third, replies=[self.node(fourth, replies=[])])
        with patch("archiver.net.fetch", side_effect=[json_answer({"thread": focal}), json_answer({"thread": deeper})]) as fetch:
            views, answered = bluesky.fetch_thread("alice.example", "3k2")
        self.assertEqual([view["uri"].rsplit("/", 1)[1] for view in views], ["3k1", "3k2", "3k3", "3k4"])
        # The thread starts as an answer to someone else: that post is given apart.
        self.assertEqual(answered["uri"], "at://did:plc:bob/app.bsky.feed.post/3k9")
        self.assertIn("uri=at%3A%2F%2Falice.example%2Fapp.bsky.feed.post%2F3k2", fetch.call_args_list[0][0][0])
        self.assertIn("3k3", fetch.call_args_list[1][0][0])
        alone = self.node(self.view("3k1"), replies=[self.node(other)])
        with patch("archiver.net.fetch", return_value=json_answer({"thread": alone})) as fetch:
            self.assertEqual(bluesky.fetch_thread(self.DID, "3k1"), ([alone["post"]], None))
        self.assertEqual(fetch.call_count, 1)

    def test_keeps_reply_to(self):
        answered = self.view("3k9", "Question", did="did:plc:bob")
        kept = []
        worker = SimpleNamespace(archive_posts=lambda item, posts, raw, fetch: kept.extend(posts), fetch_image=None)
        with patch("archiver.integrations.bluesky.fetch_thread", return_value=([self.view("3k1", "Réponse")], answered)):
            bluesky.archive({"source_url": f"https://bsky.app/profile/{self.DID}/post/3k1"}, worker)
        self.assertEqual((kept[0]["reply_to"]["text"], kept[0]["reply_to"]["url"]),
                         ("Question", "https://bsky.app/profile/did:plc:bob/post/3k9"))
        self.assertNotIn("id", kept[0]["reply_to"])
        # A post that answers no one has no such post.
        kept.clear()
        with patch("archiver.integrations.bluesky.fetch_thread", return_value=([self.view("3k1")], None)):
            bluesky.archive({"source_url": f"https://bsky.app/profile/{self.DID}/post/3k1"}, worker)
        self.assertNotIn("reply_to", kept[0])

    def test_unavailable_posts(self):
        with patch("archiver.net.fetch", side_effect=net.HTTPStatus("absent", 400)), self.assertRaises(bluesky.Gone):
            bluesky.fetch_thread(self.DID, "3k1")
        missing = {"thread": {"$type": "app.bsky.feed.defs#notFoundPost", "notFound": True}}
        with patch("archiver.net.fetch", return_value=json_answer(missing)), self.assertRaises(bluesky.Gone):
            bluesky.fetch_thread(self.DID, "3k1")
        blocked = {"thread": {"$type": "app.bsky.feed.defs#blockedPost"}}
        with patch("archiver.net.fetch", return_value=json_answer(blocked)), self.assertRaises(RuntimeError) as raised:
            bluesky.fetch_thread(self.DID, "3k1")
        self.assertNotIsInstance(raised.exception, bluesky.Gone)
        # What the API gives is archived: an author's wish to be shown to signed-in users only is not a refusal.
        asked = {"thread": self.node(self.view("3k1", labels=["!no-unauthenticated"]))}
        with patch("archiver.net.fetch", return_value=json_answer(asked)):
            self.assertEqual(len(bluesky.fetch_thread(self.DID, "3k1")[0]), 1)
        with patch("archiver.net.fetch", side_effect=net.Blocked("limite", 429)), self.assertRaises(RuntimeError) as raised:
            bluesky.fetch_thread(self.DID, "3k1")
        self.assertIn("temporarily limiting", str(raised.exception))

    def test_availability(self):
        item = {"platform": "Bluesky", "id": "bsky-0123456789abcdef", "source_url": f"https://bsky.app/profile/{self.DID}/post/3k1"}
        for answer, expected in (({"posts": [{"uri": "at://…"}]}, availability.ONLINE), ({"posts": []}, availability.GONE),
                                 ({}, availability.UNKNOWN)):
            with self.subTest(expected=expected), patch("archiver.net.fetch", return_value=json_answer(answer)):
                self.assertEqual(availability.check(item, None), expected)
        with patch("archiver.net.fetch", side_effect=net.HTTPStatus("erreur", 500)):
            self.assertEqual(availability.check(item, None), availability.UNKNOWN)
        # A handle may have changed hands: no answer about it says the post is gone.
        with patch("archiver.net.fetch", side_effect=AssertionError("not asked")):
            self.assertEqual(availability.check({**item, "source_url": "https://bsky.app/profile/alice.example/post/3k1"}, None),
                             availability.UNKNOWN)
