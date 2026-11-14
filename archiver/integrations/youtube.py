"""Public YouTube videos, downloaded by yt-dlp."""
import re
from urllib.parse import parse_qs, urlencode, urlparse

from . import WRONG_LINK, code, page

NAME = "YouTube"
ID = r"[a-zA-Z0-9_-]{11}"
HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be")


def youtube_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(WRONG_LINK.format(platform=NAME))
    value = value.strip()
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or parsed.port:
            raise ValueError()
        host = parsed.hostname
        parts = parsed.path.strip("/").split("/")
        if host == "youtu.be" and len(parts) == 1:
            video_id = parts[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in ("shorts", "embed", "live"):
                video_id = parts[1]
            else:
                raise ValueError()
        else:
            raise ValueError()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
            raise ValueError()
    except ValueError:
        raise ValueError(WRONG_LINK.format(platform=NAME))
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


def link(parsed):
    if (parsed.hostname or "").lower() in HOSTS:
        return youtube_url(parsed.geturl())


# YouTube asks clients without a browser's network fingerprint (TLS, HTTP/2) to sign in.
VIDEO_OPTIONS = ("--impersonate", "chrome")


def archive(item, worker):
    worker.download_video(item, VIDEO_OPTIONS)


def online(item, worker):
    # oEmbed answers 404 for deleted and private videos, 401 for videos that only forbid embedding.
    return page("https://www.youtube.com/oembed?" + urlencode(
        {"format": "json", "url": f"https://www.youtube.com/watch?v={code(item, '')}"}), "application/json")
