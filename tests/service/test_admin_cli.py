"""The bootstrap CLI, and the proposal-withdrawal authorship fix.

Two subjects in one file because both are about "who is allowed to do this", and
both were untested before M6.

`service/admin.py` is the answer to the bootstrap problem — the first admin cannot
come from an api that requires an admin. It is also the break-glass path when the
sole admin forgets their password, which is why it survives the admin UI. Neither
property was covered by a test, so nothing would have noticed if `user create`
started producing an account that could not sign in.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def admin_cli(repo, service_settings, monkeypatch):
    """Run `service.admin` against the test database, capturing stdout.

    `main()` builds its own repository from the environment, so the database URL is
    injected rather than the repo — which also means this exercises the real
    `_repo()` path including `db.migrate`.
    """
    from service import admin

    def run(*argv: str) -> tuple[int, str]:
        monkeypatch.setenv("RECON_DATABASE_URL", service_settings.database_url)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = admin.main(list(argv))
        return code, buf.getvalue()

    return run


def test_user_create_produces_an_account_that_can_sign_in(admin_cli, make_client):
    """The whole point of the bootstrap path. If the generated password does not
    authenticate, a fresh deployment is unreachable and nothing else matters."""
    code, out = admin_cli("user", "create", "--username", "boot@ada",
                          "--role", "admin")
    assert code == 0
    assert "boot@ada" in out and "recon.admin" in out

    # The password is the line printed on its own between blank lines.
    password = next(ln.strip() for ln in out.splitlines()
                    if ln.strip() and " " not in ln.strip()
                    and ln.strip() not in ("boot@ada",))

    client = make_client(role=None)
    r = client.post("/sessions", json={"username": "boot@ada", "password": password})
    assert r.status_code == 201
    assert r.json()["must_change_password"] is True, (
        "a password the operator has seen must not survive as a working credential")


def test_the_printed_password_is_not_in_the_database(admin_cli, repo):
    code, out = admin_cli("user", "create", "--username", "hash@ada",
                          "--role", "user")
    assert code == 0
    stored = repo.user_for_login("hash@ada")["password_hash"]
    assert stored.startswith("$argon2id$")
    for line in out.splitlines():
        token = line.strip()
        if len(token) > 8 and " " not in token:
            assert token not in stored


def test_user_create_normalizes_the_username(admin_cli, repo):
    assert admin_cli("user", "create", "--username", " Boot@ADA ",
                     "--role", "user")[0] == 0
    assert repo.user_by_username("boot@ada").username == "boot@ada"


def test_creating_the_same_username_twice_fails_cleanly(admin_cli):
    assert admin_cli("user", "create", "--username", "dupe@ada",
                     "--role", "user")[0] == 0
    from service.repository_identity import DuplicateUser
    with pytest.raises(DuplicateUser):
        admin_cli("user", "create", "--username", "dupe@ada", "--role", "user")


def test_user_list_renders_with_no_users(admin_cli):
    code, out = admin_cli("user", "list")
    assert code == 0
    assert "no users" in out


def test_user_list_shows_role_and_status(admin_cli):
    admin_cli("user", "create", "--username", "listed@ada", "--role", "viewer")
    code, out = admin_cli("user", "list")
    assert code == 0
    assert "listed@ada" in out and "recon.viewer" in out
    assert "must change password" in out


def test_reset_password_is_the_break_glass_path(admin_cli, make_client, login):
    """The reason the CLI cannot be deleted once the admin UI exists: this is the
    only way back in when the sole admin is locked out of their own account."""
    admin_cli("user", "create", "--username", "locked@ada", "--role", "admin")
    code, out = admin_cli("user", "reset-password", "--username", "locked@ada")
    assert code == 0

    password = next(ln.strip() for ln in out.splitlines()
                    if ln.strip() and " " not in ln.strip()
                    and "@" not in ln.strip())
    r = make_client(role=None).post("/sessions",
                                    json={"username": "locked@ada",
                                          "password": password})
    assert r.status_code == 201


def test_reset_password_signs_existing_sessions_out(admin_cli, login):
    client, _, record = login("recon.user", "revoked@ada")
    assert client.get("/me").status_code == 200
    code, out = admin_cli("user", "reset-password", "--username", "revoked@ada")
    assert code == 0
    assert "1 session(s) signed out" in out
    assert client.get("/me").status_code == 401


def test_disable_and_enable_through_the_cli(admin_cli, repo):
    admin_cli("user", "create", "--username", "onoff@ada", "--role", "user")
    assert admin_cli("user", "disable", "--username", "onoff@ada")[0] == 0
    assert repo.user_by_username("onoff@ada").enabled is False
    assert admin_cli("user", "enable", "--username", "onoff@ada")[0] == 0
    assert repo.user_by_username("onoff@ada").enabled is True


def test_a_second_admin_is_announced(admin_cli):
    """Creating a second admin is legitimate; doing it by accident is not."""
    admin_cli("user", "create", "--username", "first@ada", "--role", "admin")
    code, out = admin_cli("user", "create", "--username", "second@ada",
                          "--role", "admin")
    assert code == 0
    assert "enabled admins" in out


def test_the_role_aliases_match_the_enum(admin_cli):
    from service.admin import ROLE_ALIASES
    from service.auth import Role
    assert set(ROLE_ALIASES.values()) == set(Role)
    assert set(ROLE_ALIASES) == {"viewer", "user", "admin"}


# ---------------------------------------------------------------------------
# The proposal-withdrawal authorship fix
# ---------------------------------------------------------------------------

def _propose(client, value=1.09):
    return client.post("/config/proposals",
                       json={"path": ["vat_factors", "default"], "value": value,
                             "summary": "a change to withdraw later"})


def test_a_user_may_withdraw_their_own_proposal(editor_client):
    mine = editor_client("recon.user", subject="mine@ada")
    proposal_id = _propose(mine).json()["id"]
    assert mine.post(f"/config/proposals/{proposal_id}/withdraw").status_code == 200


def test_a_user_may_not_withdraw_someone_elses_proposal(editor_client):
    """Pre-existing hole, fixed in M6. Before this, ANY caller with the write role
    could withdraw anyone's pending change — and opening `propose` to every signed-in
    user would have widened the pool from operators to everybody."""
    author = editor_client("recon.user", subject="author@ada")
    other = editor_client("recon.user", subject="other@ada")
    proposal_id = _propose(author).json()["id"]

    r = other.post(f"/config/proposals/{proposal_id}/withdraw")
    assert r.status_code == 403
    assert "author@ada" in r.json()["detail"]

    # ...and it really is still pending.
    assert author.get(f"/config/proposals/{proposal_id}").json()["state"] == "pending"


def test_an_admin_may_withdraw_anyone_s_proposal(editor_client):
    author = editor_client("recon.user", subject="author2@ada")
    admin = editor_client("recon.admin", subject="admin2@ada")
    proposal_id = _propose(author).json()["id"]
    assert admin.post(f"/config/proposals/{proposal_id}/withdraw").status_code == 200
