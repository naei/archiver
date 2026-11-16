import base64
import http.server
import ipaddress
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import io
import unittest
import unittest.mock
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from archiver import availability, crypto, i18n, integrations, manifest, net, proxy, relay, version
from archiver.integrations import tiktok
from archiver.integrations.youtube import youtube_url
from archiver.downloader import WARNINGS, Downloader, excerpt, subtitle_choices
from archiver import viewer
from archiver.maintenance import Maintenance
from archiver.server import AppServer, byte_range
from archiver.storage import Storage
from archiver.store import Store, clean_tags, now, source_url
from archiver.web import extract, feed_entry, render
from archiver.integrations.x import syndication_post
from tests.fixtures import POST_ID, POST_URL, VIDEO_ID, VIDEO_URL, x_post


PASSWORD = "correct horse battery"


class ValidationTests(unittest.TestCase):
    def test_supported_urls_canonicalize_to_same_video(self):
        for url in (
            VIDEO_URL + "&list=playlist&index=2", f"https://youtu.be/{VIDEO_ID}?t=2",
            f"youtube.com/shorts/{VIDEO_ID}", f"https://m.youtube.com/watch?v={VIDEO_ID}",
            f"https://www.youtube.com/live/{VIDEO_ID}", f"https://youtube.com/embed/{VIDEO_ID}",
        ):
            with self.subTest(url=url):
                self.assertEqual(youtube_url(url), (VIDEO_ID, VIDEO_URL))

    def test_rejects_arbitrary_hosts_and_non_video_urls(self):
        for url in (
            "https://example.com/video", "https://youtube.com.evil.test/watch?v=" + VIDEO_ID,
            "http://127.0.0.1/internal", "file:///etc/passwd", "--exec=bad",
            "https://youtube.com/playlist?list=abc", "https://youtube.com/@example",
            "https://youtube.com/watch?v=../secret", None, 123,
            "https://youtube.com:9000/watch?v=" + VIDEO_ID,
            "https://username@youtube.com/watch?v=" + VIDEO_ID,
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                youtube_url(url)

    def test_x_post_urls_canonicalize_to_same_post(self):
        for url in (
            POST_URL, f"https://twitter.com/example/status/{POST_ID}/photo/2", f"x.com/i/status/{POST_ID}?s=20",
            f"https://mobile.twitter.com/i/web/status/{POST_ID}", f"https://x.com/example/status/{POST_ID}/video/1",
        ):
            with self.subTest(url=url):
                self.assertEqual(source_url(url), (f"x-{POST_ID}", f"https://x.com/i/status/{POST_ID}", "X"))
        for url in ("https://x.com/example", "https://x.com/a/status/abc", f"https://x.com/example/status/{POST_ID}/photo/5"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)

    def test_titles_are_cut_between_words(self):
        self.assertEqual(excerpt("Court"), "Court")
        self.assertEqual(excerpt("Un  texte\nassez long pour être coupé", 20), "Un texte assez long…")
        self.assertEqual(excerpt("x" * 30, 10), "x" * 10 + "…")

    def test_tags_normalize_and_deduplicate(self):
        self.assertEqual(clean_tags(["  Design  ", "design", "un   tag", ""]), ["design", "un tag"])
        # The private tag is stored under one name.
        self.assertEqual(clean_tags(["Privé", "PRIVATE", "prive", "privée"]), ["private", "privée"])
        for tags in ("tag", [123], ["x" * 41], ["a"] * 31):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                clean_tags(tags)

    def test_range_parsing_for_seeking(self):
        self.assertEqual(byte_range(None, 100), (0, 99, False))
        self.assertEqual(byte_range("bytes=10-20", 100), (10, 20, True))
        self.assertEqual(byte_range("bytes=90-", 100), (90, 99, True))
        self.assertEqual(byte_range("bytes=-10", 100), (90, 99, True))
        self.assertEqual(byte_range("bytes=0-1000", 100), (0, 99, True))
        for value in ("bytes=100-", "bytes=9-2", "bytes=-0", "bytes=1-2,4-5", "x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                byte_range(value, 100)


class WebTests(unittest.TestCase):
    def test_web_urls_canonicalize_and_drop_tracking(self):
        media_id, url, platform = source_url("Example.org/a/b?utm_source=x&b=2&a=1&fbclid=z#section")
        self.assertEqual((url, platform), ("https://example.org/a/b?a=1&b=2", "Web"))
        self.assertRegex(media_id, r"^web-[0-9a-f]{16}$")
        self.assertEqual(source_url("https://example.org/a/b?a=1&b=2")[0], media_id)
        self.assertNotEqual(source_url("http://example.org/a/b?a=1&b=2")[0], media_id)
        self.assertEqual(source_url("https://x.com.evil.test/a/status/1")[2], "Web")

    def test_web_urls_reject_internal_hosts_ports_and_unsupported_platforms(self):
        for url in ("http://localhost/", "http://127.0.0.1/", "http://169.254.169.254/latest", "http://[::1]/",
                    "http://printer.local/", "http://intranet/", "https://example.org:8443/", "https://user:pw@example.org/",
                    "ftp://example.org/", "javascript:alert(1)", "https://vm.tiktok.com/x/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_url(url)

    def test_only_public_addresses_are_allowed(self):
        for address in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                        "0.0.0.0", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "224.0.0.1", "64:ff9b::7f00:1"):
            with self.subTest(address=address):
                self.assertFalse(net.allowed(ipaddress.ip_address(address)))
        for address in ("93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"):
            self.assertTrue(net.allowed(ipaddress.ip_address(address)))
        # A host is refused when any of its addresses is internal.
        answers = [(2, 1, 6, "", ("93.184.215.14", 80)), (2, 1, 6, "", ("10.0.0.1", 80))]
        with patch("socket.getaddrinfo", return_value=answers), self.assertRaises(ValueError):
            net.resolve("example.org", 80)

    def test_render_builds_safe_html_from_a_fixed_tag_set(self):
        from lxml import etree
        main = etree.fromstring(
            '<main><head rend="h1">Titre</head><head rend="h3">Partie</head>'
            '<p>Un <hi rend="#b">texte</hi> &lt;script&gt; avec <ref target="javascript:alert(1)">piège</ref> '
            'et <ref target="/page">lien</ref><lb/>suite</p>'
            '<graphic src="img/a.png" alt="A &quot;b&quot;"/><graphic src="https://cdn.test/absente.png"/>'
            '<list rend="ol"><item>un</item></list><code>x &lt; 1</code>'
            '<table><row><cell role="head">H</cell><cell>V</cell></row></table></main>'
        )
        result = render(main, "https://example.org/dossier/", {"https://example.org/dossier/img/a.png": "/api/media/web-1/file/playback/0"}, "Titre")
        self.assertNotIn("<h1", result)
        self.assertIn("<h3>Partie</h3>", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertNotIn("javascript:", result)
        self.assertIn('<a href="https://example.org/page" target="_blank" rel="noopener noreferrer">lien</a>', result)
        self.assertIn('<img src="/api/media/web-1/file/playback/0" alt="A &quot;b&quot;" loading="lazy">', result)
        self.assertNotIn("cdn.test", result)
        self.assertIn("<ol><li>un</li></ol>", result)
        self.assertIn("<pre><code>x &lt; 1</code></pre>", result)
        self.assertIn("<th>H</th><td>V</td>", result)


def rss(link, content=None):
    encoded = f"<content:encoded><![CDATA[{content}]]></content:encoded>" if content else ""
    return (f'<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/"><channel>'
            f'<title>Le Blog</title><item><title>Autre</title><link>https://blog.test/autre</link></item>'
            f'<item><title>Le bon article</title><link>{link}</link><pubDate>Tue, 20 Oct 2026 07:14:23 +0200</pubDate>'
            f'<dc:creator>Camille</dc:creator><description>Résumé court</description>{encoded}'
            f'<media:content url="/une.png" medium="image"/></item></channel></rss>').encode()


class LocaleTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_server_messages_keep_their_variable_parts(self):
        files = sorted((self.ROOT / "archiver" / "locales").glob("*.json"))
        self.assertIn("fr", [path.stem for path in files])
        for path in files:
            for english, translation in json.loads(path.read_text(encoding="utf-8")).items():
                with self.subTest(language=path.stem, message=english):
                    self.assertEqual(set(re.findall(r"\{(\w+)\}", english)), set(re.findall(r"\{(\w+)\}", translation)))
                    self.assertTrue(translation.strip())
        # A part named twice is one text; a language without a file gets English.
        self.assertEqual(i18n.translate("This link looks like a A post, but b.example does not answer like a A server.", "fr"),
                         "Ce lien a la forme d’un post A, mais b.example ne répond pas comme un serveur A.")
        self.assertEqual(i18n.translate("This link looks like a A post, but b.example does not answer like a C server.", "fr"),
                         "This link looks like a A post, but b.example does not answer like a C server.")
        self.assertEqual((i18n.language("fr-FR", None), i18n.language(None, "en-GB,en;q=0.9"), i18n.language("xx", None), i18n.language(None, None)),
                         ("fr", "en", "en", "en"))
        self.assertEqual(i18n.localize({"error": "Page not found.", "items": [{"error": "Page not found."}]}, "fr"),
                         {"error": "Page introuvable.", "items": [{"error": "Page introuvable."}]})
        self.assertEqual(i18n.localize({"error": "Page not found."}, "en"), {"error": "Page not found."})

    def test_interface_languages_are_complete(self):
        languages = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in (self.ROOT / "web" / "locales").glob("*.json")}
        self.assertLessEqual({"en", "fr"}, set(languages))
        for code, texts in languages.items():
            with self.subTest(language=code):
                self.assertEqual(set(texts), set(languages["en"]))
                self.assertTrue(texts["language.name"] and texts["language.locale"])
                self.assertEqual(len(texts["size.units"].split(",")), 5)
                for key, text in texts.items():
                    self.assertEqual(set(re.findall(r"\{(\w+)\}", text)), set(re.findall(r"\{(\w+)\}", languages["en"][key])), key)
        # Every text the pages ask for exists.
        used = set()
        for name in ("render.js", "app.js", "viewer.js", "login.js", "index.html", "viewer.html", "login.html"):
            used.update(re.findall(r"""(?:\bt\(|data-i18n(?:-[a-z]+)?=)["']([a-zA-Z0-9.]+)["']""", (self.ROOT / "web" / name).read_text(encoding="utf-8")))
        self.assertEqual(used - set(languages["en"]), set())
        # So does the text of everything an archive may lack.
        self.assertLessEqual({f"warning.{kind}" for kind in WARNINGS}, set(languages["en"]))
        # What browsers get: the languages in the script's table, English first, and nothing that would close an inline script.
        script = i18n.interface_script()
        self.assertIn('const TRANSLATIONS = {"en":{"language.name":"English"', script)
        self.assertNotIn("/*__LOCALES__*/", script)
        self.assertNotIn("</", script[:script.index("\n\nfunction detectLanguage")])


class SubtitleTests(unittest.TestCase):
    VTT = "WEBVTT\nKind: captions\nLanguage: fr\n\nNOTE une note\n\n1\n00:00:01.000 --> 00:00:02.000 align:start\nBonjour<00:00:01.500><c> à tous</c>\n\n2\n00:00:02.000 --> 00:00:03.000\nBonjour à tous\n\n00:00:03.000 --> 00:00:04.000\nun mot rare\n"

    def test_tracks_kept(self):
        translations = {language: [{"name": language}] for language in ("en", "de", "es", "it")}
        # The author's tracks, the video's language first; no automatic track when one of them is in that language.
        info = {"language": "fr-FR", "subtitles": {"en": [{"name": "English"}], "fr": [{"name": "Français"}], "live_chat": [{}]},
                "automatic_captions": {"fr-orig": [{}], "fr": [{}], **translations}}
        self.assertEqual(subtitle_choices(info), [("fr", "fr", "Français", False), ("en", "en", "English", False)])
        # None in the video's language: the transcript of what is said, and never its automatic translations.
        info = {"language": "fr", "subtitles": {"en": [{"name": "English"}]},
                "automatic_captions": {"fr-orig": [{"name": "French (Original)"}], "fr": [{}], **translations}}
        self.assertEqual(subtitle_choices(info), [("fr-orig", "fr", "French (Original)", True), ("en", "en", "English", False)])
        self.assertEqual(subtitle_choices({"language": "ja", "automatic_captions": {"ja": [{}], "en": [{}]}}), [("ja", "ja", None, True)])
        self.assertEqual(subtitle_choices({"automatic_captions": {"de": [{}], "es-orig": [{}]}}), [("es-orig", "es", None, True)])
        # A video that declares another language than the one spoken: the transcript tells, not the declaration.
        info = {"language": "en-US", "subtitles": {"fr": [{"name": "Français"}], "en": [{"name": "English"}]},
                "automatic_captions": {"fr-orig": [{}], "fr": [{}], "en": [{}]}}
        self.assertEqual(subtitle_choices(info), [("fr", "fr", "Français", False), ("en", "en", "English", False)])
        self.assertEqual(subtitle_choices({"language": "en-US", "automatic_captions": {"fr-orig": [{}], "fr": [{}], "en": [{}]}}),
                         [("fr-orig", "fr", None, True)])
        # A track named with a mark of the platform's own keeps its file's name, and gets a language browsers know.
        self.assertEqual(subtitle_choices({"subtitles": {"en-nP7-2PuUl7o": [{}], "pt-BR": [{}], "zh-Hans": [{}]}}),
                         [("en-nP7-2PuUl7o", "en", None, False), ("pt-BR", "pt-BR", None, False), ("zh-Hans", "zh-Hans", None, False)])
        # Without knowing the language, a translation cannot be told from the transcript.
        self.assertEqual(subtitle_choices({"automatic_captions": {"de": [{}], "es": [{}]}}), [])
        self.assertEqual(subtitle_choices({"subtitles": None, "automatic_captions": "?"}), [])
        many = {"subtitles": {f"l{number:02d}": [{}] for number in range(30)} | {"../x": [{}]}}
        self.assertEqual(len(subtitle_choices(many)), 12)

    def archive(self, temp, subtitles):
        store = Store(Path(temp) / "data")
        worker = Downloader(store, Storage(store.root))
        item, _ = store.add(VIDEO_URL, [])
        directory = worker.storage.directory(item["id"])
        commands = []

        def run(command, on_line=None, keep=12):
            if command[0] in ("ffmpeg", "ffprobe"):
                return subprocess.run(command, capture_output=True, text=True, check=True).stdout
            commands.append(command)
            if "--load-info-json" in command:
                return subtitles(directory)
            video = directory / "original.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48:d=1",
                            "-pix_fmt", "yuv420p", str(video)], check=True)
            (directory / "original.info.json").write_text(json.dumps({
                "title": "Une vidéo", "language": "fr", "subtitles": {"fr": [{"name": "Français"}], "en": [{"name": "English"}]}}))
            on_line(f"archiver-file:{video}")
            return ""

        with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}), \
                patch("archiver.downloader.dependencies", return_value={}), patch.object(worker, "run", side_effect=run):
            worker.handle(item["id"])
        return store, worker, store.get(item["id"]), commands

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_subtitles_are_kept_with_the_video(self):
        def subtitles(directory):
            (directory / "original.fr.vtt").write_text(self.VTT, encoding="utf-8")
            (directory / "original.en.vtt").write_text("", encoding="utf-8")  # an empty answer is not a track
            return ""

        with tempfile.TemporaryDirectory() as temp:
            store, worker, item, commands = self.archive(temp, subtitles)
            self.assertEqual((item["status"], item["error"]), ("ready", None))
            self.assertEqual(item["details"], {"subtitles": [{"file": "original.fr.vtt", "language": "fr", "name": "Français", "automatic": False}]})
            self.assertIn("original.fr.vtt", item["files"])
            self.assertNotIn("original.en.vtt", item["files"])
            # Read from what the first run saved, with the same options, and without downloading the video again.
            first, second = commands
            self.assertEqual(second[second.index("--sub-langs") + 1], "fr,en")
            self.assertIn("--skip-download", second)
            self.assertIn("--impersonate", second)
            self.assertNotIn("--write-subs", first)
            record = json.loads((worker.storage.directory(item["id"]) / "archive.json").read_text())
            self.assertEqual(record["details"]["subtitles"][0]["file"], "original.fr.vtt")
            # What is said, once, without timings, tags or notes.
            self.assertEqual(viewer.subtitle_text(item, worker.storage), "Bonjour à tous un mot rare")
            self.assertEqual(viewer.records([item], worker.storage)[0]["subtitle_text"], "Bonjour à tous un mot rare")
            self.assertIsNone(viewer.subtitle_text({**item, "details": {}}, worker.storage))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_video_is_archived_when_subtitles_fail(self):
        def refused(directory):
            raise RuntimeError("ERROR: Unable to download video subtitles for 'fr': HTTP Error 429: Too Many Requests")

        with tempfile.TemporaryDirectory() as temp:
            store, worker, item, commands = self.archive(temp, refused)
            # The video is kept, and shown as incomplete.
            self.assertEqual((item["status"], item["error"], item["details"]), ("ready", None, {"warnings": ["subtitles"]}))
            self.assertEqual(len(commands), 2)
            self.assertNotIn("subtitle_text", viewer.records([item], worker.storage)[0])


