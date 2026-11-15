"""Public Bluesky posts and threads, read from Bluesky's public API.

Photos and videos are taken from the author's own data server, which keeps
them as they were uploaded; Bluesky's image CDN, which serves resized copies,
is the fallback."""
import hashlib
import json
import re
from functools import cache
from urllib.parse import unquote, urlencode, urlparse

from .. import net
from . import GONE, LIMITED, NO_MEDIA, ONLINE, UNKNOWN, UNREADABLE, WRONG_LINK, Gone, post_date
from . import DELETED as NO_LONGER, UNAVAILABLE as NOT_SHOWN

NAME = "Bluesky"
ID = r"bsky-(?:[a-z2-7]{13}|[0-9a-f]{16})"
HOSTS = ("bsky.app", "www.bsky.app")
API = "https://public.api.bsky.app/xrpc"
# /profile/<handle or account identifier>/post/<record key>
POST_PATH = re.compile(r"/profile/([a-zA-Z0-9.:%_-]{3,253})/post/([a-zA-Z0-9._~-]{1,64})/?")
MAX_THREAD_POSTS = 100
MAX_THREAD_FETCHES = 10
DELETED = NO_LONGER.format(platform=NAME)
# The post is hidden by a block between accounts.
UNAVAILABLE = NOT_SHOWN.format(platform=NAME)
# The server that keeps the account's files could not be found, and a video has no copy elsewhere.
NO_SERVER = NO_MEDIA.format(platform=NAME)


def link(parsed):
    if (parsed.hostname or "").lower() not in HOSTS:
        return None
    match = POST_PATH.fullmatch(parsed.path)
    if not match:
        raise ValueError(WRONG_LINK.format(platform=NAME))
    actor, key = unquote(match[1]), match[2]
    if not POST_PATH.fullmatch(f"/profile/{actor}/post/{key}"):
        raise ValueError(WRONG_LINK.format(platform=NAME))
    # A post's key is the instant it was written, to the microsecond, with a random part: it names the post
    # whether its link gives the account's handle or its permanent identifier. A key of another kind, which
    # two accounts may share, is joined to the account.
    if not re.fullmatch(r"[a-z2-7]{13}", key):
        key = hashlib.sha256(f"{actor.lower()}/{key}".encode()).hexdigest()[:16]
    return f"bsky-{key}", post_url(actor, match[2])


def post_url(actor, key):
    return f"https://bsky.app/profile/{actor}/post/{key}"


def address(item):
    """(account, record key) from an archive's link, or None."""
    match = POST_PATH.fullmatch(urlparse(item["source_url"]).path)
    return (unquote(match[1]), match[2]) if match else None


def call(method, **query):
    try:
        response = net.fetch(f"{API}/{method}?{urlencode(query)}", "application/json", ("application/json",), 16 * 1024 * 1024)
    except net.Blocked as error:
        if error.status == 429:
            raise RuntimeError(LIMITED.format(platform=NAME)) from None
        raise
    try:
        data = json.loads(response.body)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise RuntimeError(UNREADABLE.format(platform=NAME))
    return data


def is_post(node):
    return isinstance(node, dict) and node.get("$type") == "app.bsky.feed.defs#threadViewPost" and isinstance(node.get("post"), dict)


def fetch_thread(actor, key):
    """(posts, answered post): the post and, when it belongs to one, its author's whole thread, oldest first,
    then the post of someone else that the first one answers, if any; all as the API describes them."""
    def load(uri, parents):
        try:
            return call("app.bsky.feed.getPostThread", uri=uri, depth=10, parentHeight=parents).get("thread")
        except net.HTTPStatus as error:
            if error.status == 400:  # "Post not found"
                raise Gone(DELETED) from None
            raise

    focal = load(f"at://{actor}/app.bsky.feed.post/{key}", 80)
    if not is_post(focal):
        if isinstance(focal, dict) and focal.get("$type") == "app.bsky.feed.defs#notFoundPost":
            raise Gone(DELETED)
        raise RuntimeError(UNAVAILABLE)
    author = focal["post"].get("author") or {}
    chain, node = [focal["post"]], focal
    while len(chain) < MAX_THREAD_POSTS and is_post(node.get("parent")) and node["parent"]["post"]["author"]["did"] == author["did"]:
        node = node["parent"]
        chain.insert(0, node["post"])
    answered = node["parent"]["post"] if is_post(node.get("parent")) else None
    node, loaded = focal, {focal["post"]["uri"]}
    while len(chain) < MAX_THREAD_POSTS:
        replies = [reply for reply in node.get("replies") or [] if is_post(reply) and reply["post"]["author"]["did"] == author["did"]]
        if replies:
            # The earliest self-reply continues the thread; later ones are side replies.
            node = min(replies, key=lambda reply: reply["post"].get("indexedAt") or "")
            chain.append(node["post"])
            continue
        # Replies deeper than what was asked for are left out of an answer: ask again from the last post.
        uri = node["post"]["uri"]
        if uri in loaded or not node["post"].get("replyCount") or len(loaded) > MAX_THREAD_FETCHES:
            break
        loaded.add(uri)
        node = load(uri, 0)
        if not is_post(node):
            break
    return chain, answered


