"""Public Mastodon posts and threads, read from the public API of the server that shows them.

Mastodon is a network of servers with any name, so a link is told by its
shape (/@account/number) rather than by its site; the server then confirms it
speaks Mastodon's API. Servers that only answer signed-in users are left alone."""
import hashlib
import json
import logging
import re
from urllib.parse import urlparse

from lxml import html

from .. import net
from . import NOT_SERVER, Final, Gone, page, post_date
from . import DELETED as NO_LONGER, UNAVAILABLE as NOT_SHOWN

log = logging.getLogger(__name__)

NAME = "Mastodon"
ID = r"masto-[0-9a-f]{16}"
# /@account/number, where the account of another server is written account@server; or the post's permanent address.
POST_PATHS = (re.compile(r"/@([\w.-]{1,64}(?:@[\w.-]{1,253})?)/([0-9]{1,25})/?"),
              re.compile(r"/users/([\w.-]{1,64})/statuses/([0-9]{1,25})/?"))
MAX_THREAD_POSTS = 100
DELETED = NO_LONGER.format(platform=NAME)
# The server only answers signed-in users.
LOGGED_IN = NOT_SHOWN.format(platform=NAME)


def link(parsed):
    host = (parsed.hostname or "").rstrip(".").lower()
    match = next((match for path in POST_PATHS if (match := path.fullmatch(parsed.path))), None)
    if not match or "." not in host:
        return None
    # A post's number is only unique on its server.
    digest = hashlib.sha256(f"{host}/{match[2]}".encode()).hexdigest()[:16]
    return f"masto-{digest}", f"https://{host}/@{match[1]}/{match[2]}"


def address(item):
    """(server, post number) from an archive's link."""
    parsed = urlparse(item["source_url"])
    match = next(match for path in POST_PATHS if (match := path.fullmatch(parsed.path)))
    return parsed.hostname, match[2]


def api(host, path):
    data = json.loads(net.fetch(f"https://{host}/api/{path}", "application/json", ("application/json",), 8 * 1024 * 1024).body)
    if not isinstance(data, dict):
        raise ValueError
    return data


def speaks_mastodon(host):
    try:
        return bool(api(host, "v1/instance"))
    except (RuntimeError, ValueError):
        return False


def fetch_status(host, status_id):
    try:
        status = api(host, f"v1/statuses/{status_id}")
    except net.HTTPStatus as error:
        if error.status in (401, 403):
            raise RuntimeError(LOGGED_IN) from None
        if error.status in (404, 410):
            # The same answer as any site without this address: ask the server what it is.
            raise (Gone(DELETED) if speaks_mastodon(host) else Final(NOT_SERVER.format(platform=NAME, host=host))) from None
        raise
    except ValueError:
        raise Final(NOT_SERVER.format(platform=NAME, host=host)) from None
    # A boost stands for the post it shares.
    status = status.get("reblog") or status
    if not isinstance(status.get("account"), dict) or not status.get("id"):
        raise Final(NOT_SERVER.format(platform=NAME, host=host))
    return status


def thread(status, context):
    """The author's self-reply chain around `status`, oldest first, from the replies the server lists around it."""
    author = status["account"].get("id")
    known = {other["id"]: other for other in [*(context.get("ancestors") or []), *(context.get("descendants") or [])]
             if isinstance(other, dict) and other.get("id") and (other.get("account") or {}).get("id") == author}
    chain = [status]
    while len(chain) < MAX_THREAD_POSTS and chain[0].get("in_reply_to_account_id") == author:
        parent = known.get(chain[0].get("in_reply_to_id"))
        if not parent:
            break
        chain.insert(0, parent)
    while len(chain) < MAX_THREAD_POSTS:
        # The earliest self-reply continues the thread; later ones are side replies.
        children = [other for other in known.values() if other.get("in_reply_to_id") == chain[-1]["id"]]
        if not children:
            break
        chain.append(min(children, key=lambda other: other.get("created_at") or ""))
    return chain


