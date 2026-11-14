import hashlib
import ipaddress
import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import integrations


ACTIVE = ("queued", "downloading", "processing", "uploading")
TRASH_DAYS = 30
# Pauses before each automatic retry of a failed archive; after the last one, retrying is left to the user.
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=1), timedelta(hours=6), timedelta(hours=12))
# A link archived again gets the id of its first archive followed by ".2", ".3"…
# The reserved tag of private archives, hidden until the account's password is entered again. It is stored
# under this name whatever the language; the interface shows it translated, and its names below are accepted.
PRIVATE = "private"
PRIVATE_NAMES = {"private", "privé", "prive"}
PUBLIC_SQL = f" WHERE NOT EXISTS (SELECT 1 FROM json_each(media.tags) WHERE value = '{PRIVATE}')"
MEDIA_ID_PATTERN = (r"(?:web-[0-9a-f]{16}|" + "|".join(module.ID for module in integrations.ALL)
                    + r")(?:\.[0-9]{1,4})?")


def now():
    return datetime.now(timezone.utc).isoformat()


def link_id(media_id):
    """The id shared by every archive of a link, which holds the platform's own identifier."""
    return media_id.partition(".")[0]


# Platforms whose pages need dedicated support; archiving them as web pages gives login walls.
UNSUPPORTED = {"instagr.am": "Instagram", "threads.net": "Threads", "threads.com": "Threads", "b23.tv": "b23.tv"}
TRACKING = re.compile(r"utm_\w+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|yclid|mc_cid|mc_eid|igshid|_hsenc|_hsmi|mkt_tok|ref_src|oly_\w+")


def web_url(parsed):
    host = (parsed.hostname or "").rstrip(".").lower()
    platform = next((name for domain, name in UNSUPPORTED.items() if host == domain or host.endswith("." + domain)), None)
    if platform:
        raise ValueError(f"{platform} links are not supported yet.")
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip or "." not in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise ValueError("Use the public address of a web page, with a domain name.")
    query = sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not TRACKING.fullmatch(key))
    url = urlunparse((parsed.scheme, host, parsed.path or "/", "", urlencode(query), ""))
    return "web-" + hashlib.sha256(url.encode()).hexdigest()[:16], url


def source_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Enter a link to a video, a post or a web page.")
    value = value.strip()
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError()
    except ValueError:
        raise ValueError("This link is not valid: use a public http or https address.")
    found = integrations.link(parsed)
    if found:
        return found
    media_id, url = web_url(parsed)
    return media_id, url, "Web"


def clean_note(note):
    if not isinstance(note, str) or len(note) > 5000:
        raise ValueError("The note must be a text of at most 5,000 characters.")
    return note.strip()


def clean_title(title):
    """A personal title, shown in place of the archive's own; empty to go back to it."""
    if not isinstance(title, str) or len(title) > 200:
        raise ValueError("The title must be a text of at most 200 characters.")
    return " ".join(title.split())


def is_article(item):
    """A web page archived as an article, rather than a link straight to a file (image, video, sound, PDF)."""
    return item["platform"] == "Web" and (item.get("details") or {}).get("kind") != "file"


def is_private(item):
    return PRIVATE in item["tags"]


