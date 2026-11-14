"""Public Bilibili videos, read as a logged-out visitor from the public API behind Bilibili's player.

Bilibili answers 412 to a browser user agent unless the request also carries
fingerprint data computed by its scripts, video pages included; yt-dlp's
extractor reads the page first. Unlike the rest of Archiver, these requests
name Archiver as their client, which the API answers; the streams are then
downloaded and merged through yt-dlp."""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from .. import net, ytdlp
from . import GONE, NO_MEDIA, NO_PART, ONLINE, UNKNOWN, WRONG_LINK, Gone, Refused, code
from . import DELETED as NO_LONGER, UNAVAILABLE as NOT_SHOWN

log = logging.getLogger(__name__)

API = "https://api.bilibili.com/x"
HEADERS = {"User-Agent": "Archiver (+https://github.com/naei/archiver)", "Referer": "https://www.bilibili.com/"}
NAME = "Bilibili"
# Private, deleted, under review or for members only: Bilibili does not tell which.
UNAVAILABLE = NOT_SHOWN.format(platform=NAME)
DELETED = NO_LONGER.format(platform=NAME)
NO_STREAM = NO_MEDIA.format(platform=NAME)
ID = r"bili-(?:BV[1-9A-HJ-NP-Za-km-z]{10}|av[0-9]{1,20})(?:-p[0-9]{1,4})?"


def parse(media_id):
    """(BV or av number, part) from an archive's link id, such as bili-BV1JB4y1c73K-p44."""
    code, _, part = media_id.removeprefix("bili-").partition("-p")
    return code, int(part or 1)


def call(path, **query):
    try:
        response = net.fetch(f"{API}/{path}?{urlencode(query)}", "application/json", ("application/json",),
                             10 * 1024 * 1024, headers=HEADERS)
    except net.HTTPStatus as error:
        if error.status in (412, 429):
            raise Refused(str(error)) from None
        raise
    try:
        data = json.loads(response.body)
    except ValueError:
        raise RuntimeError(UNAVAILABLE) from None
    code = data.get("code") if isinstance(data, dict) else None
    if code == -404:
        raise Gone(DELETED)
    if code in (-352, -412):
        raise Refused(data.get("message") or str(code))
    if code != 0 or not isinstance(data.get("data"), dict):
        raise RuntimeError(UNAVAILABLE)
    return data["data"]


def video(code):
    """The public data of a video and its parts."""
    return call("web-interface/view", **({"bvid": code} if code.startswith("BV") else {"aid": code.removeprefix("av")}))


def fetch_video(code, part):
    """(video data, part data, streams) for one part of a public video."""
    view = video(code)
    page = next((page for page in view.get("pages") or [] if page.get("page") == part), None)
    if not page:
        raise RuntimeError(NO_PART.format(part=part))
    play = call("player/playurl", bvid=view["bvid"], cid=page["cid"], fnval=4048)
    return view, page, play


def formats(play):
    """The streams of a part as yt-dlp formats: separate video and audio, or a single file for older videos."""
    dash = play.get("dash") or {}
    audios = [*(dash.get("audio") or []), *((dash.get("dolby") or {}).get("audio") or []), (dash.get("flac") or {}).get("audio")]
    audios = [audio for audio in audios if audio and audio.get("baseUrl")]
    result = [{
        "format_id": f"audio-{audio.get('id')}", "url": audio["baseUrl"], "ext": "m4a",
        "vcodec": "none", "acodec": audio.get("codecs"), "tbr": (audio.get("bandwidth") or 0) / 1000 or None,
    } for audio in audios]
    result += [{
        "format_id": f"video-{stream.get('id')}-{stream.get('codecid')}", "url": stream["baseUrl"], "ext": "mp4",
        "vcodec": stream.get("codecs"), "acodec": "none" if audios else None, "quality": stream.get("id"),
        "width": stream.get("width"), "height": stream.get("height"),
        "fps": float(stream.get("frameRate") or 0) or None, "tbr": (stream.get("bandwidth") or 0) / 1000 or None,
    } for stream in dash.get("video") or [] if stream.get("baseUrl")]
    # Older videos may come as one file; a file split into segments is left aside.
    files = play.get("durl") or []
    if not result and len(files) == 1 and files[0].get("url"):
        url = files[0]["url"]
        result.append({"format_id": f"file-{play.get('quality')}", "url": url, "quality": play.get("quality"),
                       "ext": "flv" if ".flv" in url.split("?")[0] else "mp4"})
    return result


