"""Sign-in, sign-out, expiry and throttling — through the real `POST /sessions`.

The test that matters most here is `test_the_failure_is_uniform`. The deliberate
ambiguity between "no such account", "wrong password" and "account disabled" is a
security property, and a property asserted only by a code comment is one that
erodes the first time somebody adds a helpful error message.

Everything in this file uses the `login` fixture rather than `make_client`, because
the subject IS sign-in. `make_client` inserts a session row directly and is the
right choice everywhere else.
"""

from __future__ import annotations

import pytest


def test_a_correct_password_yields_a_working_session(login):
    client, response, record = login("recon.user", "antoni@ada")
    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("recon_s_")
    assert body["subject"] == "antoni@ada"
    assert body["role"] == "recon.user"
    assert body["must_change_password"] is False

    me = client.get("/me").json()
    assert me["subject"] == "antoni@ada" and me["method"] == "password"


def test_the_failure_is_uniform(login, make_user, test_password):
    """Unknown username, wrong password and disabled account must be
    indistinguishable — same status AND same body.

    The identical MESSAGE is only half of it; `passwords.verify_dummy()` on the
    unknown-user branch is what equalises the wall clock. Asserted structurally in
    test_passwords.py, because a timing assertion here would be flaky and would be
    skipped within a month.
    """
    make_user("recon.user", "real@ada")
    _, disabled_resp, disabled = login("recon.user", "disabled@ada")
    assert disabled_resp.status_code == 201

    client, _, _ = login("recon.admin", "admin@ada")
    # A second admin already exists (admin@ada), so disabling is allowed.
    client.post(f"/users/{disabled.id}/disable")

    unknown = client.post("/sessions", json={"username": "nobody@ada",
                                             "password": test_password})
    wrong = client.post("/sessions", json={"username": "real@ada",
                                           "password": "wrong-password-here"})
    blocked = client.post("/sessions", json={"username": "disabled@ada",
                                             "password": test_password})

    assert unknown.status_code == wrong.status_code == blocked.status_code == 401
    assert unknown.json() == wrong.json() == blocked.json()
    assert "invalid username or password" in unknown.json()["detail"]


def test_a_malformed_username_is_also_a_uniform_401(login, test_password):
    """Otherwise the endpoint is a username-format oracle: a 422 for "has space"
    and a 401 for "nobody@ada" tells an attacker which strings are even
    candidates."""
    client, _, _ = login("recon.user", "someone@ada")
    r = client.post("/sessions", json={"username": "has space",
                                       "password": test_password})
    assert r.status_code == 401
    assert "invalid username or password" in r.json()["detail"]


def test_username_is_case_insensitive_and_password_is_not(login, test_password):
    client, _, record = login("recon.user", "mixed@ada")

    ok = client.post("/sessions", json={"username": "MIXED@ADA",
                                        "password": test_password})
    assert ok.status_code == 201, "an audit identity must not depend on Shift"

    bad = client.post("/sessions", json={"username": "mixed@ada",
                                         "password": test_password.upper()})
    assert bad.status_code == 401


def test_sign_out_revokes_server_side(login):
    """Catches the whole bug class where sign-out only drops the cookie and leaves
    a valid session alive for the rest of the absolute window."""
    client, _, _ = login("recon.user", "leaving@ada")
    assert client.get("/me").status_code == 200

    assert client.delete("/sessions/current").json()["session_revoked"] is True
    assert client.get("/me").status_code == 401


def test_sign_out_twice_is_not_an_error(login):
    client, _, _ = login("recon.user", "twice@ada")
    assert client.delete("/sessions/current").status_code == 200
    # The second call is now unauthenticated, which is the honest answer.
    assert client.delete("/sessions/current").status_code == 401


def test_changing_your_password_keeps_this_session_and_kills_the_others(
        login, repo, test_password):
    client, _, record = login("recon.user", "rotate@ada")
    # A second session for the same person.
    other = client.post("/sessions", json={"username": "rotate@ada",
                                           "password": test_password})
    assert other.status_code == 201
    other_token = other.json()["token"]

    r = client.post("/me/password", json={"current_password": test_password,
                                          "new_password": "a-brand-new-password-1"})
    assert r.status_code == 200
    assert r.json()["other_sessions_signed_out"] == 1

    # This session survives — changing your password should not sign you out of
    # the tab you did it in.
    assert client.get("/me").status_code == 200

    # The other one does not.
    client.headers["Authorization"] = f"Bearer {other_token}"
    assert client.get("/me").status_code == 401


