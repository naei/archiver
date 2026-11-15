"""Exports: a ZIP of archives whose size is known before the first byte is sent.

The ZIP holds the offline viewer and catalog.json at its root, and one folder
per archive inside FOLDER, so the page is not lost among hundreds of folders.

Entries are stored uncompressed (media are compressed already), in zip64 form,
with a data descriptor after each file so no file is read twice. Every size
comes from the catalog, so the whole layout is fixed in advance: the response
has a Content-Length, and any byte range can be produced, so an interrupted
download resumes where it stopped instead of starting over.

The CRC of each file, needed by the ZIP format, is recorded in the catalog
(manifest.hash_stream). A file whose CRC is not known yet is read in full and
its CRC remembered for the next export."""
import hashlib
import json
import logging
import struct
import zlib
from datetime import date

from . import manifest, viewer

log = logging.getLogger(__name__)
ZIP64 = 0xFFFFFFFF
VERSION = 45  # zip64
FLAGS = 0x0808  # CRC and sizes in a descriptor after the data; UTF-8 names
UNIX = 3 << 8
CHUNK = 1024 * 1024
FOLDER = "media"


class Entry:
    __slots__ = ("name", "size", "data", "item", "filename", "crc", "offset")

    def __init__(self, name, size, data=None, item=None, filename=None, crc=None):
        self.name = name.encode("utf-8")
        self.size = size
        self.data = data
        self.item = item
        self.filename = filename
        self.crc = crc
        self.offset = 0

    @property
    def header_size(self):
        return 30 + len(self.name) + 20

    @property
    def total(self):
        return self.header_size + self.size + 24

    @property
    def central_size(self):
        return 46 + len(self.name) + 28


