"""Who is calling, and what they are allowed to do.

M5 closed [defect 2.1](docs/08-KNOWN-DEFECTS.md) — the api had no authentication
at all — with pasted bearer tokens. M6 replaces those with **username and
password**, because the app is browser-only now and a credential a person is
issued from a terminal is not one. Entra ID SSO is still the destination and is
still blocked on a tenant app registration (docs/13-ENTRA-SETUP.md).

The seam is `Principal`. Everything downstream — every endpoint, every audit
column — depends only on `subject` and `role`. A session supplies those today; an
Entra ID token supplies the same two fields from its `sub` and `roles` claims
tomorrow. The role STRINGS are deliberately Entra's own, so that swap changes who
vouches for a role, not what a role means.

**The api never reads a cookie.** Sign-in mints an opaque session token; the
Next.js BFF stores it in an httpOnly cookie and keeps sending it as
`Authorization: Bearer`. That is not an accident of history — it is why CSRF
against the api is structurally absent rather than mitigated, and why `curl` and
the test client exercise the real authorization path rather than a bypass.

**It fails closed.** With no auth configured, mutating endpoints refuse. This
service has an endpoint that writes the config the money math runs on, and an
unauthenticated version of that endpoint sitting on a port is worse than not
shipping it.
"""

from __future__ import annotations

import enum
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

# 32 bytes from the OS CSPRNG, urlsafe-base64'd. The entropy is what makes a fast
# hash adequate for STORAGE of a session token — see the two-kinds-of-secret
# header in migrations/003_password_auth.sql. A password is the other kind and is
# hashed with Argon2id in service/passwords.py.
SESSION_BYTES = 32

# A distinct prefix from M5's `recon_` api tokens, for two reasons: a leaked value
# is identifiable in a log as a session rather than a token, and a credential that
# LOOKS like the deleted thing invites someone to reintroduce token paste.
SESSION_PREFIX = "recon_s_"


class Role(enum.Enum):
    """Ordered least to most privileged. The ordering IS the authorization model:
    `require(Role.USER)` admits users and admins, `require(Role.ADMIN)` admits
    only admins.

    `recon.operator` was renamed `recon.user` in M6 — same tier, a word that means
    something to the people using the app. `recon.viewer` is kept rather than
    collapsed away: a finance reviewer or an auditor who should read the invoicing
    numbers and never queue a settlement run is a real person.

    Still Entra's own app-role Value strings (docs/13-ENTRA-SETUP.md).
    """

    VIEWER = "recon.viewer"
    USER = "recon.user"
    ADMIN = "recon.admin"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def satisfies(self, required: "Role") -> bool:
        return self.rank >= required.rank


_RANK = {Role.VIEWER: 0, Role.USER: 1, Role.ADMIN: 2}


@dataclass(frozen=True)
class Principal:
    """An authenticated caller. The only identity type the rest of the service
    knows about — nothing downstream asks whether it came from a password session
    or from Entra."""

    subject: str                          # == users.username, normalized
    role: Role
    session_id: int | None = None
    # 'password' | 'dev' | later 'oidc'. Recorded rather than acted on, so an
    # audit row can say how a decision was authenticated.
    method: str = "password"
    # True while a bootstrap or admin-reset password is still in force. Enforced
    # in `require`, not in the UI — see the note there.
    must_change_password: bool = False
    display_name: str | None = None

    def can(self, required: Role) -> bool:
        return self.role.satisfies(required)


class AuthError(Exception):
    """Base for the two failures an endpoint must tell apart.

    401 means "I don't know who you are"; 403 means "I know, and no". Collapsing
    them is how a permissions problem gets diagnosed as a broken login for an
    afternoon.
    """


class Unauthenticated(AuthError):
    pass


class Forbidden(AuthError):
    pass


class PasswordChangeRequired(Forbidden):
    """A 403 with a specific meaning, so the BFF can redirect to the change-password
    page instead of showing a dead end.

    Subclasses `Forbidden` so that a handler which does nothing special with it
    still returns 403 rather than a 500.
    """


# ---------------------------------------------------------------------------
# Minting and hashing
# ---------------------------------------------------------------------------

def new_session_token() -> str:
    """A fresh session secret. Returned once, to the browser's server-side half,
    and never stored — `credential_digest` is what the database sees."""
    return SESSION_PREFIX + secrets.token_urlsafe(SESSION_BYTES)