def clean_tags(tags):
    if not isinstance(tags, list) or len(tags) > 30:
        raise ValueError("Add at most 30 tags.")
    result = []
    for tag in tags:
        if not isinstance(tag, str) or len(tag.strip()) > 40:
            raise ValueError("Each tag must have at most 40 characters.")
        tag = " ".join(tag.strip().lower().split())
        if tag in PRIVATE_NAMES:
            tag = PRIVATE
        if tag and tag not in result:
            result.append(tag)
    return result


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "catalog.sqlite3"
        # Set when the library changed in a way its readers can see (not progress); cleared by the viewer writer.
        self.changed = threading.Event()
        self.changed.set()
        self.progress_written = {}
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS media (
                id TEXT PRIMARY KEY, source_url TEXT NOT NULL, platform TEXT DEFAULT 'YouTube',
                title TEXT, author TEXT, published_at TEXT, created_at TEXT NOT NULL,
                duration REAL, width INTEGER, height INTEGER, description TEXT,
                status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
                error TEXT, tags TEXT NOT NULL DEFAULT '[]', original TEXT, playback TEXT,
                thumbnail TEXT, size INTEGER NOT NULL DEFAULT 0,
                storage_bytes INTEGER NOT NULL DEFAULT 0, storage TEXT NOT NULL DEFAULT 'local'
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(media)")}
            if "attachments" not in columns:
                db.execute("ALTER TABLE media ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
            if "posts" not in columns:
                db.execute("ALTER TABLE media ADD COLUMN posts TEXT NOT NULL DEFAULT '[]'")
            for column, definition in (
                ("details", "TEXT NOT NULL DEFAULT '{}'"),
                ("note", "TEXT NOT NULL DEFAULT ''"),
                ("custom_title", "TEXT NOT NULL DEFAULT ''"),
                # Fixity: sha256 and size of each stored file, and the last verification.
                ("files", "TEXT NOT NULL DEFAULT '{}'"), ("integrity", "TEXT"), ("integrity_checked_at", "TEXT"),
                # Whether the original is still online: online, gone or unknown.
                ("availability", "TEXT"), ("availability_checked_at", "TEXT"), ("gone_since", "TEXT"),
                # In the trash since then; deleted for good after TRASH_DAYS.
                ("deleted_at", "TEXT"),
                # Automatic retries of a failed archive: how many were made, and when the next one is due.
                ("retries", "INTEGER NOT NULL DEFAULT 0"), ("retry_at", "TEXT"),
                # Whether the stored files are encrypted (see crypto.py).
                ("encrypted", "INTEGER NOT NULL DEFAULT 0"),
                # The languages preferred by the browser that asked for the archive, asked of the site in turn.
                ("languages", "TEXT"),
            ):
                if column not in columns:
                    db.execute(f"ALTER TABLE media ADD COLUMN {column} {definition}")
            # One latest revision per archive, including tombstones. Triggers cover every writer,
            # including the download worker and maintenance, in the same transaction as the change.
            db.execute("""CREATE TABLE IF NOT EXISTS media_changes (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, media_id TEXT NOT NULL UNIQUE,
                deleted INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("INSERT OR IGNORE INTO media_changes (media_id) SELECT id FROM media ORDER BY created_at DESC, id")
            for event, row, deleted in (("INSERT", "NEW", 0), ("UPDATE", "NEW", 0), ("DELETE", "OLD", 1)):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS media_sync_{event.lower()} AFTER {event} ON media
                    BEGIN
                        DELETE FROM media_changes WHERE media_id = {row}.id;
                        INSERT INTO media_changes (media_id, deleted) VALUES ({row}.id, {deleted});
                    END""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def decode(row):
        if row is None:
            return None
        item = dict(row)
        item["tags"] = json.loads(item["tags"])
        item["attachments"] = json.loads(item["attachments"])
        item["posts"] = json.loads(item["posts"])
        item["details"] = json.loads(item["details"])
        item["files"] = json.loads(item["files"])
        # Older YouTube archives retain their files and URLs after migration.
        if not item["attachments"] and item["original"]:
            item["attachments"] = [{
                "type": "video", **{key: item[key] for key in (
                    "original", "playback", "thumbnail", "width", "height", "duration", "size",
                )},
            }]
        # X archives made before threads were supported hold a single post.
        if item["platform"] == "X" and item["status"] == "ready" and not item["posts"]:
            item["posts"] = [{
                "id": link_id(item["id"]).removeprefix("x-"), "url": item["source_url"], "author": item["author"],
                "handle": None, "published_at": item["published_at"], "text": item["description"] or "",
                "quote": None, "media": list(range(len(item["attachments"]))),
            }]
        return item

    def get(self, media_id):
        with closing(self.connect()) as db:
            return self.decode(db.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone())

    def all(self):
        with closing(self.connect()) as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM media ORDER BY created_at DESC")]

    def changes(self, since=0, limit=100, private=True):
        """A bounded page of changed archives. The cursor survives server restarts.

        Readers see each page in one SQLite snapshot. If an archive changes again while
        paging, its newer revision is sent on a later page rather than being missed.
        Without `private`, private archives are sent as removed and left out of the total.
        """
        if since < 0 or not 1 <= limit <= 200:
            raise ValueError("Invalid pagination parameters.")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            latest = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM media_changes").fetchone()[0]
            reset = since > latest
            if reset:
                since = 0
            rows = db.execute("SELECT * FROM media_changes WHERE sequence > ? ORDER BY sequence LIMIT ?",
                              (since, limit + 1)).fetchall()
            page = rows[:limit]
            items, removed = [], []
            for row in page:
                item = None if row["deleted"] else self.decode(
                    db.execute("SELECT * FROM media WHERE id = ?", (row["media_id"],)).fetchone())
                if item and (private or not is_private(item)):
                    items.append(item)
                else:
                    removed.append(row["media_id"])
            total = db.execute("SELECT COUNT(*) FROM media" + ("" if private else PUBLIC_SQL)).fetchone()[0]
            return {"items": items, "removed": removed, "cursor": page[-1]["sequence"] if page else since,
                    "has_more": len(rows) > limit, "total": total, "reset": reset}

    def occurrences(self, media_id):
        """Every archive of the same link, newest first."""
        base = link_id(media_id)
        with closing(self.connect()) as db:
            return [self.decode(row) for row in db.execute(
                "SELECT * FROM media WHERE id = ? OR id GLOB ? ORDER BY created_at DESC, id DESC", (base, base + ".[0-9]*"))]

    def add(self, url, tags, again=False, languages=None):
        """Archive a link, or with `again` archive once more a link already archived.

        `languages` is the Accept-Language header of the browser that asks, sent to the site when it is downloaded.

        Returns (archive, created). When nothing is created, the archive is the link's latest one outside the trash,
        else its latest one. A link is not archived again while one of its archives is in progress."""
        media_id, url, platform = source_url(url)
        tags = clean_tags(tags)
        existing = self.occurrences(media_id)
        if existing and (not again or any(item["status"] in ACTIVE for item in existing)):
            return next((item for item in existing if not item["deleted_at"]), existing[0]), False
        if existing:
            media_id += "." + str(max(int(item["id"].partition(".")[2] or 1) for item in existing) + 1)
        # Until the real title is known, the link stands for it: it reads the same in every language.
        with closing(self.connect()) as db, db:
            created = db.execute(
                "INSERT OR IGNORE INTO media (id, source_url, platform, title, created_at, tags, languages) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (media_id, url, platform, url, now(), json.dumps(tags), languages),
            ).rowcount == 1
        if created:
            self.changed.set()
        return self.get(media_id), created

    @staticmethod
    def record_values(record, storage, record_size, encrypted=False):
        """The catalog columns of a ready archive described by its archive.json."""
        if not re.fullmatch(MEDIA_ID_PATTERN, record["id"]):
            raise ValueError("unknown archive id")
        attachments, files = record.get("attachments") or [], record.get("files") or {}
        cover = next((asset for asset in attachments if asset.get("cover")), attachments[0] if attachments else {})
        web = is_article(record)
        values = {
            **{key: record.get(key) for key in ("id", "platform", "source_url", "title", "author", "published_at",
                                                "duration", "width", "height", "description")},
            "created_at": record["archived_at"], "status": "ready", "progress": 100, "storage": storage,
            "encrypted": int(encrypted),
            "tags": json.dumps(clean_tags(record.get("tags") or [])), "note": record.get("note") or "",
            "custom_title": clean_title(record.get("custom_title") or ""),
            "original": None if web else cover.get("original"), "playback": None if web else cover.get("playback"),
            "thumbnail": cover.get("thumbnail"), "size": sum(asset.get("size") or 0 for asset in attachments),
            "storage_bytes": sum(entry["size"] for entry in files.values()) + record_size,
            **{key: json.dumps(record.get(key) or default, ensure_ascii=False)
               for key, default in (("attachments", []), ("posts", []), ("details", {}), ("files", {}))},
        }
        return values

    def restore(self, record, storage, record_size, encrypted=False):
        """Insert a ready archive described by its archive.json; False if the catalog already has it."""
        values = self.record_values(record, storage, record_size, encrypted)
        with closing(self.connect()) as db, db:
            added = db.execute(f"INSERT OR IGNORE INTO media ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                               tuple(values.values())).rowcount == 1
        if added:
            self.changed.set()
        return added

    def adopt(self, record, storage, record_size, encrypted=False):
        """Turn an archive in progress into the ready archive described by its archive.json, made elsewhere.

        It keeps when the link was added here, and merges its tags, note and personal title with the record's."""
        item = self.get(record["id"])
        values = self.record_values(record, storage, record_size, encrypted)
        del values["id"], values["created_at"]
        values.update(tags=json.dumps(clean_tags([*item["tags"], *(record.get("tags") or [])])),
                      note=item["note"] or values["note"],
                      custom_title=item["custom_title"] or values["custom_title"], error=None)
        with closing(self.connect()) as db, db:
            db.execute(f"UPDATE media SET {', '.join(key + ' = ?' for key in values)} WHERE id = ?",
                       (*values.values(), item["id"]))
        self.changed.set()

    def ping(self):
        with closing(self.connect()) as db:
            db.execute("SELECT 1 FROM media LIMIT 1").fetchall()

    def update(self, media_id, **values):
        """Write some columns of an archive and return it; progress alone is written at most once a second."""
        if values.keys() == {"progress"}:
            if time.monotonic() - self.progress_written.get(media_id, 0) < 1:
                return None
            self.progress_written[media_id] = time.monotonic()
        # Column names are internal constants, never user-supplied.
        if "tags" in values:
            values["tags"] = json.dumps(clean_tags(values["tags"]))
        for key in ("attachments", "posts", "details", "files"):
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        with closing(self.connect()) as db, db:
            db.execute(f"UPDATE media SET {', '.join(key + ' = ?' for key in values)} WHERE id = ?",
                       (*values.values(), media_id))
        if not values.keys() <= {"progress", "files", "integrity_checked_at", "availability_checked_at"}:
            self.changed.set()
        return self.get(media_id)

    def retag(self, change):
        """Apply `change(tags) -> tags` to every archive; returns the ids of the archives that changed."""
        changed = []
        with closing(self.connect()) as db, db:
            for row in db.execute("SELECT id, tags FROM media").fetchall():
                tags = json.loads(row["tags"])
                updated = clean_tags(change(tags))
                if updated != tags:
                    db.execute("UPDATE media SET tags = ? WHERE id = ?", (json.dumps(updated), row["id"]))
                    changed.append(row["id"])
        if changed:
            self.changed.set()
        return changed

    def rename_tag(self, old, new):
        # Renaming to an existing tag merges both; clean_tags removes the duplicate.
        return self.retag(lambda tags: [new if tag == old else tag for tag in tags])

    def delete_tag(self, tag):
        return self.retag(lambda tags: [value for value in tags if value != tag])

    def due(self, column, before, extra="1"):
        """The ready archive whose `column` check is the oldest and older than `before`, or None."""
        # Column names and conditions are internal constants, never user-supplied.
        with closing(self.connect()) as db:
            return self.decode(db.execute(
                f"SELECT * FROM media WHERE status = 'ready' AND deleted_at IS NULL AND ({column} IS NULL OR {column} < ?) AND ({extra}) "
                f"ORDER BY {column} IS NOT NULL, {column} LIMIT 1", (before,),
            ).fetchone())

    def trash(self, ids, deleted=True):
        """Move archives to the trash, or take them out; returns the ids that changed."""
        changed = []
        with closing(self.connect()) as db, db:
            for media_id in ids:
                if db.execute(f"UPDATE media SET deleted_at = ? WHERE id = ? AND deleted_at IS {'' if deleted else 'NOT '}NULL",
                              (now() if deleted else None, media_id)).rowcount:
                    changed.append(media_id)
        if changed:
            self.changed.set()
        return changed

    def trashed(self, before="9999"):
        """Archives in the trash since before `before`."""
        with closing(self.connect()) as db:
            return [self.decode(row) for row in db.execute(
                "SELECT * FROM media WHERE deleted_at IS NOT NULL AND deleted_at < ? ORDER BY deleted_at", (before,))]

    def claim(self, media_id):
        with closing(self.connect()) as db, db:
            return db.execute(
                "UPDATE media SET status = 'downloading', error = NULL, progress = 0 WHERE id = ? AND status = 'queued'",
                (media_id,),
            ).rowcount == 1

    def recover(self):
        with closing(self.connect()) as db, db:
            db.execute("""UPDATE media SET status = 'queued', progress = 0
                       WHERE status IN ('downloading', 'processing', 'uploading')""")

    def fail(self, media_id, error):
        """Record a failed archiving, and when it is retried automatically while RETRY_DELAYS allows."""
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT retries FROM media WHERE id = ?", (media_id,)).fetchone()
            if row is None:
                return
            due = (datetime.now(timezone.utc) + RETRY_DELAYS[row["retries"]]).isoformat() \
                if row["retries"] < len(RETRY_DELAYS) else None
            db.execute("UPDATE media SET status = 'error', error = ?, retry_at = ? WHERE id = ?", (error, due, media_id))
        self.changed.set()

    def due_retries(self):
        """Failed archives outside the trash whose automatic retry is due."""
        with closing(self.connect()) as db:
            return [row["id"] for row in db.execute(
                "SELECT id FROM media WHERE status = 'error' AND retry_at <= ? AND deleted_at IS NULL ORDER BY retry_at",
                (now(),))]

    def retry(self, media_id, automatic=False):
        """Queue a failed archive again. Retried by the user, it gets a fresh series of automatic retries."""
        retries = "retries + 1" if automatic else "0"
        with closing(self.connect()) as db, db:
            return db.execute(
                f"""UPDATE media SET status = 'queued', error = NULL, progress = 0, retries = {retries}, retry_at = NULL
                    WHERE id = ? AND status = 'error'""",
                (media_id,),
            ).rowcount == 1

    def relayable(self):
        """Failed archives outside the trash that a relay may make in this server's place, oldest first.

        Only a link's first archive: a relay names what it archives after the link alone."""
        with closing(self.connect()) as db:
            return [{"id": row["id"], "url": row["source_url"], "languages": row["languages"]} for row in db.execute(
                "SELECT id, source_url, languages FROM media WHERE status = 'error' AND deleted_at IS NULL AND instr(id, '.') = 0 "
                "ORDER BY created_at, id")]

    def hold(self, media_id):
        """Leave a failed archive to a relay: no automatic retry starts while the relay downloads it."""
        with closing(self.connect()) as db, db:
            return db.execute("UPDATE media SET retry_at = NULL WHERE id = ? AND status = 'error' AND deleted_at IS NULL",
                              (media_id,)).rowcount == 1

    def delete(self, media_id):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM media WHERE id = ?", (media_id,))
        self.changed.set()