def video_details(view, page, play, url):
    """Title, author, date, description and cover of a part, with the yt-dlp result that downloads it."""
    streams = formats(play)
    if not streams:
        raise RuntimeError(NO_STREAM)
    part = page["page"]
    title = view.get("title") or view["bvid"]
    # Each part of a video in several parts has its own name, which tells it apart from the others.
    if len(view.get("pages") or []) > 1:
        title = f"{page.get('part') or f'P{part}'} – {title}"
    published = view.get("pubdate")
    cover = view.get("pic") or ""
    return {
        "title": title, "author": (view.get("owner") or {}).get("name"),
        "published_at": datetime.fromtimestamp(published, timezone.utc).isoformat() if isinstance(published, int) and published > 0 else None,
        "description": (view.get("desc") or "").strip(), "duration": page.get("duration") or view.get("duration"),
        "cover": "https://" + cover.removeprefix("http://").removeprefix("https://").removeprefix("//") if cover else None,
        "entry": {
            "id": view["bvid"] + (f"_p{part}" if part > 1 else ""), "title": title, "formats": streams,
            "http_headers": HEADERS, "extractor": "bilibili", "extractor_key": "BiliBili", "webpage_url": url,
        },
    }


def link(parsed):
    host = (parsed.hostname or "").lower()
    if host != "bilibili.com" and not host.endswith(".bilibili.com"):
        return None
    # The interface's language may prefix the path: /zh-Hans/video/….
    match = re.fullmatch(r"(?:/[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?)?/video/(?:[bB][vV]([1-9A-HJ-NP-Za-km-z]{10})|[aA][vV](\d{1,20}))/?",
                         parsed.path)
    if not match:
        raise ValueError(WRONG_LINK.format(platform=NAME))
    video_code = f"BV{match[1]}" if match[1] else f"av{match[2]}"
    # The parts of a video in several parts share its address; ?p= picks one, the first by default.
    part = parse_qs(parsed.query).get("p", [""])[0]
    part = int(part) if re.fullmatch(r"[1-9][0-9]{0,3}", part) else 1
    if part > 1:
        return f"bili-{video_code}-p{part}", f"https://www.bilibili.com/video/{video_code}/?p={part}"
    return f"bili-{video_code}", f"https://www.bilibili.com/video/{video_code}/"


def archive(item, worker):
    media_id = item["id"]
    directory = worker.storage.directory(media_id)
    directory.mkdir(parents=True, exist_ok=True)
    worker.store.update(media_id, storage=worker.storage.mode)
    view, page, play = fetch_video(*parse(code(item, "")))
    details = video_details(view, page, play, item["source_url"])
    info_path = directory / "original.info.json"
    info_path.write_text(json.dumps({"source": "bilibili", "video": view, "part": page}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    worker.store.update(media_id, **{key: details[key] for key in ("title", "author", "published_at", "description", "duration")})
    with ytdlp.Session(worker.stopping) as session:
        original = Path(session.download(details["entry"], str(directory / "original"), lambda fraction: worker.store.update(
            media_id, progress=round(100 * fraction, 1))))
    if details["cover"]:
        # Bilibili's cover rather than the first frame, which is often black.
        thumbnail = directory / "original.jpg"
        try:
            cover = worker.fetch_image({"type": "image", "source_url": details["cover"]}, directory / "cover", None,
                                       lambda url: HEADERS)
            try:
                worker.make_thumbnail(cover, thumbnail)
            finally:
                cover.unlink()
        except RuntimeError as error:
            if worker.stopping.is_set():
                raise
            thumbnail.unlink(missing_ok=True)
            log.info("Archive %s: cover skipped: %s", media_id, error)
    worker.finish_video(media_id, original, info_path)


def online(item, worker):
    try:
        video(parse(code(item, ""))[0])
    except Gone:
        return GONE
    except (RuntimeError, ValueError):
        return UNKNOWN  # includes videos hidden or under review, which Bilibili does not tell apart from deleted ones
    return ONLINE
