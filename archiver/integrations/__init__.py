"""One file per platform.

An integration is a module of this package that knows one platform: which
links are its own, how to archive one as a logged-out visitor, and whether the
original is still online. Links no integration takes are archived as web pages.

To add a platform, write its module, add it to MODULES below, and give it an
icon and its place in the lists at the top of web/render.js.

A module defines:

    NAME      The platform's name, stored with each archive: "Bluesky".
    ID        A regular expression for the ids of its archives: a short prefix
              and the platform's own identifier of the post or video, or a
              hash of its link. Letters, digits, "-" and "_" only.

    link(parsed)
        `parsed` is the link, from urllib.parse.urlparse, already checked to be
        a public http or https address. Returns (id, canonical link) for
        something the platform archives, None for a link that is not its own,
        and raises ValueError with a message for a link of the platform that
        cannot be archived, such as a profile: ValueError(WRONG_LINK…).

    archive(item, worker)
        Archives `item`, the catalog's record of the link, with the worker's
        tools (see Downloader in downloader.py):
        - worker.archive_posts(item, posts, raw, fetch) for a post or a thread.
          `posts` are dictionaries with the keys id, url, author, handle,
          published_at (ISO 8601), text, assets and quote (None, or a
          dictionary with author, handle, text, url and assets); the first
          may also have a title, and reply_to, the post of someone else that
          it answers, in the shape of a quote. An asset has a
          type ("image" or "video"), alt_text, width, height, duration, and
          what `fetch` needs to download it. `raw` is the platform's own data,
          kept with the archive. `fetch(asset, path, on_progress)` downloads
          one medium to `path` plus an extension and returns the file's path;
          worker.fetch_image does it for assets with a `source_urls` list.
        - worker.download_video(item, VIDEO_OPTIONS) for a single video that
          yt-dlp reads. VIDEO_OPTIONS, the options the platform needs on
          yt-dlp's command, is then a name of the module.
        Failures are raised as exceptions with a message: Final when another
        attempt would not change the answer, Gone when the original was
        deleted, Refused when the platform takes the visit for a bot.

Messages shown to the user are the ones below, the same for every platform and
filled with its name: DELETED.format(platform=NAME). They are written in English
here and translated by the files of archiver/locales, so an integration adds
no text of its own and a new language is one file to write.

    online(item, worker)        (optional)
        ONLINE, GONE or UNKNOWN: whether the original can still be seen.
        GONE only on a clear answer; any refusal or doubt is UNKNOWN. Without this
        function, the platform's originals are not checked.

Content is read as a logged-out visitor would see it: no account, no cookies
of the user's browser, nothing private.
"""
from importlib import import_module

from .. import net

# What a check of the original answers.
ONLINE, GONE, UNKNOWN = "online", "gone", "unknown"

# The link is the platform's, but not of something it archives.
WRONG_LINK = ("This {platform} link does not lead to something that can be archived: use the link of a post or a video, "
              "not of a profile, a channel or a list.")
# The original was deleted, or never existed.
DELETED = "This content no longer exists on {platform}: it was deleted or its link is wrong."
# The platform does not show it to a logged-out visitor.
UNAVAILABLE = ("{platform} does not give access to this content without logging in: it may be private, deleted "
               "or for signed-in users only.")
# Too many visits for now.
LIMITED = "{platform} is temporarily limiting anonymous visits. Try again in a few minutes."
# An answer in a shape the integration does not know.
UNREADABLE = "The answer from {platform} cannot be used. Try again later."
# A photo or a video with no file to download.
NO_MEDIA = "{platform} provided no readable version of a photo or video of this content."
# A short link that no longer leads anywhere.
EXPIRED = "This {platform} short link leads to no content: it may have expired."
# A link with the shape of a platform made of many servers, on a site that is not one of them.
NOT_SERVER = "This link looks like a {platform} post, but {host} does not answer like a {platform} server."
# A video in several parts, asked for a part it does not have.
NO_PART = "This video has no part {part}."
# Mastodon comes last: its servers have any name, so its links are told by their shape.
MODULES = ("youtube", "x", "instagram", "tiktok", "facebook", "bilibili", "bluesky", "reddit", "mastodon")


class Final(RuntimeError):
    """A failure that another attempt, from another address, would not change."""


class Gone(Final):
    """The original was deleted."""


class Refused(RuntimeError):
    """The platform turned the visit away, taking it for a bot."""


def code(item, prefix):
    """The platform's own identifier in an archive's id: x-123.2, the second archive of post 123, gives 123."""
    return item["id"].partition(".")[0].removeprefix(prefix)


def page(url, accept="text/html"):
    """Whether a public address still answers: the availability check of most platforms."""
    try:
        _, response = net.open_url(url, accept)
        response.connection_.close()
        return ONLINE
    except net.UnknownHost:
        return GONE
    except net.Blocked:
        return UNKNOWN
    except net.HTTPStatus as error:
        return GONE if error.status in (404, 410) else UNKNOWN
    except (RuntimeError, ValueError):
        return UNKNOWN


ALL = tuple(import_module(f".{name}", __name__) for name in MODULES)


def find(platform):
    """The integration of a platform, by its name; None for web pages."""
    return next((module for module in ALL if module.NAME == platform), None)


def link(parsed):
    """(id, canonical link, platform name) from the first integration that takes the link, or None."""
    for module in ALL:
        if found := module.link(parsed):
            return (*found, module.NAME)
    return None
