"""Authentication and authorization — the milestone's reason for existing.

Defect 2.1 was "the api is unauthenticated". M5 closed it with bearer tokens; M6
replaced those with username and password sessions. Closing it is only worth
anything if something checks that it *stays* closed, and the failure mode to guard
against is not a missing check but a *new endpoint added without one*.

Two router-walking tests do that, and the second one exists because the first is
not enough:

* `test_every_route_names_the_role_it_needs` walks ALL routes, not just mutating
  ones, and requires each to name a role or be on a short explicit exemption list.
* `test_the_required_role_of_every_route_is_declared` pins the actual level in a
  table a reviewer can read. Without it, `POST /config/proposals/{id}/approve`
  silently dropping from ADMIN to USER would pass every other test in this file.

The pure-logic half of this file needs no database and runs anywhere.
"""

from __future__ import annotations

import pytest

from service import auth
from service.auth import (AuthPolicy, Forbidden, PasswordChangeRequired, Principal,
                          Role, Unauthenticated, authenticate, require)


# ---------------------------------------------------------------------------
# The model, with no database in sight
# ---------------------------------------------------------------------------

def test_roles_are_ordered_least_to_most_privileged():
    assert Role.ADMIN.satisfies(Role.USER)
    assert Role.ADMIN.satisfies(Role.VIEWER)
    assert Role.USER.satisfies(Role.VIEWER)
    assert not Role.VIEWER.satisfies(Role.USER)
    assert not Role.USER.satisfies(Role.ADMIN)


def test_role_values_are_the_strings_entra_will_emit():
    """The session lookup is a stand-in for an OIDC `roles` claim. Keeping the
    strings identical is what makes that swap a change of *who vouches* for a role
    rather than a change of what a role means (docs/13-ENTRA-SETUP.md).

    `recon.operator` became `recon.user` in M6 — same tier, a word that means
    something to the people using the app.
    """
    assert {r.value for r in Role} == {"recon.viewer", "recon.user", "recon.admin"}


def test_a_fresh_session_token_is_long_random_and_prefixed():
    a, b = auth.new_session_token(), auth.new_session_token()
    assert a != b
    assert a.startswith("recon_s_")
    assert len(a) > 40


def test_the_session_prefix_differs_from_the_deleted_token_prefix():
    """A value that LOOKS like M5's `recon_` api token invites someone to
    reintroduce token paste. It also makes a leaked value identifiable in a log as
    a session rather than a token."""
    assert auth.SESSION_PREFIX != "recon_"
    assert not auth.looks_like_credential("recon_" + "x" * 40)


def test_the_digest_is_what_gets_stored():
    raw = auth.new_session_token()
    digest = auth.credential_digest(raw)
    assert raw not in digest and len(digest) == 64


@pytest.mark.parametrize("header,expected", [
    ("Bearer abc", "abc"),
    ("bearer abc", "abc"),
    ("Bearer  abc  ", "abc"),
    ("Basic abc", None),
    ("abc", None),
    ("Bearer", None),
    ("Bearer  ", None),
    ("", None),
    (None, None),
])
def test_authorization_header_parsing(header, expected):
    """The scheme stays `Bearer` even though the credential is now a session,
    which is why the 401 challenge below is still literally correct."""
    assert auth.parse_authorization(header) == expected


def test_authenticate_rejects_a_missing_or_malformed_credential():
    policy = AuthPolicy(enabled=True)
    with pytest.raises(Unauthenticated):
        authenticate(policy, None, lambda _d: None)
    with pytest.raises(Unauthenticated):
        authenticate(policy, "Bearer not-one-of-ours", lambda _d: None)


def test_a_malformed_credential_is_never_looked_up():
    """A cookie or a UUID pasted into the header is a mistake, not a credential.
    Rejecting it before the lookup saves a query and keeps the database out of the
    path for obvious junk."""
    calls = []

    def lookup(digest):
        calls.append(digest)
        return None

    with pytest.raises(Unauthenticated):
        authenticate(AuthPolicy(enabled=True), "Bearer 1234", lookup)
    assert calls == []


def test_every_invalid_session_reason_is_indistinguishable():
    """ONE message for: no such session, signed out, idle-timed out,
    absolute-timed out, owner disabled, owner deleted. The person who legitimately
    owns a revoked session hears it from whoever revoked it; an attacker learns
    nothing."""
    with pytest.raises(Unauthenticated) as exc:
        authenticate(AuthPolicy(enabled=True),
                     f"Bearer {auth.new_session_token()}", lambda _d: None)
    assert "not valid" in str(exc.value)


