"""The relay's cryptography: X25519 (RFC 7748), ChaCha20 (RFC 8439), and HKDF-SHA256 (RFC 5869), in the
standard library only, and the sealed record built from them.

`seal` encrypts a record to a recipient's public key and authenticates its sender. Two key agreements, one with
a fresh ephemeral key and one with the sender's own key, feed HKDF, whose 64 bytes are a ChaCha20 key and an
HMAC-SHA256 key. Every record gets its own keys, so ChaCha20 runs under an all-zero nonce. `unseal` checks the tag
before it decrypts anything.

This code is not constant-time. Only the local daemon and CLI run it, never a service an attacker can time.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from typing import Optional

from .core import ProjectorError

VERSION = 1
INFO = b"projector-agent v1"
P = 2 ** 255 - 19
A24 = 121665
BASE = (9).to_bytes(32, "little")
MASK = 0xFFFFFFFF
SIGMA = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)


class CryptoError(ProjectorError):
    """A sealed record that does not open: malformed, forged, or altered."""


class TagMismatch(CryptoError):
    """A sealed record whose tag does not match: forged, altered, or sealed to another key."""


# X25519

def x25519(scalar: bytes, point: bytes) -> bytes:
    """RFC 7748 § 5: the Montgomery ladder over Curve25519."""
    if len(scalar) != 32 or len(point) != 32:
        raise CryptoError("an X25519 key is 32 bytes")
    clamped = bytearray(scalar)
    clamped[0] &= 248
    clamped[31] = (clamped[31] & 127) | 64
    k = int.from_bytes(clamped, "little")
    x1 = int.from_bytes(point, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in range(254, -1, -1):
        bit = (k >> t) & 1
        if swap ^ bit:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a = x2 + z2
        aa = a * a % P
        b = x2 - z2
        bb = b * b % P
        e = aa - bb
        da = (x3 - z3) * a % P
        cb = (x3 + z3) * b % P
        x3 = (da + cb) ** 2 % P
        z3 = x1 * (da - cb) ** 2 % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


def public_key(private: bytes) -> bytes:
    return x25519(private, BASE)


def new_private_key() -> bytes:
    return secrets.token_bytes(32)


def key_id(public: bytes) -> str:
    """The key's ID: the first 16 hex digits of the SHA-256 of its public key."""
    return hashlib.sha256(public).hexdigest()[:16]


def agree(private: bytes, public: bytes) -> bytes:
    shared = x25519(private, public)
    # A low-order public key gives an all-zero secret that every private key agrees on.
    if not any(shared):
        raise CryptoError("the public key is not usable")
    return shared


# ChaCha20

def _block(state: list[int]) -> bytes:
    x = list(state)
    for _ in range(10):
        for a, b, c, d in ((0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15),
                           (0, 5, 10, 15), (1, 6, 11, 12), (2, 7, 8, 13), (3, 4, 9, 14)):
            x[a] = (x[a] + x[b]) & MASK
            x[d] ^= x[a]
            x[d] = ((x[d] << 16) | (x[d] >> 16)) & MASK
            x[c] = (x[c] + x[d]) & MASK
            x[b] ^= x[c]
            x[b] = ((x[b] << 12) | (x[b] >> 20)) & MASK
            x[a] = (x[a] + x[b]) & MASK
            x[d] ^= x[a]
            x[d] = ((x[d] << 8) | (x[d] >> 24)) & MASK
            x[c] = (x[c] + x[d]) & MASK
            x[b] ^= x[c]
            x[b] = ((x[b] << 7) | (x[b] >> 25)) & MASK
    return b"".join(((value + start) & MASK).to_bytes(4, "little") for value, start in zip(x, state))


def chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    """RFC 8439 § 2.4: `data` XORed with the key stream from block `counter`."""
    if len(key) != 32 or len(nonce) != 12:
        raise CryptoError("ChaCha20 takes a 32-byte key and a 12-byte nonce")
    words = [int.from_bytes(key[i:i + 4], "little") for i in range(0, 32, 4)]
    nonce_words = [int.from_bytes(nonce[i:i + 4], "little") for i in range(0, 12, 4)]
    out = bytearray()
    for offset in range(0, len(data), 64):
        chunk = data[offset:offset + 64]
        stream = _block([*SIGMA, *words, (counter + offset // 64) & MASK, *nonce_words])[:len(chunk)]
        out += (int.from_bytes(chunk, "little") ^ int.from_bytes(stream, "little")).to_bytes(len(chunk), "little")
    return bytes(out)


# HKDF

def hkdf(ikm: bytes, *, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 over HMAC-SHA256."""
    prk = hmac.new(salt or bytes(32), ikm, hashlib.sha256).digest()
    out, block = b"", b""
    for counter in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
    return out[:length]


# Sealed records

def derive(shared_ephemeral: bytes, shared_static: bytes, ephemeral: bytes, sender: bytes,
           recipient: bytes) -> tuple[bytes, bytes]:
    """The record's cipher key and MAC key."""
    okm = hkdf(shared_ephemeral + shared_static, salt=ephemeral, info=INFO + sender + recipient, length=64)
    return okm[:32], okm[32:]


def tag(mac_key: bytes, sender: bytes, ephemeral: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(mac_key, bytes([VERSION]) + sender + ephemeral + ciphertext, hashlib.sha256).digest()


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode(value: object, size: Optional[int] = None) -> bytes:
    if not isinstance(value, str):
        raise CryptoError("a sealed field is not base64 text")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise CryptoError("a sealed field is not base64") from None
    if size is not None and len(data) != size:
        raise CryptoError(f"a sealed key is {len(data)} bytes, not {size}")
    return data


def seal(sender_private: bytes, recipient: bytes, plaintext: bytes, *,
         ephemeral_private: Optional[bytes] = None) -> dict:
    """`plaintext` encrypted to the public key `recipient` and authenticated as from `sender_private`'s key."""
    ephemeral_private = ephemeral_private or new_private_key()
    ephemeral = public_key(ephemeral_private)
    sender = public_key(sender_private)
    cipher_key, mac_key = derive(agree(ephemeral_private, recipient), agree(sender_private, recipient),
                                 ephemeral, sender, recipient)
    ciphertext = chacha20(cipher_key, 0, bytes(12), plaintext)
    return {"v": VERSION, "sender": encode(sender), "ephemeral": encode(ephemeral), "ciphertext": encode(ciphertext),
            "tag": encode(tag(mac_key, sender, ephemeral, ciphertext))}


def unseal(recipient_private: bytes, sealed: object) -> tuple[bytes, bytes]:
    """The sender's public key and the plaintext of a record sealed to `recipient_private`'s public key. The tag
    is checked before anything is decrypted."""
    if not isinstance(sealed, dict) or set(sealed) != {"v", "sender", "ephemeral", "ciphertext", "tag"} \
            or sealed["v"] != VERSION:
        raise CryptoError("the record is not a sealed record of version 1")
    sender = decode(sealed["sender"], 32)
    ephemeral = decode(sealed["ephemeral"], 32)
    ciphertext = decode(sealed["ciphertext"])
    given = decode(sealed["tag"], 32)
    recipient = public_key(recipient_private)
    cipher_key, mac_key = derive(agree(recipient_private, ephemeral), agree(recipient_private, sender),
                                 ephemeral, sender, recipient)
    if not hmac.compare_digest(tag(mac_key, sender, ephemeral, ciphertext), given):
        raise TagMismatch("the record's tag does not match")
    return sender, chacha20(cipher_key, 0, bytes(12), ciphertext)
