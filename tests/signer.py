"""A test-only Ed25519 signer, after RFC 8032 section 5.1.6, and the hashing a document needs.

The tests sign the receipts they build with a key made on the spot; no real key is
ever read or written. It is slow and not constant-time, which a test does not mind
and a signer for real use would. The verifier under test is written apart
(receipt_check/_vendor/tick_verify), so a receipt this makes passing is evidence of
both.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

_Point = tuple[int, int, int, int]


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int:
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    assert (x * x - x2) % _P == 0
    return _P - x if (x & 1) != sign else x


def _add(p: _Point, q: _Point) -> _Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p: _Point) -> _Point:
    q: _Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _compress(p: _Point) -> bytes:
    zinv = _inv(p[2])
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G: _Point = (_GX, _GY, 1, _GX * _GY % _P)


def _expand(secret: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(secret).digest()
    a = int.from_bytes(digest[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, digest[32:]


def _hash_q(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def public_key(secret: bytes) -> bytes:
    """RFC 8032 5.1.5."""
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, message: bytes) -> bytes:
    """RFC 8032 5.1.6."""
    a, prefix = _expand(secret)
    public = _compress(_mul(a, _G))
    r = _hash_q(prefix + message)
    r_point = _compress(_mul(r, _G))
    h = _hash_q(r_point + public + message)
    s = (r + h * a) % _Q
    return r_point + int.to_bytes(s, 32, "little")


class Signer:
    """A key made on the spot, with what a Tick document carries of it."""

    def __init__(self, secret: bytes | None = None) -> None:
        self.secret = secret if secret is not None else os.urandom(32)
        self.spki = _SPKI_PREFIX + public_key(self.secret)
        self.key = base64.b64encode(self.spki).decode("ascii")
        self.key_id = hashlib.sha256(self.spki).hexdigest()[:16]

    def sign_text(self, text: str) -> str:
        return base64.b64encode(sign(self.secret, text.encode("utf-8"))).decode("ascii")


def canonical(value: Any) -> str:
    """spec/SPEC.md's canonical form, for the strings, whole numbers, booleans and nulls the tests use."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def document_hash(value: Any) -> str:
    """The canonical form written as a JSON string, hashed (spec/SPEC.md, "The document hash")."""
    return hashlib.sha256(json.dumps(canonical(value), ensure_ascii=False).encode("utf-8")).hexdigest()