def test_disabled_auth_yields_an_admin_dev_principal():
    """Local development runs as admin deliberately: a lower role would make
    laptop behaviour diverge from deployed behaviour in the direction of
    'works here, 403 there'."""
    principal = authenticate(AuthPolicy(enabled=False), None, lambda _d: None)
    assert principal.role is Role.ADMIN and principal.method == "dev"
    assert principal.must_change_password is False


def test_require_separates_401_from_403():
    viewer = Principal(subject="v", role=Role.VIEWER)
    with pytest.raises(Unauthenticated):
        require(None, Role.VIEWER)
    with pytest.raises(Forbidden):
        require(viewer, Role.USER)
    assert require(viewer, Role.VIEWER) is viewer


def test_forbidden_says_what_was_needed():
    """A 403 that does not name the required role turns into a support ticket."""
    with pytest.raises(Forbidden) as exc:
        require(Principal(subject="v", role=Role.VIEWER), Role.ADMIN)
    assert "recon.viewer" in str(exc.value) and "recon.admin" in str(exc.value)


def test_a_pending_password_change_blocks_everything_by_default():
    """Enforced in `require`, not in the UI. A gate enforced only by a redirect is
    one a user walks around by typing a path, and the whole point of
    `must_change_password` is that a credential the admin knows stops working."""
    pending = Principal(subject="new@ada", role=Role.ADMIN,
                        must_change_password=True)
    with pytest.raises(PasswordChangeRequired):
        require(pending, Role.VIEWER)
    # ...and the two routes that must stay reachable say so explicitly.
    assert require(pending, Role.VIEWER,
                   allow_password_change_pending=True) is pending


def test_password_change_required_is_a_forbidden():
    """So a handler that does nothing special with it still returns 403 rather
    than a 500."""
    assert issubclass(PasswordChangeRequired, Forbidden)


# ---------------------------------------------------------------------------
# The deployment guard
# ---------------------------------------------------------------------------

def test_binding_a_public_interface_with_auth_off_is_refused(tmp_path):
    """Not a warning. Warnings are for things you might legitimately want, and an
    unauthenticated api on a routable address can queue settlement runs, read
    client revenue and rewrite the config the money math uses."""
    from service.config import ConfigError, ServiceSettings

    common = dict(database_url="postgresql://x/y", config_dir=tmp_path, input_root=tmp_path,
                  artifact_root=tmp_path, scratch_root=tmp_path, worker_id="w")

    ServiceSettings(**common, auth_enabled=False, api_host="127.0.0.1").check_safe_to_serve()
    ServiceSettings(**common, auth_enabled=True, api_host="0.0.0.0").check_safe_to_serve()

    with pytest.raises(ConfigError, match="refusing to bind"):
        ServiceSettings(**common, auth_enabled=False, api_host="0.0.0.0").check_safe_to_serve()


def test_auth_is_on_unless_explicitly_disabled(tmp_path, monkeypatch):
    """The inversion is the control: an opt-IN flag means every forgotten
    environment is unauthenticated, which is the defect this closes."""
    from service.config import ServiceSettings

    monkeypatch.setenv("RECON_DATABASE_URL", "postgresql://x/y")
    monkeypatch.delenv("RECON_AUTH_DISABLED", raising=False)
    assert ServiceSettings.from_env(root=tmp_path).auth_enabled is True

    # Presence, not truthiness — RECON_AUTH_DISABLED=false disabling auth would
    # be a trap, so the variable's name is the whole statement.
    monkeypatch.setenv("RECON_AUTH_DISABLED", "false")
    assert ServiceSettings.from_env(root=tmp_path).auth_enabled is False


# ---------------------------------------------------------------------------
# Enforcement over HTTP
# ---------------------------------------------------------------------------

def test_no_credential_is_401_with_a_challenge(make_client):
    client = make_client(role=None)
    r = client.get("/jobs")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_healthz_needs_no_credential(make_client):
    """A load balancer has no credentials, and this reveals only whether the
    database answers."""
    r = make_client(role=None).get("/healthz")
    assert r.status_code == 200
    assert r.json()["auth"] == "enabled"


