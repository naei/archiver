"""yt-dlp used as a library: reading a platform's raw post data and downloading its videos.

yt-dlp keeps up with each platform's anonymous access; its results only cover
videos, so the raw data an extractor receives is captured to also get photos,
captions and authors. Reading and downloading share one session, as some
platforms only serve videos with the cookies set while reading the post."""
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, ExtractorError, YoutubeDLError

from . import proxy


class Session:
    def __init__(self, stopping):
        self.stopping = stopping
        self.on_progress = None
        via = proxy.current()
        self.ydl = YoutubeDL({
            **({"proxy": via.url} if via else {}),
            "quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30, "retries": 3,
            "format": "bestvideo*+bestaudio/best", "merge_output_format": "mp4", "noplaylist": True,
            "progress_hooks": [self.progress],
        })

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.ydl.close()

    def readable(self, url):
        """Whether yt-dlp reads what is at `url`, without downloading it."""
        try:
            return bool(self.ydl.extract_info(url, download=False, process=False))
        except YoutubeDLError:
            return False

    def progress(self, status):
        if self.stopping.is_set():
            raise DownloadCancelled("Server shutting down.")
        if self.on_progress and status.get("status") == "downloading" and status.get("total_bytes"):
            self.on_progress(min(status.get("downloaded_bytes", 0) / status["total_bytes"], 1))

    def capture(self, extractor_class, method, url):
        """Run `extractor_class` on `url`, keeping the first argument its `method` receives.

        Returns (raw data or None, yt-dlp result or None)."""
        captured = {}

        def keep(extractor, data, *args, **kwargs):
            captured.setdefault("raw", data)
            return getattr(extractor_class, method)(extractor, data, *args, **kwargs)

        extractor = type(extractor_class.__name__, (extractor_class,), {method: keep})()
        extractor.set_downloader(self.ydl)
        try:
            info = extractor.extract(url)
        except ExtractorError:
            info = None
        if info:
            # What YoutubeDL.extract_info adds itself, needed to download the result later.
            for entry in [info, *(info.get("entries") or [])]:
                entry.setdefault("extractor", extractor.IE_NAME)
                entry.setdefault("extractor_key", extractor.ie_key())
                entry.setdefault("webpage_url", url)
        return captured.get("raw"), info

    def download(self, entry, stem, on_progress):
        """Best video and audio of a result, merged; returns the file path."""
        self.on_progress = on_progress
        self.ydl.params["outtmpl"] = {"default": f"{stem}.%(ext)s"}
        result = self.ydl.process_ie_result(dict(entry), download=True)
        downloads = result.get("requested_downloads") or [result]
        path = downloads[0].get("filepath") or downloads[0].get("_filename")
        if not path:
            raise RuntimeError("The video could not be downloaded.")
        return path
