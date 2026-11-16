"""The offline viewer: one HTML file that shows a library without the application.

It is written at the root of the storage, next to the archives' folders, and
put in every export. The catalog is embedded in the page because a page opened
from a disk (file://) cannot read a file next to it; the styles and scripts
are inlined so the page stays a single file that works for years."""
import base64
import hashlib
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

from . import i18n, manifest
from .store import is_article

log = logging.getLogger(__name__)
WEB = Path(__file__).resolve().parent.parent / "web"
# What describes an archive, without the live instance's bookkeeping (progress, check times).
FIELDS = ("id", "platform", "source_url", "title", "author", "published_at", "created_at", "duration", "width",
          "height", "description", "status", "tags", "note", "custom_title", "details", "posts", "attachments", "original",
          "playback", "thumbnail", "size", "storage_bytes", "files", "availability", "gone_since", "integrity")
# Articles already read, by (archive, sha256 of article.html): a rebuild of the viewer must not re-read them all.
_articles = {}
# Their plain text, for searching, by the same key.
_texts = {}


def article(item, storage):
    """The HTML of a web page's article, read once per version of the file."""
    key = (item["id"], item["files"].get("article.html", {}).get("sha256"))
    if key[1] and key in _articles:
        return _articles[key]
    try:
        with storage.read(item, "article.html") as stream:
            html = stream.read(10 * 1024 * 1024).decode("utf-8", "replace")
    except Exception as error:
        log.warning("Viewer: article of %s not read (%s)", item["id"], error)
        return None
    if key[1]:
        if len(_articles) > 5000:
            _articles.clear()
        _articles[key] = html
    return html


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def article_text(item, storage):
    """The plain text of a web page's article, for searching, or None if it cannot be read."""
    key = (item["id"], item["files"].get("article.html", {}).get("sha256"))
    if key[1] and key in _texts:
        return _texts[key]
    html = article(item, storage)
    if html is None:
        return None
    parser = _Text()
    parser.feed(html)
    parser.close()
    # Words of adjacent blocks stay apart; the article's own spacing does not matter for a search.
    text = " ".join(" ".join(parser.parts).split())
    if key[1]:
        if len(_texts) > 5000:
            _texts.clear()
        _texts[key] = text
    return text


def subtitle_text(item, storage):
    """What is said in a video, from its subtitle tracks, for searching; None without any."""
    tracks = [track["file"] for track in (item.get("details") or {}).get("subtitles") or [] if track.get("file")]
    if not tracks:
        return None
    key = (item["id"], tuple((item["files"].get(name) or {}).get("sha256") for name in tracks))
    if all(key[1]) and key in _texts:
        return _texts[key]
    lines = []
    for name in tracks:
        try:
            with storage.read(item, name) as stream:
                source = stream.read(10 * 1024 * 1024).decode("utf-8", "replace")
        except Exception as error:
            log.warning("Viewer: subtitles of %s not read (%s)", item["id"], error)
            return None
        for block in re.split(r"\n\s*\n", source.replace("\r", "")):
            if block.lstrip().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                continue
            for line in block.split("\n"):
                # Cue numbers and timings are left out; so are the tags that time or style words.
                line = " ".join(re.sub(r"<[^>]*>", "", line).split())
                # Automatic transcripts repeat each line as the next one scrolls in.
                if line and "-->" not in line and not line.isdigit() and (not lines or lines[-1] != line):
                    lines.append(line)
    text = " ".join(lines)
    if all(key[1]):
        if len(_texts) > 5000:
            _texts.clear()
        _texts[key] = text
    return text


def records(items, storage):
    """The catalog as the viewer and the exports carry it."""
    result = []
    for item in items:
        record = {key: item.get(key) for key in FIELDS}
        record["files"] = manifest.file_records(item["files"])
        if is_article(item) and item["status"] == "ready":
            record["article_html"] = article(item, storage)
        elif item["status"] == "ready" and (text := subtitle_text(item, storage)):
            record["subtitle_text"] = text
        result.append(record)
    return result


def build(records, exported_at, folder=""):
    """The viewer page for these records, as bytes; `folder` holds the archives' folders, relative to the page."""
    template = (WEB / "viewer.html").read_text(encoding="utf-8")
    scripts = "\n".join((i18n.interface_script(), *((WEB / name).read_text(encoding="utf-8") for name in ("render.js", "viewer.js"))))
    style = (WEB / "style.css").read_text(encoding="utf-8")
    for text in (scripts, style):
        if "</script" in text.lower() or "</style" in text.lower():
            raise ValueError("Inline code must not close its own tag")
    catalog = json.dumps({"exported_at": exported_at, "folder": folder, "items": records}, ensure_ascii=False)
    # No "<" at all: "</script" would end the block, and "<!--<script" would make the page's own script part of it.
    catalog = catalog.replace("<", "\\u003c")
    icon = "data:image/svg+xml;base64," + base64.b64encode((WEB / "favicon.svg").read_bytes()).decode()
    theme = (WEB / "theme.js").read_text(encoding="utf-8")
    # The page runs its own scripts and no other: whatever an archive's text held, it stays text.
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(
        body.replace("__ARCHIVER_THEME__", theme).replace("__ARCHIVER_SCRIPTS__", scripts).encode("utf-8")).digest()).decode() + "'"
        for body in re.findall(r"<script>(.*?)</script>", template, re.S))
    page = template.replace("__ARCHIVER_THEME__", theme).replace("__ARCHIVER_SCRIPT_HASHES__", hashes)
    for marker, value in (("__ARCHIVER_STYLE__", style), ("__ARCHIVER_SCRIPTS__", scripts),
                          ("__ARCHIVER_CATALOG__", catalog), ("__ARCHIVER_ICON__", icon)):
        page = page.replace(marker, value)
    return page.encode("utf-8")
