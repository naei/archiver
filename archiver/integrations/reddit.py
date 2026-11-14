"""Public Reddit posts and comments, read from the feed Reddit publishes for each post.

Reddit turns anonymous visitors away from its pages and its API, but still
serves each post's Atom feed, about once a minute: the title, the author, the
text as it was written and the address of the photo or video. The photos of a
gallery are listed by the page Reddit offers for embedding a post elsewhere.
A link to a comment keeps that comment, with the post it was written under.
That pace is too slow for periodic checks, so Reddit's originals are not checked."""
import html
import re
from urllib.parse import urlparse

from lxml import etree
from lxml import html as lxml_html

from .. import net, ytdlp
from . import EXPIRED, LIMITED, NO_MEDIA, UNREADABLE, WRONG_LINK, Gone
from . import DELETED as NO_LONGER, UNAVAILABLE as NOT_SHOWN

NAME = "Reddit"
ID = r"rdt-[a-z0-9]{1,10}(?:-[a-z0-9]{1,10})?|rdts-[a-zA-Z0-9]{4,24}"
HOSTS = ("reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "m.reddit.com", "redd.it")
# A post under a community or a profile, or alone; then, for a comment, the post's title and the comment's id.
POST_PATH = re.compile(r"(?:/(?:r|u|user)/[\w.-]{1,40})?/comments/([a-z0-9]{1,10})(?:/[^/]*(?:/([a-z0-9]{1,10}))?)?/?")
GALLERY_PATH = re.compile(r"/gallery/([a-z0-9]{1,10})/?")
SHARE_PATH = re.compile(r"/(r|u|user)/([\w.-]{1,40})/s/([a-zA-Z0-9]{4,24})/?")
ATOM = {"a": "http://www.w3.org/2005/Atom"}
# Photos are shown through resized previews named after the original file, which i.redd.it serves untouched.
PREVIEW = re.compile(r"https://preview\.redd\.it/(?:[^\"'\s?]*-v0-)?([a-z0-9]+)\.(jpe?g|png|gif|webp)\?[^\"'\s<>]*")
WAIT_SECONDS = 65
DELETED = NO_LONGER.format(platform=NAME)
# A private or restricted community.
UNAVAILABLE = NOT_SHOWN.format(platform=NAME)


def post_ids(path):
    """(post id, comment id or None) from the path of a link to a post or to one of its comments, or None."""
    if match := POST_PATH.fullmatch(path):
        return match[1], match[2]
    if match := GALLERY_PATH.fullmatch(path):
        return match[1], None
    return None


def link(parsed):
    host = (parsed.hostname or "").lower()
    if host not in HOSTS:
        return None
    if host == "redd.it":
        found = (match[1], None) if (match := re.fullmatch(r"/([a-z0-9]{1,10})/?", parsed.path)) else None
    else:
        found = post_ids(parsed.path)
    if found:
        post_id, comment_id = found
        if comment_id:
            return f"rdt-{post_id}-{comment_id}", f"https://www.reddit.com/comments/{post_id}/_/{comment_id}/"
        return f"rdt-{post_id}", f"https://www.reddit.com/comments/{post_id}/"
    # The link the apps share, which only Reddit can turn into a post's address.
    if host != "redd.it" and (match := SHARE_PATH.fullmatch(parsed.path)):
        return f"rdts-{match[3]}", f"https://www.reddit.com/{match[1]}/{match[2]}/s/{match[3]}"
    raise ValueError(WRONG_LINK.format(platform=NAME))


def address(item):
    """(post id, comment id or None) of an archive's link; a share link is followed to its post."""
    path = urlparse(item["source_url"]).path
    if SHARE_PATH.fullmatch(path):
        final, response = net.open_url(item["source_url"], "text/html")
        response.connection_.close()
        path = urlparse(final).path
    found = post_ids(path)
    if not found:
        raise RuntimeError(EXPIRED.format(platform=NAME))
    return found


def fetch_feed(post_id, comment_id, stopping):
    """The Atom feed of a post, or of one of its comments: the post's entry, then the comment's."""
    url = f"https://www.reddit.com/comments/{post_id}/{f'_/{comment_id}/' if comment_id else ''}.rss?limit=1"
    for attempt in range(3):
        try:
            return net.fetch(url, "application/atom+xml", ("application/atom+xml", "application/xml", "text/xml"), 8 * 1024 * 1024).body
        except net.HTTPStatus as error:
            if error.status == 404:
                raise Gone(DELETED) from None
            if error.status == 403:
                raise RuntimeError(UNAVAILABLE) from None
            if error.status != 429:
                raise
            # One anonymous visit a minute: wait for the next one, twice at most.
            if attempt == 2 or stopping.wait(WAIT_SECONDS):
                raise RuntimeError(LIMITED.format(platform=NAME)) from None
        except ValueError:
            raise RuntimeError(UNREADABLE.format(platform=NAME)) from None


def plain(element):
    """The text of a post's body, which the feed gives as HTML: paragraphs, lists, and links with their address."""
    parts = []

    def walk(node):
        if not isinstance(node.tag, str):
            pass  # a comment
        elif node.tag == "br":
            parts.append("\n")
        elif node.tag == "a" and (node.get("href") or "").startswith(("https://", "http://")):
            shown, href = " ".join(node.text_content().split()), node.get("href")
            parts.append(href if not shown or shown.rstrip("/") == href.rstrip("/") else f"{shown} ({href})")
        else:
            parts.append("– " if node.tag == "li" else "")
            parts.append(node.text or "")
            for child in node:
                walk(child)
            if node.tag in ("p", "blockquote", "pre", "ul", "ol", "table", "hr", "h1", "h2", "h3", "h4", "h5", "h6"):
                parts.append("\n\n")
            elif node.tag in ("li", "tr"):
                parts.append("\n")
        parts.append(node.tail or "")

    walk(element)
    # Spaces around a line break are the feed's own layout, not the text's.
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]*\n[ \t]*", "\n", "".join(parts))).strip()