class FeedTests(unittest.TestCase):
    def test_entry_is_found_by_address_with_metadata(self):
        entry = feed_entry(rss("https://www.blog.test/article/?utm_source=rss", "<p>Texte complet</p>"), "https://blog.test/article")
        self.assertEqual(entry["title"], "Le bon article")
        self.assertEqual((entry["author"], entry["date"], entry["site"]), ("Camille", "2026-10-20", "Le Blog"))
        self.assertEqual((entry["content"], entry["complete"]), ("<p>Texte complet</p>", True))
        self.assertEqual(entry["cover"], "https://blog.test/une.png")
        summary_only = feed_entry(rss("https://blog.test/article"), "https://blog.test/article")
        self.assertEqual((summary_only["content"], summary_only["complete"]), ("Résumé court", False))
        self.assertIsNone(feed_entry(rss("https://blog.test/article"), "https://blog.test/ailleurs"))
        self.assertIsNone(feed_entry(b"<html>pas un flux", "https://blog.test/article"))

    def test_atom_entries_and_no_external_entities(self):
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><entry><title>A</title>'
                '<link href="https://blog.test/a"/><updated>2026-10-01T10:00:00Z</updated>'
                '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Corps</p></div></content></entry></feed>').encode()
        entry = feed_entry(atom, "https://blog.test/a")
        self.assertIn("Corps", entry["content"])
        self.assertEqual(entry["date"], "2026-10-01")
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as secret:
            secret.write("SECRET"); secret.flush()
            hostile = (f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret.name}">]>'
                       f'<rss><channel><item><link>https://blog.test/a</link><description>&x;</description></item></channel></rss>').encode()
            entry = feed_entry(hostile, "https://blog.test/a")
            self.assertNotIn("SECRET", (entry or {}).get("content", ""))


class LocalSite:
    """A local HTTP server; tests allow loopback addresses only, the rest stays forbidden."""

    def __init__(self, routes):
        routes_ = routes

        self.languages = []
        languages = self.languages

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                languages.append(self.headers.get("Accept-Language"))
                status, headers, body = routes_.get(self.path, (404, {}, b""))
                body = body.replace(b"{port}", str(self.server.server_address[1]).encode())
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value.replace("{port}", str(self.server.server_address[1])))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # Loopback only: the redirect test checks that other internal addresses stay refused.
        self.patch = patch("archiver.net.allowed", lambda address: address.is_loopback)
        self.patch.start()
        self.ports = patch("archiver.net.PORTS", (None, 80, 443, self.server.server_address[1]))
        self.ports.start()

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def close(self):
        self.patch.stop()
        self.ports.stop()
        self.server.shutdown()
        self.server.server_close()


ARTICLE = ("<html><head><title>Un article de test — Le Site</title><meta property='og:site_name' content='Le Site'>"
           "<meta name='author' content='Camille Martin'></head><body><nav>Menu Accueil Contact</nav><article>"
           "<h1>Un article de test</h1>" + "".join(
               f"<p>Paragraphe {n} : un contenu principal assez long pour être reconnu comme le corps de l’article, "
               f"avec des phrases complètes et des détails utiles à conserver dans l’archive personnelle.</p>" for n in range(8))
           + "<img src='/image.png' alt='Une image'><img src='/cassee.png' alt='Absente'>"
           + "</article><footer>Mentions légales</footer></body></html>").encode()


class WebFetchTests(unittest.TestCase):
    def setUp(self):
        self.site = LocalSite({
            "/page": (200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>ok</html>"),
            "/redirection": (302, {"Location": "http://10.0.0.1/admin"}, b""),
            "/document.pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF"),
            "/gros": (200, {"Content-Type": "text/html"}, b"x" * 2048),
        })
        self.addCleanup(self.site.close)

    def test_fetch_follows_checks_and_limits(self):
        response = net.fetch(self.site.url("/page"), "text/html", ("text/html",), 1024)
        self.assertEqual((response.content_type, response.body), ("text/html", b"<html>ok</html>"))
        with self.assertRaises(ValueError):
            net.fetch(self.site.url("/redirection"), "text/html", ("text/html",), 1024)
        with self.assertRaises(ValueError):
            net.fetch(self.site.url("/document.pdf"), "text/html", ("text/html",), 1024)
        with self.assertRaises(ValueError):
            net.fetch(self.site.url("/gros"), "text/html", ("text/html",), 1024)
        with self.assertRaises(RuntimeError):
            net.fetch(self.site.url("/absente"), "text/html", ("text/html",), 1024)

    def test_sites_are_asked_for_the_languages_of_the_reader(self):
        net.fetch(self.site.url("/page"), "text/html", ("text/html",), 1024)
        with net.reading("de-DE,de;q=0.9,en;q=0.8"):
            net.fetch(self.site.url("/page"), "text/html", ("text/html",), 1024)
        # A header that is missing or could carry another one falls back to English.
        for preferred in (None, "", "de\r\nCookie: a=b", "x" * 201):
            with net.reading(preferred):
                net.fetch(self.site.url("/page"), "text/html", ("text/html",), 1024)
        self.assertEqual(self.site.languages, ["en", "de-DE,de;q=0.9,en;q=0.8", "en", "en", "en", "en"])


