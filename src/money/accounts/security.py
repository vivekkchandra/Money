"""Versioned memory-hard passwords and high-entropy one-way token storage."""

import hashlib
import secrets
import threading
from functools import lru_cache

# OWASP scrypt baseline: N=2**17, r=8, p=1 (128 MiB); bound concurrent
# admission before hashing. Never accept parameters from persisted input.
SCRYPT_N = 131072
_password_capacity = threading.BoundedSemaphore(2)


class PasswordCapacityExceeded(RuntimeError):
    """Bound per-process memory even when requests arrive concurrently."""


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    return password_hash(secrets.token_urlsafe(32))


def _derive(password: str, salt: bytes) -> bytes:
    if not _password_capacity.acquire(blocking=False):
        raise PasswordCapacityExceeded("PASSWORD_CAPACITY")
    try:
        return hashlib.scrypt(
            password.encode(), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32, maxmem=192 * 1024 * 1024
        )
    finally:
        _password_capacity.release()


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return f"scrypt-v1${salt.hex()}${digest.hex()}"


def password_valid(password: str, encoded: str) -> bool:
    try:
        version, salt_hex, expected = encoded.split("$")
        if version != "scrypt-v1" or len(salt_hex) != 32 or len(expected) != 64:
            return False
        salt = bytes.fromhex(salt_hex)
        digest = _derive(password, salt)
        return secrets.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False
