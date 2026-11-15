"""Public TikTok videos and photo posts, read from the post's public page like a logged-out visitor."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from .. import net
from . import EXPIRED, GONE, NO_MEDIA, ONLINE, UNKNOWN, WRONG_LINK, Final, Gone, code
from . import DELETED as NO_LONGER, UNAVAILABLE as NOT_SHOWN

NAME = "TikTok"
UNAVAILABLE = NOT_SHOWN.format(platform=NAME)
DELETED = NO_LONGER.format(platform=NAME)
ID = r"tt-[a-zA-Z0-9]{5,25}"
HOSTS = ("tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com")
PAGE_DATA = re.compile(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.*?)</script>', re.S)


def resolve(url):
    """The post id behind a short link (vm.tiktok.com, vt.tiktok.com, tiktok.com/t/…)."""
    final, response = net.open_url(url, "text/html")
    response.connection_.close()
    match = re.search(r"tiktok\.com/@[\w.-]*/(?:video|photo)/(\d+)", final)
    if not match:
        raise Final(EXPIRED.format(platform=NAME))
    return match[1]


def page_item(page, post_id):
    """The post data embedded in a TikTok page, or None when the page has none."""
    match = PAGE_DATA.search(page)
    try:
        detail = json.loads(match[1])["__DEFAULT_SCOPE__"]["webapp.video-detail"] if match else {}
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(detail, dict):
        return None
    status = detail.get("statusCode")
    if status in (10216, 10222):  # private post, private account
        raise RuntimeError(UNAVAILABLE)
    if status == 10204:  # "item doesn't exist"
        raise Gone(DELETED)
    item = ((detail.get("itemInfo") or {}).get("itemStruct")) or None
    if item and str(item.get("id")) != post_id:
        return None
    return item


def fetch_post(post_id):
    """(raw post data, cookies set by TikTok) for a public post; media URLs need those cookies."""
    for kind in ("video", "photo"):
        page = net.fetch(f"https://www.tiktok.com/@/{kind}/{post_id}", "text/html", ("text/html",), 10 * 1024 * 1024)
        item = page_item(page.body.decode("utf-8", "replace"), post_id)
        if item:
            break
    else:
        raise RuntimeError(UNAVAILABLE)
    if (item.get("author") or {}).get("privateAccount") or item.get("privateItem") or item.get("secret"):
        raise RuntimeError(UNAVAILABLE)
    return item, page.cookies


def media_headers(url, cookies):
    """Headers for a media URL: TikTok's own hosts expect the visit's cookies; other hosts get none."""
    headers = {"Referer": "https://www.tiktok.com/"}
    host = urlparse(url).hostname or ""
    if cookies and (host == "tiktok.com" or host.endswith(".tiktok.com")):
        headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
    return headers


def post_details(raw):
    """The post in the same shape as an X post: author, date, text and media to download."""
    author = raw.get("author") or {}
    handle = author.get("uniqueId")
    images = (raw.get("imagePost") or {}).get("images") or []
    if images:
        # Photo posts: each image comes in several formats; JPEG first, as browsers cannot show HEIC.
        assets = [{
            "type": "image", "alt_text": "", "duration": None,
            "width": image.get("imageWidth"), "height": image.get("imageHeight"),
            "source_urls": sorted((image.get("imageURL") or {}).get("urlList") or [], key=lambda url: "jpeg" not in url and "jpg" not in url),
        } for image in images]
        kind = "photo"
    else:
        video = raw.get("video") or {}
        # The highest bitrate first, then the default play and download addresses.
        variants = sorted(video.get("bitrateInfo") or [], key=lambda variant: -(variant.get("Bitrate") or 0))
        sources = [url for variant in variants for url in (variant.get("PlayAddr") or {}).get("UrlList") or []]
        sources += [url for url in (video.get("playAddr"), video.get("downloadAddr")) if url]
        assets = [{"type": "video", "alt_text": "", "source_urls": list(dict.fromkeys(sources)),
                   "width": video.get("width"), "height": video.get("height"), "duration": video.get("duration") or None}]
        kind = "video"
    if not all(asset["source_urls"] for asset in assets):
        raise RuntimeError(NO_MEDIA.format(platform=NAME))
    created = raw.get("createTime")
    try:
        published = datetime.fromtimestamp(int(created), timezone.utc).isoformat() if created else None
    except (TypeError, ValueError):
        published = None
    return {
        "id": str(raw["id"]), "url": f"https://www.tiktok.com/@{handle or ''}/{kind}/{raw['id']}",
        "author": author.get("nickname") or handle, "handle": handle, "published_at": published,
        "text": (raw.get("desc") or "").strip(), "quote": None, "assets": assets,
    }


def link(parsed):
    host = (parsed.hostname or "").lower()
    if host not in HOSTS:
        return None
    path = parsed.path
    short = re.fullmatch(r"/(?:t/)?([a-zA-Z0-9]{5,25})/?", path) if host in ("vm.tiktok.com", "vt.tiktok.com") or path.startswith("/t/") else None
    if short:
        return f"tt-{short[1]}", f"https://{host}/{'t/' if path.startswith('/t/') else ''}{short[1]}/"
    match = (re.fullmatch(r"/@([\w.-]{1,40})/(video|photo)/(\d{5,25})/?", path)
             or re.fullmatch(r"/()(embed)(?:/v2)?/(\d{5,25})/?", path))
    if not match:
        raise ValueError(WRONG_LINK.format(platform=NAME))
    kind = "photo" if match[2] == "photo" else "video"
    return f"tt-{match[3]}", f"https://www.tiktok.com/@{match[1]}/{kind}/{match[3]}"


def archive(item, worker):
    post_id = code(item, "tt-")
    raw, cookies = fetch_post(post_id if post_id.isdigit() else resolve(item["source_url"]))
    post = post_details(raw)

    def fetch(asset, path, progress):
        return worker.fetch_image(asset, path, progress, lambda url: media_headers(url, cookies))

    worker.archive_posts(item, [post], {"source": "tiktok", "post": raw}, fetch)


def online(item, worker):
    post_id = code(item, "tt-")
    if not post_id.isdigit():
        # A short link: the archive's link became the post's own when it was read.
        match = re.search(r"/(?:video|photo)/(\d+)", item["source_url"])
        if not match:
            return None
        post_id = match[1]
    try:
        response = net.fetch(f"https://www.tiktok.com/@/video/{post_id}", "text/html", ("text/html",), 10 * 1024 * 1024)
        found = page_item(response.body.decode("utf-8", "replace"), post_id)
    except Gone:
        return GONE
    except (RuntimeError, ValueError):
        return UNKNOWN  # includes posts that became private: TikTok then hides whether they exist
    return ONLINE if found else UNKNOWN
