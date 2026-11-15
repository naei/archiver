"""Public X posts and threads, read as a logged-out visitor, with their media from X's CDN."""
import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from yt_dlp import YoutubeDL

from .. import net, proxy
from . import GONE, LIMITED, NO_MEDIA, ONLINE, UNKNOWN, UNREADABLE, WRONG_LINK, Gone, code, post_date
from . import DELETED as NO_LONGER, UNAVAILABLE as NOT_SHOWN

log = logging.getLogger(__name__)

MAX_THREAD_POSTS = 100
MAX_PAGE_FETCHES = 20
NAME = "X"
# A post X answers about without showing it: deleted, protected or for signed-in users only, it does not tell which.
UNAVAILABLE = NOT_SHOWN.format(platform=NAME)
# A post X no longer knows.
DELETED = NO_LONGER.format(platform=NAME)
ID = r"x-[0-9]{1,20}"
HOSTS = ("x.com", "www.x.com", "mobile.x.com", "m.x.com",
         "twitter.com", "www.twitter.com", "mobile.twitter.com", "m.twitter.com")


class XRedirects(HTTPRedirectHandler):
    """Follow redirects within x.com pages only; never towards another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if urlparse(req.full_url).hostname == "x.com" and target.scheme == "https" and target.hostname == "x.com":
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


def cdn_url(url, hosts=("pbs.twimg.com", "video.twimg.com")):
    """Only accept direct X media URLs; never follow arbitrary redirects."""
    try:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in hosts
                or parsed.username or parsed.password or parsed.port):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError(NO_MEDIA.format(platform=NAME))
    return url


def original_photo(url):
    parsed = urlparse(cdn_url(url, ("pbs.twimg.com",)))
    query = parse_qs(parsed.query)
    extension = Path(parsed.path).suffix.lstrip(".").lower() or query.get("format", ["jpg"])[0]
    if extension not in ("jpg", "jpeg", "png", "webp"):
        raise ValueError(NO_MEDIA.format(platform=NAME))
    path = parsed.path.removesuffix("." + extension)
    return urlunparse(parsed._replace(path=path, query=urlencode({"format": extension, "name": "orig"}))), extension


def note_tweet(post_id):
    """Full text of a long post, which the public embed data truncates.

    Uses X's guest GraphQL API through yt-dlp, which keeps its endpoint and
    parameters up to date."""
    via = proxy.current()
    with YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 30, **({"proxy": via.url} if via else {})}) as ydl:
        extractor = ydl.get_info_extractor("Twitter")
        extractor.initialize()
        data = extractor._call_graphql_api(extractor._GRAPHQL_ENDPOINT, post_id) or {}
    result = (data.get("tweetResult") or {}).get("result") or {}
    result = result.get("tweet") or result
    note = ((result.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    return note if isinstance(note.get("text"), str) and note["text"] else None


def expand_links(text, links):
    for link in links or []:
        if link.get("url") and link.get("expanded_url"):
            text = text.replace(link["url"], link["expanded_url"])
    return html.unescape(text).strip()


def post_text(post):
    """Syndication post text as shown on X: media links removed, shortened links expanded."""
    note = ((post.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    if note.get("text"):
        text, entities = note["text"], note.get("entity_set") or {}
    else:
        text, entities = post.get("full_text") or post.get("text") or "", post.get("entities") or {}
    for media in (post.get("entities") or {}).get("media") or []:
        if media.get("url"):
            text = text.replace(media["url"], "")
    if post.get("note_tweet") and not note.get("text"):
        text = text.rstrip() + " […]\n\n[Long post: X only provided an excerpt of the text.]"
    return expand_links(text, entities.get("urls"))


def media_assets(details):
    if len(details) > 4:
        raise ValueError(NO_MEDIA.format(platform=NAME))
    assets = []
    for detail in details:
        kind = detail.get("type")
        dimensions = detail.get("original_info") or {}
        asset = {
            "type": "image" if kind == "photo" else "video",
            "width": dimensions.get("width"), "height": dimensions.get("height"),
            "alt_text": detail.get("ext_alt_text") or detail.get("alt_text") or "",
            "duration": None,
        }
        if kind == "photo":
            asset["source_url"], asset["extension"] = original_photo(detail.get("media_url_https"))
        elif kind in ("video", "animated_gif"):
            info = detail.get("video_info") or {}
            variants = [v for v in info.get("variants", []) if v.get("content_type") == "video/mp4" and v.get("url")]
            if not variants:
                raise ValueError(NO_MEDIA.format(platform=NAME))
            best = max(variants, key=lambda variant: variant.get("bitrate", 0))
            asset["source_url"] = cdn_url(best["url"], ("video.twimg.com",))
            asset["extension"] = "mp4"
            asset["duration"] = info.get("duration_millis", 0) / 1000 or None
            asset["animated"] = kind == "animated_gif"
        else:
            raise ValueError(NO_MEDIA.format(platform=NAME))
        assets.append(asset)
    return assets


def quoted_assets(details, post_id):
    """A quoted post's media, as the quote's "assets"; when X offers one in an unsupported form, the quote keeps
    its text alone and is marked "partial"."""
    try:
        return {"assets": media_assets(details)}
    except ValueError as error:
        log.warning("X: media of quoted post %s skipped: %s", post_id, error)
        return {"assets": [], "partial": True}


def post_url(handle, post_id):
    if handle and re.fullmatch(r"[a-zA-Z0-9_]{1,15}", handle):
        return f"https://x.com/{handle}/status/{post_id}"
    return f"https://x.com/i/status/{post_id}"


def syndication_post(post, post_id):
    """Normalize a post from X's public embed endpoint."""
    if not isinstance(post, dict) or str(post.get("id_str")) != post_id:
        raise ValueError(UNAVAILABLE)
    user = post.get("user") or {}
    date = post_date(post.get("created_at"))
    quoted = post.get("quoted_tweet")
    quote = None
    if isinstance(quoted, dict) and quoted.get("id_str"):
        quoted_user = quoted.get("user") or {}
        quote = {"author": quoted_user.get("name"), "handle": quoted_user.get("screen_name"),
                 "text": post_text(quoted), "url": post_url(quoted_user.get("screen_name"), quoted["id_str"]),
                 **quoted_assets(quoted.get("mediaDetails") or [], quoted["id_str"])}
    return {
        "id": post_id, "user_id": user.get("id_str"), "handle": user.get("screen_name"),
        "author": user.get("name") or user.get("screen_name"), "published_at": date,
        "url": post_url(user.get("screen_name"), post_id), "text": post_text(post),
        "parent_id": post.get("in_reply_to_status_id_str"), "parent_user": post.get("in_reply_to_user_id_str"),
        "quote": quote, "assets": media_assets(post.get("mediaDetails") or []), "raw": post,
    }


