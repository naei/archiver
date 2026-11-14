"""Encryption of stored files, in the age format (age-encryption.org/v1).

With ENCRYPTION_KEY set, files are encrypted before they reach the storage, so
whoever hosts it (a disk, a bucket) only holds data it cannot read. The format
is age's, so the files stay readable without the application:

    age -d -i key.txt video.mp4.age > video.mp4

An encrypted archive's folder is named by a keyed hash of its id, so the
storage does not show which post or video it holds; the id is in the folder's
encrypted archive.json.

The key is an age identity (AGE-SECRET-KEY-1…, see `run.py keygen`). A file is
encrypted to its public half, in 64 KiB chunks that are each authenticated, so
any byte range is read by fetching only the chunks that hold it: seeking in a
video costs the same as without encryption."""
import base64
import hashlib
import hmac
import io
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

SUFFIX = ".age"
CHUNK = 64 * 1024
TAG = 16
VERSION = b"age-encryption.org/v1"
# Read from the start of a file to find its header; ours take 168 bytes, other tools may add recipients.
HEAD = 4096
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


class Damaged(Exception):
    """An encrypted file that is not what was written: altered, cut short, or made for another key."""


def _hkdf(key, salt, info):
    """HKDF-SHA256, for the single 32-byte output the format needs."""
    extracted = hmac.new(salt or bytes(32), key, hashlib.sha256).digest()
    return hmac.new(extracted, info + b"\x01", hashlib.sha256).digest()


def _b64(data):
    return base64.b64encode(data).rstrip(b"=")


def _unb64(text):
    return base64.b64decode(text + b"=" * (-len(text) % 4), validate=True)


def _polymod(values):
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for index, generator in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            if top >> index & 1:
                checksum ^= generator
    return checksum


def _regroup(values, source, target, pad):
    """Bytes to 5-bit groups and back, as Bech32 packs them."""
    total, bits, result = 0, 0, []
    for value in values:
        total, bits = total << source | value, bits + source
        while bits >= target:
            bits -= target
            result.append(total >> bits & (1 << target) - 1)
    if pad and bits:
        result.append(total << target - bits & (1 << target) - 1)
    elif not pad and (bits >= source or total & (1 << bits) - 1):
        raise ValueError
    return result


def _expand(prefix):
    return [ord(char) >> 5 for char in prefix] + [0] + [ord(char) & 31 for char in prefix]


def _bech32(prefix, data):
    values = _regroup(data, 8, 5, True)
    checksum = _polymod(_expand(prefix) + values + [0] * 6) ^ 1
    return prefix + "1" + "".join(_CHARSET[value] for value in values + [checksum >> 5 * (5 - index) & 31 for index in range(6)])


def _unbech32(prefix, text):
    if text != text.lower() and text != text.upper():
        raise ValueError
    found, _, data = text.lower().rpartition("1")
    values = [_CHARSET.index(char) for char in data]
    if found != prefix or len(values) < 6 or _polymod(_expand(prefix) + values) != 1:
        raise ValueError
    return bytes(_regroup(values[:-6], 5, 8, False))


def _fill(stream, size):
    """Read `size` bytes, or fewer only at the end: some streams return less than asked."""
    parts, missing = [], size
    while missing > 0 and (part := stream.read(missing)):
        parts.append(part)
        missing -= len(part)
    return b"".join(parts)


def _nonce(index, last):
    return index.to_bytes(11, "big") + bytes([last])


class Key:
    """An age identity: the secret that reads the files, and the public half that writes them."""

    def __init__(self, identity):
        try:
            secret = _unbech32("age-secret-key-", identity.strip())
            if len(secret) != 32:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("ENCRYPTION_KEY must be an age identity (AGE-SECRET-KEY-1…), created by “run.py keygen”.") from None
        self.secret = X25519PrivateKey.from_private_bytes(secret)
        self.public = self.secret.public_key().public_bytes_raw()
        self.naming = _hkdf(secret, b"", b"archiver/folder-names")

    def folder(self, media_id):
        """The name of an encrypted archive's folder: the same for a given id and key, meaningless without the key."""
        return hmac.new(self.naming, media_id.encode(), hashlib.sha256).hexdigest()[:32]

    @staticmethod
    def generate():
        return _bech32("age-secret-key-", X25519PrivateKey.generate().private_bytes_raw()).upper()

    @property
    def recipient(self):
        """The public key, as age names it: enough to encrypt, useless to decrypt."""
        return _bech32("age", self.public)

    def header(self):
        """A new file's header and the key of its content, which only this identity can take out of it."""
        file_key = os.urandom(16)
        ephemeral = X25519PrivateKey.generate()
        share = ephemeral.public_key().public_bytes_raw()
        wrapping = _hkdf(ephemeral.exchange(X25519PublicKey.from_public_bytes(self.public)), share + self.public, VERSION + b"/X25519")
        wrapped = ChaCha20Poly1305(wrapping).encrypt(bytes(12), file_key, None)
        text = VERSION + b"\n-> X25519 " + _b64(share) + b"\n" + _b64(wrapped) + b"\n---"
        mac = hmac.new(_hkdf(file_key, b"", b"header"), text, hashlib.sha256).digest()
        return text + b" " + _b64(mac) + b"\n", file_key

    def unwrap(self, head):
        """The key of a file's content and the length of its header, from the first bytes of the file."""
        try:
            end = head.index(b"\n---")
            length = head.index(b"\n", end + 1) + 1
            lines = head[:end].split(b"\n")
            mac = _unb64(head[end + 1:length - 1].removeprefix(b"--- "))
            if lines[0] != VERSION:
                raise ValueError
        except ValueError:
            raise Damaged("not an age file") from None
        for index, line in enumerate(lines[:-1]):
            if not line.startswith(b"-> X25519 "):
                continue
            try:
                share, wrapped = _unb64(line.split(b" ")[2]), _unb64(lines[index + 1])
                wrapping = _hkdf(self.secret.exchange(X25519PublicKey.from_public_bytes(share)), share + self.public, VERSION + b"/X25519")
                file_key = ChaCha20Poly1305(wrapping).decrypt(bytes(12), wrapped, None)
            except (InvalidTag, ValueError, IndexError):
                continue  # written for another key
            expected = hmac.new(_hkdf(file_key, b"", b"header"), head[:end + 4], hashlib.sha256).digest()
            if not hmac.compare_digest(mac, expected):
                raise Damaged("altered header")
            return file_key, length
        raise Damaged("encrypted with another key")


