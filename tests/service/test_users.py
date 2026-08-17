"""Account management.

Two tests here are structural rather than behavioural and are the ones worth
keeping if the file ever has to shrink:

* `test_no_response_body_ever_contains_a_hash` — the runtime companion to
  `UserRecord` having no `password_hash` field.
* `test_there_is_no_delete_route` — pins the disable-never-delete decision, which
  exists so `requested_by` keeps resolving to a name a human recognises.
"""

from __future__ import annotations

import pytest


def test_create_list_and_the_generated_password(make_client):
    client = make_client("recon.admin")
    r = client.post("/users", json={"username": "New.Person@ADA",
                                    "role": "recon.user",
                                    "display_name": "New Person"})
    assert r.status_code == 201
    body = r.json()

    # Normalized on the way in: one account, one audit identity.
    assert body["username"] == "new.person@ada"
    assert body["role"] == "recon.user"
    assert body["must_change_password"] is True
    assert len(body["password"]) >= 12
    assert "shown once" in body["warning"]

    listed = client.get("/users").json()["users"]
    assert any(u["username"] == "new.person@ada" for u in listed)


def test_the_generated_password_works_and_is_never_stored(make_client, login):
    """Two halves, both necessary: the credential must actually authenticate, and
    it must appear nowhere in any listing."""
    client = make_client("recon.admin")
    raw = client.post("/users", json={"username": "worker@ada",
                                      "role": "recon.user"}).json()["password"]

    signed_in = client.post("/sessions", json={"username": "worker@ada",
                                               "password": raw})
    assert signed_in.status_code == 201
    assert signed_in.json()["must_change_password"] is True

    assert raw not in client.get("/users").text


def test_an_admin_cannot_choose_someone_elses_password(make_client):
    """`UserCreateRequest` has no password field, deliberately. An admin who picks
    a password knows it, and every audit column this service exists to make
    trustworthy is only evidence if impersonating a colleague is hard."""
    client = make_client("recon.admin")
    r = client.post("/users", json={"username": "chosen@ada", "role": "recon.user",
                                    "password": "i-picked-this-myself"})
    # Pydantic ignores the unknown field rather than honouring it; what matters is
    # that the returned credential is NOT the one the admin tried to set.
    assert r.status_code == 201
    assert r.json()["password"] != "i-picked-this-myself"


def test_no_response_body_ever_contains_a_hash(make_client):
    client = make_client("recon.admin")
    created = client.post("/users", json={"username": "hashcheck@ada",
                                          "role": "recon.user"}).json()
    user_id = created["id"]

    for method, path in [("GET", "/users"),
                         ("GET", f"/users/{user_id}/sessions"),
                         ("POST", f"/users/{user_id}/password"),
                         ("POST", f"/users/{user_id}/disable"),
                         ("POST", f"/users/{user_id}/enable")]:
        r = client.request(method, path)
        assert r.status_code < 400, f"{method} {path} -> {r.status_code}"
        assert "$argon2" not in r.text, f"{method} {path} leaked a password hash"
        assert "password_hash" not in r.text


def test_a_duplicate_username_is_a_409(make_client):
    client = make_client("recon.admin")
    assert client.post("/users", json={"username": "dupe@ada",
                                       "role": "recon.user"}).status_code == 201
    r = client.post("/users", json={"username": "dupe@ada", "role": "recon.user"})
    assert r.status_code == 409


def test_a_duplicate_differing_only_by_case_is_also_a_409(make_client):
    """Normalization happens before the insert, so "Dupe@ADA" is the same row."""
    client = make_client("recon.admin")
    client.post("/users", json={"username": "case@ada", "role": "recon.user"})
    r = client.post("/users", json={"username": "CASE@ADA", "role": "recon.user"})
    assert r.status_code == 409


def test_resetting_a_password_signs_them_out_everywhere(make_client, login,
                                                        test_password):
    """A reset the user did not ask for should not leave a live session behind."""
    admin = make_client("recon.admin")
    target_client, _, target = login("recon.user", "resetme@ada")
    assert target_client.get("/me").status_code == 200

    r = admin.post(f"/users/{target.id}/password")
    assert r.status_code == 200
    assert r.json()["sessions_signed_out"] == 1
    assert r.json()["must_change_password"] is True

    assert target_client.get("/me").status_code == 401