def credential_digest(raw: str) -> str:
    """sha256 of a GENERATED credential. Never use this for a password."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def looks_like_credential(raw: str) -> bool:
    return raw.startswith(SESSION_PREFIX) and len(raw) > len(SESSION_PREFIX) + 20


def parse_authorization(header: str | None) -> str | None:
    """Pull the credential out of `Authorization: Bearer <token>`.

    Returns None rather than raising for anything malformed: an absent or
    unparseable header is "not authenticated", which the caller turns into 401.

    The scheme stays `Bearer` even though the credential is now a session, which
    is why the `WWW-Authenticate: Bearer` challenge on a 401 is still literally
    correct.
    """
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


# ---------------------------------------------------------------------------
# The policy object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthPolicy:
    """How this deployment authenticates, decided once at startup.

    `enabled=False` is for local development and for the test suite. It is not a
    quiet default: `ServiceSettings.from_env` turns it on unless
    RECON_AUTH_DISABLED is set explicitly, and binding a non-loopback host with
    it off is refused outright (service/config.py).
    """

    enabled: bool = True

    # The identity `enabled=False` runs as. Admin, because a developer running
    # locally is administering their own machine — and because a *lower* role here
    # would make local runs diverge from deployed behaviour in the direction of
    # "works on my laptop, 403 in production".
    dev_subject: str = "dev@localhost"
    dev_role: Role = Role.ADMIN

    def anonymous(self) -> Principal:
        return Principal(subject=self.dev_subject, role=self.dev_role,
                         method="dev", must_change_password=False)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserRecord:
    """A user as the api is allowed to talk about one.

    NOTE: there is no `password_hash` field, and that absence IS the control.
    `from_row` filters a `select *` down to the fields declared here, and
    `models.payload()` serializes exactly these — so the hash cannot reach a
    response body because somebody forgot to strip it.
    `tests/service/test_users.py` asserts it at runtime too.
    """

    id: int
    username: str
    role: Role
    display_name: str | None = None
    must_change_password: bool = False
    created_at: datetime | None = None
    created_by: str | None = None
    last_login_at: datetime | None = None
    disabled_at: datetime | None = None
    disabled_by: str | None = None
    password_changed_at: datetime | None = None
    locked_until: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.disabled_at is None

    @classmethod
    def from_row(cls, row: dict) -> "UserRecord":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in row.items() if k in known}
        data["role"] = Role(row["role"])
        return cls(**data)


@dataclass(frozen=True)
class SessionRecord:
    """A session as the api is allowed to talk about one. No `token_sha256`
    field, for the same structural reason `UserRecord` has no hash."""

    id: int
    user_id: int
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    absolute_expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    user_agent: str | None = None
    client_ip: str | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    @classmethod
    def from_row(cls, row: dict) -> "SessionRecord":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in row.items() if k in known}
        if data.get("client_ip") is not None:
            data["client_ip"] = str(data["client_ip"])
        return cls(**data)


# ---------------------------------------------------------------------------
# The two decisions
# ---------------------------------------------------------------------------

def authenticate(policy: AuthPolicy, header: str | None, lookup) -> Principal:
    """Turn an Authorization header into a Principal, or raise Unauthenticated.

    `lookup(digest) -> Principal | None` is passed in rather than imported, so
    this function has no idea a database exists and its tests need none.
    """
    if not policy.enabled:
        return policy.anonymous()

    raw = parse_authorization(header)
    if raw is None:
        raise Unauthenticated("no session token supplied")
    if not looks_like_credential(raw):
        # Rejected before the lookup: a cookie or a UUID pasted here is a mistake,
        # not a credential, and hashing it would only waste a query.
        raise Unauthenticated("malformed session token")

    principal = lookup(credential_digest(raw))
    if principal is None:
        # Deliberately ONE message for: no such session, signed out, idle-timed
        # out, absolute-timed out, owner disabled, owner deleted. The person who
        # legitimately owns a revoked session learns that from whoever revoked it;
        # an attacker learns nothing.
        raise Unauthenticated("session is not valid")
    return principal


def require(principal: Principal | None, needed: Role, *,
            allow_password_change_pending: bool = False) -> Principal:
    """Authorize, or raise. Returns the principal so call sites can chain.

    The temp-password gate lives HERE and not in the UI. `web/app/actions.ts`
    already states the principle — hiding a button is a courtesy, not a control —
    and a gate enforced only by a redirect is one a user walks around by typing a
    path. The whole point of `must_change_password` is that a credential the admin
    knows must stop working.
    """
    if principal is None:
        raise Unauthenticated("no authenticated principal")
    if not principal.can(needed):
        raise Forbidden(
            f"{principal.subject} has {principal.role.value} but this needs "
            f"{needed.value} or higher")
    if principal.must_change_password and not allow_password_change_pending:
        raise PasswordChangeRequired(
            f"{principal.subject} must set a new password before using this. "
            f"POST /me/password.")
    return principal