def page_detail(page):
    """The TweetDetail response X embeds in the server-rendered page of a post."""
    # X writes the kind of each record as a name or, since November 2026, as a number: 0 started, 1 completed.
    started = re.search(r'kind:(?:0|"GraphQLRequestStream\.Started"),requestId:(\d+),key:"(?:[^"\\]|\\.)*",name:"TweetDetail"', page)
    if not started:
        raise ValueError(UNREADABLE.format(platform=NAME))
    completed = re.search(
        r'kind:(?:1|"GraphQLRequestStream\.Completed"),requestId:' + started[1]
        + r',key:"(?:[^"\\]|\\.)*",result:\$R\[\d+\]=\{kind:"Result\.Ok",value:("(?:[^"\\]|\\.)*")', page,
    )
    if not completed:
        raise ValueError(UNREADABLE.format(platform=NAME))
    data = json.loads(json.loads(completed[1]))
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise ValueError(UNREADABLE.format(platform=NAME))
    return data["data"]


def unwrap(result):
    if isinstance(result, dict) and result.get("__typename") == "TweetWithVisibilityResults":
        return result.get("tweet")
    return result


def page_post(raw):
    """Normalize a post from the logged-out x.com page data."""
    user = ((raw.get("core") or {}).get("user_results") or {}).get("result") or {}
    names = user.get("core") or {}
    details = raw.get("details") or {}
    note = ((raw.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
    if note.get("text"):
        text, links = note["text"], (note.get("entity_set") or {}).get("urls")
    else:
        text, links = details.get("full_text") or "", raw.get("url_entities")
    # Media and quote permalinks are t.co links without a link entity.
    known = {link.get("url") for link in links or []}
    text = re.sub(r"https://t\.co/\w+", lambda match: match[0] if match[0] in known else "", text)
    created = details.get("created_at_ms")
    quote = None
    quoted = unwrap((raw.get("quoted_tweet_results") or {}).get("result"))
    if isinstance(quoted, dict) and quoted.get("__typename") == "Tweet":
        quoted_post = page_post({**quoted, "quoted_tweet_results": None, "media_entities2": None})
        quote = {key: quoted_post[key] for key in ("author", "handle", "text", "url")}
        quote.update(quoted_assets(quoted.get("media_entities2") or [], quoted_post["id"]))
    legacy = raw.get("legacy") or {}
    return {
        "id": str(raw.get("rest_id")), "user_id": user.get("rest_id"), "handle": names.get("screen_name"),
        "author": names.get("name") or names.get("screen_name"),
        "published_at": datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if isinstance(created, (int, float)) else None,
        "url": post_url(names.get("screen_name"), raw.get("rest_id")), "text": expand_links(text, links),
        "parent_id": (raw.get("reply_to_results") or {}).get("rest_id") or legacy.get("in_reply_to_status_id_str"),
        "parent_user": (raw.get("reply_to_user_results") or {}).get("rest_id") or legacy.get("in_reply_to_user_id_str"),
        "quote": quote, "assets": media_assets(raw.get("media_entities2") or []), "raw": raw,
        "protected": bool((user.get("privacy") or {}).get("protected")),
    }


def page_posts(detail, post_id):
    """All posts of a page's conversation by id; raises if the requested post is unavailable."""
    focal = unwrap((detail.get("tweet_result_by_rest_id") or {}).get("result"))
    if not isinstance(focal, dict) or focal.get("__typename") != "Tweet":
        raise ValueError(UNAVAILABLE)
    found = {}

    def collect(node):
        if isinstance(node, dict):
            node = unwrap(node) or {}
            if node.get("__typename") == "Tweet" and node.get("rest_id") and "details" in node:
                # A post can appear several times with different fields; keep them all.
                found[str(node["rest_id"])] = {**found.get(str(node["rest_id"]), {}), **node}
            for key, value in node.items():
                if key != "quoted_tweet_results":  # quoted posts are not part of the conversation
                    collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(focal)
    collect(detail.get("threaded_conversation_with_injections_v2"))
    posts = {}
    for key, raw in found.items():
        try:
            posts[key] = page_post(raw)
        except ValueError:
            if key == post_id:
                raise
    if posts[post_id]["protected"]:
        raise ValueError(UNAVAILABLE)
    return posts


def thread(focal, known, fetch):
    """The author's self-reply chain around `focal`, oldest first.

    `known` maps post ids to posts; `fetch(post_id)` returns more posts from that
    post's page, used when X leaves part of a long thread out of a page."""
    author = focal["user_id"]
    attempted = {focal["id"]}  # `known` already comes from the focal post's page

    def load(post_id):
        if post_id not in attempted and len(attempted) < MAX_PAGE_FETCHES:
            attempted.add(post_id)
            known.update(fetch(post_id))
        return known.get(post_id)

    chain = [focal]
    while len(chain) < MAX_THREAD_POSTS and chain[0]["parent_id"] and chain[0]["parent_user"] == author:
        parent = known.get(chain[0]["parent_id"]) or load(chain[0]["parent_id"])
        if not parent or parent["user_id"] != author:
            break
        chain.insert(0, parent)
    while len(chain) < MAX_THREAD_POSTS:
        last = chain[-1]
        # The earliest self-reply continues the thread; later ones are side replies.
        children = [p for p in known.values() if p["parent_id"] == last["id"] and p["user_id"] == author]
        if children:
            chain.append(min(children, key=lambda p: int(p["id"])))
            continue
        orphans = [p for p in known.values() if p["user_id"] == author and p["parent_user"] == author
                   and int(p["id"]) > int(last["id"]) and p["parent_id"] not in known and p["parent_id"] not in attempted]
        if orphans and len(attempted) < MAX_PAGE_FETCHES:
            load(min(orphans, key=lambda p: int(p["id"]))["parent_id"])
            continue
        if last["id"] in attempted or len(attempted) >= MAX_PAGE_FETCHES:
            break
        load(last["id"])  # Its own page may list continuations cut from the first page.
    return chain


class XClient:
    def __init__(self, worker):
        self.worker = worker
        via = proxy.current()
        self.opener = build_opener(XRedirects(), *([ProxyHandler({"http": via.url, "https": via.url})] if via else []))
        self.missing = set()  # what the archive lacks, for worker.warn

    def open(self, url):
        try:
            return self.opener.open(Request(url, headers={"User-Agent": net.USER_AGENT, "Accept-Language": "en-US"}), timeout=30)
        except HTTPError as error:
            code = error.code
            error.close()
            if code == 429:
                raise net.Blocked(LIMITED.format(platform=NAME), code) from None
            raise net.HTTPStatus(f"The site refused access to the page (HTTP {code}). It must be public and accessible without logging in.", code) from None
        except (URLError, TimeoutError):
            raise RuntimeError("x.com cannot be reached. Check the connection and try again.") from None

    def read(self, url, limit):
        with self.open(url) as response:
            body = response.read(limit + 1)
        if len(body) > limit:
            raise RuntimeError(UNREADABLE.format(platform=NAME))
        return body

    def fetch_post(self, post_id):
        # Token used by X's public syndication endpoint; computed with JS number semantics.
        token = self.worker.run([
            "node", "-e",
            "console.log(((Number(process.argv[1])/1e15)*Math.PI).toString(36).replace(/(0+|\\.)/g,''))",
            post_id,
        ]).strip()
        body = self.read("https://cdn.syndication.twimg.com/tweet-result?" + urlencode({"id": post_id, "token": token}), 4 * 1024 * 1024)
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise RuntimeError(UNREADABLE.format(platform=NAME)) from None

    def fetch_page(self, handle, post_id):
        page = self.read(f"https://x.com/{handle}/status/{post_id}", 16 * 1024 * 1024).decode("utf-8", "replace")
        return page_posts(page_detail(page), post_id)

    def fetch_thread(self, post_id):
        """The post and, when it belongs to one, its whole thread. Returns (posts, raw data)."""
        try:
            embed = self.fetch_post(post_id)
        except net.HTTPStatus as error:
            if error.status == 404:
                raise Gone(DELETED) from None
            raise
        focal = syndication_post(embed, post_id)
        if focal["handle"] and re.fullmatch(r"[a-zA-Z0-9_]{1,15}", focal["handle"]):
            try:
                known = self.fetch_page(focal["handle"], post_id)
                for key in ("parent_id", "parent_user"):
                    known[post_id][key] = known[post_id][key] or focal[key]

                def more(other_id):
                    try:
                        return self.fetch_page(focal["handle"], other_id)
                    except (RuntimeError, ValueError) as error:
                        log.info("X %s: page %s skipped: %s", post_id, other_id, error)
                        self.missing.add("thread")
                        return {}

                posts = thread(known[post_id], known, more)
                raw = {"source": "x.com", "posts": [post["raw"] for post in posts]}
                # A thread that answers someone else's post is shown with it; that post comes with the page.
                parent = known.get(posts[0]["parent_id"])
                if parent and parent["user_id"] != posts[0]["user_id"]:
                    posts[0] = {**posts[0], "reply_to": {key: parent[key] for key in ("author", "handle", "text", "url", "published_at", "assets")}}
                    raw["reply_to"] = parent["raw"]
                elif posts[0]["parent_id"] and not parent:
                    self.missing.add("reply_to")
                return posts, raw
            except (RuntimeError, ValueError, KeyError, TypeError) as error:
                if self.worker.stopping.is_set():
                    raise
                log.warning("X %s: public page unreadable, falling back to the post alone: %s", post_id, error)
                # Without its page, nothing tells whether the post has a thread; the post it answers is not given.
                self.missing.add("thread")
                if focal["parent_id"] and focal["parent_user"] != focal["user_id"]:
                    self.missing.add("reply_to")
        # Fallback: the post alone, from the embed endpoint.
        if isinstance(embed.get("note_tweet"), dict):
            try:
                note = note_tweet(post_id)
            except Exception as error:
                log.warning("X %s: full text unavailable: %s", post_id, error)
                note = None
            if note:
                embed["note_tweet"]["note_tweet_results"] = {"result": note}
                focal = syndication_post(embed, post_id)
            else:
                self.missing.add("text")
        return [focal], {"source": "syndication", "posts": [embed]}

    def download(self, url, destination, on_progress):
        cdn_url(url)
        temp = destination.with_suffix(destination.suffix + ".part")
        with self.open(url) as response, temp.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                if self.worker.stopping.is_set():
                    raise RuntimeError("Server shutting down.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if total:
                    on_progress(min(downloaded / total, 1))
        if not downloaded or (total and downloaded != total):
            raise RuntimeError("The download is incomplete. Try again.")
        temp.replace(destination)


def link(parsed):
    if (parsed.hostname or "").lower() not in HOSTS:
        return None
    match = re.fullmatch(
        r"/(?:[a-zA-Z0-9_]{1,15}/status|i/web/status|i/status)/([0-9]{1,20})(?:/(?:photo|video)/[1-4])?/?",
        parsed.path,
    )
    if not match:
        raise ValueError(WRONG_LINK.format(platform=NAME))
    return f"x-{match[1]}", f"https://x.com/i/status/{match[1]}"


def archive(item, worker):
    client = XClient(worker)
    posts, raw = client.fetch_thread(code(item, "x-"))
    for post in posts:
        if (post["quote"] or {}).pop("partial", False):
            client.missing.add("quote")
    for kind in sorted(client.missing):
        worker.warn(item, kind)

    def fetch(asset, path, progress):
        path = path.with_suffix("." + asset.pop("extension"))
        client.download(asset.pop("source_url"), path, progress)
        return path

    worker.archive_posts(item, posts, raw, fetch)


def online(item, worker):
    try:
        post = XClient(worker).fetch_post(code(item, "x-"))
    except net.HTTPStatus as error:
        return GONE if error.status == 404 else UNKNOWN
    except (RuntimeError, ValueError):
        return UNKNOWN
    if not isinstance(post, dict):
        return UNKNOWN
    if post.get("__typename") == "TweetTombstone":
        return GONE
    return ONLINE if post.get("id_str") else UNKNOWN