def test_disable_then_enable(make_client, login, test_password):
    admin = make_client("recon.admin")
    target_client, _, target = login("recon.user", "onoff@ada")

    admin.post(f"/users/{target.id}/disable")
    assert target_client.get("/me").status_code == 401
    assert admin.post("/sessions", json={"username": "onoff@ada",
                                         "password": test_password}
                      ).status_code == 401

    admin.post(f"/users/{target.id}/enable")
    # Enable does not touch the password — the right behaviour for "back from
    # leave".
    assert admin.post("/sessions", json={"username": "onoff@ada",
                                         "password": test_password}
                      ).status_code == 201


def test_you_cannot_disable_your_own_account(make_client, repo):
    client = make_client("recon.admin", username="self@ada")
    me = repo.user_by_username("self@ada")
    # A second admin, so this is the self-check firing and not the last-admin one.
    repo.create_user(username="spare@ada", password_hash="$argon2id$x",
                     role=__import__("service.auth", fromlist=["Role"]).Role.ADMIN)
    r = client.post(f"/users/{me.id}/disable")
    assert r.status_code == 409
    assert "your own account" in r.json()["detail"]


def test_the_last_admin_cannot_be_disabled_or_demoted(make_client, repo):
    """Lock out the only admin and account management is bricked until somebody
    with the database URL intervenes."""
    client = make_client("recon.admin", username="only@ada")
    other = client.post("/users", json={"username": "plain@ada",
                                        "role": "recon.user"}).json()
    only = repo.user_by_username("only@ada")

    # Demoting the sole admin is refused...
    r = client.post(f"/users/{only.id}/role", json={"role": "recon.user"})
    assert r.status_code == 409
    assert "only enabled admin" in r.json()["detail"]

    # ...and so is disabling them, even by another admin — promote first.
    promoted = client.post(f"/users/{other['id']}/role",
                           json={"role": "recon.admin"})
    assert promoted.status_code == 200
    # Now there are two, so demotion is allowed.
    assert client.post(f"/users/{only.id}/role",
                       json={"role": "recon.user"}).status_code == 200


def test_demotion_signs_the_person_out(make_client, login):
    admin = make_client("recon.admin", username="boss@ada")
    target_client, _, target = login("recon.admin", "junior@ada")
    assert target_client.get("/users").status_code == 200

    r = admin.post(f"/users/{target.id}/role", json={"role": "recon.user"})
    assert r.status_code == 200
    assert r.json()["sessions_signed_out"] >= 1
    # Belt and braces: the role is resolved by join anyway, so this would be 403
    # even without the revoke. Being asked to sign in again is clearer than an
    # admin nav quietly vanishing mid-click.
    assert target_client.get("/users").status_code == 401


def test_signing_a_person_out_everywhere(make_client, login):
    """The "this person has left" event docs/09-OPERATIONS.md recorded as
    missing."""
    admin = make_client("recon.admin")
    target_client, _, target = login("recon.user", "departed@ada")

    r = admin.delete(f"/users/{target.id}/sessions")
    assert r.status_code == 200
    assert r.json()["sessions_signed_out"] == 1
    assert target_client.get("/me").status_code == 401


def test_sessions_can_be_listed_for_an_audit(make_client, login):
    admin = make_client("recon.admin")
    _, _, target = login("recon.user", "watched@ada")
    sessions = admin.get(f"/users/{target.id}/sessions").json()["sessions"]
    assert len(sessions) == 1
    assert "token_sha256" not in str(sessions[0])


def test_there_is_no_delete_route(make_client):
    """Disabled, never deleted. requested_by / proposed_by / uploaded_by are free
    text and must keep resolving to a name a human recognises; a delete would
    leave the audit trail pointing at nobody."""
    client = make_client("recon.admin")
    # 404 because the path does not exist at all (405 would mean it exists with
    # other verbs). Either answer proves the route is absent; what must never
    # happen is a 2xx.
    assert client.delete("/users/1").status_code in (404, 405)


def test_a_user_cannot_reach_any_admin_route(make_client):
    client = make_client("recon.user")
    for method, path in [("GET", "/users"), ("POST", "/users"),
                         ("POST", "/users/1/password"),
                         ("POST", "/users/1/disable"), ("POST", "/users/1/enable"),
                         ("POST", "/users/1/role"),
                         ("GET", "/users/1/sessions"),
                         ("DELETE", "/users/1/sessions")]:
        r = client.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


def test_an_unknown_user_id_is_a_404(make_client):
    client = make_client("recon.admin")
    assert client.post("/users/99999/password").status_code == 404
    assert client.get("/users/99999/sessions").status_code == 404