def plain(content, quoting=False):
    """The text of a post, which Mastodon gives as HTML: paragraphs, line breaks, and links in full."""
    if not (content or "").strip():
        return ""
    parts = []

    def walk(element):
        classes = set((element.get("class") or "").split())
        if not isinstance(element.tag, str) or (quoting and "quote-inline" in classes):
            pass  # a comment, or the "RE: link" line that stands for a quoted post on older servers
        elif element.tag == "br":
            parts.append("\n")
        elif element.tag == "a" and not classes & {"mention", "hashtag"} and (element.get("href") or "").startswith(("https://", "http://")):
            parts.append(element.get("href"))  # shown shortened
        else:
            parts.append(element.text or "")
            for child in element:
                walk(child)
            if element.tag in ("p", "blockquote", "li", "pre", "h1", "h2", "h3", "h4", "h5", "h6"):
                parts.append("\n\n")
        parts.append(element.tail or "")

    walk(html.fragment_fromstring(content, create_parent="div"))
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


def beside(status, host):
    """A post shown with the archived one, the one it answers or quotes."""
    details = post_details(status, host, quote=False)
    # It is linked on its own server, where it stays if this one forgets it.
    return {**{key: details[key] for key in ("author", "handle", "text", "url", "published_at", "assets")},
            **({"url": status["url"]} if re.match(r"https?://", status.get("url") or "") else {})}


def answered(status, host, missed=None):
    """The post of someone else that `status` answers, or None: it is read apart, and may be gone or hidden;
    `missed()` is then called."""
    if not status.get("in_reply_to_id") or status.get("in_reply_to_account_id") == status["account"].get("id"):
        return None
    try:
        parent = api(host, f"v1/statuses/{status['in_reply_to_id']}")
    except (RuntimeError, ValueError) as error:
        log.info("Mastodon %s: answered post unreadable: %s", status["id"], error)
        if missed:
            missed()
        return None
    return beside(parent, host) if isinstance(parent.get("account"), dict) and parent.get("id") else None


def post_details(status, host, quote=True):
    """A post in the shape the worker stores."""
    account = status["account"]
    name = account.get("acct") or account.get("username") or ""
    quoted = (status.get("quote") or {}).get("quoted_status") if quote and isinstance(status.get("quote"), dict) else None
    quoted = quoted if isinstance(quoted, dict) and isinstance(quoted.get("account"), dict) else None
    assets, others = [], []
    for medium in status.get("media_attachments") or []:
        sources = [url for url in (medium.get("url"), medium.get("remote_url")) if url]
        size = (medium.get("meta") or {}).get("original") or {}
        if sources and medium.get("type") in ("image", "video", "gifv"):
            video = medium["type"] != "image"
            assets.append({"type": "video" if video else "image", "alt_text": medium.get("description") or "",
                           "duration": size.get("duration") if video else None,
                           "width": size.get("width"), "height": size.get("height"), "source_urls": sources})
        elif sources:
            others.append(sources[0])  # a sound or another file: its address is kept with the text
    options = [f"– {option.get('title')}" for option in (status.get("poll") or {}).get("options") or [] if option.get("title")]
    # The content warning, the text, the poll's choices, then the files that are not photos or videos.
    text = "\n\n".join(part for part in (
        (status.get("spoiler_text") or "").strip(), plain(status.get("content"), quoted is not None), "\n".join(options), "\n".join(others),
    ) if part)
    return {
        "id": str(status["id"]), "url": f"https://{host}/@{name}/{status['id']}",
        "author": account.get("display_name") or account.get("username"), "handle": name if "@" in name else f"{name}@{host}",
        "published_at": post_date(status.get("created_at")), "text": text, "assets": assets,
        "quote": quoted and beside(quoted, host),
    }


def archive(item, worker):
    host, status_id = address(item)
    status = fetch_status(host, status_id)
    statuses = [status]
    if status.get("in_reply_to_account_id") == status["account"].get("id") or status.get("replies_count"):
        try:
            statuses = thread(status, api(host, f"v1/statuses/{status['id']}/context"))
        except (RuntimeError, ValueError) as error:
            if worker.stopping.is_set():
                raise
            log.info("Mastodon %s: thread unreadable, keeping the post alone: %s", item["id"], error)
            worker.warn(item, "thread")
    posts = [post_details(one, host) for one in statuses]
    if (reply_to := answered(statuses[0], host, lambda: worker.warn(item, "reply_to"))) and not worker.stopping.is_set():
        posts[0]["reply_to"] = reply_to
    worker.archive_posts(item, posts, {"source": "mastodon", "server": host, "posts": statuses}, worker.fetch_image)


def online(item, worker):
    host, status_id = address(item)
    return page(f"https://{host}/api/v1/statuses/{status_id}", "application/json")
