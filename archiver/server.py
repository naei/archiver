import json
import logging
import mimetypes
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from . import i18n, manifest, net, relay, viewer
from .auth import SESSION_SECONDS, TOKEN_SECONDS, UNLOCK_SECONDS, AccountExists, Auth, TooManyAttempts
from .downloader import Downloader, dependencies
from .export import Export
from .maintenance import Maintenance
from .storage import Storage
from .store import ACTIVE, MEDIA_ID_PATTERN, PRIVATE, Store, clean_note, clean_tags, clean_title, is_article, is_private


WEB = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger(__name__)
COOKIE = "archiver_session"
# Static files hold no library data and are served without a session.
ASSETS = {
    "/app.js": "app.js", "/render.js": "render.js", "/login.js": "login.js", "/i18n.js": "i18n.js", "/theme.js": "theme.js", "/style.css": "style.css",
    "/favicon.svg": "favicon.svg", "/manifest.webmanifest": "manifest.webmanifest", "/sw.js": "sw.js",
    "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png", "/apple-touch-icon.png": "apple-touch-icon.png",
}
TOO_MANY = "Too many attempts. Try again in a few minutes."


def local_path(value):
    """A path on this site to return to after logging in, never another origin."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def export_time(value):
    """When an export is made, as the client states it (kept on a resumed download), else now."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?", value or ""):
            datetime.fromisoformat(value)
            return value
    except ValueError:
        pass
    return datetime.now().isoformat(timespec="minutes")


