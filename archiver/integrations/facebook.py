"""Public Facebook videos and reels, downloaded by yt-dlp.

Facebook refuses anonymous visits too often for periodic checks: they would
risk blocking actual archiving, so its originals are not checked."""
import re
from urllib.parse import parse_qs

from . import WRONG_LINK

NAME = "Facebook"
ID = r"fb-[a-zA-Z0-9_-]{4,40}"
HOSTS = ("facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com", "fb.watch")


def link(parsed):
    host = (parsed.hostname or "").lower()
    if host not in HOSTS:
        return None
    path, query = parsed.path, parse_qs(parsed.query)
    if host == "fb.watch":
        match = re.fullmatch(r"/([a-zA-Z0-9_-]{4,40})/?", path)
        if match:
            return f"fb-{match[1]}", f"https://fb.watch/{match[1]}/"
    elif match := re.fullmatch(r"/share/([vr])/([a-zA-Z0-9_-]{4,40})/?", path):
        return f"fb-{match[2]}", f"https://www.facebook.com/share/{match[1]}/{match[2]}/"
    elif match := re.fullmatch(r"/reel/(\d{5,25})/?", path):
        return f"fb-{match[1]}", f"https://www.facebook.com/reel/{match[1]}"
    else:
        video = None
        if path.rstrip("/") in ("/watch", "/video.php"):
            video = query.get("v", [""])[0]
        elif match := re.fullmatch(r"/[\w.-]{1,80}/videos/(?:vb\.\d+/)?(\d{5,25})/?", path):
            video = match[1]
        if video and re.fullmatch(r"\d{5,25}", video):
            return f"fb-{video}", f"https://www.facebook.com/watch/?v={video}"
    # Only public videos and reels: posts, photos and groups cannot be read without logging in.
    raise ValueError(WRONG_LINK.format(platform=NAME))


VIDEO_OPTIONS = ()


def archive(item, worker):
    worker.download_video(item, VIDEO_OPTIONS)