def test_a_viewer_may_read_but_not_run(make_client):
    """The role M6 nearly deleted. A finance reviewer or auditor who should read
    the invoicing numbers and never queue a settlement run is a real person."""
    client = make_client("recon.viewer")
    assert client.get("/jobs").status_code == 200
    assert client.get("/board").status_code == 200

    r = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"})
    assert r.status_code == 403
    assert "recon.user" in r.json()["detail"]


def test_a_viewer_may_not_propose_a_config_change(make_client):
    """"Any user may request a change" means any USER. A read-only account that
    can rewrite the rules it is not trusted to run is not read-only."""
    client = make_client("recon.viewer")
    r = client.post("/config/proposals",
                    json={"path": ["vat_factors", "default"], "value": 1.10,
                          "summary": "a viewer should not be able to do this"})
    assert r.status_code == 403


def test_a_user_may_run_but_not_change_the_rules(make_client):
    client = make_client("recon.user")
    assert client.post("/jobs", json={"platform": "lazada",
                                      "period": "2026-05_l1"}).status_code == 201
    assert client.get("/config").status_code == 200

    # Proposing is allowed — finance owns the rates. Deciding is not, and neither
    # is managing accounts.
    assert client.get("/users").status_code == 403
    assert client.post("/users", json={"username": "x@y",
                                       "role": "recon.user"}).status_code == 403
    assert client.post("/config/proposals/1/approve", json={}).status_code == 403


def test_an_admin_may_do_everything(make_client):
    client = make_client("recon.admin")
    assert client.get("/board").status_code == 200
    assert client.post("/jobs", json={"platform": "lazada",
                                      "period": "2026-05_l1"}).status_code == 201
    assert client.get("/users").status_code == 200


def test_me_reports_the_authenticated_identity(make_client, issue_session):
    token = issue_session("recon.user", "antoni@ada")
    body = make_client(token=token, role=None).get("/me").json()
    assert body == {"subject": "antoni@ada", "role": "recon.user",
                    "method": "password", "must_change_password": False,
                    "display_name": None}


def test_a_revoked_session_stops_working_immediately(make_client, repo, issue_session):
    token = issue_session("recon.admin", "leaver@ada")
    client = make_client(token=token, role=None)
    assert client.get("/jobs").status_code == 200

    user = repo.user_by_username("leaver@ada")
    repo.revoke_sessions_for_user(user.id, reason="admin")
    assert client.get("/jobs").status_code == 401, (
        "revocation must take effect on the next request, not when a cache expires")


def test_disabling_the_owner_invalidates_the_session(make_client, repo, issue_session):
    """Liveness is resolved by join on every request, so this needs no session
    bookkeeping at all — which is the point."""
    token = issue_session("recon.admin", "gone@ada")
    client = make_client(token=token, role=None)
    assert client.get("/jobs").status_code == 200

    # A second admin, so the last-admin guard does not fire.
    repo.create_user(username="other@ada", password_hash="$argon2id$x",
                     role=Role.ADMIN)
    repo.set_user_disabled(repo.user_by_username("gone@ada").id,
                           disabled=True, by="test")
    assert client.get("/jobs").status_code == 401


def test_demoting_the_owner_is_visible_on_the_next_request(make_client, repo,
                                                           issue_session):
    """Pins "role by join, never copied onto the session row". Without this test
    someone adds a `role` column to `user_sessions` for one fewer join, and a
    demoted admin keeps admin until they sign out."""
    token = issue_session("recon.admin", "demoted@ada")
    client = make_client(token=token, role=None)
    assert client.get("/users").status_code == 200

    repo.create_user(username="other@ada", password_hash="$argon2id$x",
                     role=Role.ADMIN)
    repo.set_user_role(repo.user_by_username("demoted@ada").id, Role.USER, by="test")
    # The session row is untouched; only the joined role changed.
    assert client.get("/users").status_code == 403


def test_an_idle_session_is_rejected(make_client, repo, issue_session, pool):
    token = issue_session("recon.admin", "idle@ada")
    client = make_client(token=token, role=None)
    assert client.get("/jobs").status_code == 200

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("update user_sessions set last_seen_at = now() - interval '2 days'")
    assert client.get("/jobs").status_code == 401


def test_an_absolutely_expired_session_is_rejected(make_client, issue_session, pool):
    """An idle timeout ALONE means a stolen token that is being actively used
    never expires."""
    token = issue_session("recon.admin", "old@ada")
    client = make_client(token=token, role=None)
    assert client.get("/jobs").status_code == 200

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("update user_sessions set absolute_expires_at = now() "
                    "- interval '1 second'")
    assert client.get("/jobs").status_code == 401


