"""Public Instagram posts, read as a logged-out visitor through yt-dlp's Instagram extractor.

Instagram limits anonymous visits: periodic checks would risk blocking actual
archiving, so its originals are not checked."""
import re
from datetime import datetime, timezone

from yt_dlp.extractor.instagram import InstagramIE

from .. import ytdlp
from . import NO_MEDIA, WRONG_LINK, code
from . import UNAVAILABLE as NOT_SHOWN


NAME = "Instagram"
# Private, deleted, or Instagram limiting anonymous visits for a while: it does not tell which.
UNAVAILABLE = NOT_SHOWN.format(platform=NAME)
ID = r"ig-[a-zA-Z0-9_-]{5,64}"
HOSTS = ("instagram.com", "www.instagram.com", "m.instagram.com")


def fetch_post(session, shortcode):
    """(raw post data, yt-dlp result) for a public post."""
    # Photo-only posts end with a "no video" error; the captured data is what matters.
    product, info, _ = session.capture(InstagramIE, "_extract_product", f"https://www.instagram.com/p/{shortcode}/")
    if not isinstance(product, dict) or product.get("code") != shortcode:
        raise RuntimeError(UNAVAILABLE)
    if (product.get("user") or {}).get("is_private"):
        raise RuntimeError(UNAVAILABLE)
    return product, info


def best_image(media):
    candidates = [c for c in (media.get("image_versions2") or {}).get("candidates") or [] if c.get("url")]
    # The full-size version has no size constraint (such as s640x640) in its "stp" parameter.
    full = [c for c in candidates if not re.search(r"[?&]stp=[^&]*_[sp]\d+x\d+", c["url"])]
    return (full or candidates or [{}])[0].get("url") or media.get("display_uri")


def post_details(product, info):
    """The post in the same shape as an X post: author, date, text and media to download."""
    user = product.get("user") or {}
    items = product.get("carousel_media") or [product]
    entries = (info or {}).get("entries") if (info or {}).get("_type") == "playlist" else [info]
    assets = []
    for index, media in enumerate(items):
        entry = entries[index] if entries and index < len(entries) else None
        asset = {"alt_text": media.get("accessibility_caption") or "", "duration": None,
                 "width": media.get("original_width"), "height": media.get("original_height")}
        if media.get("media_type") == 2:
            if not entry or not entry.get("formats"):
                raise RuntimeError(NO_MEDIA.format(platform=NAME))
            asset.update(type="video", entry=entry)
        else:
            source = best_image(media)
            if not source:
                raise RuntimeError(NO_MEDIA.format(platform=NAME))
            asset.update(type="image", source_url=source)
        assets.append(asset)
    taken = product.get("taken_at")
    handle = user.get("username")
    return {
        "id": product["code"], "url": f"https://www.instagram.com/p/{product['code']}/",
        "author": user.get("full_name") or handle, "handle": handle,
        "published_at": datetime.fromtimestamp(taken, timezone.utc).isoformat() if isinstance(taken, (int, float)) else None,
        "text": ((product.get("caption") or {}).get("text") or "").strip(), "quote": None, "assets": assets,
    }


def link(parsed):
    if (parsed.hostname or "").lower() not in HOSTS:
        return None
    match = re.fullmatch(r"/(?:[a-zA-Z0-9_.]{1,30}/)?(?:p|reels?|tv)/([a-zA-Z0-9_-]{5,64})/?", parsed.path)
    if not match:
        raise ValueError(WRONG_LINK.format(platform=NAME))
    return f"ig-{match[1]}", f"https://www.instagram.com/p/{match[1]}/"


def archive(item, worker):
    with ytdlp.Session(worker.stopping) as session:
        product, info = fetch_post(session, code(item, "ig-"))
        post = post_details(product, info)
        worker.archive_posts(item, [post], {"source": "instagram", "post": product}, worker.social_fetcher(session))
