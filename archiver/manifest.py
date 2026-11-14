"""archive.json: a self-describing record stored with each archive's files.

It lets an archive be understood without the application or its database: what
was archived, from where and when, the personal tags and note, and the sha256 of
every file, used to detect corruption over the years. The CRC-32 of each file
lets an export be assembled with its size known in advance (see export.py)."""
import hashlib
import json
import logging
import zlib
from datetime import datetime, timezone

log = logging.getLogger(__name__)

NAME = "archive.json"
FORMAT = "archiver/1"
FIELDS = ("id", "platform", "source_url", "title", "author", "published_at", "created_at", "duration",
          "width", "height", "description", "tags", "note", "custom_title", "details", "posts", "attachments")


def hash_stream(stream):
    digest, size, crc = hashlib.sha256(), 0, 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
        crc = zlib.crc32(chunk, crc)
    return {"sha256": digest.hexdigest(), "size": size, "crc32": crc}


def same_content(actual, expected):
    """Whether a file still matches its record; older records have no CRC."""
    return actual is not None and actual["sha256"] == expected.get("sha256") and actual["size"] == expected.get("size")


def hash_file(path):
    with path.open("rb") as stream:
        return hash_stream(stream)


def file_records(files):
    """The sha256 and size of each file; the CRC is an internal detail of exports."""
    return {name: {key: value for key, value in record.items() if key != "crc32"} for name, record in (files or {}).items()}


def render(item, written_at=None):
    record = {"format": FORMAT, **{key: item.get(key) for key in FIELDS}, "files": file_records(item.get("files")),
              "written_at": written_at or datetime.now(timezone.utc).isoformat()}
    record["archived_at"] = record.pop("created_at")
    return json.dumps(record, ensure_ascii=False, indent=2).encode()


def publish(store, storage, media_id):
    """Rewrite archive.json after the catalog changed (tags, note, checks)."""
    item = store.get(media_id)
    if item and item["status"] == "ready" and item["files"]:
        storage.put(item, NAME, render(item))


def restore(store, storage):
    """Add to the catalog every archive of the storage that it lacks, from its archive.json.

    Archives already in the catalog are left as they are. Returns the counts of
    archives added, already known, and whose record could not be read."""
    added = known = unreadable = 0
    for folder, data, encrypted in storage.records(NAME):
        try:
            if data is None:
                raise ValueError("encrypted, and not readable with this ENCRYPTION_KEY")
            record = json.loads(data)
            if record.get("format") != FORMAT or not isinstance(record.get("id"), str) or storage.folder({"id": record.get("id"), "encrypted": encrypted}) != folder:
                raise ValueError("not an archive record of this folder")
            if store.restore(record, storage.mode, len(data), encrypted):
                added += 1
            else:
                known += 1
        except (KeyError, TypeError, ValueError) as error:
            log.warning("Folder %s: %s not restored (%s)", folder, NAME, error)
            unreadable += 1
    return added, known, unreadable
