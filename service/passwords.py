"""Password hashing and policy. One module, so the CLI, the admin-reset path and
the change-own-password path cannot drift on policy.

TWO KINDS OF SECRET, TWO KINDS OF HASH, and the difference is why this module
exists at all. `service/auth.py` hashes session tokens with plain sha256, and
`migrations/002_m5.sql:14-18` argues at length that this is correct — because a
token is 32 bytes from `os.urandom`, brute force is not on the table, and a slow
KDF would only add latency to every request. **That argument depends entirely on
the secret being GENERATED.** A password is chosen by a human, its entropy is
low, and guessing is very much on the table. Reaching for one file's answer while
holding the other file's secret is the mistake this docstring exists to prevent.

No pepper. A pepper defends "the database was dumped but application config was
not" — but `RECON_DATABASE_URL` and any pepper would sit in the same `deploy/.env`,
so it largely defends against the threat that already compromises both. It also
has to be byte-identical at every verify forever: lose it and every password
stops working, with no recovery but a mass reset. If it is ever wanted,
`PasswordHasher(secret=...)` takes one with no schema change.

`secrets.compare_digest` is not used here, deliberately. Argon2's `verify` does
its comparison internally and the work factor dominates any residual timing
signal. `compare_digest` is the right tool for a *fixed* secret; there are none
left in this service.
"""

from __future__ import annotations

import re
import secrets
import threading
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id at OWASP's documented minimum configuration. HARDCODED, and not an
# environment variable, for two reasons:
#
#   1. A deployment that lowered the cost would silently weaken every new hash.
#   2. Worse, `check_needs_rehash` would then classify the strong EXISTING hashes
#      as out of date and DOWNGRADE them on their owners' next login. A weakening
#      knob that retroactively rewrites your strongest data is a bad knob.
#
# m=19 MiB rather than argon2-cffi's 64 MiB default because FastAPI runs sync
# handlers in a threadpool (40 threads by default) and the worker container's 4 GB
# limit is load-bearing for the memory trigger in docs/10-ROADMAP.md. 40 x 64 MiB
# is 2.5 GB of hashing in a 4 GB box; 40 x 19 MiB is 760 MB, and _HASH_SLOTS bounds
# it further.
MEMORY_KIB = 19456
TIME_COST = 2
PARALLELISM = 1
HASH_LEN = 32
SALT_LEN = 16

MIN_LENGTH = 12
# An unbounded input is a hashing denial of service: argon2 cost is per-call, but
# the caller still pays to read and normalize whatever was posted.
MAX_LENGTH = 128

_HASHER = PasswordHasher(time_cost=TIME_COST, memory_cost=MEMORY_KIB,
                         parallelism=PARALLELISM, hash_len=HASH_LEN,
                         salt_len=SALT_LEN)

# Deliberately expensive work behind an unauthenticated endpoint is a CPU denial
# of service. `service/ratelimit.py` bounds attempts per account; this bounds
# total concurrent hashing regardless of how those attempts arrive.
_HASH_SLOTS = threading.Semaphore(4)

# Hashed once at import so the unknown-username branch of login can spend the same
# wall clock as a wrong password. Without this the uniform 401 MESSAGE is
# worthless: an unknown account answers in ~2 ms and a wrong password in ~60 ms,
# and the difference is the oracle.
_DUMMY_HASH = _HASHER.hash("dummy-password-for-timing-equalisation")

# Small and deliberately not a downloaded corpus. NIST SP 800-63B asks for length
# plus a blocklist, not composition rules — those produce "Password1!".
_BLOCKLIST = frozenset({
    "password", "passw0rd", "12345678", "123456789", "1234567890",
    "qwertyuiop", "letmein", "welcome", "iloveyou", "admin123",
    "recon", "reconciliation", "changeme", "change-me", "secret",
})

_WHITESPACE = re.compile(r"\s")


class PasswordError(ValueError):
    """A username or password that policy refuses. Carries a message meant to be
    shown to the person who typed it, so it must never name another account."""


def normalize_username(raw: str) -> str:
    """The ONE place a username is normalized. The DB check constraint in
    `003_password_auth.sql` is a backstop against a write that skipped this.

    NFKC then casefold then strip, so "Antoni@ADA" and " antoni@ada " are one
    account and one audit identity. An audit trail whose names differ by how
    somebody held Shift is not an audit trail.
    """
    if raw is None:
        raise PasswordError("a username is required")
    value = unicodedata.normalize("NFKC", str(raw)).strip().casefold()
    if not value:
        raise PasswordError("a username is required")
    if _WHITESPACE.search(value):
        raise PasswordError("a username cannot contain spaces")
    if not 3 <= len(value) <= 200:
        raise PasswordError("a username must be between 3 and 200 characters")
    return value


def check_policy(plain: str, *, username: str | None = None) -> None:
    """Raise `PasswordError` if `plain` is not an acceptable password."""
    if plain is None or not isinstance(plain, str):
        raise PasswordError("a password is required")
    if len(plain) < MIN_LENGTH:
        raise PasswordError(f"a password must be at least {MIN_LENGTH} characters")
    if len(plain) > MAX_LENGTH:
        raise PasswordError(f"a password must be at most {MAX_LENGTH} characters")
    if not plain.strip():
        raise PasswordError("a password cannot be only whitespace")
    folded = plain.casefold()
    if username and folded == username.casefold():
        raise PasswordError("a password cannot be the same as the username")
    if folded in _BLOCKLIST:
        raise PasswordError("that password is too common; choose another")


def hash_password(plain: str, *, username: str | None = None) -> str:
    """Validate policy, then hash. Returns an argon2 PHC string."""
    check_policy(plain, username=username)
    with _HASH_SLOTS:
        return _HASHER.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    """True if `plain` matches `stored_hash`. Never raises.

    A garbage value in the column — a bcrypt hash from some other system, an
    empty string, a NULL that became "None" — must read as a failed login, not a
    500 that tells an attacker the column is malformed.
    """
    if not stored_hash or plain is None:
        return False
    try:
        with _HASH_SLOTS:
            return bool(_HASHER.verify(stored_hash, plain))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:                                            # noqa: BLE001
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True if `stored_hash` was made under weaker parameters than current."""
    try:
        return bool(_HASHER.check_needs_rehash(stored_hash))
    except Exception:                                            # noqa: BLE001
        # An unparseable hash cannot be upgraded in place; it can only be reset.
        return False


def verify_dummy() -> None:
    """Spend a login's worth of wall clock against a fixed hash.

    Called on the unknown-username branch so that "no such account" and "wrong
    password" cost the same. THIS is the mechanism behind the uniform 401 — the
    identical message is only the visible half.
    """
    try:
        with _HASH_SLOTS:
            _HASHER.verify(_DUMMY_HASH, "definitely-not-the-dummy-password")
    except Exception:                                            # noqa: BLE001
        pass


def generate_password() -> str:
    """A password for bootstrap and admin reset. `secrets`, never `random`.

    Generated rather than chosen by an admin, and always paired with
    `must_change_password`: every audit column this service exists to make
    trustworthy (jobs.requested_by, config_proposals.proposed_by, .decided_by) is
    only evidence if impersonating a colleague is hard.
    """
    return secrets.token_urlsafe(12)
