"""Where archives' files are kept: a folder or an S3 bucket, encrypted or not.

An archive remembers its storage mode and whether its files are encrypted, so a
library may mix both after a change of settings. Encrypted files carry age's
extension (video.mp4.age) and sit in a folder whose name does not show the
archive's id; the rest of the application names archives and files as usual
and reads them decrypted (see crypto.py)."""
import hashlib
import os
import shutil
from pathlib import Path

from . import crypto, manifest
from .crypto import SUFFIX


class Hashing:
    """A stream that hashes what is read through it."""

    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, amount=-1):
        data = self.stream.read(amount)
        self.digest.update(data)
        return data


class Storage:
    def __init__(self, root):
        self.root = Path(root)
        self.mode = os.getenv("STORAGE_BACKEND", "local")
        if self.mode not in ("local", "s3"):
            raise ValueError("STORAGE_BACKEND must be local or s3.")
        self.bucket = os.getenv("S3_BUCKET", "")
        self.prefix = os.getenv("S3_PREFIX", "archiver").strip("/")
        self.client = None
        identity = os.getenv("ENCRYPTION_KEY", "").strip()
        # With a key, new archives are encrypted before they are stored; without one, encrypted archives cannot be read.
        self.encryption = crypto.Key(identity) if identity else None
        if self.mode == "s3":
            if not self.bucket:
                raise ValueError("S3_BUCKET est requis pour le stockage S3.")
            import boto3
            from botocore.config import Config
            self.client = boto3.client(
                "s3",
                endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
                region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": os.getenv("S3_ADDRESSING_STYLE", "auto")},
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )

    def directory(self, media_id):
        return self.root / "media" / media_id

    def key(self, media_id, filename):
        return "/".join(part for part in (self.prefix, media_id, filename) if part)

    def folder(self, item):
        """The name of an archive's folder in the storage: its id, or a keyed hash of it when encrypted."""
        if item.get("folder"):
            return item["folder"]  # a folder found in the storage, whose archive is not known yet
        return self.reading_key().folder(item["id"]) if item.get("encrypted") else item["id"]

    def stored(self, item, filename):
        """The name a file is stored under."""
        return filename + SUFFIX if item.get("encrypted") else filename

    def reading_key(self):
        if self.encryption is None:
            raise ValueError("The encryption key (ENCRYPTION_KEY) is needed to read this archive.")
        return self.encryption

    def fetch(self, item, name, first, last):
        """A stream of these bytes of a file as it is stored, and the file's whole size."""
        if item["storage"] == "s3":
            if self.client is None:
                raise ValueError("The S3 configuration is needed to read this archive.")
            response = self.client.get_object(Bucket=self.bucket, Key=self.key(self.folder(item), name), Range=f"bytes={first}-{last}")
            return response["Body"], int(response["ContentRange"].rpartition("/")[2])
        file = (self.directory(self.folder(item)) / name).open("rb")
        file.seek(first)
        return file, os.fstat(file.fileno()).st_size

    def write(self, item, name, stream):
        """Store a stream under this exact name; a local file is replaced in one step."""
        if item["storage"] == "s3":
            if self.client is None:
                raise ValueError("The S3 configuration is needed to modify this archive.")
            self.client.upload_fileobj(stream, self.bucket, self.key(self.folder(item), name))
            return
        path = self.directory(self.folder(item)) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        with temp.open("wb") as target:
            while chunk := stream.read(1024 * 1024):
                target.write(chunk)
        temp.replace(path)

    def publish(self, media_id, files):
        """Put an archive's finished files in the storage; returns whether they were encrypted.

        Without a key, local files are already in place. With one, they are written encrypted to the
        archive's own folder, and the folder they were prepared in is removed by `cleanup_work`."""
        target = {"id": media_id, "storage": self.mode, "encrypted": True}
        for file in files:
            if self.encryption:
                with file.open("rb") as stream:
                    self.write(target, file.name + SUFFIX, crypto.Encrypting(stream, self.encryption))
            elif self.mode == "s3":
                self.client.upload_file(str(file), self.bucket, self.key(media_id, file.name))
        return self.encryption is not None

    def cleanup_work(self, media_id):
        """Remove the folder an archive was prepared in, unless it is where the archive is kept."""
        if self.mode == "s3" or self.encryption:
            shutil.rmtree(self.directory(media_id), ignore_errors=True)

    def remove(self, item):
        if item["storage"] == "s3":
            if self.client is None:
                raise ValueError("Restore the S3 configuration to delete this archive.")
            prefix = self.key(self.folder(item), "") + "/"
            pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
            for page in pages:
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if keys:
                    result = self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
                    if result.get("Errors"):
                        raise RuntimeError("The S3 storage refused to delete some files.")
        shutil.rmtree(self.directory(self.folder(item)), ignore_errors=True)
        shutil.rmtree(self.directory(item["id"]), ignore_errors=True)  # where it was prepared

    def size(self, item, filename):
        """The size of a file's content, whatever the form it is stored in."""
        if item.get("encrypted"):
            # Recorded with the file's hash; otherwise worked out from the encrypted file.
            known = (item.get("files") or {}).get(filename, {}).get("size")
            if known is not None:
                return known
            key = self.reading_key()
            return crypto.size(lambda first, last: self.fetch(item, filename + SUFFIX, first, last), key)
        if item["storage"] == "s3":
            if self.client is None:
                raise ValueError("The S3 configuration is needed to read this archive.")
            return self.client.head_object(Bucket=self.bucket, Key=self.key(self.folder(item), filename))["ContentLength"]
        return (self.directory(self.folder(item)) / filename).stat().st_size

    def open(self, item, filename, start, end):
        """A readable stream of the bytes `start` to `end` (inclusive) of a file's content."""
        if item.get("encrypted"):
            key = self.reading_key()
            return crypto.reader(lambda first, last: self.fetch(item, filename + SUFFIX, first, last), key, start, end)
        return self.fetch(item, filename, start, end)[0]

    def read(self, item, filename):
        """A readable stream of a whole stored file's content."""
        if item.get("encrypted"):
            key = self.reading_key()
            return crypto.reader(lambda first, last: self.fetch(item, filename + SUFFIX, first, last), key)
        if item["storage"] == "s3":
            if self.client is None:
                raise ValueError("The S3 configuration is needed to read this archive.")
            return self.client.get_object(Bucket=self.bucket, Key=self.key(self.folder(item), filename))["Body"]
        return (self.directory(self.folder(item)) / filename).open("rb")

    def put(self, item, filename, data):
        """Write a small file (such as archive.json) next to an archive's files."""
        if item.get("encrypted"):
            data = crypto.encrypt(data, self.reading_key())
        filename = self.stored(item, filename)
        if item["storage"] == "s3":
            if self.client is None:
                raise ValueError("The S3 configuration is needed to modify this archive.")
            self.client.put_object(Bucket=self.bucket, Key=self.key(self.folder(item), filename), Body=data)
            return
        path = self.directory(self.folder(item)) / filename
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)

    def put_root(self, filename, data):
        """Write a file at the root of the storage, next to the archives' folders, in the current form only."""
        stale = filename if self.encryption else filename + SUFFIX
        if self.encryption:
            filename, data = filename + SUFFIX, crypto.encrypt(data, self.encryption)
        if self.mode == "s3":
            self.client.put_object(Bucket=self.bucket, Key=self.key("", filename), Body=data)
            self.client.delete_object(Bucket=self.bucket, Key=self.key("", stale))
            return
        path = self.root / "media" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)
        path.with_name(stale).unlink(missing_ok=True)

    def content(self, item, name):
        """A small file's content, or None when it is encrypted and this key cannot read it."""
        try:
            with self.read(item, name) as stream:
                return stream.read()
        except (crypto.Damaged, ValueError):
            if not item["encrypted"]:
                raise
            return None

    def record(self, media_id, name):
        """(content, whether it is encrypted) of an archive's file `name` in the S3 storage, such as its
        archive.json; None if it has none. The content is None when it is encrypted and cannot be read."""
        for encrypted in (True, False):
            if encrypted and not self.encryption:
                continue  # its folder cannot even be named
            item = {"id": media_id, "storage": "s3", "encrypted": encrypted}
            try:
                self.client.head_object(Bucket=self.bucket, Key=self.key(self.folder(item), self.stored(item, name)))
            except self.client.exceptions.ClientError as error:
                if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    continue
                raise
            return self.content(item, name), encrypted
        return None

    def records(self, name):
        """(folder, content, whether it is encrypted) of the file `name` in every archive's folder, such as each
        archive.json. The content is None when it is encrypted and cannot be read.

        Encrypted ones come first: an archive found in both forms was being converted, and its record is
        written last and removed first, so each form that has one is complete (see convert)."""
        found = []
        if self.mode == "s3":
            base = f"{self.prefix}/" if self.prefix else ""
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=base):
                for obj in page.get("Contents", []):
                    parts = obj["Key"].removeprefix(base).split("/")
                    if len(parts) == 2 and parts[1] in (name, name + SUFFIX):
                        found.append((parts[1] == name, parts[0]))
        else:
            for path in (self.root / "media").glob(f"*/{name}*"):
                if path.name in (name, name + SUFFIX):
                    found.append((path.name == name, path.parent.name))
        for plain, folder in sorted(found):
            item = {"folder": folder, "storage": self.mode, "encrypted": not plain}
            yield folder, self.content(item, name), not plain

    def names(self, item):
        """Names of an archive's files as they are stored."""
        if item["storage"] == "s3":
            if self.client is None:
                raise ValueError("The S3 configuration is needed to read this archive.")
            prefix = self.key(self.folder(item), "") + "/"
            pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
            return [obj["Key"].removeprefix(prefix) for page in pages for obj in page.get("Contents", [])]
        directory = self.directory(self.folder(item))
        return sorted(path.name for path in directory.iterdir() if path.is_file()) if directory.is_dir() else []

    def list(self, item):
        """Names of an archive's files, in the form the archive is kept in."""
        names = self.names(item)
        if item.get("encrypted"):
            return [name.removesuffix(SUFFIX) for name in names if name.endswith(SUFFIX)]
        return [name for name in names if not name.endswith(SUFFIX)]

    def convert(self, item, encrypt):
        """Write an archive's files in the other form, in that form's folder, each one read back and compared.

        The caller then records the archive's new form, and `prune` removes the previous one. The record is
        written last and pruned first: a form that has its record has every file."""
        key, target = self.reading_key(), {**item, "encrypted": encrypt}
        names = [name for name in self.list(item) if not name.endswith((".part", ".tmp"))]
        for name in sorted(names, key=lambda name: name == manifest.NAME):
            with self.read(item, name) as source:
                source = Hashing(source)
                self.write(target, self.stored(target, name), crypto.Encrypting(source, key) if encrypt else source)
            with self.read(target, name) as written:
                if manifest.hash_stream(written)["sha256"] != source.digest.hexdigest():
                    raise RuntimeError(f"{name} was not written as it was read")

    def prune(self, item):
        """Remove the folder of the form an archive is not kept in: what a conversion replaced or left unfinished."""
        other = {**item, "encrypted": not item.get("encrypted")}
        for name in sorted(self.names(other), key=lambda name: not name.startswith(manifest.NAME)):
            if item["storage"] == "s3":
                self.client.delete_object(Bucket=self.bucket, Key=self.key(self.folder(other), name))
            else:
                (self.directory(self.folder(other)) / name).unlink(missing_ok=True)
        if item["storage"] != "s3":
            shutil.rmtree(self.directory(self.folder(other)), ignore_errors=True)