def encrypted(stream, key):
    """The encrypted form of a stream, piece by piece."""
    header, file_key = key.header()
    salt = os.urandom(16)
    yield header + salt
    cipher = ChaCha20Poly1305(_hkdf(file_key, salt, b"payload"))
    index, block = 0, _fill(stream, CHUNK)
    while True:
        # The last chunk is marked as such, so a file cut at a chunk boundary is detected.
        following = _fill(stream, CHUNK) if len(block) == CHUNK else b""
        yield cipher.encrypt(_nonce(index, not following), block, None)
        if not following:
            return
        index, block = index + 1, following


def encrypt(data, key):
    return b"".join(encrypted(io.BytesIO(data), key))


class Encrypting:
    """A readable stream of the encrypted form of another, for uploads."""

    def __init__(self, stream, key):
        self.pieces = encrypted(stream, key)
        self.buffer = b""

    def read(self, amount=-1):
        while amount < 0 or len(self.buffer) < amount:
            piece = next(self.pieces, None)
            if piece is None:
                break
            self.buffer += piece
        cut = len(self.buffer) if amount < 0 else amount
        data, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return data


class Decrypting:
    """A readable stream of `length` decrypted bytes, from the chunks that hold them."""

    def __init__(self, source, cipher, index, count, skip, length):
        self.source, self.cipher = source, cipher
        self.index, self.count = index, count
        self.skip, self.length = skip, length
        self.buffer = b""

    def read(self, amount=-1):
        wanted = self.length if amount < 0 else min(amount, self.length)
        while len(self.buffer) < wanted and self.index < self.count:
            block = _fill(self.source, CHUNK + TAG)
            if not block:
                break  # cut short: the reader gets less than it asked for
            try:
                plain = self.cipher.decrypt(_nonce(self.index, self.index == self.count - 1), block, None)
            except InvalidTag:
                raise Damaged("altered content") from None
            self.buffer += plain[self.skip:]
            self.skip = 0
            self.index += 1
        data, self.buffer = self.buffer[:wanted], self.buffer[wanted:]
        self.length -= len(data)
        return data

    def close(self):
        if self.source:
            self.source.close()

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()


def _layout(fetch, key):
    """What reading a file needs: its cipher, where its chunks start, how many there are, and its decrypted size.

    `fetch(first, last)` gives a stream of these bytes of the encrypted file, and the file's whole size."""
    stream, total = fetch(0, HEAD - 1)
    try:
        head = _fill(stream, HEAD)
    finally:
        stream.close()
    file_key, start = key.unwrap(head)
    salt = head[start:start + 16]
    content = total - start - 16
    if len(salt) < 16 or content < TAG:
        raise Damaged("cut short")
    count = -(-content // (CHUNK + TAG))
    return ChaCha20Poly1305(_hkdf(file_key, salt, b"payload")), start + 16, count, content - count * TAG, total


def size(fetch, key):
    """The decrypted size of an encrypted file."""
    return _layout(fetch, key)[3]


def reader(fetch, key, first=0, last=None):
    """A stream of the decrypted bytes `first` to `last` (inclusive; the end of the file by default)."""
    cipher, start, count, length, total = _layout(fetch, key)
    last = length - 1 if last is None else min(last, length - 1)
    if first > last:
        return Decrypting(None, cipher, count, count, 0, 0)
    index, final = first // CHUNK, last // CHUNK
    source, _ = fetch(start + index * (CHUNK + TAG), min(total, start + (final + 1) * (CHUNK + TAG)) - 1)
    return Decrypting(source, cipher, index, count, first - index * CHUNK, last - first + 1)