class LocalProxy:
    """A local HTTP proxy that tunnels CONNECT requests, with optional credentials; records what it was asked."""

    def __init__(self, username="", password=""):
        import socket
        import socketserver
        expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode() if username else None
        self.targets = []
        targets = self.targets

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                lines = []
                while (line := self.rfile.readline().decode().strip()):
                    lines.append(line)
                method, target, _ = lines[0].split(" ", 2)
                headers = dict(line.split(": ", 1) for line in lines[1:])
                targets.append(target)
                if expected and headers.get("Proxy-Authorization") != expected:
                    self.wfile.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                    return
                host, _, port = target.rpartition(":")
                upstream = socket.create_connection((host, int(port)))
                self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self.wfile.flush()

                def pipe(source, destination):
                    try:
                        while data := source.recv(65536):
                            destination.sendall(data)
                    except OSError:
                        pass
                    finally:
                        destination.close()
                threading.Thread(target=pipe, args=(upstream, self.connection), daemon=True).start()
                pipe(self.connection, upstream)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ProxyTests(unittest.TestCase):
    def test_accepted_forms(self):
        self.assertEqual(proxy.parse(""), [])
        self.assertEqual(proxy.parse("proxy.test:8080"), [proxy.Proxy("proxy.test", 8080)])
        pool = proxy.parse('"isp.example.com:10001-10003:user-fr:p@ss:word"')  # quotes as some env files keep them
        self.assertEqual([p.port for p in pool], [10001, 10002, 10003])
        self.assertEqual((pool[0].username, pool[0].password), ("user-fr", "p@ss:word"))
        self.assertEqual(pool[0].url, "http://user-fr:p%40ss%3Aword@isp.example.com:10001")
        self.assertEqual(proxy.parse("http://us%3Aer:pa%40ss@proxy.test:3128/, other.test:80"),
                         [proxy.Proxy("proxy.test", 3128, "us:er", "pa@ss"), proxy.Proxy("other.test", 80)])
        self.assertEqual(len(proxy.parse("http://u:p@proxy.test:7000-7004")), 5)
        for invalid in ("proxy.test", "proxy.test:port", "proxy.test:9-1", "proxy.test:80:user", "socks5://proxy.test:1080", ":80"):
            with self.subTest(invalid), self.assertRaises(ValueError):
                proxy.parse(invalid)

    def test_each_job_takes_the_next_address_and_keeps_it(self):
        with patch.dict(os.environ, {"PROXY": "proxy.test:1-3"}):
            seen = []
            for _ in range(4):
                with proxy.chosen():
                    seen.append(proxy.current().port)
                    self.assertEqual(proxy.current().port, seen[-1])
            self.assertEqual(len(set(seen[:3])), 3)
            self.assertEqual(seen[3], seen[0])
            self.assertEqual(proxy.describe(), "proxy.test (3 addresses)")
        with patch.dict(os.environ, {"PROXY": ""}), proxy.chosen():
            self.assertIsNone(proxy.current())
            self.assertIsNone(proxy.describe())
        with patch.dict(os.environ, {"PROXY": "proxy.test:1:user:s3cr/et"}):
            self.assertEqual(proxy.redact("http://user:s3cr%2Fet@proxy.test:1 s3cr/et"), "http://user:****@proxy.test:1 ****")

    def test_fetch_goes_through_the_proxy_to_the_checked_address(self):
        site = LocalSite({"/page": (200, {"Content-Type": "text/html"}, b"<html>ok</html>")})
        self.addCleanup(site.close)
        local = LocalProxy("user", "pass:word")
        self.addCleanup(local.close)
        site_port = site.server.server_address[1]
        url = f"http://localhost:{site_port}/page"
        with patch.dict(os.environ, {"PROXY": f"127.0.0.1:{local.port}:user:pass:word"}), proxy.chosen():
            self.assertEqual(net.fetch(url, "text/html", ("text/html",), 1024).body, b"<html>ok</html>")
        # The tunnel goes to the address resolved and checked by Archiver, not to a name the proxy would resolve.
        self.assertEqual(local.targets, [f"127.0.0.1:{site_port}"])
        with patch.dict(os.environ, {"PROXY": f"127.0.0.1:{local.port}:user:wrong"}), proxy.chosen():
            with self.assertRaises(proxy.Failure) as raised:
                net.fetch(url, "text/html", ("text/html",), 1024)
        self.assertEqual(str(raised.exception), "The proxy refused its credentials (HTTP 407). Check the PROXY setting.")
        # Internal addresses stay refused before anything reaches the proxy.
        with patch.dict(os.environ, {"PROXY": f"127.0.0.1:{local.port}:user:pass:word"}), proxy.chosen(), \
                self.assertRaises(ValueError):
            net.fetch("http://10.0.0.1/", "text/html", ("text/html",), 1024)
        self.assertEqual(len(local.targets), 2)

    def test_unreachable_proxy_is_named(self):
        with patch.dict(os.environ, {"PROXY": "127.0.0.1:9"}), proxy.chosen(), \
                patch("archiver.net.resolve", return_value="93.184.215.14"), self.assertRaises(proxy.Failure) as raised:
            net.fetch("https://example.org/", "text/html", ("text/html",), 1024)
        self.assertEqual(str(raised.exception), "The proxy 127.0.0.1 cannot be reached. Check the PROXY setting.")

    def test_video_download_uses_the_job_proxy(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            store.add(VIDEO_URL, [])
            worker = Downloader(store, Storage(store.root))
            commands = []

            def run(command, on_line=None):
                commands.append(command)
                raise RuntimeError("ERROR: proxy http://user:secret@proxy.test:8000 failed")
            with patch.dict(os.environ, {"PROXY": "proxy.test:8000:user:secret"}), \
                    patch("archiver.downloader.dependencies", return_value={}), \
                    patch.object(worker, "run", side_effect=run):
                worker.handle(VIDEO_ID)
            command = commands[0]
            self.assertEqual(command[command.index("--proxy") + 1], "http://user:secret@proxy.test:8000")
            # The error says which way the job went; the password never reaches the catalog or the logs.
            error = store.get(VIDEO_ID)["error"]
            self.assertEqual(error, "Tried through the proxy proxy.test:8000: ERROR: proxy http://user:****@proxy.test:8000 failed")
            self.assertEqual(i18n.translate(error, "fr"), "Essai via le proxy proxy.test:8000 : ERROR: proxy http://user:****@proxy.test:8000 failed")

    def test_fallback_mode_tries_directly_then_through_the_proxy(self):
        with patch.dict(os.environ, {"PROXY": "proxy.test:8000", "PROXY_MODE": "fallback"}):
            self.assertEqual(proxy.routes(), [False, True])
            self.assertTrue(proxy.describe().endswith("after a direct attempt fails"))
        with patch.dict(os.environ, {"PROXY": "proxy.test:8000", "PROXY_MODE": ""}):
            self.assertEqual(proxy.routes(), [True])
        with patch.dict(os.environ, {"PROXY": "", "PROXY_MODE": "fallback"}):
            self.assertEqual(proxy.routes(), [False])
        with patch.dict(os.environ, {"PROXY": "proxy.test:8000", "PROXY_MODE": "sometimes"}), self.assertRaises(ValueError):
            proxy.routes()

        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            store.add(VIDEO_URL, [])
            worker = Downloader(store, Storage(store.root))
            seen = []

            def download(media_id):
                seen.append(proxy.current())
                if proxy.current() is None:
                    raise RuntimeError("ERROR: HTTP Error 429: Too Many Requests")
                store.update(media_id, status="ready")
            with patch.dict(os.environ, {"PROXY": "proxy.test:8000", "PROXY_MODE": "fallback"}), \
                    patch.object(worker, "download", side_effect=download):
                worker.handle(VIDEO_ID)
            self.assertEqual(seen, [None, proxy.Proxy("proxy.test", 8000)])
            self.assertEqual(store.get(VIDEO_ID)["status"], "ready")

            # What another address would not change is not retried through the proxy.
            store.update(VIDEO_ID, status="queued")
            seen.clear()

            def gone(media_id):
                seen.append(proxy.current())
                raise tiktok.Gone(tiktok.DELETED)
            with patch.dict(os.environ, {"PROXY": "proxy.test:8000", "PROXY_MODE": "fallback"}), \
                    patch.object(worker, "download", side_effect=gone):
                worker.handle(VIDEO_ID)
            self.assertEqual(seen, [None])
            self.assertEqual(store.get(VIDEO_ID)["error"], "Tried directly, without the proxy: " + tiktok.DELETED)

            store.update(VIDEO_ID, status="queued")
            with patch.dict(os.environ, {"PROXY": "proxy.test:8000", "PROXY_MODE": "fallback"}), \
                    patch.object(worker, "download", side_effect=RuntimeError("ERROR: HTTP Error 429: Too Many Requests")):
                worker.handle(VIDEO_ID)
            error = store.get(VIDEO_ID)["error"]
            self.assertEqual(error, "Tried directly, then through the proxy proxy.test:8000: ERROR: HTTP Error 429: Too Many Requests")
            self.assertEqual(i18n.translate(error, "fr"), "Essai direct, puis via le proxy proxy.test:8000 : ERROR: HTTP Error 429: Too Many Requests")

    def test_fallback_mode_checks_again_through_the_proxy_when_unsure(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            item, _ = store.add(VIDEO_URL, [])
            store.update(VIDEO_ID, status="ready")
            maintenance = Maintenance(store, Storage(store.root))
            seen = []

            def check(item, runner):
                seen.append(proxy.current())
                return availability.UNKNOWN if proxy.current() is None else availability.ONLINE
            with patch.dict(os.environ, {"PROXY": "proxy.test:8000", "PROXY_MODE": "fallback"}), \
                    patch("archiver.availability.check", side_effect=check):
                maintenance.check(store.get(VIDEO_ID))
            self.assertEqual(seen, [None, proxy.Proxy("proxy.test", 8000)])
            self.assertEqual(store.get(VIDEO_ID)["availability"], "online")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class WebArchiveTests(unittest.TestCase):
    def test_article_is_archived_with_local_images(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            image = Path(temp) / "image.png"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48",
                            "-frames:v", "1", str(image)], check=True)
            site = LocalSite({
                "/article": (200, {"Content-Type": "text/html; charset=utf-8"}, ARTICLE),
                "/image.png": (200, {"Content-Type": "image/png"}, image.read_bytes()),
            })
            self.addCleanup(site.close)
            store = Store(Path(temp) / "data")
            worker = Downloader(store, Storage(store.root))
            item = {"id": "web-0123456789abcdef", "source_url": site.url("/article"), "platform": "Web"}
            with closing(store.connect()) as db, db:  # a localhost URL cannot be added through the API
                db.execute("INSERT INTO media (id, source_url, platform, created_at) VALUES (?, ?, 'Web', '2026-01-01')",
                           (item["id"], item["source_url"]))
            worker.download_web(item)
            archived = store.get(item["id"])
            self.assertEqual(archived["status"], "ready")
            self.assertEqual(archived["title"], "Un article de test")
            self.assertEqual(archived["author"], "Camille Martin")
            self.assertEqual(archived["details"]["site"], "Le Site")
            self.assertEqual(len(archived["attachments"]), 1)  # the broken image is skipped
            self.assertEqual((archived["attachments"][0]["width"], archived["attachments"][0]["height"]), (64, 48))
            body = (worker.storage.directory(item["id"]) / "article.html").read_text()
            self.assertIn(f'<img src="/api/media/{item["id"]}/file/playback/0" alt="Une image"', body)
            self.assertNotIn("Menu Accueil", body)
            self.assertNotIn("Mentions légales", body)
            self.assertIn("Paragraphe 7", archived["description"])
            self.assertEqual(archived["details"]["via"], "page")
            directory = worker.storage.directory(item["id"])
            record = json.loads((directory / "archive.json").read_text())
            self.assertEqual((record["format"], record["title"], record["platform"]), ("archiver/1", "Un article de test", "Web"))
            self.assertEqual(record["files"], manifest.file_records(archived["files"]))
            self.assertEqual(archived["files"]["article.html"], manifest.hash_file(directory / "article.html"))
            self.assertEqual(archived["integrity"], "ok")

    def test_editing_links_navigation_boxes_and_in_page_links_are_left_out(self):
        paragraphs = "".join(f"<p>Paragraphe {n} : un contenu principal assez long pour être reconnu comme le corps de "
                             f"l’article, avec des phrases complètes et des détails utiles.</p>" for n in range(8))
        page = ("<html><head><title>Un sujet</title></head><body><article><h1>Un sujet</h1>"
                "<h2>Histoire<span class='mw-editsection'>[<a href='/w?action=edit'>modifier</a>]</span></h2>"
                f"<p>Une phrase avec une note<sup><a href='#cite_note-1'>[1]</a></sup> et <a href='/wiki/Autre'>un lien</a>.</p>{paragraphs}"
                "<ol><li><span class='mw-cite-backlink'><a href='#cite_ref-1'>↑</a></span> La source de la note.</li></ol>"
                "<div class='navbox authority-control' role='navigation'>Notices d’autorité : BnF, LCCN</div>"
                "</article></body></html>").encode()
        article = extract(page, "https://wiki.test/wiki/Un_sujet")
        body = render(article["main"], "https://wiki.test/wiki/Un_sujet", {}, article["title"])
        self.assertNotIn("modifier", body)
        self.assertNotIn("Notices d’autorité", body)
        self.assertNotIn("#cite", body)
        self.assertNotIn("↑", body)
        self.assertIn("[1]", body)
        self.assertIn("La source de la note.", body)
        self.assertIn('<a href="https://wiki.test/wiki/Autre"', body)
        self.assertIsNone(article["author"])

    def test_blocked_page_is_archived_from_the_site_feed(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            content = "".join(f"<p>Paragraphe {n} du flux, avec suffisamment de texte pour former un article complet.</p>" for n in range(5))
            site = LocalSite({
                "/article": (403, {"Content-Type": "text/html", "cf-mitigated": "challenge"}, b"Just a moment..."),
                "/feed": (200, {"Content-Type": "application/rss+xml"}, rss("http://127.0.0.1:{port}/article?utm_source=rss", content)),
            })
            self.addCleanup(site.close)
            store = Store(Path(temp) / "data")
            worker = Downloader(store, Storage(store.root))
            item = {"id": "web-00000000000000aa", "source_url": site.url("/article"), "platform": "Web"}
            with closing(store.connect()) as db, db:
                db.execute("INSERT INTO media (id, source_url, platform, created_at) VALUES (?, ?, 'Web', '2026-01-01')",
                           (item["id"], item["source_url"]))
            worker.download_web(item)
            archived = store.get(item["id"])
            self.assertEqual(archived["status"], "ready")
            self.assertEqual((archived["title"], archived["author"], archived["published_at"]), ("Le bon article", "Camille", "2026-10-20"))
            self.assertEqual((archived["details"]["via"], archived["details"]["excerpt"]), ("feed", False))
            self.assertIn("Paragraphe 4 du flux", archived["description"])

    def archive_link(self, temp, path, headers, body):
        site = LocalSite({path: (200, headers, body)})
        self.addCleanup(site.close)
        store = Store(Path(temp) / "data")
        worker = Downloader(store, Storage(store.root))
        item = {"id": "web-00000000000000bb", "source_url": site.url(path), "platform": "Web"}
        with closing(store.connect()) as db, db:
            db.execute("INSERT INTO media (id, source_url, platform, created_at) VALUES (?, ?, 'Web', '2026-01-01')",
                       (item["id"], item["source_url"]))
        worker.download_web(item)
        return store.get(item["id"]), worker.storage.directory(item["id"])

    def test_direct_video_link_is_archived_as_a_video(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            video = Path(temp) / "clip.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=64x48:d=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
            archived, directory = self.archive_link(temp, "/files/clip%20de%20test.mp4", {"Content-Type": "video/mp4"},
                                                    video.read_bytes())
            self.assertEqual(archived["status"], "ready")
            self.assertEqual((archived["title"], archived["details"]["kind"]), ("clip de test.mp4", "file"))
            medium = archived["attachments"][0]
            self.assertEqual((medium["type"], medium["width"], medium["height"]), ("video", 64, 48))
            self.assertEqual((archived["original"], archived["playback"]), ("original.mp4", "original.mp4"))
            self.assertTrue((directory / "thumbnail.jpg").exists())
            self.assertFalse((directory / "article.html").exists())
            record = json.loads((directory / "archive.json").read_text())
            self.assertEqual(Store.record_values(record, "local", 0)["original"], "original.mp4")

    def test_direct_gif_without_a_precise_type_is_archived_from_its_extension(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            gif = Path(temp) / "anim.gif"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=32x24:d=0.5:r=4",
                            str(gif)], check=True)
            archived, _ = self.archive_link(temp, "/anim.gif", {"Content-Type": "application/octet-stream",
                                                                "Content-Disposition": 'attachment; filename="Chat.gif"'},
                                            gif.read_bytes())
            self.assertEqual((archived["status"], archived["title"]), ("ready", "Chat.gif"))
            self.assertEqual((archived["attachments"][0]["type"], archived["original"]), ("image", "original.gif"))

    def test_direct_sound_link_is_archived_with_a_playback_copy_when_needed(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            sound = Path(temp) / "son.ogg"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", "flac",
                            str(sound)], check=True)
            archived, directory = self.archive_link(temp, "/son.ogg", {"Content-Type": "audio/ogg"}, sound.read_bytes())
            medium = archived["attachments"][0]
            self.assertEqual((archived["status"], medium["type"], medium["thumbnail"]), ("ready", "audio", None))
            self.assertEqual((archived["original"], archived["playback"], archived["thumbnail"]),
                             ("original.ogg", "playback.m4a", None))
            self.assertAlmostEqual(archived["duration"], 1, delta=0.2)
            self.assertFalse((directory / "thumbnail.jpg").exists())
            self.assertEqual(set(archived["files"]), {"original.ogg", "playback.m4a", "original.info.json"})

    def test_direct_file_takes_its_title_author_and_date_from_the_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            sound = Path(temp) / "son.flac"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", "-metadata", "title=Un  titre",
                            "-metadata", "artist=Camille", "-metadata", "date=2021-03-04", str(sound)], check=True)
            archived, _ = self.archive_link(temp, "/a1b2c3.flac", {"Content-Type": "audio/flac"}, sound.read_bytes())
            self.assertEqual((archived["title"], archived["author"], archived["published_at"]), ("Un titre", "Camille", "2021-03-04"))

    def test_direct_file_without_metadata_takes_the_date_the_server_gives(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            image = Path(temp) / "image.png"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48",
                            "-frames:v", "1", str(image)], check=True)
            archived, _ = self.archive_link(temp, "/photo.png", {"Content-Type": "image/png",
                                                                 "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"}, image.read_bytes())
            self.assertEqual((archived["title"], archived["author"], archived["published_at"]), ("photo.png", None, "2015-10-21"))

    def test_direct_pdf_link_is_archived(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            archived, directory = self.archive_link(temp, "/doc.pdf", {"Content-Type": "application/pdf"},
                                                    b"%PDF-1.4\n%%EOF\n")
            medium = archived["attachments"][0]
            self.assertEqual((archived["status"], archived["title"], medium["type"]), ("ready", "doc.pdf", "pdf"))
            self.assertEqual((archived["original"], archived["playback"]), ("original.pdf", "original.pdf"))
            self.assertEqual(medium["thumbnail"] is not None, (directory / "thumbnail.jpg").exists())

    def test_a_page_passed_off_as_a_pdf_is_refused(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            with self.assertRaisesRegex(RuntimeError, "PDF"):
                self.archive_link(temp, "/doc.pdf", {"Content-Type": "application/pdf"}, b"<html>connexion</html>")

    def test_other_file_types_are_still_refused(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            with self.assertRaisesRegex(ValueError, "application/zip"):
                self.archive_link(temp, "/doc.zip", {"Content-Type": "application/zip"}, b"PK")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class PostArchiveTests(unittest.TestCase):
    def test_quoted_media_are_archived_with_the_quote(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            image = Path(temp) / "image.png"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x48",
                            "-frames:v", "1", str(image)], check=True)
            store = Store(Path(temp) / "data")
            worker = Downloader(store, Storage(store.root))
            item, _ = store.add(POST_URL, [])
            photo = lambda name: {"type": "image", "source_url": name, "extension": "png", "width": None, "height": None,
                                  "alt_text": "", "duration": None}
            quote = {"author": "Autre", "handle": "autre", "text": "Post cité", "url": "https://x.com/autre/status/5",
                     "assets": [photo("cité")]}
            posts = [
                {"id": POST_ID, "url": POST_URL, "author": "Example", "handle": "example", "published_at": None,
                 "text": "Sans média", "quote": quote, "assets": [], "title": "Un titre à part",
                 "reply_to": {"author": "Tiers", "handle": None, "text": "Question", "url": "https://x.com/tiers/status/1",
                              "published_at": None, "assets": [photo("répondu")]}},
                {"id": "2", "url": "https://x.com/example/status/2", "author": "Example", "handle": "example",
                 "published_at": None, "text": "Suite", "quote": None, "assets": [photo("propre")]},
            ]
            fetched = []

            def fetch(asset, path, progress):
                fetched.append(asset.pop("source_url"))
                path = path.with_suffix("." + asset.pop("extension"))
                shutil.copy(image, path)
                return path

            worker.archive_posts(item, posts, {}, fetch)
            archived = store.get(item["id"])
            self.assertEqual(archived["status"], "ready")
            self.assertEqual(fetched, ["répondu", "cité", "propre"])
            self.assertEqual([(a["post"], a.get("replied", False), a.get("quoted", False)) for a in archived["attachments"]],
                             [(0, True, False), (0, False, True), (1, False, False)])
            self.assertEqual([post["media"] for post in archived["posts"]], [[], [2]])
            self.assertEqual(archived["posts"][0]["quote"]["media"], [1])
            # The post answered is kept the same way, above the post; a post's own title is the archive's.
            self.assertEqual((archived["posts"][0]["reply_to"]["text"], archived["posts"][0]["reply_to"]["media"]), ("Question", [0]))
            self.assertNotIn("assets", archived["posts"][0]["reply_to"])
            self.assertNotIn("reply_to", archived["posts"][1])
            self.assertEqual(archived["title"], "Un titre à part")
            self.assertNotIn("assets", archived["posts"][0]["quote"])
            self.assertIsNone(archived["posts"][1]["quote"])
            # The cover is the thread's own photo, not the quoted one.
            self.assertEqual(archived["original"], archived["attachments"][2]["original"])
            record = json.loads((worker.storage.directory(item["id"]) / "archive.json").read_text())
            self.assertEqual(record["posts"][0]["quote"]["media"], [1])

    def test_a_reply_on_x_is_archived(self):
        # Without its page, X only tells which post is answered, by its id: nothing of that post is kept.
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            store = Store(Path(temp) / "data")
            worker = Downloader(store, Storage(store.root))
            item, _ = store.add(POST_URL, [])
            reply = syndication_post(x_post(mediaDetails=[], in_reply_to_status_id_str="1", in_reply_to_user_id_str="2"), POST_ID)
            worker.archive_posts(item, [reply], {}, None)
            archived = store.get(item["id"])
            self.assertEqual(archived["status"], "ready")
            self.assertNotIn("reply_to", archived["posts"][0])
            self.assertNotIn("warnings", archived["details"])

    def test_what_an_archive_lacks_is_kept_with_it(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            store = Store(Path(temp) / "data")
            worker = Downloader(store, Storage(store.root))
            item, _ = store.add(POST_URL, [])
            post = syndication_post(x_post(mediaDetails=[]), POST_ID)
            for kind in ("thread", "reply_to", "thread"):
                worker.warn(item, kind)
            with self.assertRaises(ValueError):
                worker.warn(item, "something else")
            worker.archive_posts(item, [post], {}, None)
            self.assertEqual(store.get(item["id"])["details"]["warnings"], ["thread", "reply_to"])
            self.assertEqual(json.loads(manifest.render(store.get(item["id"])))["details"]["warnings"], ["thread", "reply_to"])
            # Archived again without anything missing, it is complete.
            worker.archive_posts(item, [post], {}, None)
            self.assertNotIn("warnings", store.get(item["id"])["details"])


class MemoryTests(unittest.TestCase):
    """archive.json, fixity checks and checks that originals are still online."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # No network in these tests: checks of originals are enabled only where needed.
        self.env = patch.dict(os.environ, {"STORAGE_BACKEND": "local", "ARCHIVER_CHECK_DAYS": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = Store(self.temp.name)
        self.storage = Storage(self.store.root)
        self.maintenance = Maintenance(self.store, self.storage)

    def ready_article(self, url="https://example.org/article", verified=True):
        """A ready web page with its files recorded; not `verified`, its files are due for a first check."""
        item, _ = self.store.add(url, ["lecture"])
        directory = self.storage.directory(item["id"])
        directory.mkdir(parents=True)
        (directory / "article.html").write_text("<p>Bonjour</p>")
        (directory / "description.txt").write_text("Bonjour")
        files = {name: manifest.hash_file(directory / name) for name in ("article.html", "description.txt")}
        self.store.update(item["id"], status="ready", title="Un article", files=files,
                          **({"integrity": "ok", "integrity_checked_at": now()} if verified else {}))
        manifest.publish(self.store, self.storage, item["id"])
        return self.store.get(item["id"])

    def test_viewer_is_written_at_the_root_of_the_storage(self):
        item = self.ready_article()
        self.store.update(item["id"], tags=["lecture", "web"])
        self.maintenance.step()
        page = (self.store.root / "media" / "index.html").read_text()
        self.assertIn('data-readonly="1"', page)
        self.assertIn('"web"', page)
        self.assertIn("<p>Bonjour<\\/p>", page)  # the article travels with the page, escaped inside its script
        self.assertFalse(self.store.changed.is_set())

    def test_viewer_keeps_changes_made_during_publication_pending(self):
        item = self.ready_article()
        self.store.update(item["id"], note="Before publication")
        put = self.storage.put_root

        def edited_meanwhile(name, data):
            self.store.update(item["id"], note="Changed during publication")
            put(name, data)

        with patch.object(self.storage, "put_root", side_effect=edited_meanwhile):
            self.maintenance.write_viewer()
        page = self.store.root / "media" / "index.html"
        self.assertIn("Before publication", page.read_text())
        self.assertTrue(self.store.changed.is_set())
        self.maintenance.write_viewer()
        self.assertIn("Changed during publication", page.read_text())
        self.assertFalse(self.store.changed.is_set())

    def test_viewer_failure_keeps_publication_pending(self):
        self.ready_article()
        with patch.object(self.storage, "put_root", side_effect=OSError("storage unavailable")):
            with self.assertRaises(OSError):
                self.maintenance.write_viewer()
        self.assertTrue(self.store.changed.is_set())
        self.maintenance.write_viewer()
        self.assertTrue((self.store.root / "media" / "index.html").exists())
        self.assertFalse(self.store.changed.is_set())

    def test_concurrent_record_writes_use_independent_temporary_files(self):
        item = self.ready_article()
        replace = Path.replace
        nested = False

        def publish_during_replace(source, target):
            nonlocal nested
            if not nested:
                nested = True
                self.storage.put(item, manifest.NAME, b'second record')
            return replace(source, target)

        with patch.object(Path, 'replace', publish_during_replace):
            self.storage.put(item, manifest.NAME, b'first record')
        with self.storage.read(item, manifest.NAME) as stream:
            self.assertEqual(stream.read(), b'first record')
        self.assertFalse(list(self.storage.directory(item['id']).glob('*.tmp')))

    def test_fixity_check_completes_records_without_a_crc(self):
        item = self.ready_article()
        files = {name: {"sha256": value["sha256"], "size": value["size"]} for name, value in self.store.get(item["id"])["files"].items()}
        self.store.update(item["id"], files=files)
        self.maintenance.verify(self.store.get(item["id"]))
        updated = self.store.get(item["id"])
        self.assertEqual(updated["integrity"], "ok")
        self.assertTrue(all("crc32" in value for value in updated["files"].values()))
        record = json.loads(manifest.render(updated))
        self.assertTrue(all("crc32" not in value for value in record["files"].values()))

    def test_one_unreadable_archive_never_blocks_the_others(self):
        lost = self.ready_article("https://example.org/perdu", verified=False)
        shutil.rmtree(self.storage.directory(lost["id"]))
        broken = self.ready_article(verified=False)
        other = self.ready_article("https://example.org/autre", verified=False)
        real_read = self.storage.read

        def read(item, name):
            if item["id"] == broken["id"]:
                raise OSError("disque")
            return real_read(item, name)

        with patch.object(self.storage, "read", side_effect=read):
            for _ in range(3):
                self.assertEqual(self.maintenance.step(), 1)
        self.assertEqual(self.store.get(lost["id"])["integrity"], "damaged")
        self.assertIsNone(self.store.get(broken["id"])["integrity"])  # unreadable: checked again later
        self.assertEqual(self.store.get(other["id"])["integrity"], "ok")
        self.assertEqual(self.maintenance.step(), 600)  # nothing left today

    def test_trash_is_emptied_after_thirty_days(self):
        old = self.ready_article()
        recent, _ = self.store.add("https://example.org/recent", [])
        self.store.update(recent["id"], status="ready")
        self.store.trash([old["id"], recent["id"]])
        self.store.update(old["id"], deleted_at="2020-01-01T00:00:00+00:00")
        self.assertIsNone(self.store.due("integrity_checked_at", "9999"))  # trashed archives are not checked
        self.maintenance.step()
        self.assertIsNone(self.store.get(old["id"]))
        self.assertFalse(self.storage.directory(old["id"]).exists())
        self.assertIsNotNone(self.store.get(recent["id"]))

    def test_catalog_is_rebuilt_from_archive_records(self):
        item = self.ready_article()
        self.store.update(item["id"], note="Une note", tags=["lecture", "web"])
        manifest.publish(self.store, self.storage, item["id"])
        before = self.store.get(item["id"])
        broken = self.storage.directory("web-0123456789abcdef")
        broken.mkdir(parents=True)
        (broken / "archive.json").write_text("{pas du json")
        self.store.delete(item["id"])
        self.assertEqual(manifest.restore(self.store, self.storage), (1, 0, 1))
        restored = self.store.get(item["id"])
        for key in ("title", "source_url", "platform", "tags", "note", "created_at", "status", "storage"):
            self.assertEqual(restored[key], before[key], key)
        self.assertEqual(restored["files"], manifest.file_records(before["files"]))
        self.assertIsNone(restored["integrity_checked_at"])  # verified again soon
        self.assertEqual(manifest.restore(self.store, self.storage), (0, 1, 1))  # never overwritten

    def test_fixity_check_detects_altered_and_missing_files(self):
        item = self.ready_article()
        self.maintenance.verify(self.store.get(item["id"]))
        self.assertEqual(self.store.get(item["id"])["integrity"], "ok")
        (self.storage.directory(item["id"]) / "article.html").write_text("<p>Modifié</p>")
        self.maintenance.verify(self.store.get(item["id"]))
        self.assertEqual(self.store.get(item["id"])["integrity"], "damaged")
        (self.storage.directory(item["id"]) / "article.html").write_text("<p>Bonjour</p>")
        self.maintenance.verify(self.store.get(item["id"]))
        self.assertEqual(self.store.get(item["id"])["integrity"], "ok")
        (self.storage.directory(item["id"]) / "description.txt").unlink()
        self.maintenance.verify(self.store.get(item["id"]))
        self.assertEqual(self.store.get(item["id"])["integrity"], "damaged")

    def test_vanished_original_is_dated_and_cleared_when_back(self):
        item = self.ready_article()
        with patch("archiver.availability.check", return_value=availability.GONE):
            self.maintenance.check(item)
        gone = self.store.get(item["id"])
        self.assertEqual(gone["availability"], "gone")
        self.assertTrue(gone["gone_since"])
        with patch("archiver.availability.check", return_value=availability.GONE):
            self.maintenance.check(gone)
        self.assertEqual(self.store.get(item["id"])["gone_since"], gone["gone_since"])
        with patch("archiver.availability.check", return_value=availability.ONLINE):
            self.maintenance.check(gone)
        self.assertEqual((self.store.get(item["id"])["availability"], self.store.get(item["id"])["gone_since"]), ("online", None))

    def test_one_original_is_checked_an_hour_on_each_platform(self):
        ids = []
        for url in ("https://www.instagram.com/p/ABCDEF/", "https://www.instagram.com/p/GHIJKL/", "https://www.reddit.com/comments/abc12/",
                    "https://example.org/un", "https://example.org/deux"):
            item, _ = self.store.add(url, [])
            self.store.update(item["id"], status="ready", files={"a": {}}, integrity="ok", integrity_checked_at="9000")
            ids.append(item["id"])
        self.maintenance.check_days = 7
        with patch("archiver.availability.check", return_value=availability.ONLINE) as check:
            for _ in range(4):
                self.assertEqual(self.maintenance.step(), 30)
            # The second Instagram post waits for the next hour; web pages, as many sites, do not wait.
            self.assertEqual(self.maintenance.step(), 600)
            self.assertEqual(sorted(call[0][0]["id"] for call in check.call_args_list), sorted([ids[0], *ids[2:]]))
            self.assertIsNone(self.store.get(ids[1])["availability"])
            with patch("archiver.maintenance.CHECK_SECONDS", 0):
                self.assertEqual(self.maintenance.step(), 30)
        self.assertEqual(self.store.get(ids[1])["availability"], "online")


class AvailabilityPageTests(unittest.TestCase):
    def setUp(self):
        self.site = LocalSite({
            "/page": (200, {"Content-Type": "text/html"}, b"ok"),
            "/supprimee": (410, {}, b""),
            "/protegee": (403, {"cf-mitigated": "challenge"}, b""),
            "/erreur": (500, {}, b""),
        })
        self.addCleanup(self.site.close)

    def test_only_clear_answers_mean_gone(self):
        self.assertEqual(availability.page(self.site.url("/page")), availability.ONLINE)
        self.assertEqual(availability.page(self.site.url("/absente")), availability.GONE)
        self.assertEqual(availability.page(self.site.url("/supprimee")), availability.GONE)
        self.assertEqual(availability.page(self.site.url("/protegee")), availability.UNKNOWN)
        self.assertEqual(availability.page(self.site.url("/erreur")), availability.UNKNOWN)
        with patch("socket.getaddrinfo", side_effect=__import__("socket").gaierror):
            self.assertEqual(availability.page("https://site-disparu.example/"), availability.GONE)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_deduplication_and_tags_survive_reopening(self):
        item, created = self.store.add(VIDEO_URL, ["Test"])
        self.assertTrue(created)
        second, created = self.store.add(f"https://youtu.be/{VIDEO_ID}", [])
        self.assertFalse(created)
        self.assertEqual(second["tags"], ["test"])
        self.assertEqual(len(Store(self.temp.name).all()), 1)
        self.store.update(item["id"], tags=["persistant"])
        self.assertEqual(Store(self.temp.name).get(item["id"])["tags"], ["persistant"])

    def test_recovery_and_atomic_claim(self):
        self.store.add(VIDEO_URL, [])
        self.assertTrue(self.store.claim(VIDEO_ID))
        self.assertFalse(self.store.claim(VIDEO_ID))
        self.store.recover()
        self.assertEqual(self.store.get(VIDEO_ID)["status"], "queued")
        self.assertTrue(self.store.claim(VIDEO_ID))
        self.store.update(VIDEO_ID, status="ready")
        self.store.recover()
        self.assertEqual(self.store.get(VIDEO_ID)["status"], "ready")

    def test_progress_is_written_at_most_once_a_second(self):
        self.store.add(VIDEO_URL, [])
        self.store.update(VIDEO_ID, progress=10)
        self.assertIsNone(self.store.update(VIDEO_ID, progress=20))
        self.assertEqual(self.store.get(VIDEO_ID)["progress"], 10)
        self.store.update(VIDEO_ID, status="ready", progress=100)  # other changes are always written
        self.assertEqual(self.store.get(VIDEO_ID)["progress"], 100)

    def test_worker_survives_a_catalog_failure(self):
        import sqlite3
        self.store.add(VIDEO_URL, [])
        self.store.add(POST_URL, [])
        storage = Storage(self.store.root)
        worker = Downloader(self.store, storage)
        done = []
        real_claim = self.store.claim
        def claim(media_id):
            if media_id == VIDEO_ID:
                raise sqlite3.OperationalError("database or disk is full")
            return real_claim(media_id)
        with patch.object(self.store, "claim", side_effect=claim), \
                patch.object(self.store, "update", side_effect=sqlite3.OperationalError("database or disk is full")), \
                patch.object(worker, "download", side_effect=lambda media_id: done.append(media_id)):
            worker.thread.start()
            worker.enqueue(VIDEO_ID)
            worker.enqueue(f"x-{POST_ID}")
            worker.queue.join()
        self.assertTrue(worker.thread.is_alive())
        self.assertEqual(done, [f"x-{POST_ID}"])
        worker.stop()

    def test_one_archive_at_a_time_per_platform(self):
        ids = [self.store.add(url, [])[0]["id"] for url in (VIDEO_URL, POST_URL, "https://x.com/example/status/2")]
        worker = Downloader(self.store, Storage(self.store.root))
        started, release = [], threading.Event()
        both = threading.Barrier(3)

        def download(media_id):
            started.append(media_id)
            if len(started) <= 2:
                both.wait(timeout=5)  # the video and the first post are being archived together
            release.wait(timeout=5)

        with patch.object(worker, "download", side_effect=download):
            worker.thread.start()
            for media_id in ids:
                worker.enqueue(media_id)
            both.wait(timeout=5)
            # The second post waits for the first one, of the same platform.
            self.assertEqual(sorted(started), sorted(ids[:2]))
            release.set()
            worker.queue.join()
        self.assertEqual(started[2], ids[2])
        self.assertEqual(sorted(thread.name for _, thread in worker.lanes.values()), ["archive-X", "archive-YouTube"])
        worker.stop()

    def test_age_restricted_video_gets_a_clear_error(self):
        self.store.add(VIDEO_URL, [])
        worker = Downloader(self.store, Storage(self.store.root))
        failure = RuntimeError(f"ERROR: [youtube] {VIDEO_ID}: Sign in to confirm your age. Use --cookies-from-browser or --cookies")
        with patch("archiver.downloader.dependencies", return_value={}), \
                patch.object(worker, "run", side_effect=failure):
            worker.handle(VIDEO_ID)
        error = self.store.get(VIDEO_ID)["error"]
        self.assertEqual(error, "YouTube only shows this video to signed-in users (age restriction): "
                                "it cannot be archived without logging in.")
        self.assertEqual(i18n.translate(error, "fr"), "YouTube réserve cette vidéo aux utilisateurs connectés (limite d’âge) : "
                                                      "elle ne peut pas être archivée sans connexion.")

    def test_bot_check_points_to_a_local_archive(self):
        self.store.add(VIDEO_URL, [])
        worker = Downloader(self.store, Storage(self.store.root))
        failure = RuntimeError(f"ERROR: [youtube] {VIDEO_ID}: Sign in to confirm you’re not a bot. "
                               "Use --cookies-from-browser or --cookies for the authentication.")
        with patch("archiver.downloader.dependencies", return_value={}), \
                patch.object(worker, "run", side_effect=failure):
            worker.handle(VIDEO_ID)
        error = self.store.get(VIDEO_ID)["error"]
        self.assertTrue(error.startswith("YouTube is currently refusing this anonymous download"))
        self.assertTrue(i18n.translate(error, "fr").startswith("YouTube refuse pour l’instant ce téléchargement anonyme"))

    def test_archive_made_elsewhere_in_the_shared_storage_is_taken_over(self):
        added, _ = self.store.add(VIDEO_URL, ["mine"])
        self.store.update(VIDEO_ID, status="error", error="blocked")
        self.store.retry(VIDEO_ID)
        record = manifest.render({
            "id": VIDEO_ID, "platform": "YouTube", "source_url": VIDEO_URL, "title": "Me at the zoo",
            "created_at": "2026-10-27T10:00:00+00:00", "tags": ["music"], "note": "",
            "attachments": [{"type": "video", "original": "original.mkv", "playback": "playback.mp4",
                             "thumbnail": "original.jpg", "size": 10}],
            "files": {"original.mkv": {"sha256": "0" * 64, "size": 10}},
        })
        storage = Storage(self.store.root)
        storage.mode = "s3"
        worker = Downloader(self.store, storage)
        with patch.object(storage, "record", return_value=(record, False)), patch.object(storage, "put") as put, \
                patch.object(worker, "run", side_effect=AssertionError("nothing to download")):
            worker.handle(VIDEO_ID)
        item = self.store.get(VIDEO_ID)
        self.assertEqual((item["status"], item["error"], item["storage"]), ("ready", None, "s3"))
        self.assertEqual((item["title"], item["original"]), ("Me at the zoo", "original.mkv"))
        self.assertEqual(sorted(item["tags"]), ["mine", "music"])
        self.assertEqual(item["created_at"], added["created_at"])
        # archive.json is rewritten with the merged tags.
        self.assertEqual(put.call_args.args[1], manifest.NAME)
        self.assertEqual(sorted(json.loads(put.call_args.args[2])["tags"]), ["mine", "music"])

    def test_failed_archive_is_retried_automatically_five_times(self):
        self.store.add(VIDEO_URL, [])
        delays = []
        for _ in range(5):
            before = datetime.now(timezone.utc)
            self.store.fail(VIDEO_ID, "blocked")
            item = self.store.get(VIDEO_ID)
            delays.append(round((datetime.fromisoformat(item["retry_at"]) - before).total_seconds() / 60))
            self.assertEqual(self.store.due_retries(), [])
            self.store.update(VIDEO_ID, retry_at="2020-01-01T00:00:00+00:00")
            self.assertEqual(self.store.due_retries(), [VIDEO_ID])
            self.assertTrue(self.store.retry(VIDEO_ID, automatic=True))
        self.assertEqual(delays, [5, 30, 60, 360, 720])
        # After the last automatic retry, it is left to the user, who gets a fresh series.
        self.store.fail(VIDEO_ID, "blocked")
        item = self.store.get(VIDEO_ID)
        self.assertEqual((item["status"], item["retries"], item["retry_at"]), ("error", 5, None))
        self.assertTrue(self.store.retry(VIDEO_ID))
        self.store.fail(VIDEO_ID, "blocked")
        self.assertEqual(self.store.get(VIDEO_ID)["retries"], 0)
        self.assertIsNotNone(self.store.get(VIDEO_ID)["retry_at"])

    def test_a_final_failure_is_neither_retried_nor_left_to_a_relay(self):
        self.store.add(VIDEO_URL, [])
        worker = Downloader(self.store, Storage(self.store.root))
        gone = integrations.Gone(integrations.DELETED.format(platform="YouTube"))
        # Alone, or reported by `attempt` with the route it tried when a proxy is configured.
        for env in ({}, {"PROXY": "proxy.test:8000", "PROXY_MODE": "fallback"}):
            with self.subTest(env=env), patch.dict(os.environ, env), patch.object(worker, "download", side_effect=gone):
                worker.handle(VIDEO_ID)
                item = self.store.get(VIDEO_ID)
                self.assertEqual((item["status"], item["retry_at"], item["final"]), ("error", None, 1))
                self.assertIn("no longer exists", item["error"])
                self.assertEqual((self.store.due_retries(), self.store.relayable()), ([], []))
                # Retried by the user, it is a failure like any other until it fails the same way again.
                self.assertTrue(self.store.retry(VIDEO_ID))
                self.assertEqual(self.store.get(VIDEO_ID)["final"], 0)
        # A tool missing on this computer says nothing of the original: a relay may have it.
        with patch("archiver.downloader.dependencies", return_value={"ffmpeg": False}):
            worker.handle(VIDEO_ID)
        item = self.store.get(VIDEO_ID)
        self.assertEqual((item["final"], item["error"]), (0, "Missing tools: ffmpeg. See the README or use Docker."))
        self.assertIsNotNone(item["retry_at"])
        self.assertEqual(self.store.relayable(), [{"id": VIDEO_ID, "url": VIDEO_URL, "languages": None}])

    def test_due_retries_are_queued_by_the_worker_but_not_from_the_trash(self):
        self.store.add(VIDEO_URL, [])
        worker = Downloader(self.store, Storage(self.store.root))
        with patch("archiver.downloader.dependencies", return_value={}), \
                patch.object(worker, "run", side_effect=RuntimeError("ERROR: network")):
            worker.handle(VIDEO_ID)
        self.store.update(VIDEO_ID, retry_at="2020-01-01T00:00:00+00:00")
        self.store.trash([VIDEO_ID])
        worker.enqueue_due_retries()
        self.assertEqual(self.store.get(VIDEO_ID)["status"], "error")
        self.store.trash([VIDEO_ID], deleted=False)
        worker.enqueue_due_retries()
        item = self.store.get(VIDEO_ID)
        self.assertEqual((item["status"], item["retries"], item["retry_at"]), ("queued", 1, None))
        self.assertEqual(worker.queue.get_nowait(), VIDEO_ID)

    def test_retry_only_failed_jobs(self):
        self.store.add(VIDEO_URL, [])
        self.assertFalse(self.store.retry(VIDEO_ID))
        self.store.update(VIDEO_ID, status="error", error="network")
        self.assertTrue(self.store.retry(VIDEO_ID))
        self.assertFalse(self.store.retry(VIDEO_ID))
        self.assertIsNone(self.store.get(VIDEO_ID)["error"])

    def test_failed_first_archives_are_left_to_a_relay(self):
        self.store.add(VIDEO_URL, [])
        self.store.add(POST_URL, [])
        self.assertEqual(self.store.relayable(), [])
        self.assertFalse(self.store.hold(VIDEO_ID))
        self.store.fail(VIDEO_ID, "blocked")
        self.store.fail(f"x-{POST_ID}", "blocked")
        self.store.trash([f"x-{POST_ID}"])
        self.assertEqual(self.store.relayable(), [{"id": VIDEO_ID, "url": VIDEO_URL, "languages": None}])
        # Held for a relay, it is no longer retried automatically, and stays the user's to retry.
        self.assertIsNotNone(self.store.get(VIDEO_ID)["retry_at"])
        self.assertTrue(self.store.hold(VIDEO_ID))
        self.assertIsNone(self.store.get(VIDEO_ID)["retry_at"])
        self.assertEqual(self.store.relayable(), [{"id": VIDEO_ID, "url": VIDEO_URL, "languages": None}])
        # A link's later archives are not relayed: a relay names its archive after the link alone.
        self.store.update(VIDEO_ID, status="ready")
        again, created = self.store.add(VIDEO_URL, [], again=True)
        self.assertTrue(created)
        self.store.fail(again["id"], "blocked")
        self.assertEqual(self.store.relayable(), [])


class ServerTestCase(unittest.TestCase):
    """A running server with its account, and a signed-in session used by default."""

    def setUp(self):
        self.env = patch.dict(os.environ, {"STORAGE_BACKEND": "local", "ARCHIVER_CHECK_DAYS": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = AppServer(("127.0.0.1", 0), self.temp.name, start_worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.server.auth.create("admin", PASSWORD)
        self.cookie = f"archiver_session={self.server.auth.open_session()}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        body = json.dumps(data) if data is not None else None
        request_headers = {"Content-Type": "application/json", "Cookie": self.cookie, **(headers or {})}
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result


class VersionTests(ServerTestCase):
    def test_the_version_has_major_minor_and_patch_numbers(self):
        self.assertRegex(version.VERSION, r"^\d+\.\d+\.\d+$")

    def test_the_interface_is_told_the_version_once_signed_in(self):
        with patch("archiver.server.dependencies", return_value={}):
            status, _, body = self.request("GET", "/api/system")
            self.assertEqual((status, json.loads(body)["version"]), (200, version.VERSION))
            self.assertEqual(self.request("GET", "/api/system", headers={"Cookie": ""})[0], 401)


class HTTPTests(ServerTestCase):
    def ready_item(self):
        item, _ = self.server.store.add(VIDEO_URL, [])
        directory = self.server.storage.directory(item["id"])
        directory.mkdir(parents=True)
        (directory / "original.mp4").write_bytes(bytes(range(100)))
        self.server.store.update(item["id"], status="ready", original="original.mp4", playback="original.mp4", size=100,
                                 files={"original.mp4": manifest.hash_file(directory / "original.mp4")})
        return item

    def test_create_duplicate_and_invalid_url(self):
        with patch("archiver.server.dependencies", return_value={}):
            self.assertEqual(self.request("POST", "/api/media", {"url": VIDEO_URL})[0], 201)
            self.assertEqual(self.request("POST", "/api/media", {"url": VIDEO_URL})[0], 200)
            self.assertEqual(self.request("POST", "/api/media", {"url": "http://localhost/admin"})[0], 400)
        self.assertEqual(self.server.worker.queue.qsize(), 1)

    def test_link_archived_again(self):
        self.ready_item()
        with patch("archiver.server.dependencies", return_value={}):
            status, _, body = self.request("POST", "/api/media", {"url": VIDEO_URL})
            result = json.loads(body)
            self.assertEqual((status, result["created"], [o["id"] for o in result["occurrences"]]), (200, False, [VIDEO_ID]))
            status, _, body = self.request("POST", "/api/media", {"url": VIDEO_URL, "again": True})
            self.assertEqual((status, json.loads(body)["item"]["id"]), (201, f"{VIDEO_ID}.2"))
            # Not again while an archive of the link is in progress.
            status, _, body = self.request("POST", "/api/media", {"url": VIDEO_URL, "again": True})
            self.assertEqual((status, json.loads(body)["created"]), (200, False))
            self.server.store.update(f"{VIDEO_ID}.2", status="ready")
            result = json.loads(self.request("POST", "/api/media", {"url": VIDEO_URL})[2])
            self.assertEqual([o["id"] for o in result["occurrences"]], [f"{VIDEO_ID}.2", VIDEO_ID])
            self.assertEqual(json.loads(self.request("POST", "/api/media", {"url": VIDEO_URL, "again": True})[2])["item"]["id"],
                             f"{VIDEO_ID}.3")
        self.assertEqual(self.server.worker.queue.qsize(), 2)
        self.assertEqual(self.request("GET", f"/api/media/{VIDEO_ID}.2/metadata")[0], 200)
        with patch("archiver.integrations.youtube.online", return_value="online") as youtube:
            availability.check(self.server.store.get(f"{VIDEO_ID}.2"), None)
        self.assertEqual(youtube.call_args[0][0]["id"], f"{VIDEO_ID}.2")

    def test_subtitles_are_served_and_searched(self):
        item = self.ready_item()
        (self.server.storage.directory(item["id"]) / "original.fr.vtt").write_text(SubtitleTests.VTT, encoding="utf-8")
        self.server.store.update(item["id"], details={"subtitles": [{"file": "original.fr.vtt", "language": "fr", "name": None, "automatic": True}]},
                                 files={"original.fr.vtt": {"sha256": "a" * 64, "size": len(SubtitleTests.VTT.encode())}})
        status, headers, body = self.request("GET", f"/api/media/{item['id']}/file/subtitles/0")
        self.assertEqual((status, headers["Content-Type"], body.decode()), (200, "text/vtt; charset=utf-8", SubtitleTests.VTT))
        self.assertEqual(self.request("GET", f"/api/media/{item['id']}/file/subtitles/1")[0], 404)
        self.assertEqual(self.request("GET", f"/api/media/{item['id']}/file/subtitles")[0], 404)
        self.assertEqual(json.loads(self.request("GET", "/api/articles/text")[2]), {"texts": {item["id"]: "Bonjour à tous un mot rare"}})

    def test_tags_metadata_and_purge(self):
        self.ready_item()
        status, _, body = self.request("PATCH", f"/api/media/{VIDEO_ID}", {"tags": ["Nature"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["tags"], ["nature"])
        status, _, body = self.request("GET", f"/api/media/{VIDEO_ID}/metadata")
        self.assertEqual(json.loads(body)["source_url"], VIDEO_URL)
        self.assertEqual(json.loads(body)["tags"], ["nature"])
        # Only an explicit purge, from the trash, deletes the stored files.
        self.request("POST", "/api/media/batch", {"action": "delete", "ids": [VIDEO_ID]})
        self.assertTrue(self.server.store.get(VIDEO_ID)["deleted_at"])
        self.assertTrue(self.server.storage.directory(VIDEO_ID).exists())
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", {"action": "purge", "ids": [VIDEO_ID]})[2]),
                         {"deleted": 1, "skipped": 0})
        self.assertFalse(self.server.storage.directory(VIDEO_ID).exists())
        self.assertEqual(self.request("GET", f"/api/media/{VIDEO_ID}/metadata")[0], 404)

    def test_a_failure_while_sending_never_appends_an_error_to_the_file(self):
        self.ready_item()
        class Failing(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError("stockage coupé")
                return super().read(40)
        with patch.object(self.server.storage, "open", return_value=Failing(bytes(range(100)))):
            conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            conn.request("GET", f"/api/media/{VIDEO_ID}/file/playback", headers={"Cookie": self.cookie})
            response = conn.getresponse()
            with self.assertRaises(http.client.IncompleteRead) as cut:
                response.read()
            conn.close()
        self.assertEqual(cut.exception.partial, bytes(range(40)))

    def test_trash_restore_empty_and_relink(self):
        self.ready_item()
        post, _ = self.server.store.add(POST_URL, [])
        self.server.store.update(post["id"], status="ready")
        self.request("POST", "/api/media/batch", {"action": "delete", "ids": [VIDEO_ID, post["id"]]})
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", {"action": "restore", "ids": [VIDEO_ID]})[2]), {"restored": 1})
        self.assertIsNone(self.server.store.get(VIDEO_ID)["deleted_at"])
        # Trashed archives are left out of exports.
        with zipfile.ZipFile(io.BytesIO(self.request("GET", "/api/export")[2])) as archive:
            catalog = json.loads(archive.read(next(n for n in archive.namelist() if n.endswith("catalog.json"))))
        self.assertEqual([entry["id"] for entry in catalog], [VIDEO_ID])
        # Archiving again a link that is in the trash takes it out, without downloading it again.
        with patch("archiver.server.dependencies", return_value={}):
            result = json.loads(self.request("POST", "/api/media", {"url": POST_URL})[2])
        self.assertEqual((result["created"], result["restored"], result["item"]["deleted_at"]), (False, True, None))
        self.request("POST", "/api/media/batch", {"action": "delete", "ids": [post["id"]]})
        self.assertEqual(json.loads(self.request("POST", "/api/trash/empty", {})[2]), {"deleted": 1})
        self.assertIsNone(self.server.store.get(post["id"]))
        self.assertIsNotNone(self.server.store.get(VIDEO_ID))

    def test_active_job_cannot_be_deleted_or_retried(self):
        self.server.store.add(VIDEO_URL, [])
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", {"action": "delete", "ids": [VIDEO_ID]})[2]),
                         {"deleted": 0, "skipped": 1})
        self.assertEqual(self.request("POST", f"/api/media/{VIDEO_ID}/retry", {})[0], 409)
        self.server.store.update(VIDEO_ID, status="error", error="network")
        self.assertEqual(self.request("POST", f"/api/media/{VIDEO_ID}/retry", {})[0], 200)

    def test_streaming_range_head_and_download(self):
        self.ready_item()
        url = f"/api/media/{VIDEO_ID}/file/playback"
        status, headers, body = self.request("GET", url, headers={"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 10-19/100")
        self.assertEqual(body, bytes(range(10, 20)))
        status, headers, body = self.request("HEAD", url)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Length"], "100")
        self.assertEqual(body, b"")
        status, headers, body = self.request("GET", url, headers={"Range": "bytes=200-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], "bytes */100")
        self.assertIn("attachment", self.request("GET", url.replace("playback", "original"))[1]["Content-Disposition"])

    def test_only_an_archived_pdf_may_be_framed_and_only_by_its_own_site(self):
        item, _ = self.server.store.add("https://example.org/doc.pdf", [])
        directory = self.server.storage.directory(item["id"])
        directory.mkdir(parents=True)
        (directory / "original.pdf").write_bytes(b"%PDF-1.4")
        (directory / "thumbnail.jpg").write_bytes(b"jpg")
        attachment = {"type": "pdf", "original": "original.pdf", "playback": "original.pdf", "thumbnail": "thumbnail.jpg"}
        self.server.store.update(item["id"], status="ready", attachments=[attachment], original="original.pdf",
                                 playback="original.pdf", thumbnail="thumbnail.jpg")
        url = f"/api/media/{item['id']}/file"
        status, headers, body = self.request("GET", f"{url}/playback/0")
        self.assertEqual((status, body, headers["Content-Type"]), (200, b"%PDF-1.4", "application/pdf"))
        self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Content-Disposition", headers)
        # The download of the same file, its preview and the pages themselves stay unframable.
        for path in (f"{url}/original/0", f"{url}/thumbnail/0", "/", f"/api/media/{item['id']}/metadata"):
            status, headers, _ = self.request("GET", path)
            self.assertEqual((status, headers["X-Frame-Options"]), (200, "DENY"), path)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_x_post_attachments_are_served_by_index(self):
        item, _ = self.server.store.add(POST_URL, [])
        directory = self.server.storage.directory(item["id"])
        directory.mkdir(parents=True)
        for name in ("media-01-original.jpg", "media-02-original.jpg", "original.info.json"):
            (directory / name).write_bytes(name.encode())
        (directory / "description.txt").write_text("Texte du post")
        attachments = [{"type": "image", "original": f"media-0{n}-original.jpg", "playback": f"media-0{n}-original.jpg",
                        "thumbnail": f"media-0{n}-original.jpg"} for n in (1, 2)]
        self.server.store.update(item["id"], status="ready", attachments=attachments, original=attachments[0]["original"],
                                 files={path.name: manifest.hash_file(path) for path in directory.iterdir()})
        status, headers, body = self.request("GET", f"/api/media/{item['id']}/file/original/1")
        self.assertEqual((status, body), (200, b"media-02-original.jpg"))
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(self.request("GET", f"/api/media/{item['id']}/file/original/2")[0], 404)
        self.assertEqual(self.request("GET", f"/api/media/{item['id']}/file/description")[2], "Texte du post".encode())
        self.assertEqual(len(json.loads(self.request("GET", f"/api/media/{item['id']}/metadata")[2])["attachments"]), 2)

    def test_note_is_saved_and_written_to_archive_json(self):
        item = self.ready_item()
        self.server.store.update(item["id"], files={"original.mp4": manifest.hash_file(self.server.storage.directory(item["id"]) / "original.mp4")})
        status, _, body = self.request("PATCH", f"/api/media/{VIDEO_ID}", {"note": "  Vue chez un ami  "})
        self.assertEqual((status, json.loads(body)["note"]), (200, "Vue chez un ami"))
        record = json.loads((self.server.storage.directory(VIDEO_ID) / "archive.json").read_text())
        self.assertEqual(record["note"], "Vue chez un ami")
        self.assertEqual(self.request("PATCH", f"/api/media/{VIDEO_ID}", {"note": "x" * 5001})[0], 400)
        self.assertEqual(self.request("PATCH", f"/api/media/{VIDEO_ID}", {"autre": 1})[0], 400)
        status, _, body = self.request("PATCH", f"/api/media/{VIDEO_ID}", {"custom_title": "  Mon   titre "})
        self.assertEqual((status, json.loads(body)["custom_title"]), (200, "Mon titre"))
        self.assertEqual(self.request("PATCH", f"/api/media/{VIDEO_ID}", {"custom_title": "x" * 201})[0], 400)
        self.assertEqual(json.loads(self.request("PATCH", f"/api/media/{VIDEO_ID}", {"custom_title": ""})[2])["custom_title"], "")
        self.assertEqual(json.loads(self.request("PATCH", f"/api/media/{VIDEO_ID}", {"tags": ["a"]})[2])["note"], "Vue chez un ami")

    def test_library_and_archive_exports_are_valid_zips(self):
        self.ready_item()
        self.server.store.add(POST_URL, [])  # not ready: listed in the catalog, without files
        for path, root in (("/api/export", "archiver-"), (f"/api/media/{VIDEO_ID}/export", VIDEO_ID)):
            status, headers, body = self.request("GET", path)
            self.assertEqual((status, headers["Content-Type"]), (200, "application/zip"))
            self.assertEqual(int(headers["Content-Length"]), len(body))
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                self.assertIsNone(archive.testzip())
                names = archive.namelist()
                self.assertTrue(all(name.startswith(root) for name in names))
                catalog = json.loads(archive.read(next(name for name in names if name.endswith("catalog.json"))))
                self.assertEqual(archive.read(next(name for name in names if name.endswith("original.mp4"))), bytes(range(100)))
                page = archive.read(next(name for name in names if name.endswith("/index.html"))).decode()
                # Only the page and the catalog at the root; each archive in its folder under media/.
                top = {name.split("/", 1)[1].split("/", 1)[0] for name in names}
                self.assertEqual(top, {"index.html", "catalog.json", "media"})
                self.assertIn(f"/media/{VIDEO_ID}/original.mp4", "\n".join(names))
            self.assertEqual(len(catalog), 2 if path == "/api/export" else 1)
            self.assertIn('data-readonly="1"', page)
            embedded = json.loads(page.split('id="catalog">')[1].split("</script>")[0])
            self.assertEqual(embedded["folder"], "media/")
            self.assertNotIn("/api/media", json.dumps(embedded))

    def test_export_resumes_and_keeps_its_layout(self):
        self.ready_item()
        path = "/api/export?at=2026-10-25T14:03"
        status, headers, whole = self.request("GET", path)
        self.assertEqual((status, headers["Accept-Ranges"]), (200, "bytes"))
        self.assertIn('filename="archiver-2026-10-25.zip"', headers["Content-Disposition"])
        status, partial_headers, part = self.request("GET", path, headers={"Range": "bytes=150-", "If-Range": headers["ETag"]})
        self.assertEqual((status, part), (206, whole[150:]))
        self.assertEqual(partial_headers["Content-Range"], f"bytes 150-{len(whole) - 1}/{len(whole)}")
        self.assertEqual(self.request("GET", path, headers={"Range": "bytes=0-99"})[2], whole[:100])
        # A changed library does not resume: the whole export comes back.
        self.request("PATCH", f"/api/media/{VIDEO_ID}", {"note": "Changée"})
        status, _, body = self.request("GET", path, headers={"Range": "bytes=150-", "If-Range": headers["ETag"]})
        self.assertEqual(status, 200)
        self.assertIsNone(zipfile.ZipFile(io.BytesIO(body)).testzip())

    def test_export_dates_outside_zip_range_still_produce_valid_archives(self):
        for date in ('0001-01-01', '9999-12-31'):
            with self.subTest(date=date):
                status, _, body = self.request('GET', '/api/export?at=' + date)
                self.assertEqual(status, 200)
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    self.assertIsNone(archive.testzip())

    def test_export_remembers_crcs_it_had_to_read(self):
        self.ready_item()
        record = manifest.hash_file(self.server.storage.directory(VIDEO_ID) / "original.mp4")
        self.server.store.update(VIDEO_ID, files={"original.mp4": {"sha256": record["sha256"], "size": record["size"]}})
        # The first bytes need no CRC: nothing is read for them.
        self.request("GET", "/api/export?at=2026-10-25", headers={"Range": "bytes=0-10"})
        self.assertNotIn("crc32", self.server.store.get(VIDEO_ID)["files"]["original.mp4"])
        # A CRC read is recorded before the bytes that need it are sent.
        first = self.request("GET", "/api/export?at=2026-10-25")[2]
        self.assertEqual(self.server.store.get(VIDEO_ID)["files"]["original.mp4"]["crc32"], record["crc32"])
        # Known CRCs change nothing in the file: the next export is byte for byte the same.
        self.assertEqual(self.request("GET", "/api/export?at=2026-10-25")[2], first)

    def test_files_are_confirmed_from_their_hash(self):
        self.ready_item()
        directory = self.server.storage.directory(VIDEO_ID)
        self.server.store.update(VIDEO_ID, files={"original.mp4": manifest.hash_file(directory / "original.mp4")})
        status, headers, _ = self.request("GET", f"/api/media/{VIDEO_ID}/file/playback")
        with patch.object(self.server.storage, "size", side_effect=AssertionError("storage must not be read")):
            status, _, body = self.request("GET", f"/api/media/{VIDEO_ID}/file/playback", headers={"If-None-Match": headers["ETag"]})
        self.assertEqual((status, body), (304, b""))

    def test_health_reports_a_stopped_worker_or_catalog(self):
        self.assertEqual(json.loads(self.request("GET", "/health")[2]), {"status": "ok"})
        stopped = threading.Thread(target=lambda: None)
        stopped.start()
        stopped.join()
        with patch.object(self.server.worker, "thread", stopped):
            status, _, body = self.request("GET", "/health")
        self.assertEqual((status, json.loads(body)["problems"]), (503, ["worker"]))
        with patch.object(self.server.store, "ping", side_effect=OSError("disque")):
            self.assertEqual(json.loads(self.request("GET", "/health")[2])["problems"], ["catalog"])

    def test_export_of_some_tags(self):
        self.ready_item()
        self.request("PATCH", f"/api/media/{VIDEO_ID}", {"tags": ["nature"]})
        other, _ = self.server.store.add(POST_URL, [])
        self.server.store.update(other["id"], status="ready")
        def exported(query):
            body = self.request("GET", f"/api/export?{query}")[2]
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                return sorted(entry["id"] for entry in json.loads(archive.read(next(n for n in archive.namelist() if n.endswith("catalog.json")))))
        self.assertEqual(exported("tag=nature"), [VIDEO_ID])
        self.assertEqual(exported("untagged=1"), [other["id"]])
        self.assertEqual(exported("tag=nature&untagged=1"), sorted([VIDEO_ID, other["id"]]))
        self.assertEqual(exported("tag=absent"), [])

    def test_app_install_and_share_files(self):
        self.assertIn(b'id="url"', self.request("GET", "/share?url=https://example.org")[2])
        status, headers, body = self.request("GET", "/manifest.webmanifest")
        self.assertEqual((status, headers["Content-Type"]), (200, "application/manifest+json"))
        self.assertEqual(json.loads(body)["share_target"]["action"], "/share")
        for path in ("/sw.js", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"):
            self.assertEqual(self.request("GET", path)[0], 200, path)

    def test_tags_are_renamed_merged_and_deleted_everywhere(self):
        self.ready_item()
        post, _ = self.server.store.add(POST_URL, ["Nature", "été"])
        self.server.store.update(VIDEO_ID, tags=["nature", "mer"], files={"original.mp4": manifest.hash_file(self.server.storage.directory(VIDEO_ID) / "original.mp4")})
        self.assertEqual(json.loads(self.request("POST", "/api/tags/rename", {"from": "nature", "to": "Paysage"})[2]), {"updated": 2})
        self.assertEqual(self.server.store.get(VIDEO_ID)["tags"], ["paysage", "mer"])
        self.assertEqual(self.server.store.get(post["id"])["tags"], ["paysage", "été"])
        self.assertEqual(json.loads(self.request("POST", "/api/tags/rename", {"from": "mer", "to": "paysage"})[2]), {"updated": 1})
        self.assertEqual(self.server.store.get(VIDEO_ID)["tags"], ["paysage"])
        self.assertEqual(json.loads(self.request("POST", "/api/tags/delete", {"tag": "paysage"})[2]), {"updated": 2})
        self.assertEqual((self.server.store.get(VIDEO_ID)["tags"], self.server.store.get(post["id"])["tags"]), ([], ["été"]))
        self.assertEqual(self.request("POST", "/api/tags/rename", {"from": "", "to": "x"})[0], 400)
        record = json.loads((self.server.storage.directory(VIDEO_ID) / "archive.json").read_text())
        self.assertEqual(record["tags"], [])

    def test_batch_tag_and_delete(self):
        self.ready_item()
        self.server.store.update(VIDEO_ID, files={"original.mp4": {"sha256": "0" * 64, "size": 100}})
        post, _ = self.server.store.add(POST_URL, [])
        body = {"action": "tag", "ids": [VIDEO_ID, post["id"], VIDEO_ID], "tag": " Lu "}
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", body)[2]), {"updated": 2})
        self.assertEqual(self.server.store.get(post["id"])["tags"], ["lu"])
        # The tag reaches the record kept with the archive's files.
        self.assertEqual(json.loads((self.server.storage.directory(VIDEO_ID) / "archive.json").read_text())["tags"], ["lu"])
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", body)[2]), {"updated": 0})
        status, _, result = self.request("POST", "/api/media/batch", {"action": "delete", "ids": [VIDEO_ID, post["id"], "x-404"]})
        self.assertEqual((status, json.loads(result)), (200, {"deleted": 1, "skipped": 1}))  # the queued post is kept
        self.assertTrue(self.server.store.get(VIDEO_ID)["deleted_at"])
        self.request("POST", "/api/media/batch", {"action": "purge", "ids": [VIDEO_ID]})
        self.assertIsNone(self.server.store.get(VIDEO_ID))
        self.assertFalse(self.server.storage.directory(VIDEO_ID).exists())
        for body in ({"action": "delete", "ids": []}, {"action": "delete", "ids": ["../x"]}, {"action": "autre", "ids": [post["id"]]}):
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/api/media/batch", body)[0], 400)

    def test_repeated_batch_delete_never_purges_and_purge_requires_trash(self):
        self.ready_item()
        purge = {"action": "purge", "ids": [VIDEO_ID]}
        self.assertEqual(self.request("POST", "/api/media/batch", purge)[0], 400)
        body = {"action": "delete", "ids": [VIDEO_ID]}
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", body)[2])["deleted"], 1)
        trashed_at = self.server.store.get(VIDEO_ID)["deleted_at"]
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", body)[2])["deleted"], 0)
        self.assertEqual(self.server.store.get(VIDEO_ID)["deleted_at"], trashed_at)
        self.assertTrue(self.server.storage.directory(VIDEO_ID).exists())
        other, _ = self.server.store.add(POST_URL, [])
        self.server.store.update(other["id"], status="ready")
        # Validate the entire request before deleting anything from a mixed selection.
        self.assertEqual(self.request("POST", "/api/media/batch", {
            "action": "purge", "ids": [VIDEO_ID, other["id"]],
        })[0], 400)
        self.assertIsNotNone(self.server.store.get(VIDEO_ID))
        self.assertIsNotNone(self.server.store.get(other["id"]))
        self.request("POST", "/api/media/batch", {"action": "restore", "ids": [VIDEO_ID]})
        self.assertEqual(self.request("POST", "/api/media/batch", purge)[0], 400)
        self.assertTrue(self.server.storage.directory(VIDEO_ID).exists())

    def test_emptying_the_trash_skips_archives_being_processed(self):
        self.ready_item()
        other, _ = self.server.store.add(POST_URL, [])
        self.server.store.trash([VIDEO_ID, other["id"]])
        self.assertEqual(json.loads(self.request("POST", "/api/trash/empty", {})[2]), {"deleted": 1})
        self.assertIsNone(self.server.store.get(VIDEO_ID))
        self.assertIsNotNone(self.server.store.get(other["id"]))  # still queued

    def changes(self, since=0, limit=100, headers=None):
        status, _, body = self.request("GET", f"/api/media/changes?since={since}&limit={limit}", headers=headers)
        self.assertEqual(status, 200)
        return json.loads(body)

    def unlock(self):
        status, _, body = self.request("POST", "/api/private/unlock", {"password": PASSWORD})
        self.assertEqual(status, 200)
        return json.loads(body)["token"]

    def private_post(self):
        post, _ = self.server.store.add(POST_URL, ["Privé", "voyage"])
        directory = self.server.storage.directory(post["id"])
        directory.mkdir(parents=True)
        (directory / "thumbnail.jpg").write_bytes(b"jpg")
        return self.server.store.update(post["id"], status="ready", thumbnail="thumbnail.jpg")

    def test_locked_sync_never_names_private_archives(self):
        self.ready_item()
        post = self.private_post()  # private from the start: nothing to tell
        first = self.changes()
        self.assertEqual((first["removed"], first["hidden_since"]), ([], None))
        # A public archive that becomes private is signalled without its id, and so are its later changes.
        self.server.store.update(VIDEO_ID, tags=["private"])
        for note in ("", "later"):
            self.server.store.update(VIDEO_ID, note=note)
            delta = self.changes(first["cursor"])
            self.assertEqual((delta["items"], delta["removed"], delta["total"]), ([], [], 0))
            self.assertGreaterEqual(delta["hidden_since"], first["latest"])
            self.assertNotIn(VIDEO_ID, json.dumps(delta))
        # Readers that start afterwards are told nothing new: the signal is older than their start.
        again = self.changes()
        self.assertLess(again["hidden_since"], again["latest"])
        # Deleted private archives stay unnamed; unlocked readers get everything.
        unlocked = self.changes(first["cursor"], headers={"X-Private-Unlock": self.unlock()})
        self.assertEqual([item["id"] for item in unlocked["items"]], [VIDEO_ID])
        for media_id in (VIDEO_ID, post["id"]):
            self.server.store.delete(media_id)
        delta = self.changes(first["cursor"])
        self.assertEqual((delta["items"], delta["removed"]), ([], []))
        self.assertEqual(sorted(self.changes(first["cursor"], headers={"X-Private-Unlock": self.unlock()})["removed"]),
                         sorted([VIDEO_ID, post["id"]]))

    def test_private_archives_are_hidden_until_unlocked(self):
        self.ready_item()
        post = self.private_post()
        self.assertEqual(post["tags"], ["private", "voyage"])
        locked = self.changes()
        self.assertEqual(([item["id"] for item in locked["items"]], locked["removed"], locked["total"], locked["unlocked"]),
                         ([VIDEO_ID], [], 1, False))
        self.assertNotIn(post["id"], json.dumps(locked))
        for method, path, data in (("GET", f"/api/media/{post['id']}/metadata", None),
                                   ("GET", f"/api/media/{post['id']}/file/thumbnail", None), ("GET", f"/api/media/{post['id']}/export", None),
                                   ("PATCH", f"/api/media/{post['id']}", {"note": "?"})):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, data)[0], 404)
        self.assertEqual(json.loads(self.request("POST", "/api/media/batch", {"action": "delete", "ids": [post["id"]]})[2])["deleted"], 0)
        self.assertEqual(self.request("POST", "/api/private/unlock", {"password": "wrong password"})[0], 403)
        token = self.unlock()
        headers = {"X-Private-Unlock": token}
        unlocked = self.changes(headers=headers)
        self.assertEqual((len(unlocked["items"]), unlocked["removed"], unlocked["total"], unlocked["unlocked"]), (2, [], 2, True))
        # Media and downloads, which cannot send headers, carry the token in their address.
        self.assertEqual(self.request("GET", f"/api/media/{post['id']}/file/thumbnail?unlock={token}")[2], b"jpg")
        self.assertEqual(self.request("GET", f"/api/media/{post['id']}/metadata", headers=headers)[0], 200)
        # Locking again, or logging out, revokes the token.
        self.request("POST", "/api/private/lock", {}, headers)
        self.assertFalse(self.changes(headers=headers)["unlocked"])
        self.request("POST", "/api/private/lock", {"token": self.unlock()})
        self.assertEqual(self.server.auth.unlocks, {})
        self.unlock()
        self.request("POST", "/api/auth/logout", {})
        self.assertEqual(self.server.auth.unlocks, {})

    def test_unlock_token_expires_without_use(self):
        token = self.unlock()
        with patch("archiver.auth.time.monotonic", return_value=time.monotonic() + 11 * 60):
            self.assertFalse(self.server.auth.unlocked(token))
        self.assertFalse(self.server.auth.unlocked(token))

    def test_wrong_unlock_passwords_count_towards_the_login_throttle(self):
        self.server.auth.throttle.limit = 2
        for _ in range(2):
            self.assertEqual(self.request("POST", "/api/private/unlock", {"password": "wrong password"})[0], 403)
        self.assertEqual(self.request("POST", "/api/private/unlock", {"password": PASSWORD})[0], 429)

    def test_hidden_private_archives_are_left_out_of_tags_exports_trash_and_duplicates(self):
        self.ready_item()
        post = self.private_post()
        self.server.store.update(VIDEO_ID, tags=["voyage"])
        # Renaming a tag changes private archives too, without counting them; the Private tag itself cannot be.
        self.assertEqual(json.loads(self.request("POST", "/api/tags/rename", {"from": "voyage", "to": "été"})[2]), {"updated": 1})
        self.assertEqual(self.server.store.get(post["id"])["tags"], ["private", "été"])
        for path, data in (("/api/tags/rename", {"from": "Privé", "to": "x"}), ("/api/tags/delete", {"tag": "private"})):
            self.assertEqual(self.request("POST", path, data)[0], 400)
        def exported(query, headers=None):
            body = self.request("GET", f"/api/export?{query}", headers=headers)[2]
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                return sorted(entry["id"] for entry in json.loads(archive.read(next(n for n in archive.namelist() if n.endswith("catalog.json")))))
        token = self.unlock()
        self.assertEqual(exported("private=1"), [VIDEO_ID])
        self.assertEqual(exported("", {"X-Private-Unlock": token}), [VIDEO_ID])
        self.assertEqual(exported(f"private=1&unlock={token}"), sorted([VIDEO_ID, post["id"]]))
        # A hidden archive is neither shown as a duplicate nor taken out of the trash.
        with patch("archiver.server.dependencies", return_value={}):
            self.assertEqual(json.loads(self.request("POST", "/api/media", {"url": POST_URL})[2]), {"created": False, "private": True})
            self.assertEqual(self.request("POST", "/api/media", {"url": POST_URL}, {"X-Private-Unlock": token})[0], 200)
            other = "https://x.com/demo/status/123"
            status, _, body = self.request("POST", "/api/media", {"url": other, "tags": ["privé"]})
            self.assertEqual((status, json.loads(body)), (201, {"created": True, "private": True}))
        self.server.store.trash([post["id"]])
        self.assertEqual(json.loads(self.request("POST", "/api/trash/empty", {})[2]), {"deleted": 0})
        self.assertIsNotNone(self.server.store.get(post["id"]))

    def test_library_sync_pages_and_incremental_updates(self):
        self.ready_item()
        for n in range(5):
            self.server.store.add(f"https://x.com/demo/status/{n + 100}", [])
        seen, cursor = [], 0
        while True:
            page = self.changes(cursor, 2)
            self.assertLessEqual(len(page["items"]), 2)
            self.assertEqual(page["total"], 6)
            seen.extend(item["id"] for item in page["items"])
            cursor = page["cursor"]
            if not page["has_more"]:
                break
        self.assertEqual(len(set(seen)), 6)
        self.assertEqual(len(seen), 6)
        with patch.object(self.server.store, "all", side_effect=AssertionError("no full catalog scan")):
            self.assertEqual(self.changes(cursor)["items"], [])
            self.server.store.update(VIDEO_ID, progress=42)
            delta = self.changes(cursor)
        self.assertEqual([item["id"] for item in delta["items"]], [VIDEO_ID])
        self.assertEqual(delta["items"][0]["progress"], 42)
        self.assertGreater(delta["cursor"], cursor)

    def test_library_sync_deletion_recreation_and_restart(self):
        self.ready_item()
        cursor = self.changes()["cursor"]
        self.server.store.trash([VIDEO_ID])
        delta = self.changes(cursor)
        self.assertTrue(delta["items"][0]["deleted_at"])
        cursor = delta["cursor"]
        self.server.store.delete(VIDEO_ID)
        delta = self.changes(cursor)
        self.assertEqual(delta["items"], [])
        self.assertEqual(delta["removed"], [VIDEO_ID])
        # Re-adding the same URL replaces its tombstone, even with INSERT OR IGNORE.
        cursor = delta["cursor"]
        self.server.store.add(VIDEO_URL, [])
        delta = self.changes(cursor)
        self.assertEqual([item["id"] for item in delta["items"]], [VIDEO_ID])
        self.assertEqual(delta["removed"], [])
        cursor = delta["cursor"]
        restarted = Store(self.temp.name)
        self.assertEqual(restarted.changes(cursor)["items"], [])
        self.assertEqual(restarted.changes(cursor)["cursor"], cursor)

    def test_library_sync_changes_while_paging_are_not_lost(self):
        for n in range(4):
            self.server.store.add(f"https://x.com/demo/status/{n + 100}", [])
        first = self.changes(limit=2)
        changed_id = first["items"][0]["id"]
        self.server.store.update(changed_id, note="Changed between pages")
        self.server.store.delete("x-103")
        delta = self.changes(first["cursor"])
        self.assertEqual(next(item for item in delta["items"] if item["id"] == changed_id)["note"], "Changed between pages")
        self.assertEqual(delta["removed"], ["x-103"])
        self.assertEqual(len(delta["items"]), 2)

    def test_library_sync_validation_auth_and_cursor_reset(self):
        self.ready_item()
        for query in ("since=-1", "since=no", "limit=0", "limit=201", "limit=no"):
            self.assertEqual(self.request("GET", "/api/media/changes?" + query)[0], 400)
        self.assertEqual(self.request("GET", "/api/media/changes", headers={"Cookie": ""})[0], 401)
        page = self.changes(10**12)
        self.assertTrue(page["reset"])
        self.assertEqual(len(page["items"]), 1)

    def test_errors_follow_the_interface_language(self):
        french = json.loads(self.request("POST", "/api/media", {"url": "http://localhost/x"}, {"X-Language": "fr"})[2])["error"]
        english = json.loads(self.request("POST", "/api/media", {"url": "http://localhost/x"}, {"X-Language": "en"})[2])["error"]
        self.assertEqual(french, "Utilisez l’adresse publique d’une page web, avec un nom de domaine.")
        self.assertEqual(english, "Use the public address of a web page, with a domain name.")
        browser = json.loads(self.request("POST", "/api/media", {"url": "http://localhost/x"}, {"Accept-Language": "de-DE,de;q=0.9"})[2])["error"]
        self.assertEqual(browser, english)

    def test_an_archive_keeps_the_languages_of_the_browser_that_asked(self):
        with patch.object(self.server.worker, "enqueue"):
            for url, headers, expected in (
                ("https://example.org/a", {"Accept-Language": "de-DE,de;q=0.9", "X-Language": "fr"}, "de-DE,de;q=0.9"),
                ("https://example.org/b", {"X-Language": "fr"}, "fr"),
                ("https://example.org/c", {"Accept-Language": "de\tCookie"}, None),
            ):
                status, _, body = self.request("POST", "/api/media", {"url": url}, headers)
                self.assertEqual((status, self.server.store.get(json.loads(body)["item"]["id"])["languages"]), (201, expected))
        # Stored errors are translated when served, including nested messages and parameters.
        self.server.store.add(POST_URL, [])
        self.server.store.update(f"x-{POST_ID}", status="error", error="A photo or video of the post could not be downloaded: The site refused access to the page (HTTP 403). It must be public and accessible without logging in.")
        items = json.loads(self.request("GET", "/api/media/changes", headers={"X-Language": "fr"})[2])["items"]
        item = next(item for item in items if item["id"] == f"x-{POST_ID}")
        self.assertEqual(item["error"], "Un média du post n’a pas pu être téléchargé : Le site a refusé l’accès à la page (HTTP 403). Elle doit être publique et accessible sans connexion.")
        self.assertEqual(i18n.translate("Raw yt-dlp output", "fr"), "Raw yt-dlp output")

    def test_rejects_cross_origin_mutation_and_host_rebinding(self):
        self.assertEqual(self.request("POST", "/api/media", {"url": VIDEO_URL}, {"Origin": "https://evil.test"})[0], 403)
        self.assertEqual(self.request("GET", "/api/media/changes", headers={"Host": "evil.test"})[0], 403)
        self.assertEqual(self.request("GET", "/../catalog.sqlite3")[0], 404)
        self.assertEqual(self.request("GET", "/api/media/../../file/playback")[0], 404)

    def test_login_protects_api_and_files_but_not_health_or_assets(self):
        self.ready_item()
        self.cookie = ""
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 401)
        self.assertEqual(self.request("GET", f"/api/media/{VIDEO_ID}/file/original")[0], 401)
        self.assertEqual(self.request("GET", "/api/export")[0], 401)
        self.assertEqual(self.request("PATCH", f"/api/media/{VIDEO_ID}", {"note": "x"})[0], 401)
        self.assertEqual(self.request("GET", "/health")[0], 200)
        self.assertEqual(self.request("GET", "/login.js")[0], 200)
        self.assertEqual(self.request("GET", "/login")[0], 200)
        # Pages send to the login page, and back to a shared link afterwards.
        status, headers, _ = self.request("GET", "/")
        self.assertEqual((status, headers["Location"]), (303, "/login"))
        status, headers, _ = self.request("GET", "/share?url=https://example.org/a")
        self.assertEqual(headers["Location"], "/login?next=%2Fshare%3Furl%3Dhttps%3A%2F%2Fexample.org%2Fa")
        self.assertEqual(self.request("GET", "/api/media/changes", headers={"Cookie": "archiver_session=forged"})[0], 401)

    def test_login_logout_and_credentials_sent_with_a_request_are_refused(self):
        self.cookie = ""
        state = json.loads(self.request("GET", "/api/auth")[2])
        self.assertEqual(state, {"configured": True, "signed_in": False})
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "admin", "password": "wrong password"})[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "other", "password": PASSWORD})[0], 401)
        status, headers, _ = self.request("POST", "/api/auth/login", {"username": "admin", "password": PASSWORD})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)
        self.cookie = cookie.split(";")[0]
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 200)
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertTrue(json.loads(self.request("GET", "/api/auth")[2])["signed_in"])
        # Once signed in, the login page returns to the requested page, on this site only.
        self.assertEqual(self.request("GET", "/login?next=/share?url=x")[1]["Location"], "/share?url=x")
        self.assertEqual(self.request("GET", "/login?next=//evil.test")[1]["Location"], "/")
        self.assertEqual(self.request("POST", "/api/auth/logout")[0], 200)
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 401)
        # The account's credentials sent with a request open nothing: only a session does. No browser prompt.
        good = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
        self.cookie = ""
        status, headers, _ = self.request("GET", "/api/media/changes", headers={"Authorization": "Basic " + good})
        self.assertEqual(status, 401)
        self.assertNotIn("WWW-Authenticate", headers)
        # Behind an HTTPS proxy, the cookie is restricted to HTTPS.
        cookie = self.request("POST", "/api/auth/login", {"username": "admin", "password": PASSWORD},
                              {"X-Forwarded-Proto": "https"})[1]["Set-Cookie"]
        self.assertIn("Secure", cookie)

    def test_account_is_created_once(self):
        self.server.auth.reset()
        self.cookie = ""
        self.assertEqual(json.loads(self.request("GET", "/api/auth")[2])["configured"], False)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "admin", "password": PASSWORD})[0], 409)
        self.assertEqual(self.request("POST", "/api/auth/setup", {"username": "me", "password": "short"})[0], 400)
        self.assertEqual(self.request("POST", "/api/auth/setup", {"username": " ", "password": PASSWORD})[0], 400)
        status, headers, _ = self.request("POST", "/api/auth/setup", {"username": "me", "password": PASSWORD})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 200)
        # Never a second account, even from a signed-in session.
        self.assertEqual(self.request("POST", "/api/auth/setup", {"username": "intruder", "password": PASSWORD})[0], 409)
        self.cookie = ""
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "me", "password": PASSWORD})[0], 200)
        self.assertEqual(self.request("POST", "/api/auth/setup", {"username": "x", "password": PASSWORD},
                                      {"Origin": "https://evil.test"})[0], 403)

    def test_client_outside_the_site_logs_in_for_a_token(self):
        extension = {"Origin": "chrome-extension://abcdefghijklmnop", "Sec-Fetch-Site": "cross-site", "Cookie": ""}
        credentials = {"username": "admin", "password": PASSWORD}
        # From another origin, only a login asking for a token is accepted, and it sets no cookie.
        self.assertEqual(self.request("POST", "/api/auth/login", credentials, extension)[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/login", {**credentials, "password": "wrong password", "token": True},
                                      extension)[0], 401)
        status, headers, body = self.request("POST", "/api/auth/login", {**credentials, "token": True}, extension)
        token = json.loads(body)["token"]
        self.assertEqual((status, "Set-Cookie" in headers, json.loads(body)["expires_in"]), (200, False, 365 * 24 * 3600))
        with closing(self.server.store.connect()) as db:
            expiries = sorted(row["expires_at"] for row in db.execute("SELECT expires_at FROM sessions"))
        self.assertAlmostEqual(expiries[-1] - time.time(), 365 * 24 * 3600, delta=60)
        # The token signs requests from that origin; without it, or with another, they stay refused.
        signed = {**extension, "Authorization": f"Bearer {token}"}
        self.assertEqual(self.request("POST", "/api/media", {"url": VIDEO_URL}, signed)[0], 201)
        self.assertEqual(self.request("GET", "/api/media/changes", headers=signed)[0], 200)
        self.assertEqual(self.request("POST", "/api/media", {"url": POST_URL}, extension)[0], 403)
        self.assertEqual(self.request("POST", "/api/media", {"url": POST_URL}, {**extension, "Authorization": "Bearer nope"})[0], 403)
        # The browser's cookie is still not served to another origin, with or without a wrong token.
        browser = {"Origin": "https://evil.test", "Authorization": "Bearer nope"}
        self.assertEqual(self.request("POST", "/api/media", {"url": POST_URL}, browser)[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/logout", {}, {"Origin": "https://evil.test"})[0], 403)
        # Logging the client out closes its session only: the browser's stays open, and what it unlocked too.
        unlock = json.loads(self.request("POST", "/api/private/unlock", {"password": PASSWORD})[2])["token"]
        status, headers, _ = self.request("POST", "/api/auth/logout", {}, signed)
        self.assertEqual((status, "Set-Cookie" in headers), (200, False))
        self.assertEqual(self.request("GET", "/api/media/changes", headers=signed)[0], 401)
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 200)
        self.assertTrue(self.server.auth.unlocked(unlock))
        # Changing the password closes the client's session like every other.
        token = json.loads(self.request("POST", "/api/auth/login", {**credentials, "token": True}, extension)[2])["token"]
        self.request("POST", "/api/account/password", {"current": PASSWORD, "password": PASSWORD + "!"})
        self.assertEqual(self.request("GET", "/api/media/changes", headers={"Authorization": f"Bearer {token}", "Cookie": ""})[0], 401)

    def test_article_texts_for_searching(self):
        def article(url, tags, html):
            item, _ = self.server.store.add(url, tags)
            directory = self.server.storage.directory(item["id"])
            directory.mkdir(parents=True)
            (directory / "article.html").write_text(html)
            self.server.store.update(item["id"], status="ready", files={"article.html": manifest.hash_file(directory / "article.html")})
            return item["id"]
        public = article("https://example.org/a", [], "<h2>Titre</h2><p>Un <em>mot</em>   rare</p><p>ici</p>")
        hidden = article("https://example.org/b", ["privé"], "<p>Secret</p>")
        self.assertEqual(json.loads(self.request("GET", "/api/articles/text")[2]), {"texts": {public: "Titre Un mot rare ici"}})
        texts = json.loads(self.request("GET", "/api/articles/text", headers={"X-Private-Unlock": self.unlock()})[2])["texts"]
        self.assertEqual(sorted(texts), sorted([public, hidden]))

    def test_password_change_closes_sessions_and_hides_private_archives(self):
        self.unlock()
        other = self.cookie
        self.cookie = f"archiver_session={self.server.auth.open_session()}"
        change = lambda current, new: self.request("POST", "/api/account/password", {"current": current, "password": new})
        self.assertEqual(change(PASSWORD, "short")[0], 400)
        self.assertEqual(change("wrong password", "a new long password")[0], 403)
        status, headers, _ = change(PASSWORD, "a new long password")
        self.assertEqual(status, 200)
        self.assertEqual(self.server.auth.unlocks, {})
        # The old sessions are closed; the one that changed it gets a new one.
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 401)
        self.cookie = other
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 401)
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.assertEqual(self.request("GET", "/api/media/changes")[0], 200)
        self.assertTrue(self.server.auth.check("admin", "a new long password"))
        self.assertFalse(self.server.auth.check("admin", PASSWORD))

    def test_failed_attempts_pause_logins(self):
        self.cookie = ""
        self.server.auth.throttle.limit = 3
        for _ in range(3):
            self.assertEqual(self.request("POST", "/api/auth/login", {"username": "admin", "password": "wrong password"})[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "admin", "password": PASSWORD})[0], 429)
        self.server.auth.throttle.failures.clear()
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "admin", "password": PASSWORD})[0], 200)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class PlaybackTests(unittest.TestCase):
    def test_transparent_images_get_a_white_background_in_thumbnails(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            source = Path(temp) / "logo.png"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=black@0:size=64x64,format=rgba",
                            "-frames:v", "1", str(source)], check=True)
            worker = Downloader(Store(Path(temp) / "db"), Storage(temp))
            thumbnail = worker.make_thumbnail(source, Path(temp) / "thumbnail.jpg")
            pixels = subprocess.check_output(["ffmpeg", "-nostdin", "-v", "error", "-i", str(thumbnail),
                                              "-f", "rawvideo", "-pix_fmt", "gray", "-"])
            self.assertGreater(min(pixels), 240)

    def test_audio_is_preserved_when_transcoding_or_remuxing(self):
        for codec in ("libopus", "aac"):
            with self.subTest(codec=codec), tempfile.TemporaryDirectory() as temp, patch.dict(
                os.environ, {"STORAGE_BACKEND": "local"}
            ):
                original = Path(temp) / "original.mkv"
                subprocess.run([
                    "ffmpeg", "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", codec, "-ac", "2", str(original),
                ], check=True)
                before = hashlib.sha256(original.read_bytes()).digest()
                worker = Downloader(Store(Path(temp) / "db"), Storage(temp))
                playback = worker.prepare_playback(original)
                self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), before)
                probe = subprocess.check_output([
                    "ffprobe", "-v", "error", "-select_streams", "a:0",
                    "-show_entries", "stream=codec_name,channels,duration",
                    "-of", "json", str(playback),
                ])
                audio = json.loads(probe)["streams"]
                self.assertEqual(len(audio), 1)
                self.assertEqual(audio[0]["codec_name"], "aac")
                self.assertEqual(audio[0]["channels"], 2)
                self.assertGreater(float(audio[0]["duration"]), 0.9)
                # Check decoded samples, not just the presence of an audio track.
                pcm = subprocess.check_output([
                    "ffmpeg", "-nostdin", "-v", "error", "-i", str(playback),
                    "-map", "0:a:0", "-f", "s16le", "-",
                ])
                self.assertTrue(any(pcm), "The playback audio must not be silent.")

    def test_transcoding_keeps_original_unchanged(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            original = Path(temp) / "original.mkv"
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
                "-t", "0.5", "-c:v", "ffv1", str(original),
            ], check=True)
            before = hashlib.sha256(original.read_bytes()).digest()
            worker = Downloader(Store(Path(temp) / "db"), Storage(temp))
            playback = worker.prepare_playback(original)
            self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), before)
            self.assertNotEqual(playback, original)
            self.assertTrue(playback.is_file())
            probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                             "-show_entries", "stream=codec_name,pix_fmt", "-of", "json", str(playback)])
            video = json.loads(probe)["streams"][0]
            self.assertEqual(video["codec_name"], "h264")
            self.assertEqual(video["pix_fmt"], "yuv420p")


KEY = "AGE-SECRET-KEY-1G9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9Q5ZS2PG9QSC006VW"
SIZES = (0, 1, crypto.CHUNK - 1, crypto.CHUNK, crypto.CHUNK + 1, 3 * crypto.CHUNK, 200_000)


def content(size):
    return bytes(index * 7 % 251 for index in range(size))


def stored(blob):
    """What the storage gives of an encrypted file held in memory."""
    return lambda first, last: (io.BytesIO(blob[first:last + 1]), len(blob))


class EncryptionFormatTests(unittest.TestCase):
    def setUp(self):
        self.key = crypto.Key(KEY)

    def test_any_range_is_read_from_its_chunks_only(self):
        for size in SIZES:
            data = content(size)
            blob = crypto.encrypt(data, self.key)
            if size >= 16:  # a shorter run of bytes can appear in the ciphertext by chance
                self.assertNotIn(data[:64], blob)
            self.assertEqual(crypto.size(stored(blob), self.key), size)
            self.assertEqual(crypto.reader(stored(blob), self.key).read(), data)
            for first, last in ((0, 0), (10, 20), (crypto.CHUNK - 3, crypto.CHUNK + 3), (size - 5, size + 50), (size, size + 3)):
                if first >= 0:
                    self.assertEqual(crypto.reader(stored(blob), self.key, first, last).read(), data[first:last + 1], (size, first, last))
        asked = []
        blob = crypto.encrypt(content(10 * crypto.CHUNK), self.key)

        def fetch(first, last):
            asked.append(last - first + 1)
            return stored(blob)(first, last)

        crypto.reader(fetch, self.key, 5 * crypto.CHUNK + 10, 5 * crypto.CHUNK + 20).read()
        self.assertEqual(asked, [crypto.HEAD, crypto.CHUNK + crypto.TAG])

    def test_streams_are_read_and_written_in_small_pieces(self):
        data = content(200_000)
        writer, blob = crypto.Encrypting(io.BytesIO(data), self.key), b""
        while piece := writer.read(5000):
            blob += piece
        reader, result = crypto.reader(stored(blob), self.key, 3), b""
        while piece := reader.read(7001):
            result += piece
        self.assertEqual(result, data[3:])

    def test_altered_cut_or_foreign_files_are_refused(self):
        blob = crypto.encrypt(content(100_000), self.key)
        altered = bytearray(blob)
        altered[-1] ^= 1
        with self.assertRaises(crypto.Damaged):
            crypto.reader(stored(bytes(altered)), self.key).read()
        header = bytearray(blob)
        header[30] ^= 1
        with self.assertRaises(crypto.Damaged):
            crypto.reader(stored(bytes(header)), self.key)
        # Cut at a chunk boundary: the chunk that is now the last was not written as the last.
        cut = blob[:168 + 16 + crypto.CHUNK + crypto.TAG]
        with self.assertRaises(crypto.Damaged):
            crypto.reader(stored(cut), self.key).read()
        with self.assertRaises(crypto.Damaged):
            crypto.reader(stored(blob), crypto.Key(crypto.Key.generate()))
        with self.assertRaises(crypto.Damaged):
            crypto.reader(stored(b"not an age file"), self.key)

    def test_keys_are_age_identities(self):
        self.assertTrue(self.key.recipient.startswith("age1"))
        self.assertEqual(crypto.Key(KEY.lower()).recipient, self.key.recipient)
        self.assertNotEqual(crypto.Key(crypto.Key.generate()).recipient, self.key.recipient)
        for wrong in ("", "secret", KEY[:-1] + "Q", self.key.recipient):
            with self.assertRaises(ValueError):
                crypto.Key(wrong)

    @unittest.skipUnless(shutil.which("age"), "age is not installed")
    def test_files_are_exchanged_with_the_age_tool(self):
        with tempfile.TemporaryDirectory() as temp:
            identity = Path(temp) / "key.txt"
            identity.write_text(KEY + "\n")
            for size in SIZES:
                data = content(size)
                ours = subprocess.run(["age", "-d", "-i", str(identity)], input=crypto.encrypt(data, self.key), capture_output=True, check=True)
                self.assertEqual(ours.stdout, data)
                theirs = subprocess.run(["age", "-r", self.key.recipient], input=data, capture_output=True, check=True).stdout
                self.assertEqual(crypto.reader(stored(theirs), self.key).read(), data)


class EncryptedStorageTests(unittest.TestCase):
    """A local library whose files are encrypted before they are stored."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings(KEY)
        self.store = Store(self.temp.name)

    def settings(self, key):
        self.env = patch.dict(os.environ, {"STORAGE_BACKEND": "local", "ARCHIVER_CHECK_DAYS": "0", "ENCRYPTION_KEY": key})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.storage = Storage(self.temp.name)
        self.maintenance = Maintenance(Store(self.temp.name), self.storage)

    def archive(self, url="https://example.org/article", text="<p>Bonjour</p>"):
        """Finish an archive as the download worker does."""
        item, _ = self.store.add(url, ["lecture"])
        directory = self.storage.directory(item["id"])
        directory.mkdir(parents=True)
        (directory / "article.html").write_text(text)
        (directory / "image.bin").write_bytes(content(200_000))
        Downloader(self.store, self.storage).finish(item["id"], {directory / "article.html", directory / "image.bin"}, title="Un article")
        return self.store.get(item["id"])

    def folder(self, item):
        return self.storage.directory(self.storage.folder(item))

    def test_edits_during_upload_are_in_the_recovery_record(self):
        publish = self.storage.publish

        def edit_during_upload(media_id, files):
            self.store.update(media_id, note='Edited while uploading', tags=['private'])
            return publish(media_id, files)

        with patch.object(self.storage, 'publish', side_effect=edit_during_upload):
            item = self.archive()
        with self.storage.read(item, manifest.NAME) as stream:
            record = json.load(stream)
        self.assertEqual(record['note'], item['note'])
        self.assertEqual(record['tags'], ['private'])

    def names(self, item):
        return sorted(path.name for path in self.folder(item).iterdir())

    def test_finished_archives_are_stored_encrypted_and_read_back(self):
        item = self.archive()
        self.assertEqual((item["status"], item["encrypted"]), ("ready", 1))
        self.assertEqual(self.names(item), ["archive.json.age", "article.html.age", "image.bin.age"])
        for path in self.folder(item).iterdir():
            self.assertTrue(path.read_bytes().startswith(b"age-encryption.org/v1"))
        # Nothing in the storage names the archive: its folder is a keyed hash of its id.
        self.assertEqual([path.name for path in (self.store.root / "media").iterdir()], [self.storage.folder(item)])
        self.assertRegex(self.storage.folder(item), r"^[0-9a-f]{32}$")
        self.assertNotEqual(crypto.Key(crypto.Key.generate()).folder(item["id"]), self.storage.folder(item))
        # The catalog describes the content, not its encrypted form.
        self.assertEqual(item["files"]["image.bin"]["sha256"], hashlib.sha256(content(200_000)).hexdigest())
        self.assertEqual(self.storage.list(item), ["archive.json", "article.html", "image.bin"])
        self.assertEqual(self.storage.size(item, "image.bin"), 200_000)
        self.assertEqual(self.storage.size({**item, "files": {}}, "image.bin"), 200_000)
        with self.storage.open(item, "image.bin", 70_000, 70_009) as stream:
            self.assertEqual(stream.read(), content(200_000)[70_000:70_010])
        with self.storage.read(item, "archive.json") as stream:
            self.assertEqual(json.loads(stream.read())["title"], "Un article")
        # Tags are written to the encrypted record.
        self.store.update(item["id"], tags=["lecture", "web"])
        manifest.publish(self.store, self.storage, item["id"])
        self.assertEqual(self.names(item), ["archive.json.age", "article.html.age", "image.bin.age"])
        with self.storage.read(item, "archive.json") as stream:
            self.assertEqual(json.loads(stream.read())["tags"], ["lecture", "web"])

    def test_fixity_checks_and_viewer_work_on_encrypted_files(self):
        item = self.archive()
        root = self.store.root / "media"
        (root / "index.html").write_text("left from before the key was set")
        self.maintenance.write_viewer()
        self.assertFalse((root / "index.html").exists())
        page = crypto.reader(stored((root / "index.html.age").read_bytes()), crypto.Key(KEY)).read().decode()
        self.assertIn("<p>Bonjour<\\/p>", page)
        self.maintenance.verify(item)
        self.assertEqual(self.store.get(item["id"])["integrity"], "ok")
        path = self.folder(item) / "image.bin.age"
        data = bytearray(path.read_bytes())
        data[100_000] ^= 1
        path.write_bytes(data)
        self.maintenance.verify(item)
        self.assertEqual(self.store.get(item["id"])["integrity"], "damaged")

    def test_changes_made_during_a_conversion_reach_the_record(self):
        self.settings("")
        item = self.archive()
        self.settings(KEY)
        convert = self.storage.convert

        def edited_meanwhile(item, encrypt):
            convert(item, encrypt)
            self.store.update(item["id"], note="Written meanwhile", tags=["private"])

        with patch.object(self.storage, "convert", side_effect=edited_meanwhile):
            self.assertEqual(self.maintenance.convert(True), (1, []))
        record = json.loads(self.storage.content(self.store.get(item["id"]), "archive.json"))
        self.assertEqual((record["note"], record["tags"]), ("Written meanwhile", ["private"]))

    def test_changes_made_while_the_record_is_written_reach_it(self):
        self.settings("")
        item = self.archive()
        self.settings(KEY)
        put, calls = self.storage.put, []

        def put_then_edit(item, name, data):
            put(item, name, data)
            calls.append(item["encrypted"])
            if len(calls) == 1:  # the catalog changes right after it was read for this record
                self.store.update(item["id"], note="Written meanwhile", tags=["private"])

        with patch.object(self.storage, "put", side_effect=put_then_edit):
            self.assertEqual(self.maintenance.convert(True), (1, []))
        record = json.loads(self.storage.content(self.store.get(item["id"]), "archive.json"))
        self.assertEqual((record["note"], record["tags"], calls), ("Written meanwhile", ["private"], [1, 1]))

        def put_after_conversion(item, name, data):
            calls.append(item["encrypted"])
            if len(calls) == 1:  # the archive is decrypted between the reading of the catalog and this writing
                self.storage.convert(item, False)
                self.store.update(item["id"], encrypted=0)
                self.storage.prune(self.store.get(item["id"]))
            put(item, name, data)

        calls.clear()
        with patch.object(self.storage, "put", side_effect=put_after_conversion):
            manifest.publish(self.store, self.storage, item["id"])
        self.assertEqual(calls, [1, 0])
        self.assertEqual(json.loads(self.storage.content(self.store.get(item["id"]), "archive.json"))["note"], "Written meanwhile")
        self.assertEqual(self.names(self.store.get(item["id"])), ["archive.json", "article.html", "image.bin"])
        self.assertFalse(self.folder({**item, "encrypted": 1}).exists())

    def test_conversion_reports_unstable_record_and_can_be_retried(self):
        self.settings("")
        item = self.archive()
        plain_folder = self.folder(item)
        self.settings(KEY)
        put, calls = self.storage.put, []

        def concurrent_edit(item, name, data):
            calls.append(1)
            latest = self.store.update(item["id"], note=f"Edit {len(calls)}", tags=["private"])
            # The other writer finishes first; this publication then overwrites it with its old snapshot.
            put(latest, name, manifest.render(latest))
            put(item, name, data)

        with patch.object(self.storage, "put", side_effect=concurrent_edit):
            done, failed = self.maintenance.convert(True)
        self.assertEqual(done, 0)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], item["id"])
        self.assertIn("10 attempts", failed[0][1])
        self.assertEqual(len(calls), 10)
        self.assertTrue(plain_folder.exists())  # no pruning after a failed publication
        self.assertEqual(self.store.get(item["id"])["encrypted"], 1)

        # Retrying must publish even though the files have already been converted.
        self.assertEqual(self.maintenance.convert(True), (0, []))
        record = json.loads(self.storage.content(self.store.get(item["id"]), "archive.json"))
        self.assertEqual((record["note"], record["tags"]), ("Edit 10", ["private"]))
        self.assertFalse(plain_folder.exists())

    def test_deleting_an_archive_removes_what_an_interrupted_conversion_left(self):
        self.settings("")
        item = self.archive()
        self.settings(KEY)
        self.storage.convert(item, True)  # interrupted before the archive's new form was recorded
        folders = [self.folder(item), self.folder({**item, "encrypted": 1})]
        self.assertTrue(all(folder.exists() for folder in folders))
        self.storage.remove(item)
        self.assertFalse(any(folder.exists() for folder in folders))

    def test_library_is_encrypted_and_decrypted_in_place(self):
        encrypted = self.archive()
        self.settings("")
        plain = self.archive("https://example.org/autre", "<p>Autre</p>")
        self.assertEqual(plain["encrypted"], 0)
        self.assertEqual(self.names(plain), ["archive.json", "article.html", "image.bin"])
        with self.assertRaisesRegex(ValueError, "ENCRYPTION_KEY"):
            self.storage.read(encrypted, "article.html")
        with self.assertRaisesRegex(ValueError, "ENCRYPTION_KEY"):
            self.storage.convert(plain, True)
        self.settings(KEY)
        # What an interrupted conversion leaves is finished or removed by the next one.
        unfinished = self.folder({**plain, "encrypted": 1})
        unfinished.mkdir()
        (unfinished / "article.html.age").write_bytes(b"half written")
        self.assertEqual(self.maintenance.convert(True), (1, []))
        for item in (encrypted, plain):
            item = self.store.get(item["id"])
            self.assertEqual(item["encrypted"], 1)
            self.assertEqual(self.names(item), ["archive.json.age", "article.html.age", "image.bin.age"])
            self.maintenance.verify(item)
            self.assertEqual(self.store.get(item["id"])["integrity"], "ok")
        self.assertTrue((self.store.root / "media" / "index.html.age").exists())
        self.assertEqual(self.maintenance.convert(True), (0, []))
        self.assertEqual(self.maintenance.convert(False), (2, []))
        for item in (encrypted, plain):
            item = self.store.get(item["id"])
            self.assertEqual(item["encrypted"], 0)
            self.assertEqual(self.names(item), ["archive.json", "article.html", "image.bin"])
            self.assertEqual((self.folder(item) / "image.bin").read_bytes(), content(200_000))
        self.assertEqual(sorted(path.name for path in (self.store.root / "media").iterdir()),
                         sorted([encrypted["id"], plain["id"], "index.html.age"]))

    def test_catalog_is_rebuilt_from_encrypted_records(self):
        item = self.archive()
        self.store.delete(item["id"])
        self.settings("")
        self.assertEqual(manifest.restore(self.store, self.storage), (0, 0, 1))  # unreadable without the key
        self.settings(KEY)
        # An archive caught between both forms is restored from its encrypted files.
        plain = self.storage.directory(item["id"])
        plain.mkdir()
        (plain / "archive.json").write_bytes(manifest.render({**item, "title": "Unfinished copy"}))
        self.assertEqual(manifest.restore(self.store, self.storage), (1, 1, 0))
        # A record moved to another folder is not trusted.
        moved = self.store.root / "media" / ("0" * 32)
        shutil.copytree(self.folder(item), moved)
        self.assertEqual(manifest.restore(self.store, self.storage), (0, 2, 1))
        shutil.rmtree(moved)
        restored = self.store.get(item["id"])
        self.assertEqual((restored["title"], restored["encrypted"], restored["tags"]), ("Un article", 1, ["lecture"]))
        self.maintenance.verify(restored)
        self.assertEqual(self.store.get(item["id"])["integrity"], "ok")


class EncryptedHTTPTests(ServerTestCase):
    def setUp(self):
        key = patch.dict(os.environ, {"ENCRYPTION_KEY": KEY})
        key.start()
        self.addCleanup(key.stop)
        super().setUp()

    def test_encrypted_files_are_streamed_and_exported_decrypted(self):
        item, _ = self.server.store.add(VIDEO_URL, [])
        directory = self.server.storage.directory(item["id"])
        directory.mkdir(parents=True)
        (directory / "original.mp4").write_bytes(content(200_000))
        self.server.worker.finish(item["id"], {directory / "original.mp4"}, original="original.mp4", playback="original.mp4", size=200_000)
        self.assertFalse(directory.exists())  # only the encrypted folder is left
        folder = self.server.storage.directory(self.server.storage.folder(self.server.store.get(item["id"])))
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["archive.json.age", "original.mp4.age"])
        url = f"/api/media/{VIDEO_ID}/file/playback"
        status, headers, body = self.request("GET", url, headers={"Range": "bytes=65530-65545"})
        self.assertEqual((status, headers["Content-Range"]), (206, "bytes 65530-65545/200000"))
        self.assertEqual(body, content(200_000)[65530:65546])
        status, headers, body = self.request("GET", url)
        self.assertEqual((status, headers["Content-Length"], body), (200, "200000", content(200_000)))
        self.assertEqual(self.request("HEAD", url)[1]["Content-Length"], "200000")
        self.assertEqual(self.request("GET", url, headers={"Range": "bytes=200000-"})[0], 416)
        status, _, body = self.request("GET", "/api/export?at=2026-10-25")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertIsNone(archive.testzip())
            name = next(name for name in archive.namelist() if name.endswith(f"{VIDEO_ID}/original.mp4"))
            self.assertEqual(archive.read(name), content(200_000))
            self.assertFalse([name for name in archive.namelist() if name.endswith(crypto.SUFFIX)])


