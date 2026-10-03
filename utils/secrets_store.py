"""Secure at-rest encryption for Ader secrets."""
from __future__ import annotations

import base64
import hashlib
import os

from nacl.secret import SecretBox
from nacl.utils import random


class TokenCipher:
    """Encrypt Discord bot tokens with a server-only 256-bit key."""

    def __init__(self):
        raw = os.getenv("ADER_TOKEN_ENCRYPTION_KEY", "").strip()
        if not raw:
            raise RuntimeError("ADER_TOKEN_ENCRYPTION_KEY is required to use linked bots.")
        key = None
        for decoder in (base64.urlsafe_b64decode, bytes.fromhex):
            try:
                candidate = decoder(raw.encode("ascii")) if decoder is base64.urlsafe_b64decode else decoder(raw)
                if len(candidate) == SecretBox.KEY_SIZE:
                    key = candidate
                    break
            except (ValueError, TypeError, UnicodeError):
                continue
        if key is None:
            # Allow a passphrase-style secret while still producing a fixed 256-bit key.
            key = hashlib.sha256(raw.encode("utf-8")).digest()
        self.box = SecretBox(key)

    def encrypt(self, value: str) -> str:
        data = str(value).strip().encode("utf-8")
        return base64.urlsafe_b64encode(bytes(self.box.encrypt(data, random(SecretBox.NONCE_SIZE)))).decode("ascii")

    def decrypt(self, value: str) -> str:
        raw = base64.urlsafe_b64decode(str(value).encode("ascii"))
        return self.box.decrypt(raw).decode("utf-8")

    @staticmethod
    def fingerprint(value: str) -> str:
        return hashlib.sha256(str(value).strip().encode("utf-8")).hexdigest()
