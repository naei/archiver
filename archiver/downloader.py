import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from . import integrations, manifest, net, proxy, web, ytdlp
from .integrations import Final
from .store import now

log = logging.getLogger(__name__)


def excerpt(text, length=160):
    text = " ".join(text.split())
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0] if " " in text[:length] else text[:length]
    return cut.rstrip(" ,;:.…-–—") + "…"


def file_name(url, response):
    """The name a server gives a file, or the last part of its address."""
    header = Message()
    header["Content-Disposition"] = response.getheader("Content-Disposition") or ""
    try:
        name = header.get_filename()
    except (TypeError, ValueError):
        name = None
    name = PurePosixPath(name or unquote(urlparse(url).path)).name
    return excerpt(name, 200) or urlparse(url).hostname


def file_date(value):
    """The day in a date written inside a file; a bare year or a date before the web is not one."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    try:
        return date(*map(int, match.groups())).isoformat() if match and int(match[1]) >= 1990 else None
    except ValueError:
        return None


def http_date(value):
    try:
        return parsedate_to_datetime(value).date().isoformat() if value else None
    except (TypeError, ValueError):
        return None


AGE_RESTRICTED = ("{platform} only shows this video to signed-in users (age restriction): "
                  "it cannot be archived without logging in.")
BOT_CHECK = ("{platform} is currently refusing this anonymous download, which it takes for a bot. "
             "A relay on your computer can archive it instead (see “Home relay” in the README).")
# With a proxy configured, a failure says which way the job went.
TRIED_DIRECTLY = "Tried directly, without the proxy: {error}"
TRIED_PROXY = "Tried through the proxy {proxy}: {error}"
TRIED_BOTH = "Tried directly, then through the proxy {proxy}: {error}"
RETRY_CHECK_SECONDS = 30  # how often the worker looks for automatic retries that are due
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/avif": "avif"}
VIDEO_TYPES = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv",
               "video/x-m4v": "m4v", "video/ogg": "ogv"}
AUDIO_TYPES = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac", "audio/ogg": "ogg",
               "audio/opus": "opus", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/flac": "flac",
               "audio/x-flac": "flac", "audio/webm": "weba"}
PDF_TYPE = "application/pdf"
# A link straight to an image, a video, a sound or a PDF is archived as that file, rather than as an article.
FILE_TYPES = {**IMAGE_TYPES, **VIDEO_TYPES, **AUDIO_TYPES, PDF_TYPE: "pdf"}
FILE_EXTENSIONS = {"jpeg": "image/jpeg", **{extension: kind for kind, extension in reversed(FILE_TYPES.items())}}
# The sound formats every browser plays; the others get a playback copy.
PLAYABLE_AUDIO = ("mp3", "m4a", "aac", "wav", "flac")
# What servers send for files they do not describe: the link's extension then tells the type.
UNDESCRIBED_TYPES = ("", "application/octet-stream", "binary/octet-stream", "application/binary")
PAGE_TYPES = ("text/html", "application/xhtml+xml")
MAX_PAGE_BYTES = 10 * 1024 * 1024
# The files of a page's video that are compared: its qualities, not a whole catalogue.
MAX_PAGE_VIDEOS = 8
# Subtitle tracks kept with a video: its author's, in at most this many languages.
MAX_SUBTITLES = 12
MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024


# What an archive may lack without failing, kept in its details as `warnings` and named by the interface:
# posts of its thread, the post it answers, media of the post it quotes, the end of a long text, subtitles,
# images of a page.
WARNINGS = ("thread", "reply_to", "quote", "text", "subtitles", "images")


class MissingTools(RuntimeError):
    """This computer lacks a tool the archive needs: another one, such as a relay, may have it."""


# What another address would not change: deleted content, a site that no longer exists, a tool missing here.
FINAL = (Final, net.UnknownHost, MissingTools)


def dependencies():
    return {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "node")}


def subtitle_choices(info):
    """The subtitle tracks to keep, from what yt-dlp read of a video: (key, language, name, automatic) for each.

    The tracks written by the author, the video's own language first. When none is in that language, the
    automatic transcript of what is said; its automatic translations, dozens per video, are left aside."""
    def tracks(kind):
        found = info.get(kind) if isinstance(info.get(kind), dict) else {}
        # A name that is a language code: this leaves out "live_chat", the replay of a live stream's chat.
        return {key: value for key, value in found.items() if value and re.fullmatch(r"[A-Za-z0-9-]{1,20}", key)}

    def language(key):
        # A platform may add a mark of its own to a track's language ("en-nP7-2PuUl7o"): the language and, when
        # it is one, the script or country are kept.
        code = key.removesuffix("-orig")
        return code if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z]{4})?(-([A-Za-z]{2}|[0-9]{3}))?", code) else code.split("-")[0]

    def name(track):
        return next((entry.get("name") for entry in track if isinstance(entry, dict) and entry.get("name")), None)

    written, automatic = tracks("subtitles"), tracks("automatic_captions")
    # yt-dlp names the transcript "<language>-orig" when the video also has automatic translations. It tells the
    # language spoken better than the language the video declares, which its author may have set wrongly.
    transcript = next((key for key in automatic if key.endswith("-orig")), None)
    spoken = (transcript.removesuffix("-orig") if transcript else info.get("language") or "").split("-")[0].lower()
    own = lambda key: bool(spoken) and key.split("-")[0].lower() == spoken
    chosen = [(key, language(key), name(written[key]), False) for key in sorted(written, key=lambda key: (not own(key), key))[:MAX_SUBTITLES]]
    if not any(own(key) for key in written):
        key = transcript or next((key for key in automatic if own(key)), None)
        if key:
            chosen.insert(0, (key, language(key), name(automatic[key]), True))
    return chosen


class Downloader:
    def __init__(self, store, storage):
        self.store = store
        self.storage = storage
        self.queue = queue.Queue()
        self.stopping = threading.Event()
        self.process_lock = threading.Lock()
        self.processes = set()
        # One archive at a time per platform, so that a long download only holds back the links of its own
        # platform: `work` hands each link to the lane of its platform, a queue and the thread that empties it.
        self.lanes = {}
        self.warnings = {}  # archive id -> what the archive being made lacks
        self.thread = threading.Thread(target=self.work, daemon=True, name="archive-worker")

    def start(self):
        self.store.recover()
        for item in reversed(self.store.all()):
            if item["status"] == "queued":
                self.enqueue(item["id"])
        if description := proxy.describe():
            log.info("Outgoing requests go through the proxy %s", description)
        self.thread.start()

    def enqueue(self, media_id):
        self.queue.put(media_id)

    def stop(self):
        self.stopping.set()
        with self.process_lock:
            for process in self.processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
        self.queue.put(None)
        lanes = list(self.lanes.values())
        for jobs, _ in lanes:
            jobs.put(None)
        deadline = time.monotonic() + 8
        for thread in (self.thread, *(thread for _, thread in lanes)):
            if thread.is_alive():
                thread.join(timeout=max(0, deadline - time.monotonic()))

    def run(self, command, on_line=None, keep=12):
        """Run a program and return the last `keep` lines of its output."""
        tail = deque(maxlen=keep)
        with self.process_lock:
            if self.stopping.is_set():
                raise RuntimeError("Server shutting down.")
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, start_new_session=True,
            )
            self.processes.add(process)
        try:
            for line in process.stdout:
                line = line.strip()
                if line:
                    tail.append(line)
                    if on_line:
                        on_line(line)
            code = process.wait()
            if code:
                raise RuntimeError("\n".join(tail)[-2500:] or f"The program exited with code {code}.")
            return "\n".join(tail)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            process.stdout.close()
            with self.process_lock:
                self.processes.discard(process)

    def work(self):
        while not self.stopping.is_set():
            self.enqueue_due_retries()
            try:
                media_id = self.queue.get(timeout=RETRY_CHECK_SECONDS)
            except queue.Empty:
                continue
            if media_id is None:
                break
            try:
                item = self.store.get(media_id)
            except Exception:
                log.exception("Archive %s: not started", media_id)
                item = None
            if item is None:
                self.queue.task_done()
                continue
            jobs, thread = self.lanes.get(item["platform"]) or (None, None)
            if thread is None or not thread.is_alive():
                jobs = queue.Queue()
                thread = threading.Thread(target=self.serve, args=(jobs,), daemon=True, name=f"archive-{item['platform']}")
                self.lanes[item["platform"]] = (jobs, thread)
                thread.start()
            jobs.put(media_id)

    def serve(self, jobs):
        """Archive the links of one platform, one after the other."""
        while not self.stopping.is_set():
            media_id = jobs.get()
            if media_id is None:
                break
            try:
                self.handle(media_id)
            except Exception:
                # The catalog itself failed (disk full, database locked): the job is recovered at the next
                # start, and the worker stays alive for the next ones.
                log.exception("Archive %s: outcome not recorded", media_id)
            finally:
                self.queue.task_done()

    def enqueue_due_retries(self):
        try:
            for media_id in self.store.due_retries():
                if self.store.retry(media_id, automatic=True):
                    log.info("Archive %s: automatic retry", media_id)
                    self.enqueue(media_id)
        except Exception:
            log.exception("Automatic retries not scheduled")

    def handle(self, media_id):
        try:
            if self.store.claim(media_id):
                self.attempt(media_id)
        except Exception as error:
            message = proxy.redact(str(error))
            log.warning("Archive %s: %s", media_id, message)
            if not self.stopping.is_set():
                # The failure itself, or the one `attempt` reports with the route it tried.
                self.store.fail(media_id, message[-2500:], final=isinstance(error, Final) or isinstance(error.__cause__, Final))
        finally:
            if self.store.get(media_id) is None:
                # Deleted while it was being processed: leave no files behind.
                shutil.rmtree(self.storage.directory(media_id), ignore_errors=True)

    def attempt(self, media_id):
        """Download, directly or through the proxy as PROXY_MODE says; in fallback mode,
        a direct attempt that fails is followed at once by one through the proxy."""
        routes = proxy.routes()
        for index, through in enumerate(routes):
            via = None
            try:
                with proxy.chosen(through):
                    via = proxy.current()
                    return self.download(media_id)
            except Exception as error:
                if (index == len(routes) - 1 or isinstance(error, FINAL) or self.stopping.is_set()
                        or self.store.get(media_id) is None):
                    if routes == [False]:
                        raise  # no proxy configured
                    route = TRIED_BOTH if index else TRIED_PROXY if via else TRIED_DIRECTLY
                    raise RuntimeError(route.format(proxy=via and f"{via.host}:{via.port}", error=error)) from error
                log.info("Archive %s: failed directly, retrying through the proxy: %s",
                         media_id, proxy.redact(str(error))[-300:])
                self.store.update(media_id, status="downloading", progress=0)

    def warn(self, item, kind):
        """Note that the archive being made of `item` lacks something, among WARNINGS: it is kept all the same."""
        if kind not in WARNINGS:
            raise ValueError(f"Unknown warning: {kind}")
        kinds = self.warnings.setdefault(item["id"], [])
        if kind not in kinds:
            kinds.append(kind)

    def download(self, media_id):
        self.warnings.pop(media_id, None)  # those of a previous attempt
        item = self.store.get(media_id)
        if self.storage.mode == "s3" and self.adopt(item):
            return
        missing = [name for name, available in dependencies().items() if not available]
        if missing:
            raise MissingTools("Missing tools: " + ", ".join(missing) + ". See the README or use Docker.")
        with net.reading(item["languages"]):
            if item["platform"] == "Web":
                return self.download_web(item)
            try:
                return integrations.find(item["platform"]).archive(item, self)
            except integrations.Refused:
                raise RuntimeError(BOT_CHECK.format(platform=item["platform"])) from None

    def download_video(self, item, options=(), title=None):
        """Archive a single video with yt-dlp, which reads the platform itself; `options` are added to its command,
        and `title(text)` gives the video's title from the one the platform states."""
        media_id = item["id"]
        directory = self.storage.directory(media_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.store.update(media_id, storage=self.storage.mode)
        info_path = directory / "original.info.json"
        final_path = None
        info_loaded = False

        def progress(line):
            nonlocal final_path, info_loaded
            if line.startswith("archiver-file:"):
                final_path = Path(line.removeprefix("archiver-file:")).resolve()
            if not info_loaded and info_path.exists():
                try:
                    self.read_info(media_id, info_path, title)
                    info_loaded = True
                except (ValueError, OSError):
                    pass
            if line.startswith("archiver-progress:"):
                match = re.search(r"(\d+(?:\.\d+)?)%", line)
                if match:
                    self.store.update(media_id, progress=min(float(match[1]), 100))
            if line.startswith(("[Merger]", "[VideoRemuxer]")):
                self.store.update(media_id, status="processing", progress=100)

        command = [
            # The yt-dlp installed with Archiver, the one it also uses as a library: a yt-dlp found first on the
            # PATH may be another version, which platforms may refuse.
            sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-color",
            "--js-runtimes", "node", "--no-remote-components", "--no-cache-dir",
            *options,
            "--socket-timeout", "30", "--retries", "3", "--fragment-retries", "3",
            "--abort-on-unavailable-fragments", "--match-filters", "!is_live & !is_upcoming",
            *(["--proxy", via.url] if (via := proxy.current()) else []),
            "-f", "bestvideo*+bestaudio/best", "--merge-output-format", "mkv",
            "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
            "--newline", "--progress", "--progress-delta", "1",
            "--progress-template", "download:archiver-progress:%(progress._percent_str)s",
            "--print", "after_move:archiver-file:%(filepath)s", "--no-simulate",
            "-o", str(directory / "original.%(ext)s"), "--", item["source_url"],
        ]
        try:
            self.run(command, progress)
        except RuntimeError as error:
            # Age-restricted videos are only served to signed-in viewers, even through the embedded player.
            if "Sign in to confirm your age" in str(error):
                raise Final(AGE_RESTRICTED.format(platform=item["platform"])) from None
            # A platform may ask for a sign-in when it distrusts the server's IP address, often a datacenter one.
            if "Sign in to confirm you’re not a bot" in str(error) or "Sign in to confirm you're not a bot" in str(error):
                raise RuntimeError(BOT_CHECK.format(platform=item["platform"])) from None
            raise
        if not final_path or not final_path.is_file() or final_path.parent != directory.resolve():
            raise RuntimeError("No video was retrieved. Ongoing live streams and upcoming premieres are not supported.")
        if not info_path.exists():
            raise RuntimeError("The video’s metadata could not be retrieved.")
        self.read_info(media_id, info_path, title)
        self.finish_video(media_id, final_path, info_path, self.fetch_subtitles(item, info_path, options))

    def fetch_subtitles(self, item, info_path, options=()):
        """Download a video's subtitles next to it, as original.<language>.vtt, from what yt-dlp already read of it.

        Returns what describes each track kept: none for a video without subtitles, and None when it has
        some that could not be downloaded. The video is archived either way."""
        directory = info_path.parent
        try:
            chosen = subtitle_choices(json.loads(info_path.read_text()))
        except (ValueError, OSError):
            chosen = []
        if not chosen:
            return []
        try:
            self.run([
                sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-color", "--no-cache-dir", *options,
                "--socket-timeout", "30", "--retries", "3", *(["--proxy", via.url] if (via := proxy.current()) else []),
                "--load-info-json", str(info_path), "--skip-download", "--write-subs", "--write-auto-subs",
                "--sub-langs", ",".join(re.escape(key) for key, *_ in chosen), "--sub-format", "vtt/best", "--convert-subs", "vtt",
                "-o", str(directory / "original.%(ext)s"),
            ])
        except RuntimeError as error:
            if self.stopping.is_set():
                raise
            # Some tracks may have been written before the failure: they are kept.
            log.info("Archive %s: subtitles incomplete: %s", item["id"], proxy.redact(str(error))[-300:])
            self.warn(item, "subtitles")
        tracks = [{"file": f"original.{key}.vtt", "language": language, "name": name, "automatic": automatic}
                  for key, language, name, automatic in chosen
                  if (directory / f"original.{key}.vtt").is_file() and (directory / f"original.{key}.vtt").stat().st_size]
        return tracks or None

    def finish_video(self, media_id, original, info_path, subtitles=None):
        """Finish the archive of a single video: playback copy, thumbnail (original.jpg, unless there is one already)."""
        # The downloaded file is authoritative; some platforms omit dimensions in their metadata.
        width, height, duration = self.probe_visual(original)
        self.store.update(media_id, status="processing", progress=100, width=width, height=height,
                          duration=self.store.get(media_id)["duration"] or duration)
        playback = self.prepare_playback(original)
        thumbnail = original.parent / "original.jpg"
        if not thumbnail.exists():
            self.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(original),
                      "-frames:v", "1", "-update", "1", str(thumbnail)])
        # An empty list records that the video has no subtitles; nothing is recorded when they were not looked
        # for, or could not be downloaded.
        self.finish(media_id, {original, playback, thumbnail, info_path, *(original.parent / track["file"] for track in subtitles or ())},
                    original=original.name, playback=playback.name, thumbnail=thumbnail.name,
                    size=original.stat().st_size, **({} if subtitles is None else {"details": {"subtitles": subtitles}}))

    def adopt(self, item):
        """Take over the archive of this link that another instance sharing the S3 storage made, if there is one.

        A link that this server cannot download is archived from a computer whose address
        the platform accepts (see run.py archive), then retried here."""
        found = self.storage.record(item["id"], manifest.NAME)
        if found is None:
            return False
        data, encrypted = found
        try:
            record = json.loads(data or "")
            if record.get("format") != manifest.FORMAT or record.get("id") != item["id"]:
                raise ValueError
        except ValueError:
            raise RuntimeError("The storage already holds an archive of this link, but its archive.json cannot be read.") from None
        log.info("Archive %s: taken over from the storage", item["id"])
        self.store.adopt(record, self.storage.mode, len(data), encrypted)
        manifest.publish(self.store, self.storage, item["id"])
        return True

    def read_info(self, media_id, path, title=None):
        info = json.loads(path.read_text())
        date = info.get("upload_date")
        published = f"{date[:4]}-{date[4:6]}-{date[6:]}" if date and re.fullmatch(r"\d{8}", date) else None
        stated = info.get("title") or ""
        self.store.update(
            media_id, title=(title(stated) if title else stated) or media_id,
            author=info.get("channel") or info.get("uploader"),
            published_at=published, duration=info.get("duration"),
            width=info.get("width"), height=info.get("height"),
            description=info.get("description") or "",
        )

    def social_fetcher(self, session):
        """Downloads a post's medium: a video through the yt-dlp session that read the post,
        or an image from the first of its URLs that works."""
        def fetch(asset, path, progress):
            if asset["type"] == "video":
                return Path(session.download(asset.pop("entry"), str(path), progress))
            return self.fetch_image(asset, path, progress)
        return fetch

    def fetch_image(self, asset, path, progress, headers=lambda url: None):
        """An image or a video from the first of the asset's URLs that works."""
        sources = asset.pop("source_urls", None) or [asset.pop("source_url")]
        types = VIDEO_TYPES if asset["type"] == "video" else IMAGE_TYPES
        temp = path.with_suffix(".part")
        error = None
        for source in sources:
            try:
                medium = net.fetch(source, ",".join(types), tuple(types), 1024 * 1024 * 1024, progress,
                                   self.stopping, headers=headers(source), destination=temp)
            except (RuntimeError, ValueError) as failure:
                error = failure
                continue
            path = path.with_suffix("." + types[medium.content_type])
            temp.replace(path)
            return path
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"A photo or video of the post could not be downloaded: {error}")

    def archive_posts(self, item, posts, raw, fetch):
        """Store social posts (a single post or a thread) with their media.

        `fetch(asset, path_without_extension, on_progress)` downloads one medium and returns its path."""
        media_id = item["id"]
        directory = self.storage.directory(media_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.store.update(media_id, storage=self.storage.mode)
        first = posts[0]
        texts = [post["text"] for post in posts]
        description_text = texts[0] if len(posts) == 1 else "\n\n".join(
            f"[{index}/{len(posts)}] {post['url']}\n{post['text']}" for index, post in enumerate(posts, 1))
        self.store.update(
            media_id, title=excerpt(first.get("title") or texts[0], 200 if first.get("title") else 160) or first["author"] or item["platform"],
            description="\n\n".join(texts), author=first["author"], published_at=first["published_at"],
            source_url=first["url"],
        )
        info = directory / "original.info.json"
        info.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        description = directory / "description.txt"
        description.write_text(description_text, encoding="utf-8")
        files = {info, description}
        # The media of the post answered and of the post quoted follow those of the post itself.
        assets = []
        for number, post in enumerate(posts):
            assets += [(number, None, asset) for asset in post["assets"]]
            assets += [(number, "replied", asset) for asset in (post.get("reply_to") or {}).get("assets") or []]
            assets += [(number, "quoted", asset) for asset in (post["quote"] or {}).get("assets") or []]
        attachments = []
        for index, (number, context, asset) in enumerate(assets):
            stem = f"media-{index + 1:02d}"
            self.store.update(media_id, status="downloading")
            original = fetch(asset, directory / f"{stem}-original", lambda fraction: self.store.update(
                media_id, progress=round(100 * (index + fraction) / len(assets), 1),
            ))
            self.store.update(media_id, status="processing")
            # Probe the actual downloaded file, not the preview dimensions in the post.
            width, height, duration = self.probe_visual(original)
            asset.update(width=width, height=height)
            if asset["type"] == "video":
                asset["duration"] = duration or asset["duration"]
                playback = self.prepare_playback(original, f"{stem}-playback")
            else:
                playback = original
            thumbnail = self.make_thumbnail(original, directory / f"{stem}-thumbnail.jpg")
            files.update((original, playback, thumbnail))
            attachments.append({
                **asset, "post": number, **({context: True} if context else {}), "original": original.name, "playback": playback.name,
                "thumbnail": thumbnail.name, "size": original.stat().st_size,
            })
        def media(number, context):
            return [index for index, attachment in enumerate(attachments) if attachment["post"] == number
                    and (attachment.get(context, False) if context else not (attachment.get("quoted") or attachment.get("replied")))]

        def other(post, number, context):
            """A post shown with the archived one, the one it answers or quotes: its text and where its media are."""
            return post and {**{key: value for key, value in post.items() if key != "assets"}, "media": media(number, context)}

        stored_posts = []
        for number, post in enumerate(posts):
            stored_posts.append({
                **{key: post[key] for key in ("id", "url", "author", "handle", "published_at", "text")},
                **({"reply_to": other(post["reply_to"], number, "replied")} if post.get("reply_to") else {}),
                "quote": other(post["quote"], number, "quoted"), "media": media(number, None),
            })
        # The cover is the post's own medium; one of a post it answers or quotes only when it has none.
        cover = next((attachment for attachment in attachments if not (attachment.get("quoted") or attachment.get("replied"))),
                     attachments[0] if attachments else {})
        self.finish(
            media_id, files, attachments=attachments, posts=stored_posts,
            original=cover.get("original"), playback=cover.get("playback"), thumbnail=cover.get("thumbnail"),
            width=cover.get("width"), height=cover.get("height"), duration=cover.get("duration"),
            size=sum(asset["size"] for asset in attachments),
        )

    def finish(self, media_id, files, **values):
        """Record each file's sha256, write archive.json, publish everything and mark the archive ready.

        The hashes and sizes are those of the content, whether or not it is stored encrypted."""
        hashes = {file.name: manifest.hash_file(file) for file in files}
        details = dict(values.pop("details", None) or self.store.get(media_id)["details"] or {})
        details.pop("warnings", None)
        if warnings := self.warnings.pop(media_id, None):
            details["warnings"] = warnings
        self.store.update(media_id, files=hashes, integrity="ok", integrity_checked_at=now(), details=details, **values)
        record = self.storage.directory(media_id) / manifest.NAME
        record.write_bytes(manifest.render({**self.store.get(media_id), "status": "ready"}))
        if self.storage.mode == "s3":
            self.store.update(media_id, status="uploading")
        stored = sum(entry["size"] for entry in hashes.values()) + record.stat().st_size
        encrypted = self.storage.publish(media_id, [*files, record])
        self.store.update(media_id, status="ready", progress=100, error=None, encrypted=int(encrypted), storage_bytes=stored)
        # Edits made while files were uploading could not publish a record yet. Keep the recovery
        # record in sync now that the archive is ready, including its current privacy tags.
        manifest.publish(self.store, self.storage, media_id)
        self.storage.cleanup_work(media_id)

    def probe_visual(self, path):
        """Width, height and duration of the first image or video stream; fails if there is none."""
        probe = json.loads(self.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration", "-of", "json=compact=1", str(path),
        ]))
        streams = probe.get("streams") or []
        if not streams:
            raise RuntimeError("The received file contains no readable image or video.")
        duration = probe.get("format", {}).get("duration")
        return streams[0].get("width"), streams[0].get("height"), float(duration) if duration else None

    def make_thumbnail(self, source, thumbnail):
        self.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
                  # A transparent image is laid on white, as a page shows it: JPEG would turn its background black.
                  "-vf", "scale='min(640,iw)':-2,format=rgba,negate,premultiply=inplace=1,negate,format=yuv420p",
                  "-frames:v", "1", "-update", "1", str(thumbnail)])
        return thumbnail

    def download_web(self, item):
        media_id = item["id"]
        directory = self.storage.directory(media_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.store.update(media_id, storage=self.storage.mode)
        via, feed_url = "page", None
        try:
            url, response = net.open_url(item["source_url"], "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
            content_type = net.media_type(response)
            if not content_type.startswith(PAGE_TYPES):
                return self.archive_file(item, url, response, content_type)
            page = net.receive(url, response, content_type, MAX_PAGE_BYTES, stopping=self.stopping)
            base = page.url
            # A page that is a video is archived as that video, with what the page says of it.
            described = web.video_page(page.body, base)
            if described and (video := self.page_video(base)):
                return self.archive_file(item, *video, described)
            article = web.extract(page.body, base)
        except (net.Blocked, web.NoContent) as error:
            # Sites behind bot protection usually still publish their articles in a feed.
            feed_url, entry = self.find_in_feed(item["source_url"])
            if not entry:
                if isinstance(error, net.Blocked):
                    raise RuntimeError("The site blocks automated visits (bot protection) and this article is not in "
                                       "its RSS feed. It cannot be archived for now.") from None
                raise
            base, via = item["source_url"], "feed"
            article = web.from_feed(entry, base)
            article["complete"] = entry["complete"]
        title = excerpt(article["title"] or "", 200) or article["site"]
        self.store.update(media_id, title=title, author=article["author"], published_at=article["date"], progress=10)
        # Article images, then the page's cover image when the article does not include it.
        sources = article["images"] + [article["cover"]] if article["cover"] and article["cover"] not in article["images"] else article["images"]
        attachments, local, files = [], {}, set()
        for index, source in enumerate(sources):
            if self.stopping.is_set():
                raise RuntimeError("Server shutting down.")
            stem = f"image-{len(attachments) + 1:02d}"
            try:
                image = net.fetch(source, "image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.9", tuple(IMAGE_TYPES),
                                  20 * 1024 * 1024, stopping=self.stopping)
                original = directory / f"{stem}-original.{IMAGE_TYPES[image.content_type]}"
                original.write_bytes(image.body)
                width, height, _ = self.probe_visual(original)
                thumbnail = self.make_thumbnail(original, directory / f"{stem}-thumbnail.jpg")
            except (RuntimeError, ValueError, KeyError) as error:
                if self.stopping.is_set():
                    raise
                log.info("Archive %s: image skipped %s: %s", media_id, source, error)
                self.warn(item, "images")
                continue
            finally:
                self.store.update(media_id, progress=round(10 + 90 * (index + 1) / len(sources), 1))
            local[source] = f"/api/media/{media_id}/file/playback/{len(attachments)}"
            files.update((original, thumbnail))
            attachments.append({
                "type": "image", "original": original.name, "playback": original.name, "thumbnail": thumbnail.name,
                "width": width, "height": height, "duration": None, "size": original.stat().st_size,
                "source_url": source, "cover": source == article["cover"],
            })
        self.store.update(media_id, status="processing", progress=100)
        content = web.text(article["main"])
        body = directory / "article.html"
        body.write_text(web.render(article["main"], base, local, article["title"]), encoding="utf-8")
        description = directory / "description.txt"
        description.write_text(f"{article['title'] or title}\n{article['url']}\n\n{content}", encoding="utf-8")
        info = directory / "original.info.json"
        info.write_text(json.dumps({
            **{key: article[key] for key in ("title", "author", "date", "site", "summary", "cover", "url", "images")},
            "modified": article.get("modified"),
            "requested_url": item["source_url"], "final_url": base, "via": via, "feed_url": feed_url,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        files.update((body, description, info))
        cover = next((a for a in attachments if a["cover"]), attachments[0] if attachments else {})
        words = len(content.split())
        self.finish(
            media_id, files, description=content, attachments=attachments,
            details={"site": article["site"], "summary": article["summary"], "url": article["url"],
                     "words": words, "minutes": max(1, round(words / 230)), "via": via,
                     **({"modified": article["modified"]} if article.get("modified") else {}),
                     "excerpt": via == "feed" and not article["complete"]},
            thumbnail=cover.get("thumbnail"), original=None, playback=None, width=cover.get("width"),
            height=cover.get("height"), size=sum(file.stat().st_size for file in files),
        )

    def page_video(self, url):
        """The video that the page at `url` plays, as (address, open response, content type), or None.

        A video offered in several qualities is a file for each, and nothing says which is which: the largest
        file is kept."""
        with ytdlp.Session(self.stopping) as session:
            files = session.files(url)
        best, best_size = None, -1
        for file in files[:MAX_PAGE_VIDEOS]:
            try:
                address, response = net.open_url(file, "video/*,*/*;q=0.5", headers={"Referer": url})
            except (RuntimeError, ValueError):
                continue
            content_type, size = net.media_type(response), int(response.getheader("Content-Length") or 0)
            if content_type in VIDEO_TYPES and size > best_size:
                if best:
                    best[1].connection_.close()
                best, best_size = (address, response, content_type), size
            else:
                response.connection_.close()
        return best

    def archive_file(self, item, url, response, content_type, page=None):
        """Archive a link that leads straight to an image, a video, a sound or a PDF, as a single medium;
        `page` is what the page that plays it says of it, when the link was that page's."""
        page = page or {}
        if content_type in UNDESCRIBED_TYPES:
            extension = PurePosixPath(unquote(urlparse(url).path)).suffix.lower().removeprefix(".")
            content_type = FILE_EXTENSIONS.get(extension, content_type)
        if content_type not in FILE_TYPES:
            response.connection_.close()
            raise ValueError(f"Unsupported content type ({content_type or 'unknown'}).")
        media_id = item["id"]
        directory = self.storage.directory(media_id)
        kind = ("video" if content_type in VIDEO_TYPES else "audio" if content_type in AUDIO_TYPES
                else "pdf" if content_type == PDF_TYPE else "image")
        name = file_name(url, response)
        self.store.update(media_id, title=excerpt(page.get("title") or "", 200) or name, progress=0)
        temp = directory / "original.part"
        try:
            net.receive(url, response, content_type, MAX_FILE_BYTES, lambda fraction: self.store.update(
                media_id, progress=round(100 * fraction, 1)), self.stopping, destination=temp)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        original = directory / f"original.{FILE_TYPES[content_type]}"
        temp.replace(original)
        self.store.update(media_id, status="processing", progress=100)
        width = height = duration = None
        playback, thumbnail = original, directory / "thumbnail.jpg"
        if kind == "pdf":
            with original.open("rb") as stream:
                if b"%PDF-" not in stream.read(1024):
                    raise RuntimeError("The received file is not a readable PDF.")
            thumbnail = self.optional_thumbnail(lambda: self.run([
                "pdftoppm", "-jpeg", "-f", "1", "-l", "1", "-scale-to", "640", "-singlefile",
                str(original), str(thumbnail.with_suffix(""))]), thumbnail) if shutil.which("pdftoppm") else None
        elif kind == "audio":
            duration = self.probe_audio(original)
            if original.suffix.removeprefix(".") not in PLAYABLE_AUDIO:
                playback = directory / "playback.m4a"
                self.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(original), "-map", "0:a:0",
                          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(playback)])
            # The cover embedded in the file, when it has one.
            thumbnail = self.optional_thumbnail(lambda: self.make_thumbnail(original, thumbnail), thumbnail)
        else:
            width, height, duration = self.probe_visual(original)
            if kind == "video":
                playback = self.prepare_playback(original)
            else:
                duration = None
            self.make_thumbnail(original, thumbnail)
        # What the file says about itself; failing that, its name and the date the server last saw it change.
        embedded = self.embedded_metadata(original, kind)
        title = excerpt(page.get("title") or embedded.get("title") or "", 200) or name
        author = excerpt(page.get("author") or embedded.get("author") or "", 200) or None
        published = file_date(embedded.get("date")) or http_date(response.getheader("Last-Modified"))
        self.store.update(media_id, title=title, author=author, published_at=published)
        info = directory / "original.info.json"
        info.write_text(json.dumps({
            "requested_url": item["source_url"], "final_url": url, "content_type": content_type, "name": name,
            "last_modified": response.getheader("Last-Modified"), "embedded": embedded, **({"page": page} if page else {}),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        size = original.stat().st_size
        shown = thumbnail and thumbnail.name
        attachment = {"type": kind, "original": original.name, "playback": playback.name, "thumbnail": shown,
                      "width": width, "height": height, "duration": duration, "size": size, "source_url": url}
        site = urlparse(url).hostname or ""
        self.finish(
            media_id, {original, playback, info} | ({thumbnail} if thumbnail else set()), attachments=[attachment],
            details={"kind": "file", "site": page.get("site") or site.removeprefix("www."), "url": url, "content_type": content_type},
            **({"description": page["summary"]} if page.get("summary") else {}),
            original=original.name, playback=playback.name, thumbnail=shown,
            width=width, height=height, duration=duration, size=size,
        )

    def embedded_metadata(self, path, kind):
        """The title, author and date written inside a file, for those it has."""
        try:
            if kind == "pdf":
                if not shutil.which("pdfinfo"):
                    return {}
                lines = self.run(["pdfinfo", "-isodates", str(path)], keep=200).splitlines()
                tags = {key.strip().lower(): value.strip() for key, _, value in (line.partition(":") for line in lines)}
                found = {"title": tags.get("title"), "author": tags.get("author"), "date": tags.get("creationdate")}
            else:
                probe = json.loads(self.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags",
                                             "-of", "json", str(path)], keep=2000))
                tags = {}
                for holder in [*(probe.get("streams") or []), probe.get("format") or {}]:
                    tags.update({key.lower(): value for key, value in (holder.get("tags") or {}).items()})
                found = {"title": tags.get("title"),
                         "author": tags.get("artist") or tags.get("album_artist") or tags.get("author"),
                         "date": tags.get("creation_time") or tags.get("date")}
        except (RuntimeError, ValueError):
            if self.stopping.is_set():
                raise
            return {}
        return {key: " ".join(value.split()) for key, value in found.items() if isinstance(value, str) and value.strip()}

    def optional_thumbnail(self, make, thumbnail):
        """The thumbnail `make` writes, or None when the file has nothing to show."""
        try:
            make()
        except RuntimeError:
            if self.stopping.is_set():
                raise
        if thumbnail.exists() and thumbnail.stat().st_size:
            return thumbnail
        thumbnail.unlink(missing_ok=True)
        return None

    def probe_audio(self, path):
        """Duration of a sound file; fails if it has no audio stream."""
        probe = json.loads(self.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name:format=duration", "-of", "json=compact=1", str(path),
        ]))
        if not probe.get("streams"):
            raise RuntimeError("The received file contains no playable sound.")
        duration = probe.get("format", {}).get("duration")
        return float(duration) if duration else None

    def find_in_feed(self, url):
        """(feed URL, entry) for `url` in one of its site's usual feeds, or (None, None)."""
        for candidate in web.feed_candidates(url):
            if self.stopping.is_set():
                raise RuntimeError("Server shutting down.")
            try:
                feed = net.fetch(candidate, "application/rss+xml,application/atom+xml,application/xml;q=0.9",
                                 web.FEED_TYPES, 5 * 1024 * 1024, stopping=self.stopping)
            except (RuntimeError, ValueError):
                continue
            entry = web.feed_entry(feed.body, url)
            if entry:
                return feed.url, entry
        return None, None

    def prepare_playback(self, original, stem="playback"):
        # Preserve the downloaded original; only the browser copy may be transcoded.
        probe = json.loads(self.run(["ffprobe", "-v", "error", "-show_entries",
                                    "stream=codec_name,codec_type,pix_fmt", "-of", "json=compact=1", str(original)]))
        streams = probe.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        compatible = video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p" and audio.get("codec_name") in (None, "aac")
        if original.suffix == ".mp4" and compatible:
            return original
        playback = original.parent / f"{stem}.mp4"
        temp = original.parent / f"{stem}.tmp.mp4"
        encoding = ["-c", "copy"] if compatible else [
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        ]
        self.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(original),
                  "-map", "0:v:0", "-map", "0:a:0?", *encoding, "-movflags", "+faststart", str(temp)])
        temp.replace(playback)
        return playback