RELAY_TOKEN = "r" * 40


class RelayTests(ServerTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"RELAY_TOKEN": RELAY_TOKEN})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.bearer = {"Authorization": f"Bearer {RELAY_TOKEN}", "Cookie": ""}
        self.server.store.add(VIDEO_URL, [])
        self.server.store.fail(VIDEO_ID, "blocked")
        pause = patch.object(relay, "PAUSE_SECONDS", 0)
        pause.start()
        self.addCleanup(pause.stop)
        host, port = self.server.server_address
        self.relay = relay.Relay(f"http://{host}:{port}/", RELAY_TOKEN)

    def test_token_must_be_long(self):
        with patch.dict(os.environ, {"RELAY_TOKEN": "short"}), self.assertRaises(ValueError):
            relay.token()
        with patch.dict(os.environ, {"RELAY_TOKEN": " "}):
            self.assertIsNone(relay.token())

    def test_relay_requests_need_the_token(self):
        self.assertEqual(self.request("GET", "/api/relay/links", headers={"Cookie": ""})[0], 401)
        # The account's session is not the relay's token.
        self.assertEqual(self.request("GET", "/api/relay/links")[0], 401)
        wrong = {"Authorization": "Bearer " + "x" * 40, "Cookie": ""}
        self.assertEqual(self.request("POST", f"/api/relay/links/{VIDEO_ID}", {"state": "archived"}, wrong)[0], 401)
        self.assertEqual(self.server.store.get(VIDEO_ID)["status"], "error")
        # The token opens nothing else.
        self.assertEqual(self.request("GET", "/api/media/changes", headers=self.bearer)[0], 401)
        self.server.relay_token = None
        self.assertEqual(self.request("GET", "/api/relay/links", headers=self.bearer)[0], 404)

    def test_wrong_tokens_are_throttled(self):
        wrong = {"Authorization": "Bearer " + "x" * 40, "Cookie": ""}
        for _ in range(10):
            self.assertEqual(self.request("GET", "/api/relay/links", headers=wrong)[0], 401)
        self.assertEqual(self.request("GET", "/api/relay/links", headers=self.bearer)[0], 429)

    def test_relay_takes_a_link_then_hands_it_back(self):
        status, _, body = self.request("GET", "/api/relay/links", headers=self.bearer)
        self.assertEqual((status, json.loads(body)), (200, {"links": [{"id": VIDEO_ID, "url": VIDEO_URL, "languages": None}]}))
        path = f"/api/relay/links/{VIDEO_ID}"
        self.assertEqual(self.request("POST", path, {"state": "taken"}, self.bearer)[0], 200)
        self.assertIsNone(self.server.store.get(VIDEO_ID)["retry_at"])
        self.assertEqual(self.request("POST", path, {"state": "other"}, self.bearer)[0], 404)
        self.assertEqual(self.request("POST", path, {"state": "archived"}, self.bearer)[0], 200)
        self.assertEqual(self.server.store.get(VIDEO_ID)["status"], "queued")
        self.assertEqual(self.server.worker.queue.get_nowait(), VIDEO_ID)
        # No longer failed: nothing to take or hand back.
        self.assertEqual(self.request("POST", path, {"state": "taken"}, self.bearer)[0], 409)
        self.assertEqual(self.request("POST", path, {"state": "archived"}, self.bearer)[0], 409)

    def test_relay_archives_what_the_server_could_not(self):
        with patch.object(relay, "archive", return_value={"id": VIDEO_ID, "status": "ready"}) as archive:
            self.assertEqual(self.relay.poll(), 1)
        # The relay asks the site for the languages kept with the archive (none here).
        self.assertEqual((archive.call_args.args[0], archive.call_args.args[2]), (VIDEO_URL, None))
        self.assertEqual(self.server.store.get(VIDEO_ID)["status"], "queued")
        self.assertEqual(self.server.worker.queue.get_nowait(), VIDEO_ID)
        with patch.object(relay, "archive", side_effect=AssertionError("nothing left")):
            self.assertEqual(self.relay.poll(), 0)

    def test_link_the_relay_cannot_archive_either_waits_before_another_attempt(self):
        failed = {"id": VIDEO_ID, "status": "error", "error": "Server shutting down."}
        with patch.object(relay, "archive", return_value=failed) as archive, self.assertLogs("archiver.relay", "WARNING") as logs:
            self.assertEqual(self.relay.poll(), 0)
            self.assertEqual(self.relay.poll(), 0)
            self.assertEqual(archive.call_count, 1)
            # Six hours later it is tried again, three times in all.
            for _ in range(3):
                self.relay.failures[VIDEO_ID] = (self.relay.failures[VIDEO_ID][0], time.monotonic() - relay.RETRY_SECONDS)
                self.relay.poll()
            self.assertEqual(archive.call_count, 3)
        self.assertIn("Server shutting down.", logs.output[0])
        item = self.server.store.get(VIDEO_ID)
        self.assertEqual((item["status"], item["retry_at"]), ("error", None))

    def test_unreachable_server_is_only_logged(self):
        self.relay.server = "http://127.0.0.1:1"
        with self.assertLogs("archiver.relay", "WARNING"):
            self.assertEqual(self.relay.poll(), 0)

    def test_stopped_relay_reports_nothing(self):
        def stopped(url, started, languages):
            self.relay.stopping.set()
            return {"id": VIDEO_ID, "status": "downloading"}
        with patch.object(relay, "archive", side_effect=stopped):
            self.assertEqual(self.relay.poll(), 0)
        self.assertEqual(self.relay.failures, {})
        self.assertEqual(self.server.store.get(VIDEO_ID)["status"], "error")


if __name__ == "__main__":
    unittest.main()