def test_last_seen_is_stamped(repo, make_user):
    from datetime import datetime, timedelta, timezone

    record = make_user("recon.viewer", "seen@ada")
    raw = auth.new_session_token()
    session = repo.create_session(
        user_id=record.id, digest=auth.credential_digest(raw),
        absolute_expires_at=datetime.now(timezone.utc) + timedelta(hours=12))
    assert session.last_seen_at is not None

    assert repo.principal_for_session(auth.credential_digest(raw)) is not None
    assert repo.user_by_username("seen@ada") is not None


def test_a_repository_that_cannot_authenticate_fails_loudly(pool, store, service_settings):
    """A wiring mistake must not present as a credential error.

    Found the hard way: a credential minted against one database and checked
    against another reports "not valid", which reads as "your credential is wrong"
    and sends you looking in entirely the wrong place. That particular case is
    unfixable from inside the process — but the *adjacent* one is not. Handing
    `create_app` a repository with no `users` table would reject every credential
    ever presented, silently and forever, so it raises at construction instead.
    """
    from service.api import create_app
    from service.auth import AuthPolicy
    from service.repository import Repository

    with pytest.raises(TypeError, match="cannot authenticate sessions"):
        create_app(Repository(pool), store, settings=service_settings,
                   policy=AuthPolicy(enabled=True))

    # ...and it is allowed with auth off, which is the local-development case.
    create_app(Repository(pool), store, settings=service_settings,
               policy=AuthPolicy(enabled=False))


# ---------------------------------------------------------------------------
# The router walk — the only automated guard on authorization
# ---------------------------------------------------------------------------

# Deliberately unauthenticated. Each one is here for a stated reason, and adding
# to this set is a visible act in a diff.
UNAUTHENTICATED = {
    ("GET", "/healthz"),        # a load balancer holds no credential
    ("GET", "/meta"),           # static vocabulary: platforms, roles, exit codes
    ("POST", "/sessions"),      # this IS the authentication
}

# The required role of every authenticated route, as a table a reviewer can read.
# Asserted in BOTH directions: a route missing from here fails, and a mismatch
# fails. This is what makes an accidental privilege change break a test.
EXPECTED: dict[tuple[str, str], Role] = {
    ("GET", "/me"): Role.VIEWER,
    ("DELETE", "/sessions/current"): Role.VIEWER,
    ("POST", "/me/password"): Role.VIEWER,

    ("GET", "/users"): Role.ADMIN,
    ("POST", "/users"): Role.ADMIN,
    ("POST", "/users/{user_id}/password"): Role.ADMIN,
    ("POST", "/users/{user_id}/disable"): Role.ADMIN,
    ("POST", "/users/{user_id}/enable"): Role.ADMIN,
    ("POST", "/users/{user_id}/role"): Role.ADMIN,
    ("GET", "/users/{user_id}/sessions"): Role.ADMIN,
    ("DELETE", "/users/{user_id}/sessions"): Role.ADMIN,

    ("GET", "/board"): Role.VIEWER,
    ("POST", "/jobs"): Role.USER,
    ("GET", "/jobs"): Role.VIEWER,
    ("GET", "/jobs/{job_id}"): Role.VIEWER,
    ("POST", "/jobs/{job_id}/cancel"): Role.USER,

    ("GET", "/runs/{run_id}"): Role.VIEWER,
    ("GET", "/runs/{run_id}/log"): Role.VIEWER,
    ("GET", "/runs/{run_id}/artifacts"): Role.VIEWER,
    ("GET", "/runs/{run_id}/artifacts/{name}"): Role.VIEWER,
    ("GET", "/runs/{run_id}/exceptions"): Role.VIEWER,
    ("GET", "/exceptions/{fingerprint}/history"): Role.VIEWER,

    ("POST", "/uploads"): Role.USER,
    ("GET", "/uploads"): Role.VIEWER,
    # A pure read: a viewer may watch a window fill up and cannot alter it.
    ("GET", "/uploads/plan"): Role.VIEWER,
    ("POST", "/uploads/{upload_id}/reject"): Role.USER,
    # `POST /uploads/{id}/stage` is deleted (M6, workstream B) — the bucket is the
    # window, so there is nothing to move. test_there_is_no_stage_route pins that.

    # Seeding writes uploads and a config version, so it is ADMIN — a demo that any
    # user could re-seed mid-month is a way to put synthetic rows next to real ones.
    ("POST", "/demo/seed"): Role.ADMIN,
    ("DELETE", "/demo/seed"): Role.ADMIN,

    ("GET", "/windows/{platform}/{period}"): Role.VIEWER,
    # Declaring a partial roster relaxes a hard stop, so it is USER, not VIEWER —
    # and the reason it is not ADMIN is that the person assembling the window is
    # the one who knows why a store is absent. The control is the recorded reason,
    # not the rank.
    ("POST", "/windows/roster"): Role.USER,
    ("DELETE", "/windows/{platform}/{period}/roster"): Role.USER,

    ("GET", "/config"): Role.VIEWER,
    # The sectioned form and the evidence it renders are a READ of the same file
    # `GET /config` already returns verbatim, so it is no more privileged.
    ("GET", "/config/schema"): Role.VIEWER,
    # Computing a diff commits nothing — but it is USER, matching `POST
    # /config/proposals`, because a viewer who cannot propose has no use for a
    # preview of a proposal and it would only be an odd hole in the surface.
    ("POST", "/config/preview"): Role.USER,
    ("GET", "/config/versions"): Role.VIEWER,
    ("GET", "/config/versions/{version_id}"): Role.VIEWER,
    ("GET", "/config/pins"): Role.VIEWER,
    ("POST", "/config/pins"): Role.ADMIN,
    ("DELETE", "/config/pins/{platform}/{period}"): Role.ADMIN,
    ("POST", "/config/proposals"): Role.USER,
    ("GET", "/config/proposals"): Role.VIEWER,
    ("GET", "/config/proposals/{proposal_id}"): Role.VIEWER,
    ("POST", "/config/proposals/{proposal_id}/approve"): Role.ADMIN,
    ("POST", "/config/proposals/{proposal_id}/reject"): Role.ADMIN,
    ("POST", "/config/proposals/{proposal_id}/apply"): Role.ADMIN,
    ("POST", "/config/proposals/{proposal_id}/withdraw"): Role.USER,
    # Replaying a stale proposal creates a new PENDING one and changes no config, so
    # it is the same privilege as proposing. Like withdraw, it also carries an
    # authorship check the role cannot express — you may replay your own, an admin
    # may replay anyone's.
    ("POST", "/config/proposals/{proposal_id}/rebase"): Role.USER,
}