def test_changing_a_password_requires_the_current_one(login, test_password):
    """The control that makes a stolen session non-permanent: whoever holds the
    cookie cannot lock the owner out without also knowing the password."""
    client, _, _ = login("recon.user", "guarded@ada")
    r = client.post("/me/password", json={"current_password": "not-the-password",
                                          "new_password": "a-brand-new-password-1"})
    assert r.status_code == 403
    assert "current password" in r.json()["detail"]


def test_the_new_password_must_differ(login, test_password):
    client, _, _ = login("recon.user", "same@ada")
    r = client.post("/me/password", json={"current_password": test_password,
                                          "new_password": test_password})
    assert r.status_code == 422


def test_a_weak_new_password_is_refused(login, test_password):
    client, _, _ = login("recon.user", "weak@ada")
    r = client.post("/me/password", json={"current_password": test_password,
                                          "new_password": "short"})
    assert r.status_code == 422


def test_must_change_password_gates_everything_but_the_change_itself(
        login, test_password):
    client, response, _ = login("recon.admin", "fresh@ada",
                                must_change_password=True)
    assert response.status_code == 201
    assert response.json()["must_change_password"] is True

    assert client.get("/me").status_code == 200
    blocked = client.get("/board")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "password_change_required"

    r = client.post("/me/password", json={"current_password": test_password,
                                          "new_password": "a-brand-new-password-1"})
    assert r.status_code == 200
    # ...and now the rest of the app opens up.
    assert client.get("/board").status_code == 200


def test_rehash_on_login_upgrades_a_weak_hash(login, repo, make_user, pool):
    """The only moment the plaintext is available is a successful login, which is
    why argon2's self-describing PHC string was chosen over a scheme with the cost
    in a column."""
    from argon2 import PasswordHasher

    weak_hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1,
                                 hash_len=16, salt_len=8)
    password = "a-perfectly-fine-password"
    record = make_user("recon.user", "weakhash@ada")
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("update users set password_hash = %s where id = %s",
                    (weak_hasher.hash(password), record.id))

    before = repo.user_for_login("weakhash@ada")["password_hash"]
    client, _, _ = login("recon.user", "other@ada")
    r = client.post("/sessions", json={"username": "weakhash@ada",
                                       "password": password})
    assert r.status_code == 201, "the old password must still work"

    after = repo.user_for_login("weakhash@ada")["password_hash"]
    assert after != before, "a weak hash must be upgraded in place on login"
    assert "m=19456" in after


def test_the_throttle_blocks_after_the_limit(login, make_user, test_password):
    client, _, _ = login("recon.admin", "watcher@ada")
    make_user("recon.user", "target@ada")

    for _ in range(10):
        r = client.post("/sessions", json={"username": "target@ada",
                                          "password": "wrong"})
        assert r.status_code == 401

    blocked = client.post("/sessions", json={"username": "target@ada",
                                             "password": "wrong"})
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After")
    # Phrasing must not confirm the account exists.
    assert "target@ada" not in blocked.text

    # A CORRECT password inside the cool-off is still refused — that is the point
    # of a throttle, and it is why the response is 429 rather than a silent 401.
    still = client.post("/sessions", json={"username": "target@ada",
                                           "password": test_password})
    assert still.status_code == 429


def test_a_successful_login_clears_the_counter(login, make_user, test_password):
    client, _, _ = login("recon.admin", "counter@ada")
    make_user("recon.user", "recovers@ada")

    for _ in range(3):
        client.post("/sessions", json={"username": "recovers@ada",
                                       "password": "wrong"})
    assert client.post("/sessions", json={"username": "recovers@ada",
                                          "password": test_password}
                       ).status_code == 201
    # Three more failures must not now trip a limit of ten.
    for _ in range(3):
        r = client.post("/sessions", json={"username": "recovers@ada",
                                           "password": "wrong"})
        assert r.status_code == 401


def test_requested_by_comes_from_the_session(login):
    """The audit-column property, re-proven under the new credential."""
    client, _, _ = login("recon.user", "auditme@ada")
    r = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"})
    assert r.status_code == 201
    assert r.json()["requested_by"] == "auditme@ada"


def test_a_session_records_where_it_came_from(login, repo):
    """So "sign this person out everywhere" can be reviewed afterwards."""
    client, response, record = login("recon.user", "traced@ada")
    sessions = repo.list_sessions_for_user(record.id)
    assert len(sessions) == 1
    assert sessions[0].user_agent is not None
    assert sessions[0].absolute_expires_at is not None