def data_server(did):
    """The server that keeps an account's files, named in the account's public identity document; None if unknown."""
    if did.startswith("did:plc:"):
        url = f"https://plc.directory/{did}"
    elif did.startswith("did:web:"):
        url = f"https://{unquote(did.removeprefix('did:web:'))}/.well-known/did.json"
    else:
        return None
    try:
        document = json.loads(net.fetch(url, "application/json", ("application/",), 1024 * 1024).body)
        endpoint = next(service["serviceEndpoint"] for service in document["service"] if str(service["id"]).endswith("#atproto_pds"))
    except (RuntimeError, ValueError, KeyError, TypeError, StopIteration):
        return None
    return endpoint.rstrip("/") if isinstance(endpoint, str) and endpoint.startswith("https://") else None


def post_text(record):
    """A post's text with its links in full: Bluesky shows them shortened and keeps their address apart."""
    data = (record.get("text") or "").encode()
    links = []
    for facet in record.get("facets") or []:
        uri = next((feature.get("uri") for feature in facet.get("features") or []
                    if feature.get("$type") == "app.bsky.richtext.facet#link" and feature.get("uri")), None)
        start, end = (facet.get("index") or {}).get("byteStart"), (facet.get("index") or {}).get("byteEnd")
        if uri and isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(data):
            links.append((start, end, uri))
    for start, end, uri in sorted(links, reverse=True):
        shown = data[start:end].decode("utf-8", "replace")
        # A shortened address is replaced; words that carry a link keep it next to them.
        full = uri if shown.rstrip(".…") in uri else f"{shown} ({uri})"
        data = data[:start] + full.encode() + data[end:]
    text = data.decode("utf-8", "replace").strip()
    # A link shown as a card may not be in the text.
    card = ((record.get("embed") or {}).get("external") or {}).get("uri")
    if card and card not in text:
        text = f"{text}\n\n{card}".strip()
    return text


def media_assets(embed, did, servers):
    """The photos and videos attached to a post, from what its record says of them."""
    embed = embed or {}
    if embed.get("$type") == "app.bsky.embed.recordWithMedia":
        embed = embed.get("media") or {}

    def asset(kind, blob, described, fallback):
        # Older posts name a file by "cid"; newer ones by a "ref" link.
        cid = ((blob.get("ref") or {}).get("$link") or blob.get("cid")) if isinstance(blob, dict) else None
        if not cid:
            return None
        server = servers(did)
        sources = [f"{server}/xrpc/com.atproto.sync.getBlob?" + urlencode({"did": did, "cid": cid})] if server else []
        sources += [url.format(did=did, cid=cid) for url in fallback]
        if not sources:
            raise RuntimeError(NO_SERVER)
        ratio = described.get("aspectRatio") or {}
        return {"type": kind, "alt_text": described.get("alt") or "", "duration": None,
                "width": ratio.get("width"), "height": ratio.get("height"), "source_urls": sources}

    # Up to four photos come as "images"; more as the "items" of a gallery.
    images = [image for image in embed.get("images") or embed.get("items") or [] if isinstance(image, dict)]
    assets = [asset("image", image.get("image"), image, ["https://cdn.bsky.app/img/feed_fullsize/plain/{did}/{cid}@jpeg"]) for image in images]
    assets.append(asset("video", embed.get("video"), embed, []))
    return [found for found in assets if found]


def quoted(embed, servers):
    """The post quoted by a post, from the copy of it the API attaches; None for anything else (a feed, a list, a hidden post)."""
    record = (embed or {}).get("record") or {}
    if (embed or {}).get("$type") == "app.bsky.embed.recordWithMedia#view":
        record = record.get("record") or {}
    value, author = record.get("value") or {}, record.get("author") or {}
    if (record.get("$type") != "app.bsky.embed.record#viewRecord" or value.get("$type") != "app.bsky.feed.post"
            or not author.get("did")):
        return None
    return {
        "author": author.get("displayName") or author.get("handle"), "handle": author.get("handle"),
        "text": post_text(value), "url": post_url(author["did"], record["uri"].rsplit("/", 1)[1]),
        "published_at": post_date(value.get("createdAt")), "assets": media_assets(value.get("embed"), author["did"], servers),
    }


def post_details(view, servers):
    """A post in the shape the worker stores; `servers(did)` gives an account's data server."""
    author, record = view.get("author") or {}, view.get("record") or {}
    key = view["uri"].rsplit("/", 1)[1]
    return {
        # The account's permanent identifier rather than its handle, which its owner can change.
        "id": key, "url": post_url(author["did"], key),
        "author": author.get("displayName") or author.get("handle"), "handle": author.get("handle"),
        "published_at": post_date(record.get("createdAt")), "text": post_text(record),
        "quote": quoted(view.get("embed"), servers), "assets": media_assets(record.get("embed"), author["did"], servers),
    }


def archive(item, worker):
    views, answered = fetch_thread(*address(item))
    servers = cache(data_server)
    posts = [post_details(view, servers) for view in views]
    if answered:
        details = post_details(answered, servers)
        posts[0]["reply_to"] = {key: details[key] for key in ("author", "handle", "text", "url", "published_at", "assets")}
    worker.archive_posts(item, posts, {"source": "bluesky", "posts": views, "answered": answered}, worker.fetch_image)


def online(item, worker):
    actor, key = address(item) or (None, None)
    if not actor or not actor.startswith("did:"):
        return UNKNOWN  # a handle may have changed hands: only the account's identifier tells
    try:
        posts = call("app.bsky.feed.getPosts", uris=f"at://{actor}/app.bsky.feed.post/{key}").get("posts")
    except (RuntimeError, ValueError):
        return UNKNOWN
    return ONLINE if posts else GONE if posts == [] else UNKNOWN