def _walk(app):
    """Yield (method, path, declared_role_or_None) for every APIRoute.

    Reads the role out of the `requires(role)` dependency's closure, which is why
    `service/api.py` must keep `role` as a closed-over `Role` object. Rewriting
    that as `Annotated[...]` or as decorator-level `dependencies=[...]` would make
    these tests pass vacuously — the comment there says so.
    """
    import inspect

    from fastapi.routing import APIRoute

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        params = inspect.signature(route.endpoint).parameters.values()
        principal = next(
            (p for p in params if p.annotation in (Principal, "Principal")), None)
        role = None
        if principal is not None:
            dependency = getattr(principal.default, "dependency", None)
            cells = getattr(dependency, "__closure__", None) or ()
            roles = [c.cell_contents for c in cells
                     if isinstance(c.cell_contents, Role)]
            role = roles[0] if roles else None
        for method in sorted(route.methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            yield method, route.path, role


@pytest.fixture
def walked(repo, store, service_settings):
    from service.api import create_app
    from service.auth import AuthPolicy
    app = create_app(repo, store, settings=service_settings,
                     policy=AuthPolicy(enabled=True))
    return list(_walk(app))


def test_every_route_names_the_role_it_needs(walked):
    """Walks ALL routes, not just mutating ones.

    Reading GETs too is what keeps this meaningful: a new
    `GET /users/{id}/password-hash` added without a role now fails here.
    """
    unguarded = [f"{m} {p}" for m, p, role in walked
                 if role is None and (m, p) not in UNAUTHENTICATED]
    assert not unguarded, (
        f"these routes take no authenticated principal: {unguarded}. Every route "
        f"must name the role it needs, or be listed in UNAUTHENTICATED with a "
        f"reason.")


def test_nothing_is_quietly_unauthenticated(walked):
    """The other direction: a route on the exemption list that has since GAINED a
    role should be removed from the list, and a route removed from the app should
    not linger there pretending to be covered."""
    present = {(m, p) for m, p, _ in walked}
    stale = sorted(UNAUTHENTICATED - present)
    assert not stale, (
        f"UNAUTHENTICATED lists routes that no longer exist: {stale}")


def test_the_required_role_of_every_route_is_declared(walked):
    """The table is the point.

    Under the old single-sided check, `POST /config/proposals/{id}/approve`
    dropping from ADMIN to USER would have passed — USER satisfies USER. Naming the
    expected level per route is what turns an accidental privilege change into a
    failing test.
    """
    missing, wrong = [], []
    for method, path, role in walked:
        if (method, path) in UNAUTHENTICATED:
            continue
        if (method, path) not in EXPECTED:
            missing.append(f"{method} {path}")
            continue
        if role is not EXPECTED[(method, path)]:
            wrong.append(f"{method} {path}: declared {EXPECTED[(method, path)].value}, "
                         f"actually {role.value if role else None}")

    assert not missing, (
        f"these routes are not in EXPECTED: {missing}. A new endpoint must state "
        f"its role here, where a reviewer sees it next to every other one.")
    assert not wrong, f"role mismatches: {wrong}"


def test_expected_has_no_routes_the_app_lost(walked):
    present = {(m, p) for m, p, _ in walked}
    stale = sorted(f"{m} {p}" for m, p in EXPECTED if (m, p) not in present)
    assert not stale, (
        f"EXPECTED names routes that no longer exist: {stale}. A table that "
        f"describes a router from two milestones ago is worse than no table.")


# Mutating routes a VIEWER may legitimately call, because the only thing they
# change belongs to the caller. A read-only account still has to be able to sign
# out and to stop using the password an admin generated for it — refusing either
# would mean a viewer either cannot leave or cannot own their own credential.
#
# This set is the exception to "a viewer changes nothing", and it is deliberately
# tiny. Anything that touches a job, an upload, the config or another account does
# NOT belong here.
SELF_SERVICE = {
    ("DELETE", "/sessions/current"),
    ("POST", "/me/password"),
}


def test_every_mutating_route_requires_at_least_user(walked):
    """The M5 check, kept because it states the invariant directly: a viewer must
    not be able to change anything except their own session and password.

    Added in M6 after this test caught the ambiguity: sign-out and
    change-own-password are mutating routes that a read-only account must be able
    to reach, so the invariant needed narrowing to system state rather than
    weakening to nothing.
    """
    too_weak = [f"{m} {p} requires only {role.value}" for m, p, role in walked
                if m in {"POST", "PUT", "PATCH", "DELETE"}
                and (m, p) not in UNAUTHENTICATED
                and (m, p) not in SELF_SERVICE
                and role is not None and not role.satisfies(Role.USER)]
    assert not too_weak, (
        f"these mutating routes are readable-role only: {too_weak}. A viewer must "
        f"not be able to change anything but their own session and password — and "
        f"if one of these genuinely is self-service, add it to SELF_SERVICE with a "
        f"reason rather than widening this check.")


def test_self_service_routes_only_touch_the_caller(walked):
    """The companion guard: SELF_SERVICE must stay tiny and must not accumulate.

    Every path in it has to be under /me or /sessions/current — anything with a
    `{user_id}` in it acts on somebody else and cannot be self-service by
    definition.
    """
    present = {(m, p) for m, p, _ in walked}
    stale = sorted(f"{m} {p}" for m, p in SELF_SERVICE if (m, p) not in present)
    assert not stale, f"SELF_SERVICE names routes that no longer exist: {stale}"

    wrong = sorted(f"{m} {p}" for m, p in SELF_SERVICE
                   if not (p.startswith("/me") or p == "/sessions/current"))
    assert not wrong, (
        f"these are not self-service paths: {wrong}. A route that names another "
        f"account is not something a viewer may call.")


def test_password_change_pending_blocks_everything_but_three_routes(make_client):
    """The router-walking guard for the NEW gate.

    A temp password the admin knows must not be a working credential for anything
    except getting rid of it.
    """
    client = make_client("recon.admin", must_change_password=True)

    assert client.get("/me").status_code == 200
    assert client.get("/healthz").status_code == 200

    for method, path in [("GET", "/board"), ("GET", "/jobs"), ("GET", "/config"),
                         ("GET", "/users")]:
        r = client.request(method, path)
        assert r.status_code == 403, f"{method} {path} should be blocked"
        assert r.json().get("code") == "password_change_required", (
            f"{method} {path} must say WHY, or the BFF cannot redirect")

    r = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"})
    assert r.status_code == 403
    assert r.json().get("code") == "password_change_required"