def byte_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("Invalid range")
    left, right = match.groups()
    if not left:
        length = int(right)
        if length <= 0:
            raise ValueError("Invalid range")
        start, end = max(0, size - length), size - 1
    else:
        start, end = int(left), min(int(right), size - 1) if right else size - 1
    if start >= size or end < start:
        raise ValueError("Invalid range")
    return start, end, True


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root, start_worker=True):
        self.store = Store(root)
        self.storage = Storage(self.store.root)
        self.worker = Downloader(self.store, self.storage)
        self.maintenance = Maintenance(self.store, self.storage)
        self.mutation_lock = threading.Lock()
        self.auth = Auth(self.store)
        self.relay_token = relay.token()
        if self.storage.encryption is None and any(item["encrypted"] for item in self.store.all()):
            log.warning("Some archives are encrypted and ENCRYPTION_KEY is not set: they cannot be read")
        self.local = address[0] in ("127.0.0.1", "::1")
        super().__init__(address, Handler)
        if start_worker:
            self.worker.start()
            self.maintenance.start()

    def problems(self):
        """What keeps the service from working: an unreadable catalog, or a background thread that stopped."""
        problems = [name for name, thread in (("worker", self.worker.thread), ("maintenance", self.maintenance.thread))
                    if thread.ident is not None and not thread.is_alive()]
        try:
            self.store.ping()
        except Exception:
            log.exception("Catalog unreadable")
            problems.append("catalog")
        return problems

    def server_close(self):
        self.maintenance.stop()
        self.worker.stop()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "Archiver"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # Do not log URLs, credentials or media metadata.
        if args and isinstance(args[1] if len(args) > 1 else None, str) and args[1].startswith("5"):
            log.error("HTTP %s", args[1])

    def end_headers(self):
        self.headers_sent = True
        # Nothing is shown in a frame, except an archived PDF in the page of its own archive: never by another site.
        framable, self.framable = getattr(self, "framable", False), False
        ancestors = "'self'" if framable else "'none'"
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if framable else "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                         f"media-src 'self'; connect-src 'self'; frame-ancestors {ancestors}; "
                         "base-uri 'none'; form-action 'self'")
        super().end_headers()

    def json(self, data, status=200, cookie=None):
        lang = i18n.language(self.headers.get("X-Language"), self.headers.get("Accept-Language"))
        body = json.dumps(i18n.localize(data, lang), ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def host_allowed(self):
        """A server bound to this computer only answers to its local names (no DNS rebinding)."""
        if self.server.local:
            host = urlparse("http://" + self.headers.get("Host", "")).hostname
            if host not in ("localhost", "127.0.0.1", "::1"):
                self.json({"error": "Host not allowed."}, 403)
                return False
        return True

    def session_token(self):
        try:
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
        except CookieError:
            return None
        return morsel.value if morsel else None

    def session_cookie(self, token):
        # Behind an HTTPS proxy the cookie is only ever sent over HTTPS.
        secure = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https"
        max_age = SESSION_SECONDS if token else 0
        return f"{COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")

    def bearer(self):
        """The session token a client sends itself, in an Authorization header, instead of the browser's cookie."""
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        return token.strip() if scheme.lower() == "bearer" else None

    def foreign(self):
        """Whether the request comes from a page of another origin, which the browser's cookie must not serve."""
        origin = self.headers.get("Origin")
        return bool(origin and urlparse(origin).netloc != self.headers.get("Host")) or self.headers.get("Sec-Fetch-Site") == "cross-site"

    def signed_in(self):
        """A valid session, from the browser's cookie or sent by the client itself."""
        return self.server.auth.session(self.session_token()) or self.server.auth.session(self.bearer())

    def authenticated(self):
        if self.signed_in():
            return True
        self.json({"error": "Log in to continue."}, 401)
        return False

    def unlocked(self):
        """Whether this request may see private archives: it carries a valid unlock token, in a header for the
        interface's requests, or in the address for its media and downloads, which cannot send headers."""
        token = self.headers.get("X-Private-Unlock") or parse_qs(urlparse(self.path).query).get("unlock", [""])[0]
        return self.server.auth.unlocked(token)

    def visible(self, item):
        """Whether this request may see an archive. A private one is otherwise answered as if it did not exist."""
        return item is not None and (not is_private(item) or self.unlocked())

    def auth_state(self):
        return {"configured": self.server.auth.configured(), "signed_in": self.server.auth.session(self.session_token())}

    def asset(self, name):
        asset = WEB / name
        body = i18n.interface_script().encode() if name == "i18n.js" else asset.read_bytes()
        self.send_response(200)
        content_type = "application/manifest+json" if asset.suffix == ".webmanifest" else mimetypes.guess_type(asset.name)[0]
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.headers_sent = False
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            if self.headers_sent:
                # Part of a file or export is already out: an error message would corrupt it; cut the connection.
                log.exception("Read interrupted")
                self.close_connection = True
            elif isinstance(error, (FileNotFoundError, KeyError)):
                self.json({"error": "File not found."}, 404)
            else:
                log.exception("Read failed")
                self.json({"error": "The storage is not accessible. Check the server configuration."}, 503)

    def get(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/health":
            problems = self.server.problems()
            return self.json({"status": "error", "problems": problems} if problems else {"status": "ok"}, 503 if problems else 200)
        if not self.host_allowed():
            return
        if path in ASSETS:
            return self.asset(ASSETS[path])
        if path == "/api/auth":
            return self.json(self.auth_state())
        if path.startswith("/api/relay/"):
            return self.relay(path)
        signed_in = self.server.auth.session(self.session_token())
        if path == "/login":
            return self.redirect(local_path(parse_qs(url.query).get("next", ["/"])[0])) if signed_in else self.asset("login.html")
        if path in ("/", "/share"):
            if not signed_in:
                return self.redirect("/login" + ("?" + urlencode({"next": self.path}) if self.path != "/" else ""))
            return self.asset("index.html")
        if not self.authenticated():
            return
        if path == "/api/system":
            return self.json({"dependencies": dependencies(), "storage": self.server.storage.mode})
        if path == "/api/media/changes":
            query = parse_qs(url.query)
            private = self.unlocked()
            try:
                page = self.server.store.changes(int(query.get("since", ["0"])[0]), int(query.get("limit", ["100"])[0]), private)
            except ValueError:
                return self.json({"error": "Invalid pagination parameters."}, 400)
            # The interface hides private archives again when its token is no longer accepted.
            return self.json({**page, "unlocked": private})
        if path == "/api/articles/text":
            # The text of the web pages and of the videos' subtitles, for the interface's search when asked to look inside them. The viewer
            # rewritten in the background keeps the articles in memory, so this rarely reads the storage.
            private = self.unlocked()
            texts = {}
            for item in self.server.store.all():
                if item["status"] == "ready" and (private or not is_private(item)):
                    text = (viewer.article_text(item, self.server.storage) if is_article(item)
                            else viewer.subtitle_text(item, self.server.storage))
                    if text:
                        texts[item["id"]] = text
            return self.json({"texts": texts})
        if path == "/api/export":
            query = parse_qs(url.query)
            tags = set(clean_tags(query.get("tag", [])))
            untagged = query.get("untagged", [""])[0] == "1"
            # Private archives are only exported when asked for, while they are shown.
            private = query.get("private", [""])[0] == "1" and self.unlocked()
            items = [item for item in self.server.store.all() if not item["deleted_at"] and (private or not is_private(item))]
            if tags or untagged:
                items = [item for item in items if (untagged and not item["tags"]) or tags & set(item["tags"])]
            exported_at = export_time(query.get("at", [""])[0])
            return self.export(items, f"archiver-{exported_at[:10]}", exported_at)
        match = re.fullmatch(
            rf"/api/media/({MEDIA_ID_PATTERN})/(metadata|export|file/(?:original|playback|thumbnail|info|description|article|subtitles)(?:/[0-9]+)?)", path,
        )
        if match:
            item = self.server.store.get(match[1])
            if not self.visible(item):
                return self.json({"error": "Archive not found."}, 404)
            if match[2] == "metadata":
                return self.json({key: item[key] for key in (
                    "id", "title", "author", "published_at", "source_url", "platform",
                    "created_at", "duration", "width", "height", "description", "tags", "note", "custom_title", "attachments", "posts", "details",
                )})
            if match[2] == "export":
                return self.export([item], item["id"], export_time(parse_qs(url.query).get("at", [""])[0]))
            parts = match[2].split("/")
            return self.serve_media(item, parts[1], int(parts[2]) if len(parts) > 2 else None)
        self.json({"error": "Page not found."}, 404)

    def export(self, items, root, exported_at):
        """A ZIP of archives with the offline viewer, sized in advance so an interrupted download resumes."""
        export = Export(items, root, exported_at, self.server.storage, self.server.store)
        if_range = self.headers.get("If-Range")
        wanted = self.headers.get("Range") if not if_range or if_range == export.etag else None
        try:
            start, end, partial = byte_range(wanted, export.length)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{export.length}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{root}.zip"')
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", export.etag)
        self.send_header("Cache-Control", "private, no-cache")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{export.length}")
        self.end_headers()
        if self.command != "HEAD":
            export.send(self.wfile.write, start, end)

    def serve_media(self, item, kind, index=None):
        if item["status"] != "ready":
            return self.json({"error": "This archive is not available yet."}, 409)
        if kind in ("info", "description", "article"):
            if index is not None or (kind == "description" and "description.txt" not in item["files"]) or (kind == "article" and not is_article(item)):
                return self.json({"error": "File not found."}, 404)
            filename = {"info": "original.info.json", "description": "description.txt", "article": "article.html"}[kind]
        elif kind == "subtitles":
            tracks = (item["details"] or {}).get("subtitles") or []
            if index is None or index >= len(tracks):
                return self.json({"error": "File not found."}, 404)
            filename = tracks[index]["file"]
        elif index is not None:
            if index >= len(item["attachments"]):
                return self.json({"error": "Media not found."}, 404)
            filename = item["attachments"][index][kind]
        else:
            filename = item[kind]
        if not filename or Path(filename).name != filename:
            return self.json({"error": "File not found."}, 404)
        # The archive's page reads a PDF in a frame. Said on every answer, a "not modified" one included, since its
        # headers replace those the browser kept.
        self.framable = kind == "playback" and filename.lower().endswith(".pdf")
        # A file's hash identifies its content: a cached copy is confirmed without reading the storage.
        known = (item["files"].get(filename) or {}).get("sha256")
        etag = f'"{known[:32]}"' if known else None
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-cache")
            self.end_headers()
            return
        size = self.server.storage.size(item, filename)
        try:
            start, end, partial = byte_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # Open the object before sending success headers so storage failures remain actionable.
        stream = self.server.storage.open(item, filename, start, end) if self.command != "HEAD" and size else None
        try:
            self.send_response(206 if partial else 200)
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            if filename.endswith(".mkv"):
                content_type = "video/x-matroska"
            if kind == "subtitles":
                content_type = "text/vtt; charset=utf-8"
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            self.send_header("Cache-Control", "private, no-cache")
            if etag:
                self.send_header("ETag", etag)
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if kind == "article":
                # Generated from a fixed tag set; still never let it run anything if opened directly.
                self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; img-src 'self'")
            if kind in ("original", "info", "description"):
                self.send_header("Content-Disposition", f'attachment; filename="{item["id"]}-{filename}"')
            self.end_headers()
            if stream:
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        finally:
            if stream:
                stream.close()

    def body(self):
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("The request body must be JSON.")
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 16384:
            raise ValueError("Empty or too large request.")
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError("A JSON object is expected.")
        return data

    def do_POST(self):
        self.mutate()

    def do_PATCH(self):
        self.mutate()

    def mutate(self):
        if not self.host_allowed():
            return
        try:
            path = urlparse(self.path).path
            # Another origin is refused, as it would act with the browser's cookie. Not a client that sends its own
            # session token, such as the browser extension: a page cannot know that token, nor log in to get one,
            # since browsers do not let pages send JSON to another origin that has not allowed it.
            foreign = self.foreign() and not self.server.auth.session(self.bearer())
            if path.startswith("/api/auth/"):
                return self.auth_mutation(path, foreign)
            if foreign:
                return self.json({"error": "Request origin refused."}, 403)
            if path.startswith("/api/relay/"):
                with self.server.mutation_lock:
                    return self.relay(path)
            if not self.authenticated():
                return
            with self.server.mutation_lock:
                self.mutation()
        except TooManyAttempts:
            self.json({"error": TOO_MANY}, 429)
        except AccountExists:
            self.json({"error": "The account already exists. Log in."}, 409)
        except (ValueError, json.JSONDecodeError) as error:
            self.json({"error": str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("Mutation failed")
            self.json({"error": "The operation failed. Check the server logs."}, 500)

    def relay(self, path):
        """What a relay asks (see relay.py): the links this server failed to archive, to be left alone
        with one of them, and to take over the archive it made of it in the shared storage."""
        store, throttle = self.server.store, self.server.auth.throttle
        if not self.server.relay_token:
            return self.json({"error": "Action not found."}, 404)
        if throttle.blocked():
            return self.json({"error": TOO_MANY}, 429)
        if not relay.authorized(self.headers.get("Authorization"), self.server.relay_token):
            throttle.fail()
            return self.json({"error": "Log in to continue."}, 401)
        if path == "/api/relay/links" and self.command in ("GET", "HEAD"):
            return self.json({"links": store.relayable()})
        match = re.fullmatch(rf"/api/relay/links/({MEDIA_ID_PATTERN})", path)
        state = self.body().get("state") if match and self.command == "POST" else None
        if state == "taken":
            accepted = store.hold(match[1])
        elif state == "archived":
            accepted = store.retry(match[1])
            if accepted:
                self.server.worker.enqueue(match[1])
        else:
            return self.json({"error": "Action not found."}, 404)
        if not accepted:
            return self.json({"error": "Only a failed archive can be retried."}, 409)
        self.json({"id": match[1], "state": state})

    def auth_mutation(self, path, foreign=False):
        """Create the account on first launch, log in or log out.

        A login asking for a token (`"token": true`) gets the session's token in the answer instead of a cookie,
        for a client that is not a page of this site; it is the only request accepted from another origin."""
        auth = self.server.auth
        refused = {"error": "Request origin refused."}
        if foreign and path != "/api/auth/login":
            return self.json(refused, 403)
        if self.command != "POST":
            return self.json({"error": "Method not allowed."}, 405)
        data = self.body() if path in ("/api/auth/setup", "/api/auth/login") else {}
        wants_token = path == "/api/auth/login" and data.get("token") is True
        if foreign and not wants_token:
            return self.json(refused, 403)
        if path == "/api/auth/logout":
            if self.bearer():
                # A client's own session: the browser's, and what it unlocked, stay as they are.
                auth.close_session(self.bearer(), lock=False)
                return self.json({"signed_in": False})
            auth.close_session(self.session_token())
            return self.json({"signed_in": False}, cookie=self.session_cookie(""))
        if path == "/api/auth/setup":
            auth.create(data.get("username"), data.get("password"))
        elif path == "/api/auth/login":
            if not auth.configured():
                return self.json({"error": "No account has been created yet."}, 409)
            if not auth.check(data.get("username"), data.get("password")):
                return self.json({"error": "Incorrect username or password."}, 401)
        else:
            return self.json({"error": "Action not found."}, 404)
        if wants_token:
            return self.json({"signed_in": True, "token": auth.open_session(TOKEN_SECONDS), "expires_in": TOKEN_SECONDS})
        return self.json({"signed_in": True}, cookie=self.session_cookie(auth.open_session()))

    def tag_name(self, value):
        tags = clean_tags([value]) if isinstance(value, str) else []
        if not tags:
            raise ValueError("Enter a tag name.")
        return tags[0]

    def publish_manifests(self, ids):
        """Rewrite the archive.json of these archives, several at a time: with S3 storage, each one is a request."""
        def publish(media_id):
            try:
                manifest.publish(self.server.store, self.server.storage, media_id)
            except Exception:
                log.exception("archive.json not updated for %s", media_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(publish, ids))

    def tags_mutation(self, action):
        """Rename or delete a tag on every archive that has it, private ones included."""
        data = self.body()
        tag = self.tag_name(data.get("from" if action == "rename" else "tag"))
        if tag == PRIVATE:
            raise ValueError("The “private” tag cannot be renamed or deleted: remove it archive by archive.")
        if action == "rename":
            changed = self.server.store.rename_tag(tag, self.tag_name(data.get("to")))
        else:
            changed = self.server.store.delete_tag(tag)
        self.publish_manifests(changed)
        # While private archives are hidden, the count leaves them out: it would tell that some exist.
        return self.json({"updated": sum(self.visible(self.server.store.get(media_id)) for media_id in changed)})

    def purge(self, item):
        """Permanent deletion is an explicit operation, only allowed inside the trash."""
        if not item["deleted_at"] or item["status"] in ACTIVE:
            raise ValueError("Only an archive in the trash that is not being processed can be permanently deleted.")
        self.server.storage.remove(item)
        self.server.store.delete(item["id"])

    def batch_mutation(self):
        """Delete or restore several archives, or add a tag to several archives."""
        data = self.body()
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 1000 or not all(
                isinstance(media_id, str) and re.fullmatch(MEDIA_ID_PATTERN, media_id) for media_id in ids):
            raise ValueError("Select at least one archive.")
        items = [item for item in map(self.server.store.get, dict.fromkeys(ids)) if self.visible(item)]
        if data.get("action") in ("delete", "purge"):
            permanent = data["action"] == "purge"
            if permanent and any(not item["deleted_at"] or item["status"] in ACTIVE for item in items):
                raise ValueError("Only an archive in the trash that is not being processed can be permanently deleted.")
            deleted, skipped = 0, 0
            for item in items:
                if item["status"] in ACTIVE:
                    skipped += 1
                    continue
                if permanent:
                    self.purge(item)
                    deleted += 1
                else:
                    deleted += len(self.server.store.trash([item["id"]]))
            return self.json({"deleted": deleted, "skipped": skipped})
        if data.get("action") == "restore":
            return self.json({"restored": len(self.server.store.trash([item["id"] for item in items], deleted=False))})
        if data.get("action") == "tag":
            tag = self.tag_name(data.get("tag"))
            changed = [item for item in items if tag not in item["tags"]]
            for item in changed:
                self.server.store.update(item["id"], tags=[*item["tags"], tag])
            self.publish_manifests([item["id"] for item in changed])
            return self.json({"updated": len(changed)})
        raise ValueError("Unknown bulk action.")

    def private_mutation(self, action):
        """Show private archives for the account's password typed again, or hide them again."""
        data = self.body()
        if action == "lock":
            # A page being closed sends its token in the body: it cannot add headers.
            token = self.headers.get("X-Private-Unlock") or data.get("token")
            self.server.auth.lock(token if isinstance(token, str) and token else None)
            return self.json({"unlocked": False})
        token = self.server.auth.unlock(data.get("password") if isinstance(data.get("password"), str) else "")
        if not token:
            # Not 401: the session is still valid, only this password is wrong.
            return self.json({"error": "Incorrect password."}, 403)
        return self.json({"token": token, "expires_in": UNLOCK_SECONDS})

    def mutation(self):
        path = urlparse(self.path).path
        if path == "/api/account/password" and self.command == "POST":
            data = self.body()
            if not self.server.auth.change_password(data.get("current"), data.get("password")):
                return self.json({"error": "Incorrect current password."}, 403)
            # The other sessions are closed; this one goes on with a new session.
            return self.json({"changed": True}, cookie=self.session_cookie(self.server.auth.open_session()))
        if path in ("/api/private/unlock", "/api/private/lock") and self.command == "POST":
            return self.private_mutation(path.rsplit("/", 1)[1])
        if path in ("/api/tags/rename", "/api/tags/delete") and self.command == "POST":
            return self.tags_mutation(path.rsplit("/", 1)[1])
        if path == "/api/media/batch" and self.command == "POST":
            return self.batch_mutation()
        if path == "/api/trash/empty" and self.command == "POST":
            self.body()
            items = [item for item in self.server.store.trashed() if item["status"] not in ACTIVE and self.visible(item)]
            for item in items:
                self.purge(item)
            return self.json({"deleted": len(items)})
        if path == "/api/media" and self.command == "POST":
            data = self.body()
            missing = [name for name, present in dependencies().items() if not present]
            if missing:
                return self.json({"error": "Missing tools: " + ", ".join(missing) + ". See the README or use Docker."}, 503)
            # A page is asked for in the languages of the browser that archives it, else in the interface's.
            languages = net.languages(self.headers.get("Accept-Language")) or net.languages(self.headers.get("X-Language"))
            item, created = self.server.store.add(data.get("url"), data.get("tags", []), again=data.get("again") is True,
                                                  languages=languages)
            if created:
                self.server.worker.enqueue(item["id"])
            shown = [other for other in self.server.store.occurrences(item["id"]) if self.visible(other)]
            if not created and not self.visible(item):
                # The link's latest archive is private and hidden: answer with another archive of it, if any.
                item = next((other for other in shown if not other["deleted_at"]), shown[0] if shown else None)
            if not self.visible(item):
                # Nothing more is said about an archive while private archives are hidden.
                return self.json({"created": created, "private": True}, 201 if created else 200)
            # Adding again a link whose archives are all in the trash takes the latest one out.
            restored = bool(item["deleted_at"]) and bool(self.server.store.trash([item["id"]], deleted=False))
            if restored:
                item = self.server.store.get(item["id"])
            occurrences = [{key: other[key] for key in ("id", "created_at", "status", "deleted_at")}
                           for other in self.server.store.occurrences(item["id"]) if self.visible(other)]
            return self.json({"item": item, "created": created, "restored": restored, "occurrences": occurrences},
                             201 if created else 200)
        match = re.fullmatch(rf"/api/media/({MEDIA_ID_PATTERN})(/retry)?", path)
        if not match:
            return self.json({"error": "Action not found."}, 404)
        item = self.server.store.get(match[1])
        if not self.visible(item):
            return self.json({"error": "Archive not found."}, 404)
        if match[2] == "/retry" and self.command == "POST":
            self.body()
            if not self.server.store.retry(item["id"]):
                return self.json({"error": "Only a failed archive can be retried."}, 409)
            self.server.worker.enqueue(item["id"])
            return self.json(self.server.store.get(item["id"]))
        if not match[2] and self.command == "PATCH":
            data = self.body()
            values = {}
            if "tags" in data:
                values["tags"] = clean_tags(data["tags"])
            if "note" in data:
                values["note"] = clean_note(data["note"])
            if "custom_title" in data:
                values["custom_title"] = clean_title(data["custom_title"])
            if not values:
                raise ValueError("Nothing to change.")
            updated = self.server.store.update(item["id"], **values)
            try:
                manifest.publish(self.server.store, self.server.storage, item["id"])
            except Exception:
                log.exception("archive.json not updated for %s", item["id"])
            return self.json(updated)
        self.json({"error": "Method not allowed."}, 405)
