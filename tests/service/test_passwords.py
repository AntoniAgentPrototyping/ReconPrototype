"""Password hashing and policy. No database — this is the pure-logic half.

The property most worth pinning here is not "hashing works". It is that a
GARBAGE value in `users.password_hash` reads as a failed login rather than a
500, and that the unknown-username branch of sign-in spends the same wall clock
as a wrong password. The second one is asserted structurally (the dummy verify is
called) rather than by timing: a wall-clock assertion on shared CI is flaky, gets
marked `@skip` within a month, and then proves nothing.
"""

from __future__ import annotations

import pytest

from service import passwords

GOOD = "correct horse battery staple"


def test_hash_and_verify_round_trip():
    stored = passwords.hash_password(GOOD)
    assert passwords.verify_password(stored, GOOD) is True
    assert passwords.verify_password(stored, GOOD + "!") is False


def test_the_hash_is_argon2id_at_the_declared_parameters():
    """The parameters live IN the value, which is what makes rehash-on-login a
    library call rather than another schema column."""
    stored = passwords.hash_password(GOOD)
    assert stored.startswith("$argon2id$")
    assert f"m={passwords.MEMORY_KIB}" in stored
    assert f"t={passwords.TIME_COST}" in stored
    assert f"p={passwords.PARALLELISM}" in stored


def test_the_same_password_hashes_differently_each_time():
    """A salt is present. Two identical passwords must not share a hash, or the
    table tells an attacker which accounts to attack once."""
    assert passwords.hash_password(GOOD) != passwords.hash_password(GOOD)


def test_needs_rehash_is_false_for_current_and_true_for_weaker():
    """Otherwise the rehash-on-login path in POST /sessions is untestable."""
    from argon2 import PasswordHasher

    current = passwords.hash_password(GOOD)
    assert passwords.needs_rehash(current) is False

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1,
                          hash_len=16, salt_len=8).hash(GOOD)
    assert passwords.needs_rehash(weak) is True


@pytest.mark.parametrize("garbage", [
    "", "not-a-hash", "$2b$12$abcdefghijklmnopqrstuv",  # a bcrypt hash
    "$argon2id$truncated", "None",
])
def test_a_garbage_stored_hash_is_a_failed_login_not_a_crash(garbage):
    """A stray value in the column must not become a 500 that tells an attacker
    the column is malformed."""
    assert passwords.verify_password(garbage, GOOD) is False


def test_needs_rehash_survives_a_garbage_hash():
    assert passwords.needs_rehash("not-a-hash") is False


# --- username normalization -------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("antoni@ada", "antoni@ada"),
    (" Antoni@ADA ", "antoni@ada"),
    ("ANTONI@ADA", "antoni@ada"),
    ("ａｄｍｉｎ", "admin"),   # full-width NFKC folds
])
def test_normalize_username(raw, expected):
    assert passwords.normalize_username(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "ab", "has space", "a" * 201, None])
def test_normalize_username_refuses(bad):
    with pytest.raises(passwords.PasswordError):
        passwords.normalize_username(bad)


def test_case_variants_are_one_audit_identity():
    """The point of normalizing: jobs.requested_by must not depend on how
    somebody held Shift."""
    assert (passwords.normalize_username("Antoni@ADA")
            == passwords.normalize_username("antoni@ada"))


# --- policy -----------------------------------------------------------------

def test_a_reasonable_passphrase_is_accepted():
    passwords.check_policy(GOOD, username="antoni@ada")


@pytest.mark.parametrize("bad,reason", [
    ("short", "too short"),
    ("a" * 129, "too long"),
    ("            ", "whitespace only"),
    ("password", "blocklisted"),
    ("changeme", "blocklisted"),
])
def test_check_policy_refuses(bad, reason):
    with pytest.raises(passwords.PasswordError):
        passwords.check_policy(bad)


def test_a_password_equal_to_the_username_is_refused():
    name = "a-long-enough-username"
    with pytest.raises(passwords.PasswordError):
        passwords.check_policy(name, username=name)


def test_no_composition_rules():
    """NIST SP 800-63B asks for length plus a blocklist. Composition rules
    produce `Password1!`, so an all-lowercase passphrase must pass."""
    passwords.check_policy("alllowercaselettersnodigits", username="x@y")


def test_hash_password_enforces_policy_before_hashing():
    with pytest.raises(passwords.PasswordError):
        passwords.hash_password("short")


# --- the timing-equalisation mechanism --------------------------------------

def test_verify_dummy_runs_without_raising():
    passwords.verify_dummy()


def test_verify_dummy_actually_hashes(monkeypatch):
    """Structural, not wall-clock. The unknown-username branch must do real
    argon2 work, and this proves the call reaches the hasher against the dummy
    hash rather than short-circuiting.

    The whole hasher is swapped rather than its method: `PasswordHasher.verify`
    is read-only, and `verify_dummy` reads the module global anyway.
    """
    calls = []
    real = passwords._HASHER

    class Spy:
        def verify(self, stored, plain):
            calls.append((stored, plain))
            return real.verify(stored, plain)

    monkeypatch.setattr(passwords, "_HASHER", Spy())
    passwords.verify_dummy()
    assert len(calls) == 1
    assert calls[0][0] == passwords._DUMMY_HASH
    # And it must swallow the mismatch it is guaranteed to get.
    assert calls[0][1] != passwords._DUMMY_HASH


# --- generated passwords ----------------------------------------------------

def test_generated_passwords_are_random_and_pass_policy():
    a, b = passwords.generate_password(), passwords.generate_password()
    assert a != b
    passwords.check_policy(a)
    passwords.check_policy(b)
    assert len(a) >= passwords.MIN_LENGTH