def read_entries(feed):
    """The feed's entries as dictionaries: kind ("post" or "comment"), id, author, community, title, date, link, text, target."""
    try:
        root = etree.fromstring(feed, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        raise RuntimeError(UNREADABLE.format(platform=NAME)) from None
    entries = []
    for entry in root.findall("a:entry", ATOM):
        def field(path, attribute=None):
            found = entry.find(path, ATOM)
            return None if found is None else found.get(attribute) if attribute else (found.text or "").strip()

        name, kind, _, code = field("a:author/a:name") or "", *(field("a:id") or "").partition("_")
        content = lxml_html.fragment_fromstring(field("a:content") or "", create_parent="div")
        body = content.find(".//div[@class='md']")
        # The line under the text: "submitted by … [link] [comments]", where [link] is what the post shows.
        target = next((anchor.get("href") for anchor in content.iter("a") if (anchor.text or "").strip() == "[link]"), None)
        entries.append({
            "kind": {"t3": "post", "t1": "comment"}.get(kind), "id": code, "author": name.removeprefix("/") or None,
            "community": field("a:category", "label"), "title": html.unescape(field("a:title") or ""),
            "published_at": field("a:published") or field("a:updated"), "url": field("a:link", "href"),
            "text": plain(body) if body is not None else "", "target": target,
        })
    return entries


def embed(url):
    """The page Reddit offers to embed a post elsewhere."""
    page = net.fetch(f"https://embed.reddit.com{urlparse(url).path}?embed=true", "text/html", ("text/html",), 10 * 1024 * 1024,
                     headers={"Accept-Language": "en-US,en;q=0.9"})
    return page.body.decode("utf-8", "replace")


def gallery(page):
    """The photos of a gallery post, from its embed page."""
    photos = {}
    for match in PREVIEW.finditer(html.unescape(page)):
        width = int((re.search(r"[?&]width=(\d+)", match[0]) or [0, 0])[1])
        if width >= photos.get(match[1], (0,))[0]:
            photos[match[1]] = (width, match[2], match[0])
    if not photos:
        raise RuntimeError(NO_MEDIA.format(platform=NAME))
    return [{"type": "image", "alt_text": "", "duration": None, "width": None, "height": None,
             "source_urls": [f"https://i.redd.it/{name}.{extension}", preview]} for name, (_, extension, preview) in photos.items()]


def post_details(entry, photos):
    """A post in the shape the worker stores; `photos()` lists those of a gallery."""
    if entry["author"] == "u/[deleted]" and entry["text"] in ("[deleted]", "[removed]", ""):
        raise Gone(DELETED)
    target = urlparse(entry["target"] or "")
    assets, text = [], entry["text"]
    asset = {"alt_text": "", "duration": None, "width": None, "height": None}
    if target.hostname == "i.redd.it":
        assets = [{**asset, "type": "image", "source_urls": [entry["target"]]}]
    elif target.hostname == "v.redd.it" and (video := re.fullmatch(r"/([a-z0-9]{5,20})/?", target.path)):
        # yt-dlp reads the list of the video's qualities and merges the best picture with its sound.
        assets = [{**asset, "type": "video", "entry": {
            "_type": "url", "url": f"https://v.redd.it/{video[1]}/DASHPlaylist.mpd", "ie_key": "Generic"}}]
    elif GALLERY_PATH.fullmatch(target.path) and (target.hostname or "").endswith("reddit.com"):
        assets = photos()
    elif entry["target"] and post_ids(target.path) != post_ids(urlparse(entry["url"] or "").path):
        # A post that shares a link: the link is its content.
        text = f"{text}\n\n{entry['target']}".strip()
    return {
        "id": entry["id"], "url": entry["url"], "title": entry["title"],
        "author": " · ".join(part for part in (entry["author"], entry["community"]) if part) or None, "handle": None,
        "published_at": entry["published_at"], "text": text, "quote": None, "assets": assets,
    }


def archive(item, worker):
    post_id, comment_id = address(item)
    feed = fetch_feed(post_id, comment_id, worker.stopping)
    entries = read_entries(feed)
    post = next((entry for entry in entries if entry["kind"] == "post" and entry["id"] == post_id), None)
    comment = next((entry for entry in entries if entry["kind"] == "comment" and entry["id"] == comment_id), None)
    if not post or (comment_id and not comment):
        raise Gone(DELETED)
    details = post_details(post, lambda: gallery(embed(post["url"])))
    if comment:
        if comment["text"] in ("[deleted]", "[removed]"):
            raise Gone(DELETED)
        # The comment is the archive; the post it was written under is shown above it.
        under = {**{key: details[key] for key in ("author", "handle", "url", "published_at", "assets")},
                 "text": f"{details['title']}\n\n{details['text']}".strip()}
        details = {"id": comment["id"], "url": comment["url"], "author": comment["author"], "handle": None,
                   "published_at": comment["published_at"], "text": comment["text"], "quote": None, "assets": [], "reply_to": under}
    with ytdlp.Session(worker.stopping) as session:
        worker.archive_posts(item, [details], {"source": "reddit", "feed": feed.decode("utf-8", "replace")}, worker.social_fetcher(session))