class Export:
    def __init__(self, items, root, exported_at, storage, store=None):
        self.storage = storage
        self.store = store
        day = date.fromisoformat(exported_at[:10])
        # ZIP timestamps have a seven-bit year offset from 1980. Keep the requested date
        # in the catalog, but clamp file timestamps so an unusual client clock cannot break an export.
        day = min(max(day, date(1980, 1, 1)), date(2107, 12, 31))
        self.dos_date = ((day.year - 1980) << 9) | (day.month << 5) | day.day
        self.records = viewer.records(items, storage)
        page = viewer.build(self.records, exported_at, f"{FOLDER}/")
        catalog = json.dumps(self.records, ensure_ascii=False, indent=2).encode("utf-8")
        self.entries = [Entry(f"{root}/index.html", len(page), data=page, crc=zlib.crc32(page)),
                        Entry(f"{root}/catalog.json", len(catalog), data=catalog, crc=zlib.crc32(catalog))]
        digest = hashlib.sha256(page + catalog)
        written_at = f"{exported_at[:10]}T00:00:00+00:00"
        for item in items:
            if item["status"] != "ready":
                continue
            files = item["files"]
            # The record is rebuilt from the catalog: always current, and sized without a storage call.
            record = manifest.render(item, written_at)
            self.entries.append(Entry(f"{root}/{FOLDER}/{item['id']}/{manifest.NAME}", len(record), data=record, crc=zlib.crc32(record)))
            digest.update(record)
            for name in sorted(name for name in files if name != manifest.NAME):
                known = files.get(name) or {}
                size = known.get("size")
                if size is None:
                    try:
                        size = self.storage.size(item, name)
                    except Exception as error:
                        log.warning("Export: %s/%s skipped (%s)", item["id"], name, error)
                        continue
                self.entries.append(Entry(f"{root}/{FOLDER}/{item['id']}/{name}", size, item=item, filename=name, crc=known.get("crc32")))
                digest.update(f"{item['id']}/{name}:{size}:{known.get('sha256', '')}".encode())
        offset = 0
        for entry in self.entries:
            entry.offset = offset
            offset += entry.total
        self.central_offset = offset
        self.central_size = sum(entry.central_size for entry in self.entries)
        self.length = offset + self.central_size + 56 + 20 + 22
        self.etag = f'"{digest.hexdigest()[:32]}"'
        self.pos = 0

    def local_header(self, entry):
        extra = struct.pack("<HHQQ", 1, 16, entry.size, entry.size)
        return struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", VERSION, FLAGS, 0, 0, self.dos_date, 0, ZIP64, ZIP64,
                           len(entry.name), len(extra)) + entry.name + extra

    def central_header(self, entry):
        extra = struct.pack("<HHQQQ", 1, 24, entry.size, entry.size, entry.offset)
        return struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", UNIX | VERSION, VERSION, FLAGS, 0, 0, self.dos_date,
                           entry.crc or 0, ZIP64, ZIP64, len(entry.name), len(extra), 0, 0, 0, 0o100644 << 16, ZIP64) + entry.name + extra

    def end_records(self):
        count = len(self.entries)
        zip64_end = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, UNIX | VERSION, VERSION, 0, 0, count, count,
                                self.central_size, self.central_offset)
        locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, self.central_offset + self.central_size, 1)
        end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, ZIP64, ZIP64, 0)
        return zip64_end + locator + end

    def send(self, write, start, end):
        """Write the bytes from `start` to `end` (inclusive) of the ZIP."""
        self.pos = 0

        def emit(data):
            first, last = max(start, self.pos), min(end + 1, self.pos + len(data))
            if first < last:
                write(data[first - self.pos:last - self.pos])
            self.pos += len(data)

        for entry in self.entries:
            if self.pos > end:
                return  # the requested range is served
            emit(self.local_header(entry))
            self.send_data(entry, write, start, end)
            emit(struct.pack("<4sIQQ", b"PK\x07\x08", entry.crc or 0, entry.size, entry.size))
        for entry in self.entries:
            emit(self.central_header(entry))
        emit(self.end_records())

    def send_data(self, entry, write, start, end):
        first, last = max(start, self.pos), min(end + 1, self.pos + entry.size)
        wanted = first < last
        if entry.data is not None:
            if wanted:
                write(entry.data[first - self.pos:last - self.pos])
        # The CRC is needed by the descriptor right after the data, and by the central directory at the end.
        elif wanted or (entry.crc is None and end >= self.pos + entry.size):
            # Without a known CRC the whole file is read, and only the wanted part sent.
            whole = entry.crc is None
            lower, upper = (0, entry.size - 1) if whole else (first - self.pos, last - 1 - self.pos)
            crc, done, complete = 0, 0, True
            try:
                stream = self.storage.open(entry.item, entry.filename, lower, upper)
            except Exception as error:
                log.warning("Export: %s unreadable (%s)", entry.name.decode(), error)
                stream = None
            try:
                remaining = upper - lower + 1
                while remaining > 0:
                    try:
                        chunk = stream.read(min(CHUNK, remaining)) if stream else b""
                    except Exception as error:
                        log.warning("Export: %s interrupted (%s)", entry.name.decode(), error)
                        chunk = b""
                    if not chunk:
                        # Missing or shorter than recorded: pad to keep the layout; the entry fails its CRC check.
                        chunk, complete = b"\0" * min(remaining, CHUNK), False
                    position = self.pos + lower + done
                    chunk_first, chunk_last = max(first, position), min(last, position + len(chunk))
                    if chunk_first < chunk_last:
                        write(chunk[chunk_first - position:chunk_last - position])
                    if whole:
                        crc = zlib.crc32(chunk, crc)
                    done += len(chunk)
                    remaining -= len(chunk)
            finally:
                if stream:
                    stream.close()
            if whole and complete:
                entry.crc = crc
                self.remember(entry)
        self.pos += entry.size

    def remember(self, entry):
        """Keep a CRC read during this export, at once: an interrupted download does not read the file again."""
        if not self.store:
            return
        item = self.store.get(entry.item["id"])
        known = (item or {}).get("files", {}).get(entry.filename)
        if known and known.get("size") == entry.size and "crc32" not in known:
            known["crc32"] = entry.crc
            self.store.update(item["id"], files=item["files"])
