"""Fixtures for the M4 service suite.

**A throwaway database per session, never the one in the URL.** The fixture
connects to `RECON_TEST_DATABASE_URL`, creates `recon_test_<pid>`, migrates it,
and drops it at the end. Nothing here can truncate a database somebody was
using, which matters because the natural mistake — pointing the suite at
`RECON_DATABASE_URL` and truncating between tests — would delete an operator's
run history the first time it was made.

`RECON_DATABASE_URL` is deliberately NOT a fallback. A test suite that quietly
runs against the production queue is worse than one that skips.

Tests that need no database (QueueRunLog, the artifact store, the import lints)
live in the same directory and do not use these fixtures, so they run everywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKIP_REASON = (
    "no RECON_TEST_DATABASE_URL — the Postgres-backed service tests need a real "
    "server, because `FOR UPDATE SKIP LOCKED` and lease expiry cannot be proven "
    "against a substitute. Point it at any Postgres and they run; the suite "
    "creates and drops its own database."
)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("RECON_TEST_DATABASE_URL")
    if not url:
        pytest.skip(SKIP_REASON, allow_module_level=True)

    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    info = conninfo_to_dict(url)
    admin_db = info.get("dbname") or "postgres"
    name = f"recon_test_{os.getpid()}"

    admin = psycopg.connect(url, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))
            cur.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    finally:
        admin.close()

    yield make_conninfo(url, dbname=name)

    admin = psycopg.connect(make_conninfo(url, dbname=admin_db), autocommit=True)
    try:
        with admin.cursor() as cur:
            # Anything still holding a connection would block the drop; a leaked
            # pool in a failing test must not leave a database behind.
            cur.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                        "where datname = %s and pid <> pg_backend_pid()", (name,))
            cur.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))
    finally:
        admin.close()


@pytest.fixture(scope="session")
def pool(database_url: str):
    from service import db
    p = db.make_pool(database_url, min_size=1, max_size=8)
    with p.connection() as conn:
        db.migrate(conn)
    yield p
    p.close()


@pytest.fixture
def repo(pool):
    """A clean queue for every test.

    `restart identity` so ids start at 1 in each test: a test asserting on an id
    it computed itself is fine, but a test that happens to pass because job 3 was
    created by an earlier test is not.

    Returns the M5 repository — one object with the queue and the record-keeping
    on it, which is how the api holds it.
    """
    from service.repository_m5 import M5Repository
    with pool.connection() as conn, conn.cursor() as cur:
        # `user_sessions` is named explicitly even though `cascade` from `users`
        # would reach it, so that `restart identity` applies to both. `api_tokens`
        # is gone — the table was dropped in 003_password_auth.sql, and naming a
        # missing table here is an immediate hard error in every DB test.
        cur.execute("truncate jobs, users, user_sessions, config_versions, "
                    "config_proposals, uploads restart identity cascade")
        # Per-window state. `window_references` was added in M8/2.1 and its absence
        # here was caught immediately and unmistakably: a test in another file
        # supplied reference totals for the smoke window, and three worker tests
        # that assert UNVERIFIED started reporting VARIANCE. `windows` is truncated
        # alongside it because it is the same shape of leak waiting to happen —
        # both are keyed on (platform, period), and every worker test reuses one
        # synthetic window name.
        cur.execute("truncate window_references, windows")
        # `config_pin_events` (migration 014) is per-window too, and append-only, so
        # it accumulates across a session that reuses one window name — which is
        # exactly how the `window_references` leak above was found. Note
        # `period_config` needs no entry: `config_versions` is truncated with cascade,
        # which takes its rows with it.
        cur.execute("truncate config_pin_events")
        # The normalized config tables (migration 007). Truncated with the rest so
        # a test that imports a contract cannot leak it into the next one — and so
        # `render_config()` correctly reports "empty, seed me from the file" for
        # every test that has not deliberately imported.
        cur.execute("truncate config_scalars, config_platforms, config_reading, "
                    "config_column_maps, config_stores, config_store_aliases, "
                    "config_store_brands, config_tolerances, "
                    "config_settlement_bounds, config_fee_types, config_vat_sku")
    return M5Repository(pool)


@pytest.fixture(scope="session")
def test_password():
    """One password for the whole suite."""
    return "test-suite-password-9f3a"


@pytest.fixture(scope="session")
def test_password_hash(test_password):
    """Argon2 ONCE for the whole suite, not once per client.

    `make_client` is called ~26 times across tests/service and each call would
    otherwise pay a full Argon2id hash at m=19 MiB. The hashing itself is proven by
    test_passwords.py and by the tests that go through POST /sessions, so paying for
    it per client buys no coverage and is a visible fraction of a ~13s suite.
    """
    from service import passwords
    return passwords.hash_password(test_password)


@pytest.fixture
def make_user(repo, test_password_hash):
    """Create a real user row of a given role."""
    from service.auth import Role

    def make(role: str = "recon.admin", username: str | None = None, *,
             must_change_password: bool = False, display_name: str | None = None):
        parsed = Role(role)
        return repo.create_user(
            username=username or f"{parsed.name.lower()}@test",
            password_hash=test_password_hash, role=parsed,
            display_name=display_name, created_by="conftest",
            must_change_password=must_change_password)

    return make


@pytest.fixture
def issue_session(repo, make_user):
    """A real user plus a real session row; returns the raw session token.

    Inserts the session directly rather than going through `POST /sessions`. That
    is the FAST path and it is deliberate — see `login` for the slow one. Both are
    needed: without this the suite pays a login per client for no coverage, and
    without `login` nothing exercises the actual sign-in route.
    """
    from datetime import datetime, timedelta, timezone

    from service import auth as auth_mod

    def issue(role: str = "recon.admin", username: str | None = None, *,
              must_change_password: bool = False) -> str:
        record = make_user(role, username, must_change_password=must_change_password)
        raw = auth_mod.new_session_token()
        repo.create_session(
            user_id=record.id, digest=auth_mod.credential_digest(raw),
            absolute_expires_at=datetime.now(timezone.utc) + timedelta(hours=12))
        return raw

    return issue


@pytest.fixture
def make_client(repo, store, service_settings, issue_session):
    """A TestClient authenticated as a given role, with auth genuinely ON.

    The M4 tests run through this too, so every one of them also proves its
    endpoint is reachable *with* a credential — and `test_auth.py` proves what
    happens without one.

    A fresh app per call, which also gives every test its own `Throttle`: a test
    that deliberately fails ten logins cannot throttle another test.
    """
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from service.api import create_app
    from service.auth import AuthPolicy

    def build(role: str | None = "recon.admin", token: str | None = None, *,
              username: str | None = None, must_change_password: bool = False):
        app = create_app(repo, store, settings=service_settings,
                         policy=AuthPolicy(enabled=True))
        client = TestClient(app)
        if token is None and role is not None:
            token = issue_session(role, username,
                                  must_change_password=must_change_password)
        if token:
            client.headers["Authorization"] = f"Bearer {token}"
        return client

    return build


@pytest.fixture
def login(repo, store, service_settings, make_user, test_password):
    """A TestClient authenticated through the REAL `POST /sessions` route.

    Slower than `make_client` (one argon2 verify per call), so it is used only by
    the tests whose subject is sign-in itself.
    """
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from service.api import create_app
    from service.auth import AuthPolicy

    def build(role: str = "recon.admin", username: str | None = None, *,
              password: str | None = None, must_change_password: bool = False):
        record = make_user(role, username, must_change_password=must_change_password)
        app = create_app(repo, store, settings=service_settings,
                         policy=AuthPolicy(enabled=True))
        client = TestClient(app)
        response = client.post("/sessions", json={
            "username": record.username,
            "password": password if password is not None else test_password})
        if response.status_code == 201:
            client.headers["Authorization"] = f"Bearer {response.json()['token']}"
        return client, response, record

    return build


@pytest.fixture
def sandbox_settings(service_settings, tmp_path):
    """A writable copy of the real settings.yaml.

    A copy, not a fixture file: the config-editor tests are only worth anything
    against the actual 400-line contract, with its Vietnamese header keys and its
    comment density. And obviously never the real one — those tests write.
    """
    import shutil
    from dataclasses import replace
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(service_settings.config_dir / "settings.yaml",
                 config_dir / "settings.yaml")
    return replace(service_settings, config_dir=config_dir)


@pytest.fixture
def seeded_config(repo, sandbox_settings):
    """The config tables, loaded from the sandboxed contract.

    Since M8/1.6 the tables ARE the editable contract — an editor test against
    empty tables would be testing the "seed me first" refusal rather than the
    editor. Seeded from the sandbox copy, not the real `config/`, so the two agree
    about what the base contract is when a test compares a rendered diff.
    """
    from service import config_import
    with repo._conn() as conn:                                  # noqa: SLF001
        config_import.import_settings(conn, sandbox_settings.config_dir,
                                      changed_by="test", source="seed")
        conn.commit()
    return repo


@pytest.fixture
def editor_client(repo, store, sandbox_settings, issue_session, seeded_config):
    """Like `make_client`, but pointed at a sandboxed copy of settings.yaml so an
    applied proposal cannot rewrite the real one, and with the config tables
    seeded from that copy."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from service.api import create_app
    from service.auth import AuthPolicy

    def build(role: str = "recon.admin", subject: str | None = None):
        app = create_app(repo, store, settings=sandbox_settings,
                         policy=AuthPolicy(enabled=True))
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {issue_session(role, subject)}"
        return client

    return build


@pytest.fixture
def store(tmp_path: Path):
    from service.artifacts import LocalArtifactStore
    return LocalArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def service_settings(tmp_path: Path, database_url: str):
    from service.config import ServiceSettings
    return ServiceSettings(
        database_url=database_url,
        config_dir=ROOT / "config",
        input_root=tmp_path / "input",
        artifact_root=tmp_path / "artifacts",
        scratch_root=tmp_path / "scratch",
        upload_root=tmp_path / "uploads",
        worker_id="test-worker",
        lease_seconds=60,
        poll_interval_s=0.01,
        log_flush_lines=5,
        log_flush_seconds=0.0,
        exception_row_cap=50,
    )
